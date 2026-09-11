"""Altium PCB layer identity: names, primitive layer bytes, V7 ids and mechanical roles.

A *toolkit layer id* is Altium's own layer byte for everything up to Multi-Layer (74). Mechanical
17..32 have no byte of their own — Altium stores 72 (Mechanical 16) in the primitive header and
disambiguates with the V7 id — so they take the reader ids 83..98 (66 + n) that pyaltiumlib
reports, and the writer converts them back on the way out.
"""

import re
from .common import require

# Layer byte -> canonical name, for everything that is not a numbered layer family.
SPECIAL = {
    1: "Top",
    32: "Bottom",
    33: "TopOverlay",
    34: "BottomOverlay",
    35: "TopPaste",
    36: "BottomPaste",
    37: "TopSolder",
    38: "BottomSolder",
    55: "DrillGuide",
    56: "KeepOut",
    73: "DrillDrawing",
    74: "MultiLayer",
}
# Accepted spellings, normalised by NORM below.
ALIASES = {
    "toplayer": 1,
    "bottomlayer": 32,
    "topsilkscreen": 33,
    "bottomsilkscreen": 34,
    "silkscreen": 33,
    "topsoldermask": 37,
    "bottomsoldermask": 38,
    "toppastemask": 35,
    "bottompastemask": 36,
    "keepoutlayer": 56,
    "multi": 74,
    "multilayer": 74,
}


def NORM(s):
    return re.sub(r"[\s_\-]", "", str(s)).lower()


def mech_id(n):
    """Toolkit layer id of Mechanical n."""
    require(
        isinstance(n, int) and 1 <= n <= 32,
        "Mechanical layers are numbered 1..32.",
    )
    return 56 + n if n <= 16 else 66 + n


def mech_number(lid):
    """Mechanical number of a toolkit layer id, or None."""
    if 57 <= lid <= 72:
        return lid - 56
    if 83 <= lid <= 98:
        return lid - 66
    return None


def layer_id(value):
    """Toolkit layer id from a JSON layer field: a name, `M15`, or a raw layer number."""
    if isinstance(value, bool):
        require(False, "Layer must be a name or a number.")
    if isinstance(value, int):
        require(
            1 <= value <= 98 and value not in range(75, 83),
            f"Unknown layer id {value}.",
        )
        return value
    require(
        isinstance(value, str) and value.strip() != "",
        "Layer must be a name or a number.",
    )
    key = NORM(value)
    if key in ALIASES:
        return ALIASES[key]
    for lid, name in SPECIAL.items():
        if key == NORM(name):
            return lid
    m = re.fullmatch(r"(?:m|mech|mechanical)(\d{1,2})", key)
    if m:
        return mech_id(int(m.group(1)))
    m = re.fullmatch(r"(?:mid|midlayer)(\d{1,2})", key)
    if m and 1 <= int(m.group(1)) <= 30:
        return 1 + int(m.group(1))
    m = re.fullmatch(r"(?:internalplane|plane)(\d{1,2})", key)
    if m and 1 <= int(m.group(1)) <= 16:
        return 38 + int(m.group(1))
    require(
        False,
        f"Unknown layer name {value!r}. Use Top, Bottom, TopOverlay, TopPaste, "
        "TopSolder, KeepOut, DrillDrawing, MultiLayer, M1..M32, MidLayer1..30 or InternalPlane1..16.",
    )


def layer_name(lid):
    """Canonical name of a toolkit layer id."""
    if lid in SPECIAL:
        return SPECIAL[lid]
    n = mech_number(lid)
    if n:
        return f"M{n}"
    if 2 <= lid <= 31:
        return f"MidLayer{lid-1}"
    if 39 <= lid <= 54:
        return f"InternalPlane{lid-38}"
    require(False, f"Unknown layer id {lid}.")


def layer_byte(lid):
    """Byte written into a primitive's 13-byte common header."""
    n = mech_number(lid)
    return 72 if n and n > 16 else lid


def v7(lid):
    """The redundant `V7 layer id` every primitive repeats in its tail."""
    n = mech_number(lid)
    if n:
        return 0x01020000 + n
    if lid == 32:
        return 0x0100FFFF
    if 1 <= lid <= 31:
        return 0x01000000 + lid
    if 39 <= lid <= 54:
        return 0x01010000 + lid - 38
    return {
        33: 0x01030006,
        34: 0x01030007,
        35: 0x01030008,
        36: 0x01030009,
        37: 0x0103000A,
        38: 0x0103000B,
        55: 0x0103000C,
        56: 0x0103000D,
        73: 0x0103000E,
        74: 0x0103000F,
    }[lid]


def v7_name(lid):
    """The `V7_LAYER=` spelling regions and 3D bodies carry in their parameter text."""
    n = mech_number(lid)
    if n:
        return f"MECHANICAL{n}"
    if 2 <= lid <= 31:
        return f"MID{lid-1}"
    if 39 <= lid <= 54:
        return f"INTERNALPLANE{lid-38}"
    return {
        1: "TOP",
        32: "BOTTOM",
        33: "TOPOVERLAY",
        34: "BOTTOMOVERLAY",
        35: "TOPPASTE",
        36: "BOTTOMPASTE",
        37: "TOPSOLDER",
        38: "BOTTOMSOLDER",
        55: "DRILLGUIDE",
        56: "KEEPOUT",
        73: "DRILLDRAWING",
        74: "MULTILAYER",
    }[lid]


# Semantic mechanical-layer kinds Altium stores in Library/LayerKindMapping and the MECHKIND fields.
KINDS = {
    "Undefined": 0,
    "AssemblyNotes": 3,
    "BoardOutline": 4,
    "VCutScoring": 0x19,
    "RoutingToolPaths": 0x1C,
}
# Component layer pairs: role -> (top kind, bottom kind).
ROLES = {
    "Assembly": (1, 2),
    "ConformalCoating": (5, 6),
    "ComponentCenter": (7, 8),
    "ComponentOutline": (9, 10),
    "Courtyard": (11, 12),
    "Designator": (13, 14),
    "Glue": (0x13, 0x14),
    "ComponentValue": (0x17, 0x18),
    "3DBody": (0x1A, 0x1B),
}
