# SchLib input schema, layout modes and geometry

## File shape

A file contains a `components` array; several components can share one library. Every component
needs `name`, `pin_count`, `expected_pin_numbers`, and pins arranged on sides.

## Component fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Library entry name, at most 31 UTF-16 units, no `/ \ ! :` |
| `pin_count` | yes | Physical pin count from the datasheet; cross-checked against the layout |
| `expected_pin_numbers` | yes | Independent completeness check; see below |
| `units` | one of these | Array of functional parts |
| `left` / `right` | one of these | Sides placed directly on the component: a single part, no functional split |
| `description` | no | Component description |
| `manufacturer`, `manufacturer_part_number`, `package`, `datasheet` | no | Written as hidden parameters |
| `designator_prefix` | no | 1–4 letters, default `U`. Use `J` or `P` for connectors, `RN` for networks |
| `uniform_width` | no | `true` widens every part of the component to the widest fitted part |
| `parameters` | no | Extra hidden metadata. `Comment` and `Designator` are managed by the writer |

`expected_pin_numbers` is the check that the layout is complete: the writer rejects the build if
the pins laid out in the sides do not exactly match that set. Populate it from the datasheet's pin
list, not by copying what you already typed into the layout. Entries are strings; an entry of the
form `"1-68"` or `"A1-A12"` expands into that inclusive range, so a plain sequential connector is
one entry. A designator that merely contains a hyphen, such as `"P1-2"`, is left alone.

## Unit fields

A unit is one part of the symbol. With `units` omitted, the component itself is the single unit and
takes the same fields.

| Field | Required | Meaning |
| --- | --- | --- |
| `left` / `right` | at least one | Pins on that side of the body |
| `title` | no | Bold heading inside the top of the body. Omit for a plain box |
| `note` | no | Small grey line inside the bottom of the body |
| `width` | no | 10 mil units, multiple of 10. Omit (or `"auto"`) to fit the body to its text |
| `min_width` | no | Floor for the fitted width, for matching parts by eye |
| `pin_length` | no | 10 mil units, multiple of 10. Omit to fit the stub to the pin numbers |
| `hide_pin_names` | no | `true` draws no pin names in this unit; the names are still stored |

Parts are lettered in array order: A, B, C … up to 26.

## Side shapes

A side accepts three forms, and they can be mixed in one list:

```json
"right": [
  {"number": "1", "name": "VBUS", "electrical": "power"},
  {"title": "DATA", "pins": [
    {"number": "2", "name": "D+", "electrical": "bidirectional"},
    {"number": "3", "name": "D-", "electrical": "bidirectional"}
  ]}
]
```

- **A bare pin.** Consecutive bare pins collect into one unlabelled run.
- **A group** — `{"title": ..., "pins": [...]}` — drawn with a small grey heading. An empty or
  missing `title` gives an unlabelled group.
- **A single group object** instead of a list.

A side may be omitted entirely. Pins go on `left` and `right` only: `top`, `bottom` and their
synonyms are rejected, as are `side`, `orientation`, `rotation`, `angle`, `x` and `y` on a pin.
A pin's position and direction follow from the side it is listed under.

## Pin fields

| Field | Required | Meaning |
| --- | --- | --- |
| `number` | yes | Package designator, as a string. Alphanumerics are fine |
| `name` | yes | Manufacturer's signal name; `#` marks an overbar |
| `electrical` | yes | One keyword from the table below |
| `description` | no | Where source-backed subtleties belong — reset-time strap behavior, external-clock behavior, tri-state gating, mode-dependent polarity. Descriptions do not clutter the drawing |
| `hide_name` | no | `true` hides this one pin's name |

## Electrical types

| Keyword | Altium code | Use |
| --- | ---: | --- |
| `input` | 0 | Digital input |
| `bidirectional` | 1 | Input/output, including documented bidirectional analog terminals |
| `output` | 2 | Output |
| `open_collector` | 3 | Open-collector/open-drain, when the source supports it |
| `passive` | 4 | Passive terminals; connector contacts; NC pins, with explicit NC names/descriptions |
| `tri_state` | 5 | Output with a high-impedance state |
| `open_emitter` | 6 | Open-emitter output |
| `power` | 7 | Both supply and ground terminals |

Altium's eight-type field has no separate NC type. NC pins stay visible and individually numbered.
No pins are stacked, hidden, or auto-connected to a power net.

## Overbar notation

Put `#` **after** the portion that needs an overbar: `CS#`, `RAS#`, `R/W#`. The writer converts
this to Altium character overbars. Do not permanently mark a configurable signal active-low just
because one common operating mode uses that polarity.

## Layout recipes

**Multi-part device** — one unit per functional block:

```json
{"components": [{
  "name": "DEMO_MCU", "pin_count": 4, "expected_pin_numbers": ["1-4"],
  "units": [
    {"title": "HOST BUS",
     "left":  [{"title": "ADDRESS", "pins": [{"number": "1", "name": "A0", "electrical": "input"}]}],
     "right": [{"title": "DATA",    "pins": [{"number": "2", "name": "D0", "electrical": "bidirectional"}]}]},
    {"title": "POWER",
     "left":  [{"number": "3", "name": "VCC", "electrical": "power"}],
     "right": [{"number": "4", "name": "GND", "electrical": "power"}]}
  ]}]}
```

**Single part, no subunits** — sides directly on the component:

```json
{"components": [{
  "name": "DEMO_GATE", "pin_count": 3, "expected_pin_numbers": ["1-3"],
  "left":  [{"number": "1", "name": "A", "electrical": "input"},
            {"number": "2", "name": "B", "electrical": "input"}],
  "right": [{"number": "3", "name": "Y", "electrical": "output"}]}]}
```

**Connector** — one part, one side, no headings, a `J?` designator:

```json
{"components": [{
  "name": "DEMO_HDR_4", "designator_prefix": "J",
  "pin_count": 4, "expected_pin_numbers": ["1-4"],
  "right": [{"number": "1", "name": "P1", "electrical": "passive"},
            {"number": "2", "name": "P2", "electrical": "passive"},
            {"number": "3", "name": "P3", "electrical": "passive"},
            {"number": "4", "name": "P4", "electrical": "passive"}]}]}
```

The schema snippets above are fictional demonstration data, not real parts.
`examples/DEMO-LAYOUT-MODES.json` is a complete, buildable version of the last two.

## Coordinate conventions

Dimensions are **10 mil units**, not millimeters — `width: 160` is a 1600 mil body. Pin pitch is 10
units (100 mil); the default pin stub is 20 units (200 mil). Group headings and gaps are inserted
automatically: a heading sits one pitch above the group it labels, with two pitches of clear space
above it.

Every vertical step is a whole pitch and every width is a multiple of 10, so **every pin, wire
endpoint and body corner lands on the 100 mil schematic grid** and wires connect on Altium's default
grid without nudging. `check-schlib` asserts this. A library generated before this convention put
its pins on a 50 mil offset; rebuilding one moves every pin 50 mil relative to the component origin,
which sheets that already place the symbol will see as moved pins.

The stored pin position is its **body end**. Left-side orientation is `2`, right-side is `0`. The
active wire endpoint extends outward by the stub length. Swapping the endpoint convention produces
a symbol that looks plausible and is electrically unusable.

Part IDs are 1-based. The on-disk component `PartCount` is the number of usable parts **plus one**,
and the library index mirrors that convention.

## How a body is fitted

Text is measured with the metrics of the fonts the symbol names — Times New Roman for pin names and
numbers, Arial for headings and titles — so fitting does not depend on the fonts installed on the
machine running the tools. Font sizes are treated as 10 mil per point: a size 10 name has a 100 mil
em, one pin pitch.

The fitted width is the widest of these, rounded up to a whole 100 mil pitch:

- for each row, the left column's text plus 100 mil of clear space plus the right column's text
- the part title, and the note, plus 100 mil either side
- `min_width`, and a 300 mil floor

Pin names start 40 mil inside the body edge, group headings 50 mil. An empty side simply contributes
nothing, which is why a connector comes out narrow. The pin stub grows past 200 mil only when a pin
number needs the room; every stub in a unit shares one length so the edge stays straight.

A declared `width` must be a multiple of 10 and at least the fitted width; a declared `pin_length`
must be a multiple of 10 and at least the fitted stub. Both are rejected at build time with the
value they need. Prefer omitting them.

## If `check-schlib` reports a text collision

The message names the two strings. Shorten a group `title` or a `note`, or omit `width` so the body
is fitted. The check uses the symbol's own font metrics, so a reported collision is a real overlap
in Altium, not a preview artifact.

## pyaltiumlib 0.7 quirks the checker works around

- Part membership must be read through `rawdata.get('ownerpartid')`; the convenience
  `owner_part_id` can behave as a boolean.
- Its schematic pin-description parsing is offset incorrectly, so the checker reads that field
  straight from the raw binary.
