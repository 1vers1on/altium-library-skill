"""Provenance: what was checked, with which toolkit, and whether the QA directory is current.

A passing check is only evidence if it was run against the bytes actually being delivered,
by an unmodified toolkit, into a directory that holds no results from an earlier run. These
helpers record all three in every ``validation.json`` so a delivery claim can be audited
after the fact instead of taken on trust.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from .common import require

SKILL_ROOT = Path(__file__).resolve().parents[2]
INTEGRITY_FILE = SKILL_ROOT / "scripts/INTEGRITY.json"
OWNER_FILE = ".qa-owner.json"


def write_text(path, text):
    """Write UTF-8 with LF endings on every platform.

    ``Path.write_text`` would emit CRLF on Windows, which changes a file's digest and so
    would make an integrity manifest recorded on one platform fail on another.
    """
    Path(path).write_bytes(text.encode("utf-8"))


def write_json(path, payload):
    write_text(path, json.dumps(payload, indent=2) + "\n")


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest_file(path):
    return digest_bytes(Path(path).read_bytes())


def timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- toolkit integrity --------------------------------------------------------------------------


def toolkit_files():
    """Every Python file the toolkit runs, relative to the skill root, in a stable order."""
    scripts = SKILL_ROOT / "scripts"
    found = [scripts / "toolkit.py"] + sorted((scripts / "altium_tools").rglob("*.py"))
    return [p for p in found if p.is_file() and "__pycache__" not in p.parts]


def toolkit_hashes():
    return {
        str(p.relative_to(SKILL_ROOT)).replace(os.sep, "/"): digest_file(p)
        for p in toolkit_files()
    }


def record_integrity():
    """Write the manifest. A maintenance action: run it after deliberately editing the toolkit."""
    hashes = toolkit_hashes()
    combined = digest_bytes(
        "".join(f"{k}:{v}\n" for k, v in sorted(hashes.items())).encode()
    )
    write_text(
        INTEGRITY_FILE,
        json.dumps(
            {
                "algorithm": "sha256",
                "recorded": timestamp(),
                "digest": combined,
                "files": hashes,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return combined, len(hashes)


def integrity_status():
    """``('unmodified', [])`` or ``('modified', [note, ...])``; never raises."""
    if not INTEGRITY_FILE.is_file():
        return "unrecorded", [
            "No scripts/INTEGRITY.json; toolkit edits cannot be detected."
        ]
    try:
        recorded = json.loads(INTEGRITY_FILE.read_text(encoding="utf-8"))["files"]
    except (ValueError, KeyError, OSError):
        return "unrecorded", ["scripts/INTEGRITY.json is unreadable."]
    current = toolkit_hashes()
    notes = [
        f"{name} was edited after the manifest was recorded."
        for name in sorted(set(recorded) & set(current))
        if recorded[name] != current[name]
    ]
    notes += [f"{name} is missing." for name in sorted(set(recorded) - set(current))]
    notes += [
        f"{name} is not in the manifest."
        for name in sorted(set(current) - set(recorded))
    ]
    return ("unmodified", []) if not notes else ("modified", notes)


# --- output location ----------------------------------------------------------------------------


def refuse_writes_into_skill(path, what="output"):
    """Keep generated work out of the skill directory, so bundled files stay pristine."""
    resolved = Path(path).resolve()
    try:
        inside = resolved.relative_to(SKILL_ROOT)
    except ValueError:
        return
    require(
        False,
        f"Refusing to write {what} inside the skill directory ({inside}). "
        "Generated libraries, JSON and QA output belong in the user's workspace. "
        "Pass a path outside the skill, or copy the skill elsewhere first if you are "
        "deliberately maintaining it.",
    )


# --- QA directory hygiene -----------------------------------------------------------------------


def claim_qa_dir(qa_dir, signature):
    """Take ownership of ``qa_dir``, clearing this checker's own earlier output.

    A directory holding anything else, or results for a different source/library pair, is
    refused: old images and reports must never be mistaken for the current run.
    """
    qa_dir = Path(qa_dir)
    owner = qa_dir / OWNER_FILE
    if qa_dir.exists():
        require(qa_dir.is_dir(), f"{qa_dir} exists and is not a directory.")
        contents = [p for p in qa_dir.iterdir()]
        if contents:
            require(
                owner.is_file(),
                f"{qa_dir} already holds files this checker did not write. Use a fresh "
                "--qa-dir so an earlier report cannot be mistaken for this run.",
            )
            try:
                previous = json.loads(owner.read_text(encoding="utf-8"))
            except ValueError:
                previous = {}
            require(
                previous.get("signature") == signature,
                f"{qa_dir} holds QA output for a different source/library pair. Use a fresh "
                "--qa-dir rather than mixing two results in one directory.",
            )
            for name in previous.get("artifacts", []):
                (qa_dir / name).unlink(missing_ok=True)
            (qa_dir / "validation.json").unlink(missing_ok=True)
    qa_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        owner,
        {"signature": signature, "started": timestamp(), "artifacts": []},
    )
    return qa_dir


def release_qa_dir(qa_dir, artifacts):
    qa_dir = Path(qa_dir)
    owner = qa_dir / OWNER_FILE
    info = json.loads(owner.read_text(encoding="utf-8")) if owner.is_file() else {}
    info["artifacts"] = sorted(artifacts)
    info["finished"] = timestamp()
    write_json(owner, info)


# --- report header ------------------------------------------------------------------------------


def header(command, source, library, qa_dir, pinmap, version):
    """The provenance block every ``validation.json`` starts with."""
    state, notes = integrity_status()
    signature = digest_bytes((digest_file(source) + digest_file(library)).encode())
    return dict(
        command=command,
        generated_utc=timestamp(),
        toolkit_version=version,
        toolkit_integrity=state,
        toolkit_integrity_notes=notes,
        source_json=str(Path(source).resolve()),
        source_sha256=digest_file(source),
        library=str(Path(library).resolve()),
        library_sha256=digest_file(library),
        library_bytes=Path(library).stat().st_size,
        independent_map=str(Path(pinmap).resolve()) if pinmap else "omitted",
        independent_map_sha256=digest_file(pinmap) if pinmap else None,
        qa_dir=str(Path(qa_dir).resolve()),
        qa_signature=signature,
        native_altium_tested=False,
    )
