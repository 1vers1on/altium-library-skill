# Contributing

Issues and pull requests are welcome. This toolkit writes binary files that other
people load into a PCB design, so the bar for a change is that it stays
*verifiable*, not just that it runs.

## Before you open a pull request

Run the selftest. It builds and reads back every bundled example and is the
single gate that matters:

```bash
python scripts/toolkit.py selftest
```

If you touched anything under `scripts/`, re-record the integrity manifest and
commit it with your change:

```bash
python scripts/toolkit.py record-integrity
```

Skipping this leaves every `validation.json` a user generates reporting the
toolkit as `modified`, which is a false alarm that devalues the real one. CI
fails on a stale manifest.

The two checks CI runs beyond the selftest are both runnable locally:

```bash
python .github/check_integrity.py        # manifest matches the sources
python .github/check_no_dependencies.py  # a full run imports only the stdlib
```

## The constraints

These are not style preferences. They are what the repository claims about
itself, and a change that breaks one makes the README untrue.

- **Standard library only.** No `pip install`, no virtualenv, no network at any
  point — at build time, test time or run time. Everything needed is vendored
  under `scripts/altium_tools/vendor/`. If you need functionality that does not
  exist there, vendor a minimal implementation rather than adding a dependency.
- **Python 3.10 and newer.** Do not use syntax or standard-library APIs added
  after 3.10. CI tests 3.10 through 3.13 on Linux, macOS and Windows.
- **Assertions are load-bearing.** Validation is written with `assert`. Never
  run or recommend running under `python -O` or `PYTHONOPTIMIZE`; the CLI
  refuses to start in that mode, and CI checks that it still does.
- **LF line endings.** `.gitattributes` enforces this. The toolkit hashes its
  own sources, so a CRLF checkout would report an unmodified toolkit as
  modified. The same file also marks `*.SchLib` and `*.PcbLib` binary — these
  are byte-exact compound files and a single converted byte destroys them.

## Scope

Keep the scope narrow: new features should extend *verified* generation, not add
unverified shortcuts. Concretely, a new generator feature needs a matching
readback check. If the checker cannot confirm the thing you wrote, say so in the
report's `checks_not_performed` rather than letting a build imply a guarantee it
does not make.

Be careful about one known limitation: the PCB checker builds its expected
records using the writer's own routines, so an encoding assumption shared by
writer and checker can escape it. When you change PCB encoding, confirm the
result opens correctly in Altium and say in the pull request that you did.

## Adding an example

Examples are the test suite, so they must be real:

1. Transcribe from an actual datasheet, not from another library.
2. Include the independent pin or pad map (`<name>.pinmap.json` or
   `<name>.padmap.json`). The map has to be transcribed separately from the
   source JSON — that independence is the entire point, and a map copied from
   the JSON it is meant to check is worse than none.
3. Register the stem in `SCHEMATICS` or `FOOTPRINTS` in
   `scripts/altium_tools/selftest.py` so CI covers it.
4. Confirm `python scripts/toolkit.py selftest` passes.

## Reporting a bug

A readback mismatch or a bad library is almost never reproducible from prose.
Include the source JSON, the exact command, and the `validation.json` from the
QA directory. The provenance block in that file records the toolkit version,
digests and integrity state, which is usually enough to tell what happened.
