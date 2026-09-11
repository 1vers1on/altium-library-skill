#!/usr/bin/env python3
"""Fail if scripts/INTEGRITY.json no longer matches the toolkit sources.

Every ``validation.json`` the toolkit writes records whether the toolkit was edited
after its manifest was recorded. That signal is only worth anything if the manifest
is kept current: a stale one makes every user's report carry a "modified" warning,
and a warning that is usually false is a warning nobody reads.

Compare hashes rather than re-recording and diffing the file — the manifest carries a
``recorded`` timestamp that changes on every run, so a diff would always be dirty.
Run it the same way CI does::

    python .github/check_integrity.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from altium_tools.provenance import integrity_status  # noqa: E402


def main():
    state, notes = integrity_status()
    if state == "unmodified":
        print("ok: scripts/INTEGRITY.json matches the toolkit sources")
        return 0
    print(f"error: scripts/INTEGRITY.json is {state}.", file=sys.stderr)
    for note in notes:
        print(f"  - {note}", file=sys.stderr)
    print(
        "\nIf the change was intentional, run:\n"
        "    python scripts/toolkit.py record-integrity\n"
        "and commit the updated manifest with it.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
