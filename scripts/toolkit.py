#!/usr/bin/env python3
"""Altium library toolkit: build native .SchLib/.PcbLib files and verify them by readback.

Runs on a stock Python 3.10+ with no third-party packages and no network access; the
container, library and image code it needs is bundled under ``altium_tools/vendor``.

See SKILL.md for the required workflow and references/ for the JSON schemas.
"""

import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from altium_tools import __version__  # noqa: E402
from altium_tools.provenance import (  # noqa: E402
    refuse_writes_into_skill,
    record_integrity,
    integrity_status,
)


def read_json(path, what):
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"error: no such {what}: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise SystemExit(f"error: {what} {path} is not valid JSON: {error}")


def warn_if_modified():
    state, notes = integrity_status()
    if state != "unmodified":
        print(f'WARNING: toolkit integrity is "{state}".', file=sys.stderr)
        for note in notes[:10]:
            print(f"  - {note}", file=sys.stderr)
        print(
            "  This is recorded in every validation.json this run writes. Editing the "
            "toolkit to make a check pass is never a fix for a source-JSON error.",
            file=sys.stderr,
        )
    return state


def build_parser():
    parser = argparse.ArgumentParser(
        prog="toolkit.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"altium-library toolkit {__version__}",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name, help_text):
        return sub.add_parser(name, help=help_text, description=help_text)

    for name, help_text in (
        ("build-schlib", "Write a .SchLib from a schematic JSON document."),
        ("build-pcblib", "Write a .PcbLib from a footprint JSON document."),
    ):
        command = add(name, help_text)
        command.add_argument("source", type=Path, help="Source JSON.")
        command.add_argument(
            "-o",
            "--output",
            type=Path,
            required=True,
            help="Library to write.",
        )
        command.add_argument(
            "--allow-skill-writes",
            action="store_true",
            help="Permit writing inside the skill directory. Maintenance only.",
        )
        if name == "build-pcblib":
            command.add_argument(
                "--layers",
                type=Path,
                help="Layer profile, overriding layer_profile.",
            )
            command.add_argument(
                "--template", type=Path, help="Native PcbLib template."
            )

    for name, help_text in (
        (
            "check-schlib",
            "Read a .SchLib back and compare it with its source JSON.",
        ),
        (
            "check-pcblib",
            "Read a .PcbLib back and compare it with its source JSON.",
        ),
    ):
        command = add(name, help_text)
        command.add_argument(
            "source", type=Path, help="The JSON the library was built from."
        )
        command.add_argument("library", type=Path, help="The library to read back.")
        command.add_argument(
            "--qa-dir",
            type=Path,
            required=True,
            help="Directory for the report and preview images. Must be fresh, or previously "
            "written by this checker for the same source/library pair.",
        )
        group = command.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--pinmap",
            type=Path,
            help='Independently transcribed map to compare against: {"1": "SIGNAL"} for a '
            'SchLib, {"1": [x_mm, y_mm]} for a PcbLib.',
        )
        group.add_argument(
            "--without-map",
            action="store_true",
            help="Run with no independent map. The omission is recorded in validation.json "
            "and must be reported; readback alone is not datasheet verification.",
        )
        command.add_argument(
            "--allow-skill-writes",
            action="store_true",
            help="Permit writing inside the skill directory. Maintenance only.",
        )
        if name == "check-pcblib":
            command.add_argument(
                "--layers",
                type=Path,
                help="Layer profile, overriding layer_profile.",
            )

    fit = add(
        "fit-schlib",
        "Report fitted symbol dimensions without writing a library.",
    )
    fit.add_argument("source", type=Path, help="Source JSON.")

    selftest = add(
        "selftest",
        "Build and check the bundled examples in a temporary directory.",
    )
    selftest.add_argument(
        "--keep", type=Path, help="Write the run into this directory instead."
    )

    add(
        "record-integrity",
        "Re-record scripts/INTEGRITY.json. Maintenance only, after an "
        "intentional toolkit change.",
    )
    return parser


def resolve_profile(args, spec):
    layers = getattr(args, "layers", None)
    if layers is None:
        if "layer_profile" not in spec:
            raise SystemExit(
                'error: the footprint JSON has no "layer_profile"; pass --layers.'
            )
        layers = args.source.parent / spec["layer_profile"]
    return read_json(layers, "layer profile")


def main(argv=None):
    if not __debug__:
        raise SystemExit(
            "error: do not run with -O or PYTHONOPTIMIZE; validation uses assertions."
        )
    args = build_parser().parse_args(argv)

    if args.command == "record-integrity":
        digest, count = record_integrity()
        print(f"Recorded {count} files; digest {digest}")
        return 0
    if args.command == "selftest":
        from altium_tools.selftest import run

        return run(args.keep)

    warn_if_modified()
    spec = read_json(args.source, "source JSON")
    allow = getattr(args, "allow_skill_writes", False)

    try:
        if args.command == "fit-schlib":
            from altium_tools.schematic import prepare, summary

            print(summary(prepare(spec)))
        elif args.command == "build-schlib":
            from altium_tools.schematic import write, summary

            if not allow:
                refuse_writes_into_skill(args.output, "a library")
            print(summary(write(spec, args.output)))
            print(args.output)
            print(
                "Not verified yet. Run check-schlib on this file before delivering it."
            )
        elif args.command == "build-pcblib":
            from altium_tools.pcb import write, summary

            if not allow:
                refuse_writes_into_skill(args.output, "a library")
            print(
                summary(
                    write(
                        spec,
                        resolve_profile(args, spec),
                        args.output,
                        args.template,
                    )
                )
            )
            print(args.output)
            print(
                "Not verified yet. Run check-pcblib on this file before delivering it."
            )
        else:
            if not allow:
                refuse_writes_into_skill(args.qa_dir, "QA output")
            pinmap = read_json(args.pinmap, "independent map") if args.pinmap else None
            if args.command == "check-schlib":
                from altium_tools.validation import check_schlib

                result = check_schlib(
                    spec,
                    args.source,
                    args.library,
                    args.qa_dir,
                    pinmap,
                    args.pinmap,
                    __version__,
                )
            else:
                from altium_tools.validation import check_pcblib

                result = check_pcblib(
                    spec,
                    resolve_profile(args, spec),
                    args.source,
                    args.library,
                    args.qa_dir,
                    pinmap,
                    args.pinmap,
                    __version__,
                )
            print(json.dumps(result, indent=2))
            if result["independent_map"] == "omitted":
                print(
                    "NOTE: no independent map was supplied. Say so when reporting this "
                    "result; readback compared the library only with the JSON it was built "
                    "from.",
                    file=sys.stderr,
                )
            print(
                f"Now open the images in {args.qa_dir} and inspect them.",
                file=sys.stderr,
            )
    except ValueError as error:
        raise SystemExit(f"error: {error}")
    except AssertionError as error:
        detail = (
            " ".join(str(a) for a in error.args) if error.args else "assertion failed"
        )
        print(traceback.format_exc(), file=sys.stderr)
        raise SystemExit(
            f"readback mismatch: {detail}\n"
            "The saved library does not match its source JSON. Fix the source JSON and "
            "rebuild; do not edit the toolkit or the checker to make this pass."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
