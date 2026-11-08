"""v0.3.7: the shipped files must not contain what the Comfy Registry scanner flags.

Found with https://api.comfy.org/nodes/comfyui-h3md-director/versions?include_status_reason=true (2026-11-08):
every version from 0.2.0 was Flagged by YARA rules (severity "info", one hit is enough):
  python_environment_manipulation  ($env_read2: reading environment variables)
  python_network_operations        ($socket4: also matches ".bind(" in JavaScript)
  python_bytecode_manipulation     (__import__ / importlib)
  python_minified_code             ($semicolon_5 / $multi_import_7, seen on other node packs)
This test checks the files that go into the registry archive (git files minus .comfyignore)."""
import fnmatch
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PATTERNS = {
    "environment variable": re.compile(r"\bos\.environ\b|\bgetenv\s*\(|\bputenv\s*\(|\benviron\.get\s*\("),
    "dynamic import": re.compile(r"\bimportlib\b|__import__"),
    "bind (network rule)": re.compile(r"\.bind\s*\("),
    "socket / connect": re.compile(r"\bsocket\.socket\b|\bcreate_connection\b|\bWebSocket\s*\("),
    "eval / exec": re.compile(r"(?<![\w.])(eval|exec)\s*\(|\.(eval|exec)\s*\("),
    "process": re.compile(r"\bsubprocess\b|\bos\.system\s*\(|\bos\.popen\s*\(|\bPopen\s*\("),
    "code objects": re.compile(r"\bmarshal\b|\bpickle\.loads?\s*\(|\bnew Function\s*\("),
}
CODE_EXT = (".py", ".js", ".mjs", ".ts", ".html", ".css", ".md", ".txt", ".json", ".toml", ".yml", ".yaml")  # 0.3.7 was
# flagged for ".bind(" quoted in CHANGELOG.md: the network rule reads every file, not only code


def _ignored(rel, rules):
    for r in rules:
        if r.endswith("/") and (rel + "/").startswith(r):
            return True
        if fnmatch.fnmatch(rel, r):
            return True
    return False


def shipped_files():
    rules = [ln.strip() for ln in open(os.path.join(ROOT, ".comfyignore"), encoding="utf-8")
             if ln.strip() and not ln.startswith("#")]
    files = [f for f in subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True,
                                       check=True).stdout.split("\0") if f]
    return [f for f in files if not _ignored(f, rules) and f.endswith(CODE_EXT)]


def findings():
    out = []
    for rel in shipped_files():
        text = open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read()
        for n, line in enumerate(text.splitlines(), 1):
            for name, rx in PATTERNS.items():
                if rx.search(line):
                    out.append(f"{rel}:{n}: {name}: {line.strip()[:120]}")
            if rel.endswith(".py"):
                if line.count(";") >= 5:
                    out.append(f"{rel}:{n}: minified (semicolons): {line.strip()[:120]}")
                m = re.match(r"\s*import\s+(.+)$", line)
                if m and len(m.group(1).split(",")) >= 7:
                    out.append(f"{rel}:{n}: minified (one-line imports): {line.strip()[:120]}")
    return out


def test_no_registry_scanner_patterns():
    hits = findings()
    assert not hits, "registry scanner patterns in shipped files:\n" + "\n".join(hits)


if __name__ == "__main__":
    print("\n".join(findings()) or "clean")
