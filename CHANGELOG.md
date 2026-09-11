# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Continuous integration: the selftest runs on Python 3.10 through 3.13 across
  Linux, macOS and Windows.
- A dependency audit (`.github/check_no_dependencies.py`) that runs a full
  selftest and fails if anything outside the standard library was imported, so
  the zero-dependency claim is tested rather than asserted.
- `.github/check_integrity.py`, which fails when `scripts/INTEGRITY.json` no
  longer matches the sources, so a stale manifest cannot reach users as a
  spurious "modified" warning in every report.
- `CONTRIBUTING.md`, covering the constraints a change has to preserve, and
  issue templates that ask for the source JSON and `validation.json`.

### Changed

- `.gitattributes` now marks `*.SchLib` and `*.PcbLib` as binary. Previously the
  blanket `* text=auto eol=lf` rule left the bundled `native-template.PcbLib`
  eligible for end-of-line conversion, which would have corrupted the compound
  file it reads verbatim.
- `examples/ellie-layers.json` is now `examples/example-layers.json`, so the
  default layer profile is identifiable as such.
- Reported minimum copper clearance is rounded to 6 decimal places. The outlines
  are segment approximations, so the trailing digits were arithmetic artefacts
  reported as measurements (`0.9399999999999977` for what is `0.94`).

### Fixed

- A selftest failure now prints the exception type and a traceback. A bare
  `assert` previously reported an empty reason.
- The footprint selftest loads each example's JSON and layer profile inside its
  own error handler, so one malformed example is reported as a single failure
  instead of aborting the whole run.

## [2.0.0]

- Footprint generation beyond through-hole: SMD, NPTH and slotted, rotated and
  stacked pads, pad arrays, regions, text, mechanical layer pairs and extruded
  3D bodies.
- Readback checking for both library types, with provenance recorded in every
  `validation.json`.
