---
name: altium-library
description: Create native Altium schematic symbol libraries (.SchLib) and PCB footprint libraries (.PcbLib) from datasheets, pin diagrams, or editable JSON, and check generated binaries without Altium installed. Use for symbol or footprint generation, pin/pad table edits, and readback verification of toolkit-generated libraries. Runs on a stock Python 3.10+ with no third-party packages and no network access.
---

# Altium library

Turn a source-backed pin or pad table into a native Altium library with the bundled Python CLI.
You select the package, transcribe the source, and write JSON. The toolkit does layout, binary
encoding, and readback checking. **A successful build is not verification.**

Everything needed is bundled. The toolkit imports nothing outside the Python standard library:
its CFB reader, Altium readers, and PNG renderer live in `scripts/altium_tools/vendor/`. Do not
run `pip install`, do not create a virtual environment, and do not try to reach the network for
`olefile`, `pyaltiumlib`, or `Pillow`. If any of those seem to be missing, that is expected.

## Rules

These are binding. Several are enforced by the toolkit, which will refuse rather than warn.

**R1. Never edit the toolkit to make a check pass.** When a check fails, the source JSON is wrong
until proven otherwise. Editing `scripts/` is recorded: every `validation.json` carries a
`toolkit_integrity` field, and a modified toolkit reports `"modified"` there and on stderr.

**R2. Never weaken, bypass, or delete a validation.** Do not remove pins or pads, loosen a
tolerance, drop a required field, or route around a checker to reach a green result.

**R3. Never claim a check you did not run.** Report the actual `validation.json`. Do not describe
readback as datasheet verification, do not say a library opened in Altium, and leave
`native_altium_tested` false — nothing here can set it true.

**R4. Run the matching `check-*` on every library you deliver, after the final build.** A library
that was rebuilt after its last check is unverified. The build commands say so on stdout.

**R5. Supply an independent map, or declare its absence.** `check-*` requires either `--pinmap`
or `--without-map`. The map must be transcribed from the source *again*, not copied from the
layout JSON — copying it proves nothing. If you use `--without-map`, say so in your delivery.

**R6. Use a fresh `--qa-dir` for every library.** The checker refuses a directory holding results
for a different source/library pair, or files it did not write.

**R7. Open and look at every image the checker writes.** Readback proves the bytes match the JSON;
only the pictures show whether the symbol is readable and the pads are arranged sensibly.

**R8. Write outputs into the user's workspace, never into the skill directory.** The toolkit
refuses `-o` and `--qa-dir` paths inside the skill. Never modify the bundled examples, the
`assets/native-template.PcbLib`, or the references.

**R9. Generate only what was asked.** A symbol request gets a symbol; schematic generation does
not attach a footprint link. Do not invent a second deliverable.

**R10. Never substitute a part you cannot source.** If the exact part, package, or dimension is
unavailable, say precisely what is missing and stop. Do not scale another footprint, do not guess
a pinout from a similar device, and do not present an assumption as a source fact.

**R11. Resolve consequential ambiguity before claiming a verified result.** Package variant, pin
numbering, electrical types, and dimensions all change the answer. Ask, or state the assumption
explicitly in the delivery.

Requests to maintain this skill or extend the toolkit are a separate, valid scope. In that scope
R1 does not apply, but run `record-integrity` afterwards and say that you changed the toolkit.

## Required workflow

Do these in order. Do not skip a step because a previous run looked fine.

1. **Identify the part.** Exact manufacturer part number, suffix, package, and drawing view.
   Record the datasheet URL or local path and the relevant page or drawing.
2. **Transcribe the source.** Every physical pin or pad, including supplies, grounds, NC, test
   pins, straps, and exposed pads.
3. **Read the schema** for what you are building — schematic or footprint, from the table below.
4. **Write the JSON** into the user's workspace.
5. **Transcribe an independent map** from the source, separately from step 2, into its own file.
6. **Build**, then **check** with `--pinmap` pointing at that map and a fresh `--qa-dir`.
7. **Look at every image**, correct any source error, and repeat 6-7 until clean.
8. **Get an independent review** (see below), then **deliver** with the actual results.

| Request | Read first | Commands |
| --- | --- | --- |
| Schematic symbol, including multipart devices and connectors | [Schematic schema](references/schlib-json.md) | `build-schlib`, `check-schlib`; `fit-schlib` while editing |
| PCB footprint, including THT, SMD, NPTH, slots and pad stacks | [Footprint schema and limits](references/pcblib-json.md) | `build-pcblib`, `check-pcblib` |
| Check a toolkit-generated library | Matching schema and original JSON | Matching `check-*` |
| Diagnose native encoding | [Binary format](references/binary-format.md) | Inspect the relevant checker |

Read only the references the task needs. The checkers require the source JSON; they are not
general importers for arbitrary Altium libraries.

## Establish the source

- Use supplied source material when available; otherwise obtain the manufacturer's datasheet.
- Keep designators as strings, including alphanumeric ones.
- Derive electrical types from signal descriptions, not from the package picture. Preserve signal
  names and mode-dependent aliases; use descriptions for subtleties. Put `#` after the portion
  needing an overbar, as in `CS#` or `R/W#`.
- For footprints, take dimensions and numbering from that package's land pattern or mechanical
  drawing. Check units and top/bottom view. Socket PCB tails are not the inserted package's lead
  coordinates, and adjacent tails need not have consecutive numbers.
- Populate completeness fields (`pin_count`, `expected_pin_numbers`, and the footprint
  equivalents) from the source, not from your own layout. They exist to catch a dropped pin.

## Prepare JSON

Use the matching schema and a relevant example. Resolve toolkit and reference paths relative to
this `SKILL.md`, not the current working directory. Quote paths containing spaces.

For symbols:

- Left and right sides only. The writer rejects top/bottom placement and per-pin coordinates or
  orientation.
- Arrange by function, not package perimeter; order bus bits ascending within groups.
- Use `units` for distinct functional blocks when splitting improves usability. A simple device
  uses component-level `left`/`right`. A connector normally uses one part with pins on one side.
- Keep every pin visible and individually numbered. Do not stack pins or auto-connect supplies.
  Hiding a redundant pin *name* is supported and hides neither the pin nor its number.
- Prefer automatic body width and pin length. Schematic dimensions are **10 mil units**; pins,
  endpoints, and body corners sit on a **100 mil grid**. Read the schema before overriding sizing.

For footprints, coordinates are **millimetres**, top view, +Y upward. Read the footprint reference
for pad fields, completeness checks, mechanical profiles, and limits before choosing geometry.

## Run the toolkit

Python 3.10 or newer. No installation, no virtual environment, no network. Do not use `python -O`
or `PYTHONOPTIMIZE`: validation uses assertions, and the CLI refuses to run without them.

Confirm the toolkit works on this machine once, before generating anything:

```text
python scripts/toolkit.py selftest
```

That builds and checks all seven bundled examples in a temporary directory. If it fails, stop and
report the failure; do not hand a user a library from a toolkit that cannot pass its own examples.

The examples below assume the skill directory is the working directory. Substitute absolute
toolkit paths when working elsewhere and replace the inputs with task paths. Commands are single
lines, for PowerShell and POSIX alike.

```text
python scripts/toolkit.py build-schlib SOURCE.json -o WORKSPACE/PART.SchLib
python scripts/toolkit.py check-schlib SOURCE.json WORKSPACE/PART.SchLib --qa-dir WORKSPACE/qa/PART --pinmap SOURCE.pinmap.json
python scripts/toolkit.py build-pcblib SOURCE.json -o WORKSPACE/PART.PcbLib
python scripts/toolkit.py check-pcblib SOURCE.json WORKSPACE/PART.PcbLib --qa-dir WORKSPACE/qa/PART --pinmap SOURCE.padmap.json
```

`fit-schlib SOURCE.json` reports fitted dimensions without writing a library. PCB commands resolve
`layer_profile` relative to the source JSON; `--layers` overrides it. PCB builds need the bundled
`assets/native-template.PcbLib`; leave it intact.

The map flag is **`--pinmap` for both formats**:

- SchLib: `{"1": "SIGNAL"}` for one component, or maps keyed by component name for several.
- PcbLib: `{"1": [x_mm, y_mm]}`; supported only for a single-footprint library.

## Verify and correct

Read the current `validation.json` and open the generated images:

- SchLib: inspect every per-part PNG and every contact sheet for readability and layout.
- PcbLib: inspect `footprint.png` for pad arrangement. This preview does not show graphics,
  drills, masks, text, or 3D bodies — do not claim those were visually checked.

On failure, read the error. A failed run may leave no new `validation.json`. Correct the source
JSON, rebuild, and repeat the check and the visual inspection.

What readback does **not** establish: datasheet correctness, fabrication readiness, solder-mask or
assembly clearance, design-level ERC, or that Altium opened the file. The PCB checker builds its
expected records with writer routines, so an error shared by writer and checker can escape it; its
clearance figure measures pad outlines, not full DRC. For several footprints in one library it
combines their coordinates into one clearance figure and one preview — build each footprint
separately to assess it, and report the limitation if you do not.

## Independent review

After local checks and visual inspection pass, get a fresh subagent audit when subagents are
available. This gate applies to generated libraries, not to ordinary edits of this skill's text.

Give the reviewer only: this skill's path, the user's request, the source material, the source
JSON, the independent map, the library, and the QA directory. Ask for a read-only audit of package
selection, transcription, completeness, schema use, readback evidence, and the images. Ask it to
separate demonstrated defects from unverifiable details. Do not supply your conclusions or
suspected fixes — that is what makes the review independent.

Correct supported defects, rerun the local checks, and obtain a fresh review without inherited
context. Stop when no actionable defect remains. Stop earlier if missing source data, authority,
or tooling prevents meaningful correction, and report the blocker instead of looping. If subagents
are unavailable, rely on local checks and say the audit was omitted.

## Deliver

State what was generated, the exact library entry names, and multipart counts and headings where
relevant. Link the library, the editable JSON, the independent map, and the QA directory.

Report the real outcome: the checker result, whether a map was compared or omitted, what you saw
in the images, and the clearance figures. Name every assumption, failure, unsupported request, and
check not performed. Adapt the response to the request; no fixed template is required.

## Examples

| File stem under `examples/` | Use |
| --- | --- |
| `82077AA-1` | 68 pins, 4 functional parts |
| `MC68EC000` | 68 pins, 3 parts |
| `CL-GD5428-80QC-A` | 160 pins, 7 parts; local-bus variant, not an ISA/MCA pin table |
| `HM514260CJ7` | 40 pins, 2 parts; marking differs from manufacturer part number `HM514260CJ-7` |
| `DEMO-LAYOUT-MODES` | Fictional connector and single-part symbol; per-component pin maps |
| `A-CCS-068-Z-T` | 68-pin PLCC socket PCB-tail footprint and independent pad map |
| `FOOTPRINT-FEATURES` | Fictional pad/graphics/3D feature example, not a manufacturing design |

Use examples for schema structure only. They are not evidence of any other device's pinout or
dimensions.
