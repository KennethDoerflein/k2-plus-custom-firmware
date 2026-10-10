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
from types import SimpleNamespace

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
    for base in cls.bases:
        base_name = getattr(base, "id", getattr(base, "attr", None))
        if base_name:
            for extra_file in EXTRAS.glob("*.py"):
                sub_tree = parse(extra_file.name)
                for sub in ast.walk(sub_tree):
                    if isinstance(sub, ast.ClassDef) and sub.name == base_name:
                        for node in ast.walk(sub):
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                members.add(node.name)
                            elif isinstance(node, ast.Attribute) and chain(node.value) == "self":
                                if isinstance(node.ctx, ast.Store):
                                    members.add(node.attr)
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
        source_box = (EXTRAS / "box.py").read_text()
        tree_box = ast.parse(source_box)
        body = function_source(tree_box, source_box, "_register_tools")
        self.assertIn("select_tool", body)
        self.assertNotIn("change_engine.change(", body)

        routing_path = EXTRAS / "box_routing.py"
        source_routing = (routing_path if routing_path.exists() else EXTRAS / "box.py").read_text()
        tree_routing = ast.parse(source_routing)
        body = function_source(tree_routing, source_routing, "cmd_set_routing")
        self.assertIn("select_tool", body)
        self.assertNotIn("change_engine.change(", body)


class RfidStartup(unittest.TestCase):
    def test_cached_present_tags_populate_remaining_without_forced_read(self):
        sys.path.insert(0, str(ROOT))
        self.addCleanup(sys.path.remove, str(ROOT))
        import extras.box as box_module

        class FakeDriver:
            def __init__(self):
                self.forced_reads = []

            def set_rfid_insert_reading(self, enabled, timeout):
                return SimpleNamespace(status=box_protocol.STATUS_OK)

            def query_slot_mask(self, timeout):
                return SimpleNamespace(
                    status=box_protocol.STATUS_OK, value=0b0011)

            def query_rfid_records(self, mask, timeout):
                return SimpleNamespace(
                    status=box_protocol.STATUS_OK,
                    records={"A": "A" * 40, "B": "B" * 40},
                    fields={"A": {"mat_id": "A"}, "B": {"mat_id": "B"}})

            def query_rfid_remaining(self, mask, timeout):
                return SimpleNamespace(
                    status=box_protocol.STATUS_OK,
                    values={"A": 2, "B": 96, "C": 0, "D": 0})

            def force_rfid_read(self, mask):
                self.forced_reads.append(mask)

        box = object.__new__(box_module.Box)
        driver = FakeDriver()
        box.drivers = {1: driver}
        box.store = SimpleNamespace(
            setting=lambda name, default=False: (
                True if name == "rfid_startup_reading_enabled" else default))
        box.rfid_presence = {}
        box.rfid_live_slots = set()
        box.rfid_percent = {}
        box.operation_depth = 0
        box.snapshot = box_module.BoxSnapshot()

        box._initialize_rfid()

        self.assertEqual(box.rfid_percent, {0: 2, 1: 96})
        self.assertEqual(driver.forced_reads, [])


class StartupUnload(unittest.TestCase):
    def make_box(self, recovery_point=None, loaded_slot=0):
        sys.path.insert(0, str(ROOT))
        self.addCleanup(sys.path.remove, str(ROOT))
        import extras.box as box_module

        scripts = []
        recovery = None
        if recovery_point is not None:
            recovery = SimpleNamespace(
                store=SimpleNamespace(
                    recovery_point=lambda: recovery_point))

        box = object.__new__(box_module.Box)
        box.drivers = {1: None}
        box.store = SimpleNamespace(
            setting=lambda name, default=False: (
                True if name == "unload_at_startup_enabled" else default))
        box.printer = SimpleNamespace(
            lookup_object=lambda name, default=None: (
                recovery if name == "power_loss_recovery" else default))
        box.gcode = SimpleNamespace(
            run_script_from_command=scripts.append)
        box.change_engine = SimpleNamespace(
            _is_print_active=lambda: False,
            _is_print_paused=lambda: False)
        box.drivers_ready = True
        box.klippy_ready = True
        box.operation_depth = 0
        box.runout_active = False
        box.read_live_state = lambda: box_module.BoxSnapshot(
            data_ready=True, loaded_slot=loaded_slot)
        box.is_valid_slot = lambda slot: slot == loaded_slot and slot >= 0
        box._info = lambda _gcmd, _message: None
        box._warn = lambda _message: None
        return box, scripts

    def test_startup_unload_is_scheduled_once_after_both_ready(self):
        box, _scripts = self.make_box()
        callbacks = []
        box.reactor = SimpleNamespace(register_callback=callbacks.append)
        box.startup_unload_scheduled = False
        box.klippy_ready = True
        box.drivers_ready = False

        box._schedule_startup_unload()
        self.assertEqual(callbacks, [])

        box.drivers_ready = True
        box._schedule_startup_unload()
        box._schedule_startup_unload()
        self.assertEqual(len(callbacks), 1)

    def test_loaded_filament_unloads_but_recovery_checkpoint_is_preserved(self):
        box, scripts = self.make_box()
        box._startup_unload(0.0)
        self.assertEqual(scripts, ["BOX_UNLOAD"])

        recovering_box, recovery_scripts = self.make_box(recovery_point={})
        recovering_box._startup_unload(0.0)
        self.assertEqual(recovery_scripts, [])


class CommandRegistration(unittest.TestCase):
    """Runs the real registration code, which static checks cannot cover."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        self.addCleanup(sys.path.remove, str(ROOT))
        import extras.box as box_module
        self.box_module = box_module

        class FakeGcode:
            def __init__(self):
                self.handlers = {}

            def register_command(self, name, handler, desc=None):
                self.handlers[name] = handler

        engine = type("Engine", (), {
            "parse_flush_volumes": lambda *a: None,
            "capture_pause": lambda *a: None,
            "prepare_resume": lambda *a: None,
            "complete_pause_resume": lambda *a: None})()
        box = object.__new__(box_module.Box)
        box.gcode = FakeGcode()
        box.change_engine = engine
        box._register_commands()
        self.handlers = box.gcode.handlers

    def test_mixin_commands_are_registered(self):
        for name in ("BOX_SET_ROUTING", "BOX_CLEAR_ROUTING",
                     "BOX_SET_DRY_MODE", "BOX_GET_DRY_MODE", "BOX_PAUSE_DRY",
                     "CONTINUE_PAUSE_DRY", "_BOX_SET_UNLOAD_AT_STARTUP",
                     "BOX_SET_AUTO_DRY_MODE",
                     "BOX_SET_AUTO_HUMIDITY_MODE"):
            self.assertIn(name, self.handlers)

    def test_widget_commands_are_guarded(self):
        for name in self.box_module.SAFE_WIDGET_COMMANDS:
            self.assertIn(name, self.handlers, name)
            self.assertEqual(self.handlers[name].__name__, "guarded", name)


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
