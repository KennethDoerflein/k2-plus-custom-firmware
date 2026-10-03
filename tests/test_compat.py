"""Cross-module compatibility checks, run after every upstream merge.

Upstream and our fork edit the same modules, and a merge that keeps one side
of a file can leave the other side calling something that no longer exists.
Each of those has crashed the printer at boot before. These checks catch the
mismatch statically, with the standard library only (no Klipper needed):

    python -m unittest discover -s tests -v
"""

import ast
import dataclasses
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRAS = ROOT / "extras"
sys.path.insert(0, str(EXTRAS))

import box_protocol  # noqa: E402


def parse(name):
    path = EXTRAS / name
    return ast.parse(path.read_text(), filename=str(path))


def chain(node):
    """Dotted name of an attribute chain, e.g. self.change_engine.change."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def accessed(tree, prefix):
    """Attribute names read on `prefix` (a dotted name such as self.box)."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and chain(node.value) == prefix:
            found.add(node.attr)
    return found


def class_members(tree, class_name):
    """Methods, class attributes and self.<x> assignments of a class."""
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == class_name:
            break
    else:
        raise AssertionError("class %s not found" % class_name)
    members = set()
    for node in ast.walk(cls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members.add(node.name)
        elif isinstance(node, ast.Attribute) and chain(node.value) == "self":
            if isinstance(node.ctx, ast.Store):
                members.add(node.attr)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            members.add(node.id)
    return members


def function_source(tree, source, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError("function %s not found" % name)


class AutoAddressContract(unittest.TestCase):
    """box_addr.py consumes what box_protocol.py decodes (the `mode` crash)."""

    def test_reply_fields_cover_what_box_addr_reads(self):
        read = accessed(parse("box_addr.py"), "reply")
        fields = {f.name for f in dataclasses.fields(
            box_protocol.AutoAddressReply)}
        self.assertLessEqual(
            read, fields,
            "box_addr.py reads AutoAddressReply attributes that "
            "box_protocol.py does not define: %s" % sorted(read - fields))

    def test_client_methods_cover_what_box_addr_calls(self):
        called = accessed(parse("box_addr.py"), "client")
        methods = class_members(parse("box_protocol.py"), "AutoAddressClient")
        self.assertLessEqual(called, methods, sorted(called - methods))


class BoxInterfaces(unittest.TestCase):
    """box.py and box_change.py call into each other."""

    def test_box_uses_only_existing_change_engine_members(self):
        used = accessed(parse("box.py"), "self.change_engine")
        members = class_members(parse("box_change.py"), "BoxChangeEngine")
        self.assertLessEqual(used, members, sorted(used - members))

    def test_change_engine_uses_only_existing_box_members(self):
        used = accessed(parse("box_change.py"), "self.box")
        members = class_members(parse("box.py"), "Box")
        self.assertLessEqual(used, members, sorted(used - members))

    def test_tool_commands_go_through_select_tool(self):
        """T<n> must use select_tool or BOX_PRINT_START maps are ignored."""
        source = (EXTRAS / "box.py").read_text()
        tree = ast.parse(source)
        for name in ("_register_tools", "cmd_set_routing"):
            body = function_source(tree, source, name)
            self.assertIn("select_tool", body, name)
            self.assertNotIn("change_engine.change(", body, name)


class ReleaseFiles(unittest.TestCase):
    def test_every_module_compiles(self):
        for path in sorted(EXTRAS.glob("*.py")):
            compile(path.read_text(), str(path), "exec")

    def test_release_index_is_current(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "release_index.py"),
             "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
