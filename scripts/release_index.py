#!/usr/bin/env python3
"""Regenerate (or verify) the files that hold checksums of other files.

These files change on every upstream merge and conflict constantly, so they
must never be merged by hand:

* extras/manifest.json   sha256/size of every file in extras/
* index                  sha256/size of bootstrap, swap and the manifest
* install.py             the SWAP_SHA256 fallback constant

Hashes are computed over LF-normalised content because git stores LF and the
printer runs Linux; a Windows checkout with CRLF must hash the same.
kernel.img and rootfs.ext2 are Git LFS blobs rebuilt by CI, so their index
entries are left untouched.

Usage:
    scripts/release_index.py            # rewrite the generated files
    scripts/release_index.py --check    # exit 1 if anything is stale
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRAS = ROOT / "extras"
MANIFEST = EXTRAS / "manifest.json"
INDEX = ROOT / "index"
INSTALL = ROOT / "install.py"
SWAP_RE = re.compile(r'^(SWAP_SHA256 = ")[0-9a-f]{64}(")$', re.MULTILINE)


def digest(path):
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def dump(obj):
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def expected_manifest():
    current = json.loads(MANIFEST.read_text())
    files = {
        path.name: digest(path)
        for path in sorted(EXTRAS.iterdir())
        if path.is_file() and path.suffix in (".py", ".json")
        and path.name != MANIFEST.name
    }
    return {"files": files, "format": current["format"],
            "version": current["version"]}


def expected_index(manifest_text):
    index = json.loads(INDEX.read_text())
    index["bootstrap"] = digest(ROOT / "bootstrap")
    index["swap"] = digest(ROOT / "swap")
    manifest = hashlib.sha256(manifest_text.encode()).hexdigest()
    index["extras"] = {"manifest_sha256": manifest,
                       "manifest_size": len(manifest_text.encode())}
    return index


def expected_install(swap_sha256):
    text = INSTALL.read_text()
    if not SWAP_RE.search(text):
        raise SystemExit("install.py: SWAP_SHA256 constant not found")
    return SWAP_RE.sub(lambda m: m.group(1) + swap_sha256 + m.group(2), text)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="report stale files and exit 1 instead of writing")
    args = parser.parse_args()

    manifest_text = dump(expected_manifest())
    index = expected_index(manifest_text)
    targets = {
        MANIFEST: manifest_text,
        INDEX: dump(index),
        INSTALL: expected_install(index["swap"]["sha256"]),
    }
    stale = []
    for path, text in targets.items():
        current = path.read_text().replace("\r\n", "\n")
        if current != text:
            stale.append(path)
            if not args.check:
                path.write_text(text, newline="\n")
    names = ", ".join(str(p.relative_to(ROOT)) for p in stale)
    if args.check:
        if stale:
            print("STALE (run scripts/release_index.py): " + names)
            return 1
        print("release index is up to date")
        return 0
    print("updated: " + names if stale else "already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
