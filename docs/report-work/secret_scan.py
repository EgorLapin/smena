# -*- coding: utf-8 -*-
# Быстрая проверка отслеживаемых git-файлов на секреты перед публичным доступом.
# Только чтение: git ls-files / git grep.
import re
import subprocess

PATTERNS = {
    "github token": r"ghp_[A-Za-z0-9]{20,}",
    "aws key": r"AKIA[0-9A-Z]{16}",
    "private key block": r"BEGIN [A-Z ]*PRIVATE KEY",
    "slack token": r"xox[baprs]-[A-Za-z0-9-]{10,}",
    "generic secret assign": r"(?i)(secret|token|api_?key|passwd|password)\s*[:=]\s*['\"][^'\"]{8,}['\"]",
}
IGNORE_OK = re.compile(r"demo-pass-123|\.env\.example|SECRET_KEY не задан|example|dummy|not-a-real")


def run(*args):
    return subprocess.run(["git"] + list(args), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


files = run("ls-files").stdout.splitlines()
print("отслеживается файлов:", len(files))

hits = 0
for label, pat in PATTERNS.items():
    r = run("grep", "-n", "-I", "-E", pat, "--", *files)
    lines = [ln for ln in r.stdout.splitlines() if not IGNORE_OK.search(ln)]
    if lines:
        print(f"\n[{label}] совпадений: {len(lines)}")
        for ln in lines[:12]:
            print("   ", ln[:220])
        hits += len(lines)
    else:
        print(f"[{label}] чисто")

print("\nитог подозрительных строк:", hits)
