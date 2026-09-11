"""Readback verification: decode the saved library and compare it with its source JSON.

The library is re-read by :mod:`altium_tools.vendor.altium`, which decodes the container
and record layouts independently of the writer, so a mismatch between what was meant and
what was stored shows up here rather than in Altium. Every check that can be expressed as
an equality is asserted; what cannot be checked is named in the report instead of assumed.

Both checkers also render previews with the bundled rasteriser and record provenance --
source and library digests, toolkit integrity, and whether an independent map was supplied
-- so a report cannot be quoted as evidence for a build it did not examine.
"""

import math
import struct
from pathlib import Path

from . import metrics, provenance
from .common import require
from .schematic import prepare, altium_name
from .vendor import altium, raster
from .vendor.altium import AltiumFormatError

PART_SCALE = 4  # preview pixels per 10 mil unit
SHEET_SCALE = 1.6  # ... for the multi-part contact sheet
SHEET_COLUMNS = 3

BODY_FILL = "#ffffb0"
BODY_EDGE = "#800000"
PIN_INK = "#800000"
TITLE_INK = "#000080"
NOTE_INK = "#666666"
PAPER = "#ffffff"
SHEET_BG = "#eeeeee"


def overbar(name):
    """Split an Altium wire name into its characters and their overbar flags."""
    chars = []
    marks = []
    for c in name:
        if c == "\\":
            require(bool(chars), "Malformed pin overbar.")
            marks[-1] = True
        else:
            chars.append(c)
            marks.append(False)
    return "".join(chars), marks


# --- preview scene ------------------------------------------------------------------------------


class Scene:
    """Drawing operations in 10 mil schematic units, Y increasing downward.

    Recording the picture before rasterising it lets the same scene be rendered at more
    than one scale, and keeps the geometry assertions above independent of the renderer.
    """

    def __init__(self, left, top, width, height):
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.ops = []

    def rect(self, x0, y0, x1, y1, fill, outline):
        self.ops.append(("rect", x0, y0, x1, y1, fill, outline))

    def line(self, points, color, width=1.0):
        self.ops.append(("line", points, color, width))

    def dot(self, x, y, radius, color):
        self.ops.append(("dot", x, y, radius, color))

    def text(self, string, x, y, font, anchor, color, marks=None):
        self.ops.append(("text", str(string), x, y, font, anchor, color, marks))

    def render(self, scale):
        image = raster.Canvas(
            round(self.width * scale), round(self.height * scale), PAPER
        )
        ox = self.left * scale
        oy = self.top * scale

        def at(x, y):
            return (ox + x * scale, oy + y * scale)

        for op in self.ops:
            if op[0] == "rect":
                _, x0, y0, x1, y1, fill, outline = op
                a = at(x0, y0)
                b = at(x1, y1)
                image.rect(a[0], a[1], b[0], b[1], fill=fill)
                image.line(
                    [a, (b[0], a[1]), b, (a[0], b[1]), a],
                    outline,
                    max(1.0, scale / 2),
                )
            elif op[0] == "line":
                _, points, color, width = op
                image.line(
                    [at(x, y) for x, y in points],
                    color,
                    max(1.0, width * scale / 2),
                )
            elif op[0] == "dot":
                _, x, y, radius, color = op
                px, py = at(x, y)
                image.dot(px, py, max(1.0, radius * scale), color)
            else:
                _, string, x, y, font, anchor, color, marks = op
                size = metrics.size(font) * scale
                advances = [metrics.width(c, font) * scale for c in string]
                ink = metrics.INK * size
                px, py = at(x, y)
                image.text(
                    px,
                    py,
                    string,
                    size,
                    advances,
                    anchor,
                    color,
                    ink=ink,
                    weight=1.6 if font == 2 else 1.0,
                )
                if marks and any(marks):
                    total = sum(advances)
                    pen = {"l": px, "m": px - total / 2.0, "r": px - total}[anchor[0]]
                    top = (py - ink) if anchor[1] == "s" else (py - ink / 2.0)
                    bar = top - max(1.0, size * 0.07)
                    for index, flag in enumerate(marks):
                        if flag:
                            start = pen + sum(advances[:index])
                            image.line(
                                [(start, bar), (start + advances[index], bar)],
                                color,
                                max(1.0, size * 0.055),
                            )
        return image


def _contact_sheet(scenes, path, scale=SHEET_SCALE):
    images = [scene.render(scale) for scene in scenes]
    gap = round(12 * scale)
    width = sum(i.width for i in images) + gap * (len(images) + 1)
    height = max(i.height for i in images) + 2 * gap
    sheet = raster.Canvas(width, height, SHEET_BG)
    x = gap
    for image in images:
        sheet.paste(image, x, gap)
        x += image.width + gap
    sheet.save(path)


# --- schematic ----------------------------------------------------------------------------------


def _compare_pin_map(library, pinmap):
    """Compare saved pin number -> name against an independently transcribed map."""
    per_component = (
        pinmap if pinmap and all(isinstance(v, dict) for v in pinmap.values()) else None
    )
    if per_component is None:
        require(
            len(library.components) == 1,
            "A flat pin map applies to one-component libraries only. Key the map by "
            "component name to check several components at once.",
        )
        per_component = {library.components[0].name: pinmap}
    require(
        set(per_component) == {c.name for c in library.components},
        "Pin map component names differ from the library contents.",
    )
    for component in library.components:
        actual = {p.designator: p.name for p in component.pins}
        wanted = {
            str(k): altium_name(v) for k, v in per_component[component.name].items()
        }
        if actual != wanted:
            differing = sorted(
                k for k in set(actual) | set(wanted) if actual.get(k) != wanted.get(k)
            )
            detail = "; ".join(
                f"{k}: saved {actual.get(k)!r} vs map {wanted.get(k)!r}"
                for k in differing[:6]
            )
            require(
                False,
                f"{component.name}: saved pin mapping differs from the supplied "
                f"independent map ({len(differing)} pin(s)). {detail}",
            )


def _check_component(component, expected):
    """Assert every stored pin field against the prepared source, then return part scenes."""
    name = component.name
    assert component.name == expected["name"], (name, "component name")
    assert component.part_count == len(expected["units"]), (name, "part count")
    assert len(component.pins) == expected["pin_count"], (name, "pin count")
    by_number = {p.designator: p for p in component.pins}
    assert len(by_number) == len(component.pins), (
        name,
        "duplicate pin designators",
    )
    assert set(by_number) == set(expected["expected_pin_numbers"]), (
        name,
        "pin number set",
    )

    for pin in expected["pins"]:
        stored = by_number[pin["number"]]
        assert stored.name == pin["wire_name"], (
            name,
            pin["number"],
            "pin name",
        )
        assert stored.description == pin["description"], (
            name,
            pin["number"],
            "pin description",
        )
        assert stored.electrical == pin["electrical"], (
            name,
            pin["number"],
            "electrical type",
        )
        assert stored.formal_type == 1, (
            name,
            pin["number"],
            "formal type byte",
        )
        assert stored.owner_part_id == pin["part"], (
            name,
            pin["number"],
            "owning part",
        )
        assert stored.show_name == (not pin["hide_name"]), (
            name,
            pin["number"],
            "name visibility",
        )
        assert stored.show_designator, (
            name,
            pin["number"],
            "designator visibility",
        )
        assert not stored.hidden, (name, pin["number"], "pin is hidden")
        # A rotated pin would sit on the top or bottom edge; the writer never emits one.
        assert stored.horizontal, (
            name,
            pin["number"],
            "pin is not horizontal",
        )
        assert stored.orientation == (2 if pin["side"] == "left" else 0), (
            name,
            pin["number"],
            "side",
        )
        assert stored.x == pin["x"] and stored.y == pin["y"], (
            name,
            pin["number"],
            "pin location",
        )
        assert stored.length == pin["length"], (
            name,
            pin["number"],
            "pin length",
        )
        # Every pin sits on the 100 mil schematic grid, so wires connect in the editor.
        assert stored.x % 10 == 0 and stored.y % 10 == 0, (
            name,
            pin["number"],
            "pin is off the 100 mil grid",
        )

    assert not any(getattr(r, "record", None) == 45 for r in component.records), (
        name,
        "unexpected record 45",
    )

    scenes = []
    report = []
    for part in range(1, component.part_count + 1):
        unit = expected["units"][part - 1]
        records = component.records_for_part(part)
        rectangles = [r for r in records if isinstance(r, altium.SchRectangle)]
        assert len(rectangles) == 1, (
            name,
            part,
            "expected exactly one body rectangle",
        )
        body = rectangles[0]
        width = body.corner_x
        height = -body.y
        assert body.x == 0 and body.corner_y == 0, (
            name,
            part,
            "body rectangle is not anchored at the origin",
        )
        assert width == unit["width"] and height == unit["height"], (
            name,
            part,
            "body size",
        )
        assert width % 10 == 0 and height % 10 == 0, (
            name,
            part,
            "body is off the 100 mil grid",
        )

        pins = [r for r in records if isinstance(r, altium.SchPin)]
        stub = int(unit["pin_length"])
        for pin in pins:
            left = pin.orientation == 2
            assert pin.x == (0 if left else width), (
                name,
                part,
                pin.designator,
                "pin is not on a body edge",
            )
            depth = -pin.y
            assert 0 < depth < height, (
                name,
                part,
                pin.designator,
                "pin is outside the body",
            )
            assert pin.end_x == (-stub if left else width + stub), (
                name,
                part,
                pin.designator,
                "wire endpoint",
            )
            assert pin.end_y == pin.y, (
                name,
                part,
                pin.designator,
                "pin is not horizontal",
            )
            # The wire endpoint is on the 100 mil grid too.
            assert pin.end_x % 10 == 0 and pin.end_y % 10 == 0, (
                name,
                part,
                pin.designator,
                "endpoint off grid",
            )
        assert len({(p.x, p.y) for p in pins}) == len(pins), (
            name,
            part,
            "two pins share a location",
        )

        scene, boxes = _part_scene(
            component, expected, part, unit, width, height, records, pins
        )
        for i, (text_a, box_a) in enumerate(boxes):
            for text_b, box_b in boxes[i + 1 :]:
                assert not metrics.overlap(box_a, box_b), (
                    name,
                    part,
                    "text collision",
                    text_a,
                    text_b,
                    'widen the body or shorten a label; omit "width" to fit automatically',
                )
        scenes.append(scene)
        report.append(
            dict(
                component=name,
                part=part,
                part_letter=chr(64 + part),
                pins=len(pins),
                width=width,
                height=height,
                pin_length=stub,
                width_source="auto" if unit["auto_width"] else "declared",
                pin_sides=sorted(
                    {"left" if p.orientation == 2 else "right" for p in pins}
                ),
                text_collisions=0,
            )
        )
    return scenes, report


def _part_scene(component, expected, part, unit, width, height, records, pins):
    """Build the preview for one part and collect the ink boxes the collision test uses."""
    margin = int(unit["pin_length"]) + 20
    title_width = metrics.width(component.name, 1)
    canvas = max(width + 2 * margin, margin + 20 + int(math.ceil(title_width)))
    scene = Scene(margin, 55, canvas, height + 75)
    scene.rect(0, 0, width, height, BODY_FILL, BODY_EDGE)
    boxes = []

    def write(text, x, y, font, anchor, color, marks=None, track=True):
        scene.text(text, x, y, font, anchor, color, marks)
        # Collision testing uses the metrics of the fonts the symbol names, never whichever
        # face a preview machine happens to have.
        if track:
            boxes.append((str(text), metrics.box(str(text), font, x, y, anchor)))

    write(
        expected["designator_prefix"] + "?" + chr(64 + part),
        0,
        -30,
        1,
        "ls",
        TITLE_INK,
        track=False,
    )
    write(component.name, 0, -15, 1, "ls", TITLE_INK, track=False)
    for pin in pins:
        left = pin.orientation == 2
        depth = -pin.y
        end = pin.end_x
        scene.line([(pin.x, depth), (end, depth)], PIN_INK, 1.0)
        # The open endpoint marker is QA-only; the line and labels come from the saved file.
        scene.dot(end, depth, 1.0, PIN_INK)
        if pin.show_name:
            shown, marks = overbar(pin.name)
            write(
                shown,
                pin.x + (4 if left else -4),
                depth,
                1,
                "lm" if left else "rm",
                PIN_INK,
                marks,
            )
        write(pin.designator, (pin.x + end) / 2, depth - 1, 1, "ms", PIN_INK)
    for record in records:
        if getattr(record, "record", None) != 4:
            continue
        anchor = {3: "lm", 4: "mm", 5: "rm"}.get(record.justification, "ls")
        write(
            record.text,
            record.x,
            -record.y,
            record.font_id,
            anchor,
            TITLE_INK if record.font_id == 2 else NOTE_INK,
        )
    return scene, boxes


def check_schlib(
    document,
    source_path,
    library,
    qa_dir,
    pinmap=None,
    pinmap_path=None,
    version="?",
):
    library = Path(library)
    qa_dir = Path(qa_dir)
    signature = provenance.digest_bytes(
        (provenance.digest_file(source_path) + provenance.digest_file(library)).encode()
    )
    provenance.claim_qa_dir(qa_dir, signature)
    try:
        lib = altium.read(library)
    except AltiumFormatError as error:
        raise ValueError(f"The saved library could not be read back: {error}") from None

    expected = prepare(document)
    assert lib.component_count == len(expected), (
        "library component count",
        lib.component_count,
        len(expected),
    )
    assert [c.name for c in lib.components] == [
        e["name"] for e in expected
    ], "component names or order"
    if pinmap is not None:
        _compare_pin_map(lib, pinmap)

    report = []
    artifacts = []
    for component, expectation in zip(lib.components, expected):
        scenes, parts = _check_component(component, expectation)
        report.extend(parts)
        for part, scene in enumerate(scenes, 1):
            name = f"{component.name}-{chr(64+part)}.png"
            scene.render(PART_SCALE).save(qa_dir / name)
            artifacts.append(name)
        for start in range(0, len(scenes), SHEET_COLUMNS):
            name = f"{component.name}-sheet-{start//SHEET_COLUMNS+1}.png"
            _contact_sheet(scenes[start : start + SHEET_COLUMNS], qa_dir / name)
            artifacts.append(name)
        print(
            f"{component.name}: {len(component.pins)} pins, {component.part_count} part(s); "
            "every pin field, side, length and horizontal endpoint verified against the source JSON"
        )

    result = provenance.header(
        "check-schlib", source_path, library, qa_dir, pinmap_path, version
    )
    result.update(
        components=len(expected),
        pins=sum(e["pin_count"] for e in expected),
        parts=report,
        images=sorted(artifacts),
        checks_not_performed=[
            "datasheet correctness",
            "electrical rule check",
            "opening the library in Altium",
        ],
    )
    provenance.write_json(qa_dir / "validation.json", result)
    provenance.release_qa_dir(qa_dir, artifacts + ["validation.json"])
    return result


# --- PCB ----------------------------------------------------------------------------------------


def check_pcblib(
    spec,
    profile,
    source_path,
    library,
    qa_dir,
    padmap=None,
    padmap_path=None,
    version="?",
):
    """Read back every toolkit-written PcbLib record and compare it with its JSON source.

    Record bodies are rebuilt by the writer and compared byte for byte against the parsed
    file, so a shared misunderstanding of a field's meaning would pass. What this does
    catch is any difference between the intent in the JSON and the bytes on disk.
    """
    from .pcb import (
        prepare,
        footprints_of,
        ROLES,
        footprint_records,
        load_template,
    )
    from .geometry import pad_outline, distance

    library = Path(library)
    qa_dir = Path(qa_dir)
    signature = provenance.digest_bytes(
        (provenance.digest_file(source_path) + provenance.digest_file(library)).encode()
    )
    provenance.claim_qa_dir(qa_dir, signature)
    try:
        lib = altium.read(library)
    except AltiumFormatError as error:
        raise ValueError(f"The saved library could not be read back: {error}") from None

    expected = [prepare(entry) for entry in footprints_of(spec)]
    names = [item["name"] for item in expected]
    assert lib.footprint_names == names, (
        "footprint names or order",
        lib.footprint_names,
        names,
    )
    params, pad_base, text_base, _ = load_template(None)

    for index, pair in enumerate(profile.get("pairs", [])):
        for side, column, kind in zip(
            ("top", "bottom"), ("L1", "L2"), ROLES[pair["role"]]
        ):
            number = pair[side]
            assert (
                lib.parameters.get(f"mechpair{index}{column}") == f"MECHANICAL{number}"
            ), ("mechanical layer pair", index, side)
            key = 56 + number if number <= 16 else 0x04000000 | number
            assert lib.layer_kinds.get(key) == kind, (
                "mechanical layer kind",
                number,
            )

    report = []
    points = []
    outlines = []
    for footprint, saved in zip(expected, lib.footprints):
        records, _, texts = footprint_records(footprint, pad_base, text_base)
        assert saved.name == footprint["name"], "footprint name"
        assert len(saved.records) == len(records), (
            footprint["name"],
            "primitive count",
            len(saved.records),
            len(records),
        )
        for position, (
            (want_type, want_blob),
            (got_type, got_blocks),
        ) in enumerate(zip(records, saved.records)):
            assert got_type == want_type, (
                footprint["name"],
                position,
                "primitive type",
            )
            offset = 1
            want_blocks = []
            for _ in range(len(got_blocks)):
                size = struct.unpack_from("<I", want_blob, offset)[0]
                offset += 4
                want_blocks.append(want_blob[offset : offset + size])
                offset += size
            assert len(want_blocks) == len(got_blocks), (
                footprint["name"],
                position,
                "block count",
            )
            for block, (want, got) in enumerate(zip(want_blocks, got_blocks)):
                if want_type == 2 and block == 4:
                    # Bytes 126..157 of a pad's fifth block hold a per-instance identifier.
                    assert want[:126] == got[:126] and want[158:] == got[158:], (
                        footprint["name"],
                        position,
                        "pad block 4",
                    )
                else:
                    assert want == got, (
                        footprint["name"],
                        position,
                        f"block {block}",
                    )

        pads = footprint["pads"]
        assert len({p["number"] for p in pads}) == len(pads), (
            footprint["name"],
            "duplicate pad number",
        )
        assert len(saved.pads) == len(pads), (footprint["name"], "pad count")
        report.append(
            dict(
                name=footprint["name"],
                pads=len(pads),
                records=len(saved.records),
                primitives=saved.counts,
            )
        )
        points += [tuple(p["at"]) for p in pads] + [tuple(t["at"]) for t in texts]
        for pad in pads:
            outlines.append(
                (
                    pad["number"],
                    pad_outline(
                        tuple(pad["at"]),
                        pad["size"],
                        pad["shape"],
                        pad["rotation"],
                        pad["radius"],
                    ),
                )
            )

    if padmap is not None:
        require(
            len(expected) == 1,
            "A pad map applies to a single-footprint library only.",
        )
        positions = {p["number"]: p["at"] for p in expected[0]["pads"]}
        require(
            set(map(str, padmap)) == set(positions),
            "Pad-map designators differ from the library contents.",
        )
        for number, point in padmap.items():
            require(
                isinstance(point, list)
                and len(point) == 2
                and all(
                    abs(float(a) - float(b)) < 0.000003
                    for a, b in zip(point, positions[str(number)])
                ),
                f"Pad map position differs for pad {number}: map {point}, library {positions[str(number)]}.",
            )

    gaps = [
        distance(a[1], b[1]) for i, a in enumerate(outlines) for b in outlines[i + 1 :]
    ]
    minimum = min(gaps) if gaps else None
    require(minimum is None or minimum > 0, "Copper pads overlap or touch.")
    if minimum is not None:
        # The outlines are segment approximations of curves, so digits far below a
        # nanometre are artefacts of the arithmetic rather than measurements. Round
        # the reported figure so it reads as the under-estimate it is.
        minimum = round(minimum, 6)

    artifacts = []
    if points:
        _footprint_preview(points, outlines, qa_dir / "footprint.png")
        artifacts.append("footprint.png")

    result = provenance.header(
        "check-pcblib", source_path, library, qa_dir, padmap_path, version
    )
    result.update(
        footprints=report,
        minimum_copper_clearance_mm=minimum,
        layer_pairs=profile.get("pairs", []),
        images=sorted(artifacts),
        combined_clearance_measurement=len(expected) > 1,
        checks_not_performed=[
            "datasheet and land-pattern correctness",
            "full board DRC",
            "solder-mask and assembly clearance",
            "fabrication readiness",
            "opening the library in Altium",
        ],
    )
    if len(expected) > 1:
        result["checker_limitation"] = (
            "Clearance and the preview combine every footprint in "
            "this library. Build each footprint on its own to assess it individually."
        )
    provenance.write_json(qa_dir / "validation.json", result)
    provenance.release_qa_dir(qa_dir, artifacts + ["validation.json"])
    return result


def _footprint_preview(points, outlines, path):
    x0, y0, x1, y1 = bounds_of(points)
    scale = min(40, 1400 / max(x1 - x0 + 8, y1 - y0 + 8))
    image = raster.Canvas(
        max(80, round((x1 - x0 + 8) * scale)),
        max(80, round((y1 - y0 + 8) * scale)),
        "#101821",
    )
    size = max(7.0, scale * 0.55)
    for number, outline in outlines:
        polygon = [((p[0] - x0 + 4) * scale, (y1 - p[1] + 4) * scale) for p in outline]
        image.polygon(polygon, fill="#eabf64")
        cx = sum(p[0] for p in polygon) / len(polygon)
        cy = sum(p[1] for p in polygon) / len(polygon)
        advances = [size * 0.56] * len(str(number))
        image.text(cx, cy, str(number), size, advances, "mm", "#ffffff")
    image.save(path)


def bounds_of(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)
