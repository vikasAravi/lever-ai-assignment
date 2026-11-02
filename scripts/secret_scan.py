#!/usr/bin/env python3
"""CLAUDE.md R2: no secrets anywhere but .env.

Scans text files for
  1. any value from .env (when .env exists) of 8 or more characters, and
  2. secret-shaped strings: Anthropic keys, GLPI token headers/parameters with a
     long value, private-key blocks.
Default scope: every tracked file. --staged: the staged content of files about to
be committed. --paths A B ...: those files (used for logs, test output, transcripts).
Prints file:line and the rule, never the secret. Exits 1 on any finding.

Standard library only (Python 3.9+).
"""
import os
import re
import subprocess
import sys

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
PATTERNS = [
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("glpi-token", re.compile(r"(?i)(app[-_]token|session[-_]token|user_token)[\"'\s:=]+(user_token\s+)?[A-Za-z0-9]{20,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def env_values():
    path = os.path.join(ROOT, ".env")
    values = {}
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    value = value.strip().strip("'\"")
                    if len(value) >= 8:
                        values[key.strip()] = value
    return values


def files_to_scan(mode, extra):
    if mode == "paths":
        return [(p, None) for p in extra]
    if mode == "staged":
        names = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                               cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        return [(n, "staged") for n in names]
    names = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return [(n, None) for n in names]


def read(name, source):
    if source == "staged":
        data = subprocess.run(["git", "show", f":{name}"], cwd=ROOT, capture_output=True).stdout
    else:
        full = name if os.path.isabs(name) else os.path.join(ROOT, name)
        if not os.path.isfile(full):
            return None
        with open(full, "rb") as fh:
            data = fh.read()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None  # binary (seed dump, workbook): nothing text-shaped to leak


def main():
    args = sys.argv[1:]
    mode = "staged" if "--staged" in args else ("paths" if "--paths" in args else "tracked")
    extra = args[args.index("--paths") + 1:] if mode == "paths" else []
    secrets = env_values()
    findings = []
    scanned = 0
    for name, source in files_to_scan(mode, extra):
        if os.path.basename(name) == ".env":
            continue
        text = read(name, source)
        if text is None:
            continue
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            for key, value in secrets.items():
                if value in line:
                    findings.append(f"{name}:{lineno}: contains the value of .env {key}")
            for rule, rx in PATTERNS:
                if rx.search(line):
                    findings.append(f"{name}:{lineno}: matches {rule}")
    if findings:
        print("secret_scan: FAIL (CLAUDE.md R2)")
        for f in findings:
            print(f"  {f}")
        sys.exit(1)
    print(f"secret_scan: ok, {scanned} text files scanned ({mode}), {len(secrets)} .env values checked")


if __name__ == "__main__":
    main()
