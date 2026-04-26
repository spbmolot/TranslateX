"""
Устанавливает git hooks для проекта TranslateX.

Создаёт .git/hooks/pre-commit, который прогоняет pytest перед каждым
commit-ом. Если тесты красные — commit отменяется.

Использование:
    python scripts/install_hooks.py
"""
import os
import stat
import sys
from pathlib import Path


PRE_COMMIT_BODY = """#!/bin/sh
# TranslateX pre-commit hook: runs pytest before every commit.
# Skip with: git commit --no-verify
set -e

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

echo "[pre-commit] Running pytest..."
if ! python -m pytest tests -q --tb=line 2>&1 | tail -n 20; then
    echo ""
    echo "[pre-commit] TESTS FAILED -- commit aborted."
    echo "Fix the tests or bypass with: git commit --no-verify"
    exit 1
fi

echo "[pre-commit] OK"
exit 0
"""


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    hooks_dir = repo_root / ".git" / "hooks"

    if not hooks_dir.exists():
        print(f"ERROR: {hooks_dir} не найдена. Сначала выполни 'git init'.")
        return 1

    pre_commit_path = hooks_dir / "pre-commit"

    # Пишем с LF, без BOM — важно для bash shebang
    pre_commit_path.write_bytes(
        PRE_COMMIT_BODY.replace("\r\n", "\n").encode("utf-8")
    )

    # Executable-флаг для unix-like (на Windows Git эмулирует)
    try:
        current = pre_commit_path.stat().st_mode
        pre_commit_path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass  # на Windows chmod — no-op, это ОК

    print(f"Installed: {pre_commit_path}")
    print("Hooks теперь прогоняют pytest перед каждым commit.")
    print("Обход при необходимости: git commit --no-verify")
    return 0


if __name__ == "__main__":
    sys.exit(main())
