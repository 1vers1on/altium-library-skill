# PcbLib JSON schema and limits

## File shape and units

A single-footprint document contains the footprint fields below plus `layer_profile`. For multiple
entries, use `{"layer_profile": "layers.json", "footprints": [...]}` with footprint objects in the
array. The CLI requires a top-level `layer_profile` unless `--layers` is supplied.

All positions and sizes are **millimeters**, top view, +Y upward. Rotations are degrees.
Choose and document the origin from the package drawing; the socket example uses the body center.

| Footprint field | Meaning |
| --- | --- |
| `name` | Required library entry name, at most 31 UTF-16 units; no `/`, `\`, `!`, `:`; reserved names `FILEHEADER`, `LIBRARY`, `SECTIONKEYS` are rejected |
| `expected_pin_numbers` | Explicit list of designators; preferred completeness check. Unlike SchLib, ranges are not expanded |
| `expected_pin_count` | Alternative positive count; do not combine with `expected_pin_numbers` |
| `pads`, `pad_arrays` | Explicit pads and/or generated arrays; combined result must contain at least one pad |
| `description`, `height_mm`, `notes` | Optional description, nonnegative component height, and source notes; notes are not a substitute for geometry |
| `tracks`, `arcs`, `fills`, `regions`, `texts`, `bodies`, `outlines` | Optional graphics and geometry collections |

Unknown footprint and primitive fields are rejected. Each pad number must be unique. Two pads
cannot share the same center on the same layer. A pad **stack** means different copper sizes/shapes
through the board, not duplicate numbered pads stacked at one location.

The writer supports THT, SMD and NPTH pads, slots, rotation, mask settings, pad arrays, graphics,
mechanical layers M1-M32, and extruded outline bodies. Embedded STEP, vias, dimensions, coordinate
objects, teardrops, and polygon pours are not implemented. Regions are supported separately from
polygon pours. Do not approximate a required unsupported feature without resolving the request.

## Pads

Prefer explicit `type`, `size_mm`, and named layers when authoring new inputs.

| Field | Meaning |
| --- | --- |
| `number`, `at` | Required designator and center `[x, y]` |
| `type` | `tht`, `smd`, or `npth`; inferred as `tht` when `hole_mm` exists, otherwise `smd` |
| `size_mm` | Required copper size: number for equal X/Y, or `[x, y]`; `diameter_mm` is a compatible alias |
| `shape` | `round` (default), `oval`, `rect`, `square`, `octagonal`, or `rounded_rect`; aliases also accepted |
| `rotation` | Pad rotation, default 0 |
| `corner_radius_percent` | Integer 0-100 for rounded rectangles, default 50 |
| `hole_mm` | Required positive drill width for THT/NPTH; forbidden on SMD |
| `hole_shape` | `round` (default), `square`, or `slot` |
| `slot_length_mm`, `slot_rotation` | For a slot, length must exceed `hole_mm`; rotation defaults to 0 |
| `layer` | SMD `Top` (default) or `Bottom`; omit for THT/NPTH, which use Multi-Layer |
| `plated` | Normally omit: THT is plated, NPTH is unplated |
| `solder_mask_expansion_mm` | Nonnegative mm or `"rule"`; default 0.075 mm |
| `paste_mask_expansion_mm` | Nonnegative mm or `"rule"`; default `"rule"` |
| `tenting` | `none` (default), `top`, `bottom`, or `both` |
| `keepout` | Optional boolean |
| `stack` | THT/NPTH only: `middle` and/or `bottom` objects containing `size_mm` and/or `shape`; omitted values inherit top geometry |

The implementation requires both pad dimensions to exceed `hole_mm`, including for NPTH. Check
that this restriction can represent the requested mechanical hole; do not silently change its size.
Do not assume the paste default disables paste openings.

Minimal fictional SMD example (supply the referenced layer profile):

```json
{
  "name": "DEMO_SMD_2",
  "layer_profile": "layers.json",
  "expected_pin_numbers": ["1", "2"],
  "pads": [
    {"number": "1", "at": [-1, 0], "type": "smd", "size_mm": [1, 1.5], "shape": "rect", "layer": "Top"},
    {"number": "2", "at": [1, 0], "type": "smd", "size_mm": [1, 1.5], "shape": "rect", "layer": "Top"}
  ]
}
```

## Pad arrays

Arrays expand into ordinary pads before writing and checking. Each entry combines pad fields with
`kind` and the relevant placement fields. Verify the generated numbering against the source drawing.

| `kind` | Main placement fields and ordering |
| --- | --- |
| `linear` | `count`, `pitch_mm`, optional `start`, `direction` (`+x`, `-x`, `+y`, `-y`) or `angle_deg` |
| `dual` | Even `count`, `pitch_mm`, `span_mm`, optional `center`, `rows` (`left-right` or `top-bottom`), `numbering` (`ccw` or `zigzag`) |
| `quad` | `count` divisible by 4, `pitch_mm`, `span_x_mm`, optional `span_y_mm`, `center`, `rotate_sides`; starts at top of left side and proceeds counterclockwise |
| `grid` | `rows`, `columns`, `pitch_mm` or `pitch_x_mm`, optional `pitch_y_mm`, `center`, `row_labels`; A1 at top left |
| `circular` | `count`, `radius_mm`, optional `center`, `start_angle_deg` (default 90), `direction` (`cw` default or `ccw`), `rotate_pads` |

Non-grid arrays accept explicit `numbers` or sequential `start_number` (default 1). Grid numbering
uses row labels and column numbers; supply `row_labels` if the package differs from the generator's
letter sequence. `skip` removes named positions; `first` overrides pad fields on the first position.
For exact edge-case behavior, read the relevant function in
[scripts/altium_tools/generators.py](../scripts/altium_tools/generators.py).

## Graphics and bodies

Every primitive requires a `layer`. Prefer names such as `TopOverlay`, `Top`, or `M15`. Multi-Layer
carries pads only. Graphics also accept `keepout`, `locked`, and `tenting`.

| Collection | Fields |
| --- | --- |
| `tracks` | `start`, `end`, positive `width_mm` |
| `arcs` | `center` (alias `at`), positive `radius_mm`, `width_mm`, optional `start_angle` (0), `end_angle` (360) |
| `fills` | `corner1`, `corner2`, optional `rotation` |
| `regions` | `outline` of at least 3 points, optional `holes` contours, integer `kind`, `name`, `board_cutout` |
| `texts` | `text`, `at`, positive `height_mm`; optional `stroke_mm`, `rotation`, `mirror`, `anchor`, `font`, `font_name`, `bold`, `italic`, `stroke_font` |
| `bodies` | `outline` of at least 3 points, positive `height_mm`, mechanical `layer`; optional `standoff_mm`, `color`, `opacity`, `name`, `identifier` |

Text can be literal or `.Designator`/`.Comment`. It must be one printable CP1252 line, at most 255
bytes. `anchor` is `<bottom|middle|top>-<left|center|right>`, default `bottom-left`. `font` is `stroke`
(default) or `truetype`; stroke fonts are `default`, `sans_serif` (default), or `serif`.

Bodies are extrusions, not imported STEP models. `height_mm` is extrusion thickness above the
nonnegative `standoff_mm`; `color` accepts `#RRGGBB`, and `opacity` is greater than 0 and at most 1.
A footprint-level `height_mm` or a 3D layer pair alone does not create a body.

`outlines` supplies geometry helpers expanded into tracks/arcs. See the relevant helper in
[generators.py](../scripts/altium_tools/generators.py) for accepted fields.
[FOOTPRINT-FEATURES.json](../examples/FOOTPRINT-FEATURES.json) demonstrates slots, arrays, graphics,
text, and an extruded body using fictional data. Read only the implementation for the feature you
need if these tables and the example do not cover it.

## Mechanical layer profile

`layer_profile` is a filename relative to the specification JSON. `--layers path/to/profile.json`
overrides it for either PCB command. Use the same profile for build and check.

[examples/example-layers.json](../examples/example-layers.json) supplies these component layer pairs:

| Role | Top | Bottom |
| --- | --- | --- |
| 3D body | M13 | M14 |
| Assembly | M9 | M10 |
| Component center | M17 | M18 |
| Component outline | M2 | M3 |
| Courtyard | M15 | M16 |
| Designator | M11 | M12 |

M1 is standalone. M1-M32 can carry primitives, provided the profile enables each used mechanical
layer. Use symbolic names instead of guessing raw IDs; extended mechanical layers use distinct
native ID systems handled by the writer. Standard copper, overlay, paste, and solder layers are
also accepted; see [pcb_layers.py](../scripts/altium_tools/pcb_layers.py) for exact names.

The profile sets names, enabled state, semantic roles, and mirror pairs, synchronizing native
registry and layer-kind records. It does not move geometry when roles change, so update primitive
layer assignments too. It does not define board dielectric thickness, impedance, copper weights,
or display colors.

## Template and verification limits

Use the bundled `assets/native-template.PcbLib` intact. `--template` accepts a compatible
toolkit-format template, not an arbitrary Altium library. Complete native metadata matters even
when a Python parser accepts a file.

`check-pcblib` compares saved records to records reconstructed from JSON and checks layer pairs.
This shares writer logic and is not an independent test of all encoding assumptions. `--pinmap`
accepts a separate `{"1": [x_mm, y_mm]}` pad map for a single-footprint library only, and is
required unless you pass `--without-map`, which records the omission in `validation.json`.

The checker writes `validation.json` and a pad-only `footprint.png`. It computes pairwise pad-outline
clearance without separating copper layers or footprint entries. Thus multiple footprints at the
same origin, or pads overlapping only on different layers, can trigger a false overlap failure.
Use separate single-footprint checks where helpful; report unresolved checker limits rather than
moving correct geometry or disabling assertions. The preview does not verify drills, graphics,
masks, text, or 3D appearance. Full DRC and native Altium compatibility remain separate tests.
