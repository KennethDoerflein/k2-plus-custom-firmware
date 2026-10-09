#!/usr/bin/env python3
"""Regenerate (or verify) the files that hold checksums of other files.

These files change on every upstream merge and conflict constantly, so they
must never be merged by hand:

* extras/manifest.json   sha256/size of every file in extras/
* index                  sha256/size of bootstrap, swap, extras, kernel, and rootfs
* install.py             SWAP_SHA256, KERNEL_SHA256, ROOTFS_SHA256 constants
* bootstrap              FIRMWARE_VERSION constant when releasing

Hashes are computed over LF-normalised content for text/code files because git
stores LF and the printer runs Linux; a Windows checkout with CRLF must hash the
same. Binary images (kernel.img, rootfs.ext2) are hashed in raw binary chunks.

Usage:
    scripts/release_index.py                        # rewrite the generated files
    scripts/release_index.py --check                # exit 1 if anything is stale
    scripts/release_index.py --version 6.19         # update version across all targets
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
BOOTSTRAP = ROOT / "bootstrap"
SWAP = ROOT / "swap"
KERNEL = ROOT / "kernel.img"
ROOTFS = ROOT / "rootfs.ext2"

VERSION_RE = re.compile(r'^(FIRMWARE_VERSION = ")[^"]+(")$', re.MULTILINE)
SWAP_RE = re.compile(r'^(SWAP_SHA256 = ")[0-9a-f]{64}(")$', re.MULTILINE)
ROOTFS_RE = re.compile(r'^(ROOTFS_SHA256 = ")[0-9a-f]{64}(")$', re.MULTILINE)
KERNEL_RE = re.compile(r'^(KERNEL_SHA256 = ")[0-9a-f]{64}(")$', re.MULTILINE)


def digest(path):
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def digest_binary(path):
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
            size += len(chunk)
    return {"sha256": h.hexdigest(), "size": size}


def dump(obj):
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def expected_manifest(version=None):
    current = json.loads(MANIFEST.read_text())
    files = {
        path.name: digest(path)
        for path in sorted(EXTRAS.iterdir())
        if path.is_file() and path.suffix in (".py", ".json")
        and path.name != MANIFEST.name
    }
    return {
        "files": files,
        "format": current["format"],
        "version": version if version else current.get("version", "")
    }


def expected_index(manifest_text, version=None):
    index = json.loads(INDEX.read_text())
    index["bootstrap"] = digest(BOOTSTRAP)
    index["swap"] = digest(SWAP)
    manifest = hashlib.sha256(manifest_text.encode()).hexdigest()
    index["extras"] = {"manifest_sha256": manifest,
                       "manifest_size": len(manifest_text.encode())}
    # Only hash binary images if present and not an unpulled Git LFS pointer (<1MB)
    if KERNEL.is_file() and KERNEL.stat().st_size > 1024 * 1024:
        index["kernel"] = digest_binary(KERNEL)
    if ROOTFS.is_file() and ROOTFS.stat().st_size > 1024 * 1024:
        index["rootfs"] = digest_binary(ROOTFS)
    if version:
        index["version"] = version
    return index


def expected_install(index, version=None):
    text = INSTALL.read_text()
    if not SWAP_RE.search(text):
        raise SystemExit("install.py: SWAP_SHA256 constant not found")
    text = SWAP_RE.sub(lambda m: m.group(1) + index["swap"]["sha256"] + m.group(2), text)
    if "kernel" in index and KERNEL_RE.search(text):
        text = KERNEL_RE.sub(lambda m: m.group(1) + index["kernel"]["sha256"] + m.group(2), text)
    if "rootfs" in index and ROOTFS_RE.search(text):
        text = ROOTFS_RE.sub(lambda m: m.group(1) + index["rootfs"]["sha256"] + m.group(2), text)
    if version and VERSION_RE.search(text):
        text = VERSION_RE.sub(lambda m: m.group(1) + version + m.group(2), text)
    return text


def expected_bootstrap(version=None):
    text = BOOTSTRAP.read_text()
    if version and VERSION_RE.search(text):
        text = VERSION_RE.sub(lambda m: m.group(1) + version + m.group(2), text)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="report stale files and exit 1 instead of writing")
    parser.add_argument("--version", type=str, default=None,
                        help="update firmware version across manifest, index, install.py, and bootstrap")
    args = parser.parse_args()

    version = args.version.lstrip("v") if args.version else None

    manifest_text = dump(expected_manifest(version))
    index = expected_index(manifest_text, version)
    targets = {
        MANIFEST: manifest_text,
        INDEX: dump(index),
        INSTALL: expected_install(index, version),
        BOOTSTRAP: expected_bootstrap(version),
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
