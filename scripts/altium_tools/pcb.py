"""Native PcbLib writer: pads (through-hole, SMD, slotted, rotated, stacked), tracks, arcs, fills,
regions, free and special text, and extruded 3D bodies, on any layer a footprint may use.

Every record is built at the byte offsets documented in `references/binary-format.md`; the layer
profile and library metadata come from the bundled native template. What is still out of scope:
embedded STEP models, vias inside a footprint, dimensions, coordinates and polygon pours.
"""

from pathlib import Path
import copy
import math
import re
import struct
import uuid
from .common import record, uid, compound_file, require

# Container access, record framing and parameter decoding all live in ``vendor.altium``, so
# the writer and the checker read a saved library through the same independent parser and
# never through these writer routines.
from .vendor.altium import (
    open_container,
    decode_pcb_parameters as decode_params,
    parse_pcb_records as parse_records,
    PCB_NAMES as NAMES,
)
from . import pcb_layers as L
from .generators import pads_from_arrays, graphics_from_outlines
from . import metrics

# scripts/altium_tools/pcb.py -> scripts/ -> skill root, where assets/ lives.
SKILL_ROOT = Path(__file__).resolve().parents[2]


def U32(n):
    return struct.pack("<I", n)


def block(b):
    return U32(len(b)) + b


def string(s):
    b = s.encode("cp1252")
    require(len(b) <= 255, "String exceeds 255 bytes.")
    return block(bytes([len(b)]) + b)


def cstring_block(text):
    """The nested `|KEY=VALUE|...` parameter block regions and 3D bodies carry."""
    return block(text.encode("cp1252") + b"\0")


def coord(mm):
    require(
        isinstance(mm, (int, float)) and not isinstance(mm, bool) and math.isfinite(mm),
        "Coordinates must be finite numbers.",
    )
    n = round(mm / 0.0254 * 10000)
    require(-(2**31) <= n < 2**31, "Coordinate overflow.")
    return n


def mil(mm):
    """Altium's mil-suffixed coordinate spelling, trailing zeros trimmed."""
    return f"{mm/.0254:.6f}".rstrip("0").rstrip(".") + "mil" if mm else "0mil"


def angle(value, field="rotation"):
    require(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value),
        f'"{field}" must be a number of degrees.',
    )
    return float(value) % 360.0


FLAG_UNLOCKED = 0x04
FLAG_SAVED = 0x08
FLAG_TENT_TOP = 0x20
FLAG_TENT_BOTTOM = 0x40
FLAG_KEEPOUT = 0x200
TENTING = {
    "none": (False, False),
    "top": (True, False),
    "bottom": (False, True),
    "both": (True, True),
}


def common(lid, tenting="none", keepout=False, locked=False):
    """The 13-byte header every primitive starts with."""
    require(tenting in TENTING, '"tenting" must be none, top, bottom or both.')
    top, bottom = TENTING[tenting]
    flags = FLAG_SAVED | (0 if locked else FLAG_UNLOCKED)
    flags |= FLAG_TENT_TOP if top else 0
    flags |= FLAG_TENT_BOTTOM if bottom else 0
    flags |= FLAG_KEEPOUT if keepout else 0
    return bytes([L.layer_byte(lid)]) + struct.pack("<H", flags) + b"\xff" * 10


# --- layer profile ------------------------------------------------------------------------------

ROLES = L.ROLES


def layer_profile(params, profile, used):
    """Rewrite the template's layer registry from the profile, and enable the layers in use."""
    params = copy.deepcopy(params)
    roles = {}
    names = {}
    pairs = profile.get("pairs", [])
    for s in profile.get("standalone", []):
        n = s["number"]
        require(n not in roles, "Layer reused in profile.")
        kind = s.get("kind", "Undefined")
        require(kind in L.KINDS, f"Unsupported standalone layer kind {kind!r}.")
        roles[n] = (L.KINDS[kind], kind)
        names[n] = s.get("name", f"Mechanical {n}")
    for pair in pairs:
        require(
            pair["role"] in ROLES,
            f'Unsupported component layer role {pair.get("role")!r}.',
        )
        for side, kind in zip(("top", "bottom"), ROLES[pair["role"]]):
            n = pair[side]
            require(n not in roles, "Layer belongs to more than one pair.")
            roles[n] = (kind, pair["role"] + side.title())
            names[n] = f"M{n}"
    require(
        all(isinstance(n, int) and 1 <= n <= 32 for n in roles),
        "Mechanical numbers must be 1..32.",
    )
    require(
        used <= roles.keys(),
        f"A primitive uses mechanical layer(s) {sorted(used-roles.keys())}, which the profile leaves disabled.",
    )
    for n in range(1, 33):
        prefixes = [f"LAYER{56+n}" if n <= 16 else f"LAYERV7_{n-17}"]
        for k, val in list(params.items()):
            if (
                re.fullmatch(r"LAYER_V8_\d+LAYERID", k)
                or re.fullmatch(r"V9_CACHE_LAYER\d+_LAYERID", k)
            ) and val == str(0x01020000 + n):
                prefixes.append(k[:-7])
        require(
            len(prefixes) == 3,
            "Template must contain native V7/V8/V9 mechanical registry rows.",
        )
        for pre in prefixes:
            params[pre + "NAME"] = names.get(n, f"Mechanical {n}")
            params[pre + "MECHENABLED"] = "TRUE" if n in roles else "FALSE"
            params[pre + "MECHKIND"] = roles.get(n, (0, "Undefined"))[1]
            if pre + "USEDBYPRIMS" in params:
                params[pre + "USEDBYPRIMS"] = "TRUE" if n in used else "FALSE"
    for k in list(params):
        if re.fullmatch(r"MECHPAIR\d+L[12]", k):
            del params[k]
    for i, p in enumerate(pairs):
        params[f"MECHPAIR{i}L1"] = f"MECHANICAL{p['top']}"
        params[f"MECHPAIR{i}L2"] = f"MECHANICAL{p['bottom']}"
    for k in ("LAYERSET1LAYERS", "LAYERSET5LAYERS"):
        tokens = [
            s
            for s in params.get(k, "").split(",")
            if s and not re.fullmatch(r"Mechanical\d+", s)
        ]
        params[k] = ",".join(tokens + [f"Mechanical{n}" for n in sorted(roles)])
    entries = [
        (56 + n if n <= 16 else 0x04000000 | n, kind)
        for n, (kind, _) in sorted(roles.items())
        if kind
    ]
    mapping = (
        U32(8)
        + "1.0\0".encode("utf-16le")
        + U32(0)
        + U32(len(entries))
        + b"".join(struct.pack("<II", *e) for e in entries)
    )
    return params, mapping


# --- pads ---------------------------------------------------------------------------------------

SHAPES = {
    "round": 1,
    "circle": 1,
    "oval": 1,
    "rect": 2,
    "rectangle": 2,
    "square": 2,
    "octagonal": 3,
    "octagon": 3,
    "rounded_rect": 9,
    "roundedrectangle": 9,
    "rounded": 9,
}
HOLE_SHAPES = {"round": 0, "square": 1, "slot": 2}
PAD_KEYS = {
    "number",
    "at",
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
}
STACK_KEYS = {"middle", "bottom"}


def _size(value, field="size_mm"):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = [value, value]
    require(
        isinstance(value, list) and len(value) == 2,
        f'"{field}" must be a number or [x, y] in mm.',
    )
    x, y = value
    require(
        all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            for v in (x, y)
        ),
        f'"{field}" must be positive.',
    )
    return float(x), float(y)


def _shape(value, field="shape"):
    require(
        value in SHAPES,
        f'"{field}" must be one of {", ".join(sorted(set(SHAPES)))}.',
    )
    return SHAPES[value]


def _expansion(value, field):
    """A mask expansion is either a distance in mm (manual, mode 2) or "rule" (mode 1)."""
    if value == "rule":
        return 1, 0.0
    require(
        isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0,
        f'"{field}" must be a non-negative number of mm or "rule".',
    )
    return 2, float(value)


def pad_geometry(pad):
    """Resolve one pad specification into the values the record needs."""
    unknown = set(pad) - PAD_KEYS
    require(not unknown, f'Unsupported pad field(s): {", ".join(sorted(unknown))}.')
    require("number" in pad and "at" in pad, 'Every pad needs "number" and "at".')
    number = str(pad["number"])
    require(0 < len(number.encode("cp1252")) <= 255, "Bad pad number.")
    require(
        isinstance(pad["at"], list) and len(pad["at"]) == 2,
        'Pad "at" must be [x_mm, y_mm].',
    )
    kind = pad.get("type") or ("smd" if "hole_mm" not in pad else "tht")
    require(kind in ("tht", "smd", "npth"), 'Pad "type" must be tht, smd or npth.')
    size = _size(pad["size_mm"]) if "size_mm" in pad else None
    if size is None:
        require(
            "diameter_mm" in pad,
            'Every pad needs "size_mm" (or "diameter_mm" for a round pad).',
        )
        size = _size(pad["diameter_mm"], "diameter_mm")
    shape = _shape(pad.get("shape", "round"))
    radius = pad.get("corner_radius_percent", 50)
    require(
        isinstance(radius, int) and 0 <= radius <= 100,
        '"corner_radius_percent" must be 0..100.',
    )
    rotation = angle(pad.get("rotation", 0))
    if kind == "smd":
        require(
            "hole_mm" not in pad,
            'An SMD pad has no hole; use type "tht" or "npth".',
        )
        layer = L.layer_id(pad.get("layer", "Top"))
        require(layer in (1, 32), "An SMD pad sits on Top or Bottom.")
        hole = 0.0
        plated = bool(pad.get("plated", False))
    else:
        require("layer" not in pad, "Through-hole pads are always on Multi-Layer.")
        layer = 74
        hole = pad.get("hole_mm")
        require(
            isinstance(hole, (int, float)) and not isinstance(hole, bool) and hole > 0,
            'A through-hole pad needs a positive "hole_mm".',
        )
        hole = float(hole)
        plated = bool(pad.get("plated", kind == "tht"))
        require(
            plated == (kind == "tht"),
            'A "tht" pad is plated and an "npth" pad is not; drop "plated" or change "type".',
        )
    hole_shape = HOLE_SHAPES.get(pad.get("hole_shape", "round"))
    require(hole_shape is not None, '"hole_shape" must be round, square or slot.')
    slot = float(pad.get("slot_length_mm", 0) or 0)
    if hole_shape == 2:
        require(
            slot > hole,
            'A slot needs "slot_length_mm" longer than the hole width "hole_mm".',
        )
    else:
        require(
            "slot_length_mm" not in pad,
            '"slot_length_mm" applies to hole_shape "slot".',
        )
        slot = hole
    slot_rotation = angle(pad.get("slot_rotation", 0), "slot_rotation")
    if kind != "smd":
        require(min(size) > hole, "A through-hole pad must be wider than its hole.")
    paste_mode, paste = _expansion(
        pad.get("paste_mask_expansion_mm", "rule"), "paste_mask_expansion_mm"
    )
    solder_mode, solder = _expansion(
        pad.get("solder_mask_expansion_mm", 0.075), "solder_mask_expansion_mm"
    )
    stack = pad.get("stack") or {}
    require(
        isinstance(stack, dict) and not set(stack) - STACK_KEYS,
        '"stack" takes "middle" and "bottom" only.',
    )
    layers = {"top": (size, shape)}
    for side in ("middle", "bottom"):
        entry = stack.get(side) or {}
        require(
            isinstance(entry, dict) and not set(entry) - {"size_mm", "shape"},
            f'"stack.{side}" takes "size_mm" and "shape".',
        )
        layers[side] = (
            _size(entry["size_mm"]) if "size_mm" in entry else size,
            _shape(entry["shape"]) if "shape" in entry else shape,
        )
    mode = 0 if all(layers[s] == layers["top"] for s in ("middle", "bottom")) else 1
    require(
        mode == 0 or kind != "smd",
        'A stacked pad needs a through-hole "type".',
    )
    return dict(
        number=number,
        at=[float(pad["at"][0]), float(pad["at"][1])],
        kind=kind,
        layer=layer,
        size=size,
        shape=shape,
        radius=radius,
        rotation=rotation,
        hole=hole,
        hole_shape=hole_shape,
        slot=slot,
        slot_rotation=slot_rotation,
        plated=plated,
        mode=mode,
        layers=layers,
        paste=(paste_mode, paste),
        solder=(solder_mode, solder),
        tenting=pad.get("tenting", "none"),
        keepout=bool(pad.get("keepout", False)),
    )


def pad_record(geometry, fp_name, pad_base):
    """Pad: designator + marker blocks, the 202-byte geometry block, and the size/shape block."""
    g = bytearray(pad_base)
    p = geometry
    g[:13] = common(p["layer"], p["tenting"], p["keepout"])
    struct.pack_into("<ii", g, 13, *map(coord, p["at"]))
    for offset, side in ((21, "top"), (29, "middle"), (37, "bottom")):
        (sx, sy), _ = p["layers"][side]
        struct.pack_into("<ii", g, offset, coord(sx), coord(sy))
    struct.pack_into("<i", g, 45, coord(p["hole"]))
    # Altium stores a rounded rectangle as a round base shape plus per-layer shape 9 and a radius.
    for offset, side in ((49, "top"), (50, "middle"), (51, "bottom")):
        shape = p["layers"][side][1]
        g[offset] = 1 if shape == 9 else shape
    struct.pack_into("<d", g, 52, p["rotation"])
    g[60] = 1 if p["plated"] else 0
    g[62] = p["mode"]
    struct.pack_into("<i", g, 86, coord(p["paste"][1]))
    struct.pack_into("<i", g, 90, coord(p["solder"][1]))
    g[101] = p["paste"][0]
    g[102] = p["solder"][0]
    struct.pack_into("<I", g, 114, L.v7(p["layer"]))
    struct.pack_into("<i", g, 121, coord(p["solder"][1]))
    g[125] = 0
    for offset, suffix in ((126, "A"), (142, "B")):
        g[offset : offset + 16] = uuid.uuid5(
            uuid.NAMESPACE_URL, f'{fp_name}/{p["number"]}/{suffix}'
        ).bytes_le
    extended = b""
    if p["shape"] == 9 or p["hole_shape"] != 0 or p["slot"] != p["hole"]:
        extended = pad_size_shape_block(p)
    num = p["number"].encode("cp1252")
    return [
        bytes([len(num)]) + num,
        b"\0",
        b"\x04|&|0",
        b"\0",
        bytes(g),
        extended,
    ]


def pad_size_shape_block(p):
    """The 651-byte size/shape block: internal layers, hole shape, per-layer shapes and radii."""
    (mx, my), mshape = p["layers"]["middle"]
    b = bytearray(651)
    for i in range(29):
        struct.pack_into("<i", b, 4 * i, coord(mx))
        struct.pack_into("<i", b, 116 + 4 * i, coord(my))
        b[232 + i] = 1 if mshape == 9 else mshape
    b[262] = p["hole_shape"]
    struct.pack_into("<i", b, 263, coord(p["slot"]))
    struct.pack_into("<d", b, 267, p["slot_rotation"])
    b[531] = 1 if p["shape"] == 9 else 0
    for i in range(32):
        b[532 + i] = 9 if p["shape"] == 9 else 1
        b[564 + i] = p["radius"] if p["shape"] == 9 else 0
    # Full-stack tail: one canonical opening entry, as Altium writes for every pad with this block.
    struct.pack_into("<ii", b, 628, 1, 15)
    b[636:641] = bytes([4, 0, 0x80, 1, p["shape"]])
    struct.pack_into("<ii", b, 641, coord(p["size"][0]), coord(p["size"][1]))
    b[649] = 50
    b[650] = 0
    return bytes(b)


# --- graphics -----------------------------------------------------------------------------------


def _points(value, field, minimum=2):
    require(
        isinstance(value, list) and len(value) >= minimum,
        f'"{field}" needs at least {minimum} [x, y] points.',
    )
    out = []
    for point in value:
        require(
            isinstance(point, list) and len(point) == 2,
            f'"{field}" points are [x_mm, y_mm].',
        )
        out.append((float(point[0]), float(point[1])))
    return out


def _width(entry, name):
    width = entry.get("width_mm")
    require(
        isinstance(width, (int, float)) and not isinstance(width, bool) and width > 0,
        f'Every {name} needs a positive "width_mm".',
    )
    return float(width)


def _graphic_common(entry, keys, name):
    unknown = set(entry) - set(keys) - {"layer", "keepout", "locked", "tenting"}
    require(
        not unknown,
        f'Unsupported {name} field(s): {", ".join(sorted(unknown))}.',
    )
    require("layer" in entry, f'Every {name} needs a "layer".')
    lid = L.layer_id(entry["layer"])
    require(
        lid != 74,
        "Multi-Layer carries pads only; put graphics on copper, an overlay or a mechanical layer.",
    )
    return lid, common(
        lid,
        entry.get("tenting", "none"),
        bool(entry.get("keepout", False)),
        bool(entry.get("locked", False)),
    )


def track_record(entry):
    lid, head = _graphic_common(entry, ("start", "end", "width_mm"), "track")
    require("start" in entry and "end" in entry, 'A track needs "start" and "end".')
    start = _points([entry["start"], entry["end"]], "track")
    width = _width(entry, "track")
    require(start[0] != start[1], "A track needs distinct endpoints.")
    g = bytearray(49)
    g[:13] = head
    struct.pack_into("<iiiii", g, 13, *map(coord, (*start[0], *start[1], width)))
    struct.pack_into("<I", g, 41, L.v7(lid))
    return lid, [bytes(g)]


def arc_record(entry):
    lid, head = _graphic_common(
        entry,
        ("center", "at", "radius_mm", "start_angle", "end_angle", "width_mm"),
        "arc",
    )
    require("center" in entry or "at" in entry, 'An arc needs a "center".')
    center = _points([entry.get("center") or entry["at"]], "arc", 1)[0]
    width = _width(entry, "arc")
    radius = entry.get("radius_mm")
    require(
        isinstance(radius, (int, float))
        and not isinstance(radius, bool)
        and radius > 0,
        'An arc needs a positive "radius_mm".',
    )
    start = float(entry.get("start_angle", 0))
    end = float(entry.get("end_angle", 360))
    require(
        math.isfinite(start) and math.isfinite(end) and start != end,
        "An arc needs distinct start and end angles.",
    )
    g = bytearray(60)
    g[:13] = head
    struct.pack_into("<iii", g, 13, *map(coord, (*center, radius)))
    struct.pack_into("<dd", g, 25, start, end)
    struct.pack_into("<i", g, 41, coord(width))
    struct.pack_into("<I", g, 52, L.v7(lid))
    return lid, [bytes(g)]


def fill_record(entry):
    lid, head = _graphic_common(entry, ("corner1", "corner2", "rotation"), "fill")
    require(
        "corner1" in entry and "corner2" in entry,
        'A fill needs "corner1" and "corner2".',
    )
    (x0, y0), (x1, y1) = _points([entry["corner1"], entry["corner2"]], "fill")
    require(x0 != x1 and y0 != y1, "A fill needs two distinct corners.")
    g = bytearray(50)
    g[:13] = head
    struct.pack_into("<iiii", g, 13, *map(coord, (x0, y0, x1, y1)))
    struct.pack_into("<d", g, 29, angle(entry.get("rotation", 0)))
    struct.pack_into("<I", g, 42, L.v7(lid))
    return lid, [bytes(g)]


def region_record(entry):
    lid, head = _graphic_common(
        entry, ("outline", "holes", "kind", "name", "board_cutout"), "region"
    )
    require("outline" in entry, 'A region needs an "outline".')
    outline = _points(entry["outline"], "outline", 3)
    holes = [_points(h, "holes", 3) for h in entry.get("holes", [])]
    kind = entry.get("kind", 0)
    require(isinstance(kind, int), 'A region "kind" is an integer (0 = copper).')
    params = [("V7_LAYER", L.v7_name(lid)), ("NAME", entry.get("name", ""))]
    if entry.get("keepout"):
        params.append(("KEEPOUT", "TRUE"))
    if entry.get("board_cutout"):
        params.append(("ISBOARDCUTOUT", "TRUE"))
    params += [
        ("KIND", str(kind)),
        ("SUBPOLYINDEX", "-1"),
        ("UNIONINDEX", "0"),
        ("ARCRESOLUTION", "0.5mil"),
        ("ISSHAPEBASED", "FALSE"),
        ("CAVITYHEIGHT", "0mil"),
    ]
    body = bytearray(head)
    body += bytes([0]) + struct.pack("<H", len(holes)) + b"\0\0"
    body += cstring_block("|".join(f"{k}={v}" for k, v in params))
    for contour in [outline] + holes:
        body += U32(len(contour)) + b"".join(
            struct.pack("<dd", float(coord(x)), float(coord(y))) for x, y in contour
        )
    return lid, [bytes(body)]


BODY_KEYS = (
    "outline",
    "standoff_mm",
    "height_mm",
    "color",
    "opacity",
    "name",
    "identifier",
)


def body_record(entry, fp_name, index):
    lid, head = _graphic_common(entry, BODY_KEYS, "3D body")
    require(L.mech_number(lid), "A 3D body sits on a mechanical layer.")
    require(
        "outline" in entry and "height_mm" in entry,
        'A 3D body needs an "outline" and a "height_mm".',
    )
    outline = _points(entry["outline"], "outline", 3)
    standoff = float(entry.get("standoff_mm", 0))
    height = float(entry["height_mm"])
    require(
        height > 0 and standoff >= 0,
        'A 3D body needs a positive "height_mm" and a non-negative "standoff_mm".',
    )
    colour = entry.get("color", 0x808080)
    if isinstance(colour, str):
        require(
            re.fullmatch(r"#?[0-9a-fA-F]{6}", colour),
            '"color" is #RRGGBB or an integer.',
        )
        rgb = int(colour.lstrip("#"), 16)
        colour = (
            ((rgb & 0xFF) << 16) | (rgb & 0xFF00) | (rgb >> 16)
        )  # Altium stores BGR
    require(
        isinstance(colour, int) and 0 <= colour <= 0xFFFFFF,
        '"color" is #RRGGBB or an integer.',
    )
    opacity = float(entry.get("opacity", 1.0))
    require(0 < opacity <= 1, '"opacity" must be in (0, 1].')
    model = uuid.uuid5(uuid.NAMESPACE_URL, f"{fp_name}/body/{index}")
    params = [
        ("V7_LAYER", L.v7_name(lid)),
        ("NAME", entry.get("name", "")),
        ("KIND", "0"),
        ("SUBPOLYINDEX", "-1"),
        ("UNIONINDEX", "0"),
        ("ARCRESOLUTION", "0.5mil"),
        ("ISSHAPEBASED", "FALSE"),
        ("CAVITYHEIGHT", "0mil"),
        ("STANDOFFHEIGHT", mil(standoff)),
        ("OVERALLHEIGHT", mil(standoff + height)),
        ("BODYPROJECTION", "0"),
        ("ARCRESOLUTION", "0.5mil"),
        ("BODYCOLOR3D", str(colour)),
        ("BODYOPACITY3D", f"{opacity:.3f}"),
        (
            "IDENTIFIER",
            (
                ",".join(str(ord(c)) for c in entry.get("identifier", ""))
                if entry.get("identifier")
                else ""
            ),
        ),
        ("TEXTURE", ""),
        ("TEXTURECENTERX", "0mil"),
        ("TEXTURECENTERY", "0mil"),
        ("TEXTURESIZEX", "0mil"),
        ("TEXTURESIZEY", "0mil"),
        ("TEXTUREROTATION", " 0.00000000000000E+0000"),
        ("MODELID", "{" + str(model).upper() + "}"),
        ("MODEL.CHECKSUM", "0"),
        ("MODEL.EMBED", "FALSE"),
        ("MODEL.NAME", ""),
        ("MODEL.2D.X", "0mil"),
        ("MODEL.2D.Y", "0mil"),
        ("MODEL.2D.ROTATION", "0.000"),
        ("MODEL.3D.ROTX", "0.000"),
        ("MODEL.3D.ROTY", "0.000"),
        ("MODEL.3D.ROTZ", "0.000"),
        ("MODEL.3D.DZ", "0mil"),
        ("MODEL.MODELTYPE", "0"),
        ("MODEL.EXTRUDED.MINZ", mil(standoff)),
        ("MODEL.EXTRUDED.MAXZ", mil(standoff + height)),
    ]
    body = bytearray(head)
    body += U32(0) + bytes([0])
    body += cstring_block("|".join(f"{k}={v}" for k, v in params))
    body += U32(len(outline)) + b"".join(
        struct.pack("<dd", float(coord(x)), float(coord(y))) for x, y in outline
    )
    return lid, [bytes(body)]


# --- text ---------------------------------------------------------------------------------------

STROKE_FONTS = {"default": 0, "sans_serif": 1, "serif": 2}
ANCHORS = {"left": 0.0, "center": 0.5, "right": 1.0}
VERTICAL = {"bottom": 0.0, "middle": 0.5, "top": 1.0}
TEXT_KEYS = (
    "text",
    "at",
    "height_mm",
    "stroke_mm",
    "rotation",
    "mirror",
    "anchor",
    "font",
    "font_name",
    "bold",
    "italic",
    "stroke_font",
)


def text_extent(text, height, truetype, bold=False):
    """Advance width and cap height of a string in mm, from the metrics Altium renders with.

    TrueType text uses the Arial advance table; Altium's stroke font advances a constant 0.6 em
    per character. Both only position an `anchor` — the record itself stores a lower-left corner.
    """
    if truetype:
        face = "arial_bold" if bold else "arial"
        table = metrics.WIDTHS[face]
        fallback = metrics.FALLBACK[face]
        per_mille = sum(
            (
                table[ord(c) - metrics.FIRST]
                if 0 <= ord(c) - metrics.FIRST < len(table)
                else fallback
            )
            for c in text
        )
        return per_mille / 1000.0 * height, height
    return len(text) * 0.6 * height, height


def text_placement(entry):
    """Validate a text entry and resolve its anchor into the lower-left corner Altium stores."""
    lid, head = _graphic_common(entry, TEXT_KEYS, "text")
    require(
        "text" in entry and "at" in entry and "height_mm" in entry,
        'Text needs "text", "at" and "height_mm".',
    )
    text = entry["text"]
    require(isinstance(text, str) and text != "", 'Text needs a non-empty "text".')
    require(
        text.isprintable() and len(text.encode("cp1252")) <= 255,
        "Text must be one printable line of at most 255 bytes.",
    )
    height = entry["height_mm"]
    stroke = entry.get("stroke_mm", 0.15)
    require(
        isinstance(height, (int, float)) and height > 0,
        '"height_mm" must be positive.',
    )
    require(
        isinstance(stroke, (int, float)) and stroke > 0,
        '"stroke_mm" must be positive.',
    )
    font = entry.get("font", "stroke")
    require(font in ("stroke", "truetype"), '"font" must be stroke or truetype.')
    stroke_font = STROKE_FONTS.get(entry.get("stroke_font", "sans_serif"))
    require(
        stroke_font is not None,
        '"stroke_font" must be default, sans_serif or serif.',
    )
    rotation = angle(entry.get("rotation", 0))
    at = _points([entry["at"]], "at", 1)[0]
    anchor = str(entry.get("anchor", "bottom-left"))
    parts = anchor.split("-")
    require(
        len(parts) == 2 and parts[0] in VERTICAL and parts[1] in ANCHORS,
        '"anchor" is <bottom|middle|top>-<left|center|right>.',
    )
    bold = bool(entry.get("bold"))
    truetype = font == "truetype"
    width, cap = text_extent(text, height, truetype, bold)
    dx = -ANCHORS[parts[1]] * width
    dy = -VERTICAL[parts[0]] * cap
    theta = math.radians(rotation)
    return (
        lid,
        head,
        dict(
            text=text,
            at=[
                at[0] + dx * math.cos(theta) - dy * math.sin(theta),
                at[1] + dx * math.sin(theta) + dy * math.cos(theta),
            ],
            anchor_at=list(at),
            anchor=anchor,
            width=width,
            height=float(height),
            stroke=float(stroke),
            rotation=rotation,
            mirror=bool(entry.get("mirror")),
            truetype=truetype,
            bold=bold,
            italic=bool(entry.get("italic")),
            font_name=entry.get("font_name", "Arial") if truetype else "Arial",
            stroke_font=stroke_font,
            layer=lid,
        ),
    )


def text_record(entry, text_base, index):
    lid, head, p = text_placement(entry)
    g = bytearray(text_base)
    g[:13] = head
    struct.pack_into(
        "<iii", g, 13, coord(p["at"][0]), coord(p["at"][1]), coord(p["height"])
    )
    struct.pack_into("<H", g, 25, p["stroke_font"])
    struct.pack_into("<d", g, 27, p["rotation"])
    g[35] = 1 if p["mirror"] else 0
    struct.pack_into("<i", g, 36, coord(p["stroke"]))
    g[40] = int(p["text"] == ".Comment")
    g[41] = int(p["text"] == ".Designator")
    g[43] = 1 if p["truetype"] else 0
    g[44] = 1 if p["bold"] else 0
    g[45] = 1 if p["italic"] else 0
    g[46:110] = p["font_name"].encode("utf-16le")[:62].ljust(64, bytes(1))
    struct.pack_into("<i", g, 115, index)
    g[160] = 1 if p["truetype"] else 0
    struct.pack_into("<I", g, 226, L.v7(lid))
    encoded = p["text"].encode("cp1252")
    return lid, [bytes(g), bytes([len(encoded)]) + encoded], p


# --- footprints ---------------------------------------------------------------------------------

FOOTPRINT_KEYS = {
    "name",
    "description",
    "height_mm",
    "expected_pin_numbers",
    "expected_pin_count",
    "pads",
    "pad_arrays",
    "tracks",
    "arcs",
    "fills",
    "regions",
    "texts",
    "bodies",
    "outlines",
    "layer_profile",
    "notes",
}


def prepare(spec):
    """Expand generators and validate one footprint specification."""
    unknown = set(spec) - FOOTPRINT_KEYS
    require(
        not unknown,
        f'Unsupported footprint field(s): {", ".join(sorted(unknown))}.',
    )
    name = spec.get("name")
    require(
        isinstance(name, str)
        and 0 < len(name.encode("utf-16le")) <= 62
        and not any(s in name for s in "/\\!:"),
        "Use a simple footprint name <=31 UTF-16 units.",
    )
    require(
        name.upper() not in ("FILEHEADER", "LIBRARY", "SECTIONKEYS"),
        "Reserved footprint name.",
    )
    pads = list(spec.get("pads", [])) + pads_from_arrays(spec.get("pad_arrays", []))
    require(bool(pads), "A footprint needs at least one pad.")
    geometry = [pad_geometry(p) for p in pads]
    numbers = [g["number"] for g in geometry]
    require(len(numbers) == len(set(numbers)), "Duplicate pad numbers.")
    if "expected_pin_numbers" in spec:
        require(
            "expected_pin_count" not in spec,
            "Give expected_pin_numbers or expected_pin_count, not both.",
        )
        expected = [str(n) for n in spec["expected_pin_numbers"]]
        require(
            len(expected) == len(set(expected)),
            "Duplicate entries in expected_pin_numbers.",
        )
        require(
            set(expected) == set(numbers),
            f"Pads do not match expected_pin_numbers: missing {sorted(set(expected)-set(numbers))}, "
            f"unexpected {sorted(set(numbers)-set(expected))}.",
        )
    else:
        count = spec.get("expected_pin_count")
        require(
            isinstance(count, int) and count > 0,
            "A footprint needs expected_pin_numbers or expected_pin_count.",
        )
        require(
            count == len(numbers),
            f"This footprint places {len(numbers)} pads, not {count}.",
        )
    seen = set()
    for g in geometry:
        key = (g["layer"], round(g["at"][0], 6), round(g["at"][1], 6))
        require(
            key not in seen,
            f'Pad {g["number"]} sits on top of another pad on the same layer.',
        )
        seen.add(key)
    tracks, arcs = graphics_from_outlines(spec.get("outlines", []))
    height = spec.get("height_mm", 0)
    require(
        isinstance(height, (int, float)) and math.isfinite(height) and height >= 0,
        "Height must be finite and non-negative.",
    )
    return dict(
        name=name,
        description=spec.get("description", ""),
        height=height,
        pads=geometry,
        tracks=list(spec.get("tracks", [])) + tracks,
        arcs=list(spec.get("arcs", [])) + arcs,
        fills=list(spec.get("fills", [])),
        regions=list(spec.get("regions", [])),
        texts=list(spec.get("texts", [])),
        bodies=list(spec.get("bodies", [])),
    )


def footprint_records(fp, pad_base, text_base):
    """Build every primitive of one footprint, in Altium's own record order."""
    out = []
    used = set()
    texts = []

    def add(t, blocks, lid=None):
        out.append((t, bytes([t]) + b"".join(block(b) for b in blocks)))
        n = L.mech_number(lid) if lid else None
        if n:
            used.add(n)

    for entry in fp["arcs"]:
        lid, blocks = arc_record(entry)
        add(1, blocks, lid)
    for pad in fp["pads"]:
        add(2, pad_record(pad, fp["name"], pad_base))
    for entry in fp["tracks"]:
        lid, blocks = track_record(entry)
        add(4, blocks, lid)
    for index, entry in enumerate(fp["texts"]):
        lid, blocks, placed = text_record(entry, text_base, index)
        add(5, blocks, lid)
        texts.append(placed)
    for entry in fp["fills"]:
        lid, blocks = fill_record(entry)
        add(6, blocks, lid)
    for entry in fp["regions"]:
        lid, blocks = region_record(entry)
        add(11, blocks, lid)
    # 3D bodies go last: they are the one record whose block count readers disagree on, and a
    # trailing position keeps every other primitive parseable.
    for index, entry in enumerate(fp["bodies"]):
        lid, blocks = body_record(entry, fp["name"], index)
        add(12, blocks, lid)
    return out, used, texts


def footprint_streams(fp, records, texts):
    name = fp["name"]
    encoded = {
        f"ENCODEDTEXT{i}": ",".join(str(ord(c)) for c in t["text"])
        for i, t in enumerate(texts)
    }
    return {
        (name, "Header"): U32(len(records)),
        (name, "Parameters"): record(
            {
                "PATTERN": name,
                "HEIGHT": mil(fp["height"]),
                "DESCRIPTION": fp["description"],
                "ITEMGUID": "",
                "REVISIONGUID": "",
            }
        ),
        (name, "Data"): string(name) + b"".join(b for _, b in records),
        (name, "WideStrings"): record(encoded) if encoded else block(b"\0"),
        (name, "UniqueIDPrimitiveInformation", "Header"): U32(len(records)),
        (name, "UniqueIDPrimitiveInformation", "Data"): b"".join(
            record(
                {
                    "PRIMITIVEINDEX": i + 1,
                    "PRIMITIVEOBJECTID": NAMES[t],
                    "UNIQUEID": uid(f"{name}/{i}"),
                }
            )
            for i, (t, _) in enumerate(records)
        ),
    }


def load_template(template):
    template = (
        Path(template) if template else SKILL_ROOT / "assets/native-template.PcbLib"
    )
    require(template.is_file(), f"Missing native PcbLib template: {template}")
    with open_container(template) as ole:
        params, tail = decode_params(ole.read("Library/Data"))
        require(
            struct.unpack_from("<I", tail)[0] == 1,
            "Simple template must have one footprint.",
        )
        n = tail[8]
        template_name = tail[9 : 9 + n].decode("cp1252")
        _, reference = parse_records(ole.read([template_name, "Data"]))
        pad_base = next(bs[4] for t, bs in reference if t == 2)
        text_base = next(bs[0] for t, bs in reference if t == 5)
        header = ole.read("FileHeader")
    require(
        len(pad_base) == 202 and len(text_base) == 252,
        "Unexpected native template record layout.",
    )
    return params, pad_base, text_base, header


def footprints_of(spec):
    """A specification is either one footprint or a `footprints` list sharing a layer profile."""
    if "footprints" in spec:
        require(
            set(spec) <= {"footprints", "layer_profile", "name", "description"},
            'A multi-footprint library takes "footprints" and "layer_profile" only.',
        )
        entries = spec["footprints"]
        require(
            isinstance(entries, list) and entries,
            '"footprints" must be a non-empty list.',
        )
        return entries
    return [spec]


def write(spec, profile, output, template=None):
    prepared = [prepare(fp) for fp in footprints_of(spec)]
    names = [fp["name"] for fp in prepared]
    require(len(names) == len(set(names)), "Two footprints share a name.")
    params, pad_base, text_base, header = load_template(template)
    streams = {}
    used = set()
    summary = []
    for fp in prepared:
        records, fp_used, texts = footprint_records(fp, pad_base, text_base)
        used |= fp_used
        streams.update(footprint_streams(fp, records, texts))
        summary.append(
            dict(
                name=fp["name"],
                pads=len(fp["pads"]),
                primitives=len(records),
                mechanical_layers=sorted(fp_used),
            )
        )
    params, mapping = layer_profile(params, profile, used)
    params["FILENAME"] = Path(output).name
    for k in params:
        if "CONFIGFULLFILENAME" in k:
            params[k] = "(Not Saved)"
    streams.update(
        {
            ("FileHeader",): header,
            ("Library", "Header"): U32(1),
            ("Library", "Data"): record(params)
            + U32(len(names))
            + b"".join(string(n) for n in names),
            ("Library", "EmbeddedFonts"): U32(0),
            ("Library", "LayerKindMapping", "Header"): U32(1),
            ("Library", "LayerKindMapping", "Data"): mapping,
        }
    )
    for storage in ("Models", "ModelsNoEmbed", "Textures"):
        streams[("Library", storage, "Header")] = U32(0)
        streams[("Library", storage, "Data")] = b""
    streams[("Library", "PadViaLibrary", "Header")] = U32(1)
    streams[("Library", "PadViaLibrary", "Data")] = record(
        {
            "PADVIALIBRARY.LIBRARYID": "{"
            + str(uuid.uuid5(uuid.NAMESPACE_URL, names[0])).upper()
            + "}",
            "PADVIALIBRARY.LIBRARYNAME": "<Local>",
            "PADVIALIBRARY.DISPLAYUNITS": 1,
        }
    )
    output = Path(output)
    require(output.suffix.lower() == ".pcblib", "Output must use .PcbLib.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(compound_file(streams))
    return summary


def summary(entries):
    lines = []
    for fp in entries:
        layers = ", ".join(f"M{n}" for n in fp["mechanical_layers"]) or "none"
        lines.append(
            f'{fp["name"]}: {fp["pads"]} pads, {fp["primitives"]} primitives, mechanical layers {layers}'
        )
    return "\n".join(lines)
