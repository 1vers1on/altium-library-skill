#!/usr/bin/env python3
"""Fail if the toolkit imports anything outside the standard library.

The whole premise of this repository is that it runs on a stock interpreter with no
install step and no network. That is easy to state and easy to break: one convenience
import of ``requests`` or ``numpy`` during maintenance and the claim is false for
every user who does not happen to have it.

This runs a full selftest in a subprocess and compares the modules loaded afterwards
against :data:`sys.stdlib_module_names`, ignoring whatever the host environment had
already injected before the toolkit started (setuptools' ``_distutils_hack``, sitecustomize
hooks and the like). Run it locally the same way CI does::

    python .github/check_no_dependencies.py
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = textwrap.dedent(
    """
    import json, pathlib, runpy, sys

    # Whatever the environment injected before we touched anything is not ours.
    baseline = set(sys.modules)

    root = pathlib.Path(sys.argv[1])
    sys.path.insert(0, str(root / "scripts"))
    sys.argv = ["toolkit.py", "selftest"]
    try:
        runpy.run_path(str(root / "scripts" / "toolkit.py"), run_name="__main__")
    except SystemExit as exit_code:
        status = exit_code.code or 0
    else:
        status = 0

    stdlib = set(sys.stdlib_module_names)
    foreign = sorted(
        name
        for name, module in sys.modules.items()
        if module is not None
        and name not in baseline
        and name.split(".")[0] not in stdlib
        and not name.startswith(("altium_tools", "__"))
        and getattr(module, "__file__", None)
    )
    print("RESULT " + json.dumps({"status": status, "foreign": foreign}))
    """
)


def main():
    run = subprocess.run(
        [sys.executable, "-c", PROBE, str(ROOT)],
        capture_output=True,
        text=True,
    )
    marker = [l for l in run.stdout.splitlines() if l.startswith("RESULT ")]
    if not marker:
        print(run.stdout[-4000:])
        print(run.stderr[-4000:], file=sys.stderr)
        print("error: the selftest probe did not report a result.", file=sys.stderr)
        return 1

    report = json.loads(marker[-1][len("RESULT ") :])
    if report["status"] != 0:
        print(run.stdout[-4000:])
        print(run.stderr[-4000:], file=sys.stderr)
        print("error: the selftest failed; dependency audit is inconclusive.", file=sys.stderr)
        return 1
    if report["foreign"]:
        print("error: the toolkit imported non-stdlib modules:", file=sys.stderr)
        for name in report["foreign"]:
            print(f"  - {name}", file=sys.stderr)
        print(
            "This repository must run on a stock interpreter with no install step.\n"
            "Bundle what you need under scripts/altium_tools/vendor/ instead.",
            file=sys.stderr,
        )
        return 1
    print(
        f"ok: a full selftest on Python {sys.version.split()[0]} imported only the "
        "standard library and the bundled altium_tools package."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
