#!/usr/bin/env python3
"""CLAUDE.md R1: the assignment pack is read-only.

Compares every file listed in scripts/pack.sha256 (taken from the import commit
151af6b) with the working tree, and with --staged also with the index (what is
about to be committed). Exits 1 and names each file that differs or is missing.

Usage: python3 scripts/check_pack.py [--staged]
Standard library only (Python 3.9+).
"""
import hashlib
import os
import subprocess
import sys

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
MANIFEST = os.path.join(ROOT, "scripts", "pack.sha256")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    staged = "--staged" in sys.argv[1:]
    expected = {}
    with open(MANIFEST) as fh:
        for line in fh:
            if line.strip():
                digest, path = line.rstrip("\n").split("  ", 1)
                expected[path] = digest

    problems = []
    for path, digest in sorted(expected.items()):
        full = os.path.join(ROOT, path)
        if not os.path.exists(full):
            problems.append(f"missing on disk: {path}")
        else:
            with open(full, "rb") as fh:
                if sha(fh.read()) != digest:
                    problems.append(f"changed on disk: {path}")
        if staged:
            blob = subprocess.run(["git", "show", f":{path}"], cwd=ROOT, capture_output=True)
            if blob.returncode != 0:
                problems.append(f"missing from index: {path}")
            elif sha(blob.stdout) != digest:
                problems.append(f"changed in index: {path}")

    if problems:
        print("check_pack: FAIL (CLAUDE.md R1, the pack is read-only)")
        for p in problems:
            print(f"  {p}")
        sys.exit(1)
    print(f"check_pack: ok, {len(expected)} pack files match the import commit" + (" (disk and index)" if staged else " (disk)"))


if __name__ == "__main__":
    main()
