"""Pre-release check (not shipped): build the file list the Comfy Registry archive gets (git files minus .comfyignore)
and make sure it has no tests/ and none of the words scanners flag. Run from the repo root:  python tests/check_registry_package.py"""
import fnmatch
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORDS = re.compile(r"\b(subprocess|__import__|eval|exec)\b")


def files():
    out = []
    ignore = [l.strip() for l in open(os.path.join(ROOT, ".comfyignore"), encoding="utf-8")
              if l.strip() and not l.startswith("#")]
    listing = os.popen(f'git -C "{ROOT}" ls-files').read().split("\n")
    for f in filter(None, listing):
        if any(f.startswith(p) if p.endswith("/") else fnmatch.fnmatch(f, p) for p in ignore):
            continue
        out.append(f)
    return out


def main():
    bad = []
    fl = files()
    if any(f.startswith("tests/") for f in fl):
        bad.append("tests/ would be shipped")
    for f in fl:
        if not f.endswith((".py", ".js", ".json", ".toml", ".md", ".txt", ".yaml", ".yml")):
            continue
        try:
            text = open(os.path.join(ROOT, f), encoding="utf-8").read()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        if f.endswith((".py", ".js")):
            for n, line in enumerate(text.splitlines(), 1):
                m = WORDS.search(line)
                if m:
                    bad.append(f"{f}:{n}: {m.group(1)}")
    print(f"{len(fl)} files shipped")
    for b in bad:
        print("FLAG", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
