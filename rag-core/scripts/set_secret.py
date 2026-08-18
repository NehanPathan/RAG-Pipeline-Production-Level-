#!/usr/bin/env python
"""Set a secret in .env without it landing anywhere it shouldn't.

    python scripts/set_secret.py TAVILY_API_KEY

Prompts with hidden input and rewrites the single line in place. The point is
what it *avoids*: typing `TAVILY_API_KEY=tvly-...` into a terminal puts the
key in shell history, and pasting it into a chat or an issue puts it in a
transcript. Neither is recoverable once done.

Also refuses to print the value back, and reports only a masked form, so a
screen-share or a scrollback does not leak it either.
"""
from __future__ import annotations

import argparse
import getpass
import shutil
import sys
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def set_key(path: Path, key: str, value: str) -> str:
    """Replace or append `key`. Returns 'updated' or 'added'."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.partition("=")[0].strip() == key:
            lines[index] = f"{key}={value}\n"
            path.write_text("".join(lines), encoding="utf-8")
            return "updated"

    if lines and not lines[-1].endswith("\n"):
        lines.append("\n")
    lines.append(f"{key}={value}\n")
    path.write_text("".join(lines), encoding="utf-8")
    return "added"


def main() -> int:
    parser = argparse.ArgumentParser(description="Set a secret in .env safely.")
    parser.add_argument("key", help="Env var name, e.g. TAVILY_API_KEY")
    parser.add_argument(
        "--no-backup", action="store_true", help="Skip writing .env.backup first"
    )
    args = parser.parse_args()
    key = args.key.strip().upper()

    if not ENV_PATH.exists():
        print(f"No .env at {ENV_PATH}. Run:  cp .env.example .env", file=sys.stderr)
        return 1

    value = getpass.getpass(f"{key} (input hidden): ").strip()
    if not value:
        print("Empty value — nothing changed.")
        return 1

    if not args.no_backup:
        shutil.copy(ENV_PATH, ENV_PATH.with_suffix(".env.backup"))

    action = set_key(ENV_PATH, key, value)
    print(f"{key} {action}: {mask(value)}")
    print("Restart the API for it to take effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
