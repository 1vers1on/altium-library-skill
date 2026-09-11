# altium-library

[![selftest](https://github.com/1vers1on/altium-library-skill/actions/workflows/selftest.yml/badge.svg)](https://github.com/1vers1on/altium-library-skill/actions/workflows/selftest.yml)

Generate native Altium binary libraries from plain JSON pin tables, then verify
them by reading the saved binary back. No Altium Designer installation is
required at any step.

This repository is packaged as a Claude Code skill (see `SKILL.md`), but the
toolkit under `scripts/` is a standalone Python CLI and can be used on its own.
The skill has also been validated with OpenAI Codex.

## What it does

- **SchLib** (schematic symbols): one part, several functional parts, or a
  one-sided connector. Bodies are auto-fitted to their text. Fully supported.
- **PcbLib** (footprints): multi-footprint libraries with THT, SMD, NPTH and
  slotted/rotated/stacked pads; graphics, pad arrays, regions, text, mechanical
  layer pairs, and extruded 3D bodies. See the footprint schema for limits.

Each generator has a separate readback checker that re-decodes the saved file
and compares it with its JSON. A successful build only proves a file was
written; run the matching `check-*` command. The PCB checker builds its expected
records with writer routines, so an encoding assumption shared by writer and
checker can still escape it.

## Requirements

Python 3.10 or newer. That is the whole list. CI builds and reads back every
bundled example on 3.10 through 3.13, on Linux, macOS and Windows.

There are no third-party dependencies, no install step, and no network access at
any point. Everything the toolkit needs is bundled under
`scripts/altium_tools/vendor/`:

| Module | Replaces | Scope |
| --- | --- | --- |
| `cfb.py` | `olefile` | Strict Compound File Binary reader |
| `altium.py` | `pyaltiumlib` | SchLib and PcbLib readers built on `cfb` |
| `raster.py`, `strokefont.py` | `Pillow` | Anti-aliased canvas, stroke-font text, PNG encoder |

Preview text uses a bundled single-stroke vector font, so no font files are
needed either. Text fitting and collision tests use the metrics of the fonts the
symbol actually names, so layout never depends on what is installed.

Do not run under `python -O` or with `PYTHONOPTIMIZE` set. Validation relies on
assertions and the CLI refuses optimized execution.

## Installing the skill

The skill is a plain directory: `SKILL.md` plus the `scripts/`, `references/`,
`examples/`, and `assets/` folders. Install it by placing that directory where
your agent looks for skills.

**Claude Code**

- Per user (available in every project):

  ```bash
  git clone https://github.com/1vers1on/altium-library-skill.git \
    ~/.claude/skills/altium-library-skill
  ```

- Per project (checked in with the repo, shared with your team):

  ```bash
  git clone https://github.com/1vers1on/altium-library-skill.git \
    .claude/skills/altium-library-skill
  ```

Restart Claude Code or start a new session. The skill then loads automatically
when relevant, or on demand with `/altium-library`. There is nothing to install.

**OpenAI Codex**

Codex loads skills from `~/.codex/skills/<skill-name>/`:

```bash
git clone https://github.com/1vers1on/altium-library-skill.git \
  ~/.codex/skills/altium-library
```

Start a new Codex session afterward. As with Claude Code, there is nothing to
install.

**Any other agent or by hand**

`SKILL.md` is ordinary Markdown. Read it directly and run the commands under
Usage yourself, or feed it to any coding agent as context.

## Setup

None. Confirm the toolkit runs here by building and checking every bundled
example in a temporary directory:

```bash
python scripts/toolkit.py selftest
```

## Usage

Build and check a schematic symbol:

```bash
python scripts/toolkit.py build-schlib examples/82077AA-1.json -o build/82077AA-1.SchLib
python scripts/toolkit.py check-schlib examples/82077AA-1.json build/82077AA-1.SchLib \
  --pinmap examples/82077AA-1.pinmap.json \
  --qa-dir build/qa/82077AA-1
```

Build and check a footprint:

```bash
python scripts/toolkit.py build-pcblib examples/A-CCS-068-Z-T.json -o build/A-CCS-068-Z-T.PcbLib
python scripts/toolkit.py check-pcblib examples/A-CCS-068-Z-T.json build/A-CCS-068-Z-T.PcbLib \
  --pinmap examples/A-CCS-068-Z-T.padmap.json \
  --qa-dir build/qa/socket
```

`fit-schlib` prints the fitted width, height, and stub lengths for a layout
without writing a file, which is useful while editing a pin table.

Both check commands write `validation.json` into the QA directory. Schematic
checks also write per-part PNG previews and contact sheets; PCB checks write a
pad-only `footprint.png`. Inspect the report and all available images. See
`references/pcblib-json.md` for preview and clearance-check limitations.

## Commands

| Command | Purpose |
| --- | --- |
| `build-schlib` | Write a `.SchLib` from a symbol JSON |
| `check-schlib` | Reparse a `.SchLib` and validate it against the JSON and an independent pin map |
| `fit-schlib` | Print the fitted layout summary without writing a file |
| `build-pcblib` | Write a `.PcbLib` from a footprint JSON |
| `check-pcblib` | Reparse a `.PcbLib` and validate pads, tracks, layers, and clearances |
| `selftest` | Build and check all bundled examples, proving the toolkit works here |
| `record-integrity` | Re-record `scripts/INTEGRITY.json` after an intentional toolkit change |

Each `validation.json` records the source and library SHA-256 digests, whether an
independent map was compared, and whether the toolkit was modified after its
integrity manifest was recorded.

## Repository layout

```
SKILL.md                     Skill instructions and the full workflow
scripts/toolkit.py           CLI entry point
scripts/altium_tools/        Generators, checkers, metrics, validation
scripts/altium_tools/vendor/ Bundled CFB/Altium readers and PNG renderer
scripts/INTEGRITY.json       Hashes of the toolkit sources, for tamper reporting
references/schlib-json.md     Schematic JSON schema and layout options
references/pcblib-json.md     Footprint JSON schema and layer profile
references/binary-format.md   Notes on the native binary encoding
examples/*.json              Worked pin tables, most from real datasheets
examples/example-layers.json  Layer profile the footprint examples build against
assets/native-template.PcbLib  Native metadata the PCB writer requires
.github/workflows/selftest.yml  CI: selftest, dependency audit, integrity check
.github/check_no_dependencies.py  Proves a full run imports only the stdlib
.github/check_integrity.py    Proves INTEGRITY.json matches the sources
```

## What the checkers do and do not prove

The checkers verify the encoded contents of the saved binary: pin and pad
numbers, names, types, positions, grid alignment, body size against the fitted
layout, duplicate positions, track geometry, layer pairs, text placement, and
minimum copper clearance.

They cannot tell you a hand-transcribed source table matches the datasheet.
Only an independent comparison against the source does that. They do not run
PCB DRC, fabrication or solder-mask sliver checks, assembly clearance, or
design-level ERC. Run those in your EDA tool as usual.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for
the constraints a change has to preserve — standard library only, Python 3.10
compatibility, load-bearing assertions and LF line endings — and for how to add
an example.

Two things to do before opening a pull request: run
`python scripts/toolkit.py selftest`, and if you touched anything under
`scripts/`, run `python scripts/toolkit.py record-integrity` and commit the
result. CI checks both.

## License

MIT. See `LICENSE`.
