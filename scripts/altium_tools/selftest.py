"""End-to-end self-test: build and check every bundled example with no network or install.

Run it once on a new machine to prove the toolkit works there. It exercises the bundled
CFB writer and reader, the SchLib and PcbLib writers and checkers, the independent-map
comparison and the PNG renderer, and it fails loudly rather than skipping anything.
"""

import json
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

from .provenance import SKILL_ROOT, integrity_status

EXAMPLES = SKILL_ROOT / "examples"
SCHEMATICS = (
    "82077AA-1",
    "MC68EC000",
    "CL-GD5428-80QC-A",
    "HM514260CJ7",
    "DEMO-LAYOUT-MODES",
)
FOOTPRINTS = ("A-CCS-068-Z-T", "FOOTPRINT-FEATURES")


def _load(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _describe(error):
    """A one-line reason for a failure, even from a bare ``assert`` with no message."""
    detail = " ".join(str(a) for a in error.args).strip()
    return f"{type(error).__name__}: {detail}" if detail else type(error).__name__


def _map_for(stem, suffix):
    path = EXAMPLES / f"{stem}.{suffix}.json"
    return (
        (json.loads(path.read_text(encoding="utf-8")), path)
        if path.is_file()
        else (None, None)
    )


def run(keep=None):
    from . import __version__
    from .validation import check_schlib, check_pcblib
    from .schematic import write as write_schlib
    from .pcb import write as write_pcblib

    started = time.time()
    workspace = (
        Path(keep) if keep else Path(tempfile.mkdtemp(prefix="altium-selftest-"))
    )
    workspace.mkdir(parents=True, exist_ok=True)
    state, notes = integrity_status()
    print(f"toolkit {__version__}, Python {sys.version.split()[0]}, integrity {state}")
    for note in notes[:5]:
        print(f"  - {note}")
    print(f"workspace {workspace}")

    failures = []
    checked = 0
    try:
        for stem in SCHEMATICS:
            source = EXAMPLES / f"{stem}.json"
            library = workspace / f"{stem}.SchLib"
            try:
                write_schlib(_load(f"{stem}.json"), library)
                pinmap, pinmap_path = _map_for(stem, "pinmap")
                result = check_schlib(
                    _load(f"{stem}.json"),
                    source,
                    library,
                    workspace / "qa" / stem,
                    pinmap,
                    pinmap_path,
                    __version__,
                )
                images = [workspace / "qa" / stem / name for name in result["images"]]
                missing = [
                    i.name for i in images if not i.is_file() or i.stat().st_size < 100
                ]
                if missing:
                    raise AssertionError(f"preview images missing or empty: {missing}")
                print(
                    f'  ok  {stem}: {result["components"]} component(s), {result["pins"]} pins, '
                    f'{len(images)} images, map {"compared" if pinmap else "omitted"}'
                )
                checked += 1
            except Exception as error:
                failures.append((stem, error))
                print(f"  FAIL {stem}: {_describe(error)}")
                traceback.print_exc()

        for stem in FOOTPRINTS:
            source = EXAMPLES / f"{stem}.json"
            library = workspace / f"{stem}.PcbLib"
            try:
                spec = _load(f"{stem}.json")
                profile = json.loads(
                    (EXAMPLES / spec["layer_profile"]).read_text(encoding="utf-8")
                )
                write_pcblib(spec, profile, library, None)
                padmap, padmap_path = _map_for(stem, "padmap")
                result = check_pcblib(
                    spec,
                    profile,
                    source,
                    library,
                    workspace / "qa" / stem,
                    padmap,
                    padmap_path,
                    __version__,
                )
                pads = sum(f["pads"] for f in result["footprints"])
                print(
                    f'  ok  {stem}: {len(result["footprints"])} footprint(s), {pads} pads, '
                    f'clearance {result["minimum_copper_clearance_mm"]}, '
                    f'map {"compared" if padmap else "omitted"}'
                )
                checked += 1
            except Exception as error:
                failures.append((stem, error))
                print(f"  FAIL {stem}: {_describe(error)}")
                traceback.print_exc()
    finally:
        if keep is None:
            shutil.rmtree(workspace, ignore_errors=True)

    elapsed = time.time() - started
    if failures:
        print(
            f"\nSELFTEST FAILED: {len(failures)} of {checked+len(failures)} examples "
            f"({elapsed:.1f}s). The toolkit is not usable here; fix it before generating "
            "anything for a user."
        )
        return 1
    print(
        f"\nSelftest passed: {checked} examples built and checked in {elapsed:.1f}s with no "
        "third-party packages."
    )
    return 0
