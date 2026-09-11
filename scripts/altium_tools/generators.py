"""Parametric pad arrays and outline helpers.

These expand into ordinary `pads`, `tracks` and `arcs` before anything is written, so a generated
footprint is checked by exactly the same readback as a hand-written one. Millimetres throughout,
top view, +Y up, angles in degrees counter-clockwise.
"""

import math
import string
from .common import require

PAD_TEMPLATE_KEYS = (
    "type",
    "size_mm",
    "diameter_mm",
    "shape",
    "corner_radius_percent",
    "rotation",
    "hole_mm",
    "hole_shape",
    "slot_length_mm",
    "slot_rotation",
    "plated",
    "layer",
    "solder_mask_expansion_mm",
    "paste_mask_expansion_mm",
    "tenting",
    "keepout",
    "stack",
)
# JEDEC ball-row letters: I, O, Q, S, X and Z are never used.
LETTERS = [c for c in string.ascii_uppercase if c not in "IOQSXZ"]


def row_label(i):
    """A1-style row label: A..Y, then AA..AY, BA.. — the sequence Altium and JEDEC use."""
    out = ""
    while True:
        out = LETTERS[i % len(LETTERS)] + out
        i = i // len(LETTERS) - 1
        if i < 0:
            return out


def _template(entry, extra):
    require(isinstance(entry, dict), "Each generator entry must be an object.")
    unknown = (
        set(entry)
        - set(PAD_TEMPLATE_KEYS)
        - set(extra)
        - {"kind", "numbers", "start_number", "first", "skip"}
    )
    require(
        not unknown,
        f'Unsupported pad-array field(s): {", ".join(sorted(unknown))}.',
    )
    return {k: v for k, v in entry.items() if k in PAD_TEMPLATE_KEYS}


def _numbers(entry, count):
    if "numbers" in entry:
        numbers = [str(n) for n in entry["numbers"]]
        require(
            len(numbers) == count,
            f"This array places {count} pads but lists {len(numbers)} numbers.",
        )
        return numbers
    start = entry.get("start_number", 1)
    require(
        isinstance(start, int) and start >= 0,
        "start_number must be a non-negative integer.",
    )
    return [str(start + i) for i in range(count)]


def _place(entry, points, extra_keys):
    """Turn placed centres into pad dictionaries."""
    template = _template(entry, extra_keys)
    skip = {str(s) for s in entry.get("skip", [])}
    numbers = entry.get("_numbers") or _numbers(entry, len(points))
    first = entry.get("first", {})
    require(
        isinstance(first, dict),
        '"first" must be an object of pad fields for the first pad.',
    )
    pads = []
    for i, (number, (x, y)) in enumerate(zip(numbers, points)):
        if number in skip:
            continue
        pad = dict(template)
        pad["number"] = number
        pad["at"] = [round(x, 6), round(y, 6)]
        if i == 0:
            pad.update(first)
        pads.append(pad)
    return pads


def _num(entry, key, required=True, default=None):
    if key not in entry:
        require(
            not required,
            f'Pad array of kind {entry.get("kind")!r} needs "{key}".',
        )
        return default
    v = entry[key]
    require(
        isinstance(v, (int, float)) and math.isfinite(v),
        f'"{key}" must be a number.',
    )
    return float(v)


def _point(entry, key, default=(0.0, 0.0)):
    v = entry.get(key, default)
    require(
        len(v) == 2 and all(isinstance(c, (int, float)) for c in v),
        f'"{key}" must be [x, y] in mm.',
    )
    return float(v[0]), float(v[1])


def _count(entry, key="count", multiple=1):
    n = entry.get(key)
    require(isinstance(n, int) and n > 0, f'"{key}" must be a positive integer.')
    require(n % multiple == 0, f'"{key}" must be a multiple of {multiple}.')
    return n


def linear(entry):
    """A single row: `count` pads at `pitch_mm` from `start`, running along `direction`."""
    count = _count(entry)
    pitch = _num(entry, "pitch_mm")
    x, y = _point(entry, "start")
    direction = entry.get("direction", "+x")
    if "angle_deg" in entry:
        angle = math.radians(_num(entry, "angle_deg"))
    else:
        require(
            direction in ("+x", "-x", "+y", "-y"),
            '"direction" must be +x, -x, +y or -y.',
        )
        angle = math.radians({"+x": 0, "+y": 90, "-x": 180, "-y": 270}[direction])
    dx, dy = math.cos(angle) * pitch, math.sin(angle) * pitch
    return _place(
        entry,
        [(x + i * dx, y + i * dy) for i in range(count)],
        ("count", "pitch_mm", "start", "direction", "angle_deg"),
    )


def dual(entry):
    """Two parallel rows — SOIC, SOP, DIP, dual-row header.

    `rows: "left-right"` (default) puts the rows either side of the Y axis and numbers them the way
    an IC is numbered: down the left row, then up the right row. `numbering: "zigzag"` numbers
    across the rows instead (1 left, 2 right, 3 left, ...), the usual header convention.
    """
    count = _count(entry, multiple=2)
    pitch = _num(entry, "pitch_mm")
    span = _num(entry, "span_mm")
    cx, cy = _point(entry, "center")
    half = count // 2
    rows = entry.get("rows", "left-right")
    numbering = entry.get("numbering", "ccw")
    require(
        rows in ("left-right", "top-bottom"),
        '"rows" must be left-right or top-bottom.',
    )
    require(numbering in ("ccw", "zigzag"), '"numbering" must be ccw or zigzag.')
    offset = (half - 1) * pitch / 2
    if rows == "left-right":
        first = [
            (cx - span / 2, cy + offset - i * pitch) for i in range(half)
        ]  # left row, top to bottom
        second = [
            (cx + span / 2, cy - offset + i * pitch) for i in range(half)
        ]  # right row, bottom to top
    else:
        first = [
            (cx - offset + i * pitch, cy + span / 2) for i in range(half)
        ]  # top row, left to right
        second = [
            (cx + offset - i * pitch, cy - span / 2) for i in range(half)
        ]  # bottom row, right to left
    if numbering == "ccw":
        points = first + second
    else:
        second = list(reversed(second))
        points = [p for pair in zip(first, second) for p in pair]
    return _place(
        entry,
        points,
        ("count", "pitch_mm", "span_mm", "center", "rows", "numbering"),
    )


def quad(entry):
    """Four sides, pin 1 at the top of the left side, counter-clockwise — QFP, PLCC, QFN."""
    count = _count(entry, multiple=4)
    pitch = _num(entry, "pitch_mm")
    span_x = _num(entry, "span_x_mm")
    span_y = _num(entry, "span_y_mm", required=False, default=None)
    if span_y is None:
        span_y = span_x
    cx, cy = _point(entry, "center")
    side = count // 4
    offset = (side - 1) * pitch / 2
    points = [
        (cx - span_x / 2, cy + offset - i * pitch) for i in range(side)
    ]  # left, top to bottom
    points += [
        (cx - offset + i * pitch, cy - span_y / 2) for i in range(side)
    ]  # bottom, left to right
    points += [
        (cx + span_x / 2, cy - offset + i * pitch) for i in range(side)
    ]  # right, bottom to top
    points += [
        (cx + offset - i * pitch, cy + span_y / 2) for i in range(side)
    ]  # top, right to left
    entry = dict(entry)
    if "rotation" not in entry and entry.get("rotate_sides", True):
        # Left and right pads keep the array's own rotation; top and bottom turn 90 degrees.
        pads = _place(
            entry,
            points,
            (
                "count",
                "pitch_mm",
                "span_x_mm",
                "span_y_mm",
                "center",
                "rotate_sides",
            ),
        )
        for i, pad in enumerate(pads):
            if side <= i < 2 * side or 3 * side <= i:
                pad["rotation"] = 90
        return pads
    return _place(
        entry,
        points,
        (
            "count",
            "pitch_mm",
            "span_x_mm",
            "span_y_mm",
            "center",
            "rotate_sides",
        ),
    )


def grid(entry):
    """A ball or pin grid: `rows` x `columns`, A1 at the top left, letters down, numbers right."""
    rows = _count(entry, "rows")
    cols = _count(entry, "columns")
    px = _num(entry, "pitch_x_mm", required=False) or _num(entry, "pitch_mm")
    py = _num(entry, "pitch_y_mm", required=False) or px
    cx, cy = _point(entry, "center")
    labels = entry.get("row_labels") or [row_label(i) for i in range(rows)]
    require(len(labels) == rows, '"row_labels" must have one entry per row.')
    points = []
    numbers = []
    for r in range(rows):
        for c in range(cols):
            points.append(
                (
                    cx + (c - (cols - 1) / 2) * px,
                    cy + ((rows - 1) / 2 - r) * py,
                )
            )
            numbers.append(f"{labels[r]}{c+1}")
    entry = dict(entry)
    entry["_numbers"] = numbers
    return _place(
        entry,
        points,
        (
            "rows",
            "columns",
            "pitch_mm",
            "pitch_x_mm",
            "pitch_y_mm",
            "center",
            "row_labels",
            "_numbers",
        ),
    )


def circular(entry):
    """Pads on a circle — DIN, circular connector, mounting pattern."""
    count = _count(entry)
    radius = _num(entry, "radius_mm")
    start = _num(entry, "start_angle_deg", required=False, default=90.0)
    direction = entry.get("direction", "cw")
    cx, cy = _point(entry, "center")
    require(direction in ("cw", "ccw"), '"direction" must be cw or ccw.')
    sign = -1 if direction == "cw" else 1
    points = []
    rotations = []
    for i in range(count):
        a = math.radians(start + sign * i * 360.0 / count)
        points.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
        rotations.append(math.degrees(a))
    pads = _place(
        entry,
        points,
        (
            "count",
            "radius_mm",
            "start_angle_deg",
            "direction",
            "center",
            "rotate_pads",
        ),
    )
    if entry.get("rotate_pads"):
        for pad, angle in zip(pads, rotations):
            pad["rotation"] = round(angle % 360, 6)
    return pads


KINDS = {
    "linear": linear,
    "dual": dual,
    "quad": quad,
    "grid": grid,
    "circular": circular,
}


def pads_from_arrays(arrays):
    out = []
    for entry in arrays:
        require(
            isinstance(entry, dict) and entry.get("kind") in KINDS,
            f'Each pad array needs "kind": one of {", ".join(sorted(KINDS))}.',
        )
        out += KINDS[entry["kind"]](entry)
    return out


# --- graphics helpers -------------------------------------------------------------------------


def _outline_points(entry):
    shape = entry["shape"]
    if shape == "rect":
        if "center" in entry or "size_mm" in entry:
            cx, cy = _point(entry, "center")
            size = entry["size_mm"]
            size = [size, size] if isinstance(size, (int, float)) else size
            require(len(size) == 2, '"size_mm" must be a number or [x, y].')
            x0, y0, x1, y1 = (
                cx - size[0] / 2,
                cy - size[1] / 2,
                cx + size[0] / 2,
                cy + size[1] / 2,
            )
        else:
            x0, y0 = _point(entry, "corner1")
            x1, y1 = _point(entry, "corner2")
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], True
    if shape == "polyline":
        points = [(float(p[0]), float(p[1])) for p in entry["points"]]
        require(len(points) >= 2, "A polyline needs at least two points.")
        return points, bool(entry.get("closed", False))
    require(False, f"Unsupported outline shape {shape!r}.")


def graphics_from_outlines(outlines):
    """Expand `outlines` into tracks and arcs."""
    tracks = []
    arcs = []
    for entry in outlines:
        require(
            isinstance(entry, dict) and "shape" in entry,
            'Each outline needs a "shape".',
        )
        shape = entry["shape"]
        layer = entry.get("layer")
        width = _num(entry, "width_mm")
        require(layer is not None, 'Each outline needs a "layer".')
        if shape == "circle":
            arcs.append(
                dict(
                    center=list(_point(entry, "center")),
                    radius_mm=_num(entry, "radius_mm"),
                    start_angle=0.0,
                    end_angle=360.0,
                    layer=layer,
                    width_mm=width,
                )
            )
            continue
        points, closed = _outline_points(entry)
        segments = list(zip(points, points[1:] + ([points[0]] if closed else [])))
        for start, end in segments:
            require(start != end, "An outline segment has identical endpoints.")
            tracks.append(
                dict(
                    start=list(start),
                    end=list(end),
                    layer=layer,
                    width_mm=width,
                )
            )
    return tracks, arcs
