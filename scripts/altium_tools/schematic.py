"""Author SchLibs from package-verified JSON pin tables.

Coordinates use 10 mil units, Y up. A pin's stored location is its body end;
orientation 2 points out to the left, 0 out to the right. Pins are placed on
the left and right sides only -- there is no top or bottom side.
"""

import copy
import math
import re
import struct
from pathlib import Path
from .common import record, ps, uid, compound_file, require
from . import metrics

ELECTRICAL = {
    "input": 0,
    "bidirectional": 1,
    "output": 2,
    "open_collector": 3,
    "passive": 4,
    "tri_state": 5,
    "open_emitter": 6,
    "power": 7,
}

SIDES = ("left", "right")
# Rejected outright: pins never go on the top or bottom of a symbol.
NOT_A_SIDE = (
    "top",
    "bottom",
    "up",
    "down",
    "upper",
    "lower",
    "north",
    "south",
    "above",
    "below",
)
# Rejected on pins: side and angle are decided by the side the pin is listed under.
NOT_A_PIN_FIELD = (
    "side",
    "orientation",
    "rotation",
    "rotated",
    "angle",
    "x",
    "y",
)

# Vertical rhythm, 10 mil units. Every step here is a whole pin pitch and the
# first row sits on one, so every pin lands on the 100 mil schematic grid.
TOP_ROW_TITLED = 30  # body top to the first content row, unit with a title
TOP_ROW_PLAIN = 20  # body top to the first content row, unit without one
HEADING_ROW = 10  # a group heading sits one pitch above the group it labels
PIN_PITCH = 10  # 100 mil, Altium's standard pin spacing
GROUP_GAP = 20  # blank space between two groups on one side
BOTTOM_PAD = 20  # last pin down to the body bottom
NOTE_ROW = 20  # extra bottom space when a unit has a note
PIN_LENGTH = 20  # 200 mil wire stub, lengthened when a pin number needs the room
MAX_PIN_LENGTH = 200

# Horizontal fitting, 10 mil units.
NAME_INSET = 4  # body edge to the start of a pin name
HEADING_INSET = 5  # body edge to the start of a group heading
COLUMN_GAP = 10  # clear space kept between the left and right text columns
CENTER_MARGIN = 10  # clear space kept either side of a centred title or note
MIN_WIDTH = 30
MAX_WIDTH = 30000


def altium_name(name):
    return re.sub(r"([A-Za-z0-9_]+)#", lambda m: "".join(c + "\\" for c in m[1]), name)


def displayed(wire_name):
    """The characters Altium draws: overbar marks add height, not width."""
    return wire_name.replace("\\", "")


def expand_numbers(value):
    """Expand `"1-68"` style ranges inside expected_pin_numbers.

    A range expands only when both ends carry the same alphabetic prefix, so
    `A1-A12` expands and a literal designator such as `P1-2` is left alone.
    """
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        text = str(item)
        m = re.fullmatch(r"\s*([A-Za-z]*)(\d+)\s*-\s*([A-Za-z]*)(\d+)\s*", text)
        if (
            m
            and m[1].upper() == m[3].upper()
            and int(m[2]) <= int(m[4])
            and int(m[4]) - int(m[2]) <= 4096
        ):
            out.extend(f"{m[1]}{n}" for n in range(int(m[2]), int(m[4]) + 1))
        else:
            out.append(text)
    return out


def as_groups(entries, where):
    """Normalise one side into `[{title, pins}]`.

    A side may be a list of groups, a bare list of pins, a mix of the two, or a
    single group object. Consecutive bare pins collect into one untitled group.
    """
    if entries is None:
        entries = []
    if isinstance(entries, dict):
        entries = [entries]
    require(isinstance(entries, list), f"{where} must be a list of pins or groups.")
    groups = []
    for entry in entries:
        require(isinstance(entry, dict), f"{where}: every entry must be an object.")
        if "pins" in entry:
            require(
                "number" not in entry,
                f'{where}: an entry has both "pins" and "number".',
            )
            groups.append(
                dict(
                    title=str(entry.get("title") or ""),
                    pins=list(entry["pins"]),
                    implicit=False,
                )
            )
        else:
            require(
                "number" in entry,
                f'{where}: an entry needs either "pins" (a group) or "number" (a pin).',
            )
            if not groups or not groups[-1]["implicit"]:
                groups.append(dict(title="", pins=[], implicit=True))
            groups[-1]["pins"].append(entry)
    for g in groups:
        require(bool(g["pins"]), f"{where}: a group must contain at least one pin.")
    return groups


def prepare_pin(p, where):
    require(isinstance(p, dict), f"{where}: a pin must be an object.")
    for key in p:
        require(
            key.lower() not in NOT_A_PIN_FIELD,
            f'{where}: "{key}" is not a pin field. Position and angle follow from the side a pin is listed under.',
        )
    for field in ("number", "name", "electrical"):
        require(
            field in p,
            f'{where}: every pin needs "number", "name" and "electrical"; "{field}" is missing.',
        )
    p["number"] = str(p["number"])
    require(
        bool(p["name"]) and bool(p["number"]),
        "Every pin needs a name and number.",
    )
    require(
        p["electrical"] in ELECTRICAL,
        f"{where}: unknown electrical type {p['electrical']!r}. "
        f"Use one of: {', '.join(sorted(ELECTRICAL))}.",
    )
    p["electrical"] = ELECTRICAL[p["electrical"]]
    p.setdefault("description", "")
    p["hide_name"] = bool(p.get("hide_name", False))
    p["wire_name"] = altium_name(p["name"])
    for s in (p["description"], p["wire_name"], p["number"]):
        require(
            len(s.encode("cp1252")) <= 255,
            "A pin field exceeds the binary short-string limit.",
        )
    return p


def measure(unit, part, component_name):
    """Lay out one unit vertically and measure how wide its text needs the body.

    Returns the rows, the fitted width, and the body bottom. Horizontal
    positions are assigned later by `place`, once the final width is known.
    """
    where = f"{component_name} part {chr(64+part)}"
    for key in unit:
        require(
            key.lower() not in NOT_A_SIDE,
            f'{where}: "{key}" is not a supported side. Pins go on "left" and "right" only.',
        )
    title = str(unit.get("title") or "")
    note = str(unit.get("note") or "")
    hide_all = bool(unit.get("hide_pin_names", False))
    rows = {}
    pins = []
    labels = []
    lowest = []
    top = -(TOP_ROW_TITLED if title else TOP_ROW_PLAIN)

    def claim(y, side, extent):
        row = rows.setdefault(y, {"left": 0.0, "right": 0.0})
        row[side] = max(row[side], extent)

    for side in SIDES:
        y = top
        groups = as_groups(unit.get(side), f"{where} {side}")
        for index, group in enumerate(groups):
            if index:
                y -= GROUP_GAP
            if group["title"]:
                labels.append(dict(text=group["title"], side=side, y=y, font=3))
                claim(y, side, HEADING_INSET + metrics.width(group["title"], 3))
                y -= HEADING_ROW
            for raw in group["pins"]:
                p = prepare_pin(raw, where)
                if hide_all:
                    p["hide_name"] = True
                p.update(
                    part=part,
                    side=side,
                    y=y,
                    orientation=2 if side == "left" else 0,
                )
                require(
                    -32768 <= y <= 32767,
                    "Unit is too tall for the supported binary pin coordinates.",
                )
                claim(
                    y,
                    side,
                    (
                        0.0
                        if p["hide_name"]
                        else NAME_INSET + metrics.width(displayed(p["wire_name"]), 1)
                    ),
                )
                pins.append(p)
                y -= PIN_PITCH
        if y != top:
            lowest.append(y + PIN_PITCH)
    require(
        bool(pins),
        f"{where}: a unit needs at least one pin. Empty units are not supported.",
    )

    needed = MIN_WIDTH
    for row in rows.values():
        needed = max(needed, row["left"] + COLUMN_GAP + row["right"])
    for text, font in ((title, 2), (note, 3)):
        if text:
            needed = max(needed, metrics.width(text, font) + 2 * CENTER_MARGIN)
    minimum = unit.get("min_width", 0)
    require(
        isinstance(minimum, int) and 0 <= minimum <= MAX_WIDTH,
        f"{where}: min_width must be an integer in 10 mil units.",
    )
    needed = max(needed, minimum)
    # Rounded to a whole pin pitch so both wire endpoints stay on the 100 mil grid.
    fitted = int(math.ceil(needed / PIN_PITCH) * PIN_PITCH)
    require(
        fitted <= MAX_WIDTH,
        f"{where}: fitted width {fitted} exceeds the supported maximum.",
    )

    # The pin number is drawn centred on the wire stub, so a long alphanumeric
    # designator lengthens every stub in the unit and keeps the edge straight.
    widest_number = max(metrics.width(p["number"], 1) for p in pins)
    fitted_length = max(PIN_LENGTH, int(math.ceil((widest_number + 2) / 10.0) * 10))
    length = unit.get("pin_length", "auto")
    if length in (None, "auto", "fit"):
        length = fitted_length
    else:
        require(
            isinstance(length, int)
            and PIN_LENGTH <= length <= MAX_PIN_LENGTH
            and length % 10 == 0,
            f'{where}: pin_length must be "auto" or a multiple of 10 from {PIN_LENGTH} to {MAX_PIN_LENGTH}.',
        )
        require(
            length >= fitted_length,
            f"{where}: pin_length {length} is too short for its pin numbers; it needs at least "
            f'{fitted_length}. Omit "pin_length" to fit the stubs automatically.',
        )

    bottom = min(lowest) - BOTTOM_PAD
    bottom = int(math.floor((bottom - (NOTE_ROW if note else 0)) / 10.0) * 10)
    return dict(
        part=part,
        title=title,
        note=note,
        pins=pins,
        labels=labels,
        fitted=fitted,
        bottom=bottom,
        length=length,
        where=where,
    )


def place(unit, plan, width):
    """Assign x coordinates once the unit's width is settled."""
    for p in plan["pins"]:
        p.update(x=0 if p["side"] == "left" else width, length=plan["length"])
    for label in plan["labels"]:
        left = label["side"] == "left"
        label.update(
            x=HEADING_INSET if left else width - HEADING_INSET,
            just=3 if left else 5,
        )
    unit.update(
        part=plan["part"],
        title=plan["title"],
        note=plan["note"],
        width=width,
        pins=plan["pins"],
        labels=plan["labels"],
        bottom=plan["bottom"],
        pin_length=plan["length"],
        auto_width=plan["auto"],
        height=-plan["bottom"],
    )


def prepare(document):
    components = copy.deepcopy(document["components"])
    require(bool(components), "At least one component is required.")
    names = set()
    for c in components:
        for field in ("name", "pin_count", "expected_pin_numbers"):
            require(field in c, f'Every component needs "{field}".')
        name = c["name"]
        require(
            isinstance(name, str)
            and 0 < len(name.encode("utf-16le")) <= 62
            and not any(s in name for s in "/\\!:"),
            "Use a simple component name of at most 31 UTF-16 units.",
        )
        require(name.upper() not in names, "Duplicate component name.")
        names.add(name.upper())
        require(
            name.upper() not in ("FILEHEADER", "STORAGE", "SECTIONKEYS"),
            "Reserved CFB stream name.",
        )
        prefix = str(c.get("designator_prefix", "U"))
        require(
            re.fullmatch(r"[A-Za-z]{1,4}", prefix),
            "designator_prefix must be 1 to 4 letters, for example U, J or RN.",
        )
        c["designator_prefix"] = prefix.upper()

        # A component may carry sides directly instead of a units array; that is
        # one unnamed part, the layout for a part with no functional split.
        inline = {k: c[k] for k in ("left", "right") if k in c}
        for key in c:
            require(
                key.lower() not in NOT_A_SIDE,
                f'{name}: "{key}" is not a supported side. Pins go on "left" and "right" only.',
            )
        if inline:
            require(
                "units" not in c,
                f'{name}: use either "units" or a top-level "left"/"right", not both.',
            )
            unit = dict(inline)
            for key in (
                "title",
                "note",
                "width",
                "min_width",
                "pin_length",
                "hide_pin_names",
            ):
                if key in c:
                    unit[key] = c[key]
            c["units"] = [unit]
        require(
            bool(c.get("units")) and len(c["units"]) <= 26,
            f"{name}: use 1 through 26 functional parts.",
        )

        expected = expand_numbers(c["expected_pin_numbers"])
        c["expected_pin_numbers"] = expected
        require(
            len(expected) == len(set(expected)) == c["pin_count"],
            f"{name}: expected pin numbers must be unique and match pin_count "
            f'({len(set(expected))} unique of {len(expected)} listed, pin_count {c["pin_count"]}).',
        )

        plans = [measure(u, part, name) for part, u in enumerate(c["units"], 1)]
        for u, plan in zip(c["units"], plans):
            declared = u.get("width", "auto")
            plan["auto"] = declared in (None, "auto", "fit")
            if plan["auto"]:
                plan["width"] = plan["fitted"]
            else:
                require(
                    isinstance(declared, int)
                    and MIN_WIDTH <= declared <= MAX_WIDTH
                    and declared % PIN_PITCH == 0,
                    f'{plan["where"]}: width must be "auto" or an integer multiple of 10 in 10 mil units, '
                    "so that the right-hand wire endpoints stay on the 100 mil grid.",
                )
                require(
                    declared >= plan["fitted"],
                    f'{plan["where"]}: width {declared} is too narrow for its text; it needs at least '
                    f'{plan["fitted"]}. Omit "width" to fit the body automatically.',
                )
                plan["width"] = declared
        if c.get("uniform_width"):
            widest = max(p["width"] for p in plans)
            for plan in plans:
                plan["width"] = widest
        for u, plan in zip(c["units"], plans):
            place(u, plan, plan["width"])

        all_pins = [p for u in c["units"] for p in u["pins"]]
        actual = [p["number"] for p in all_pins]
        require(
            len(actual) == len(set(actual)) == c["pin_count"],
            f'{name}: missing or duplicated physical pins ({len(actual)} laid out, pin_count {c["pin_count"]}).',
        )
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        require(
            not missing and not extra,
            f"{name}: physical pin set differs from expected_pin_numbers. Missing {missing}; unexpected {extra}.",
        )
        c["pins"] = all_pins
    return components


def summary(components):
    """One line per part, for the build log."""
    lines = []
    for c in components:
        lines.append(
            f"{c['name']}: {c['pin_count']} pins, {len(c['units'])} part(s), designator {c['designator_prefix']}?"
        )
        for u in c["units"]:
            sides = [s for s in SIDES if any(p["side"] == s for p in u["pins"])]
            lines.append(
                f"  {chr(64+u['part'])}  width {u['width']}{' (auto)' if u['auto_width'] else ' (declared)'}"
                f"  height {u['height']}  pin length {u['pin_length']}  pins {len(u['pins'])} on {'+'.join(sides)}"
                f"{'  '+u['title'] if u['title'] else ''}"
            )
    return "\n".join(lines)


def binary_pin(p):
    b = struct.pack("<iBhBBBBB", 2, 0, p["part"], 0, 0, 0, 0, 0) + ps(p["description"])
    flags = 0x10 if p["hide_name"] else 0x18
    b += struct.pack(
        "<BBBhhhI",
        1,
        p["electrical"],
        flags | p["orientation"],
        p["length"],
        p["x"],
        p["y"],
        128,
    )
    b += ps(p["wire_name"]) + ps(p["number"]) + ps("") + ps("|&|") + ps("")
    return struct.pack("<I", len(b) | 0x01000000) + b


def component_stream(c):
    rows = [
        record(
            dict(
                RECORD=1,
                LibReference=c["name"],
                ComponentDescription=c.get("description", ""),
                PartCount=len(c["units"]) + 1,
                DisplayModeCount=1,
                IndexInSheet=-1,
                OwnerPartId=-1,
                CurrentPartId=1,
                LibraryPath="*",
                SourceLibraryName="*",
                SheetPartFileName="*",
                TargetFileName="*",
                AreaColor=11599871,
                Color=128,
                PartIDLocked="T",
                AllPinCount=c["pin_count"],
                UniqueID=uid(c["name"]),
            )
        )
    ]
    idx = 0

    def add(kind, part, **props):
        nonlocal idx
        d = dict(RECORD=kind, IndexInSheet=idx, OwnerPartId=part)
        d.update(props)
        d["UniqueID"] = uid(f"{c['name']}-{idx}")
        rows.append(record(d))
        idx += 1

    for u in c["units"]:
        add(
            14,
            u["part"],
            IsNotAccesible="T",
            **{
                "Location.X": 0,
                "Location.Y": u["bottom"],
                "Corner.X": u["width"],
                "Corner.Y": 0,
            },
            LineWidth=1,
            Color=128,
            AreaColor=11599871,
            IsSolid="T",
        )
    for p in c["pins"]:
        rows.append(binary_pin(p))
        idx += 1
    for u in c["units"]:
        if u["title"]:
            add(
                4,
                u["part"],
                **{"Location.X": u["width"] // 2, "Location.Y": -12},
                Justification=4,
                FontID=2,
                Color=8388608,
                Text=u["title"],
            )
        for label in u["labels"]:
            add(
                4,
                u["part"],
                **{"Location.X": label["x"], "Location.Y": label["y"]},
                Justification=label["just"],
                FontID=3,
                Color=8421504,
                Text=label["text"],
            )
        if u["note"]:
            add(
                4,
                u["part"],
                **{
                    "Location.X": u["width"] // 2,
                    "Location.Y": u["bottom"] + 10,
                },
                Justification=4,
                FontID=3,
                Color=8421504,
                Text=u["note"],
            )
    titled = "; ".join(
        chr(64 + u["part"]) + ": " + u["title"] for u in c["units"] if u["title"]
    )
    params = {
        "Manufacturer": c.get("manufacturer", ""),
        "Manufacturer Part Number": c.get("manufacturer_part_number", c["name"]),
        "Package": c.get("package", ""),
        "Datasheet": c.get("datasheet", ""),
    }
    if titled:
        params["Symbol Parts"] = titled
    params.update(c.get("parameters", {}))
    require(
        not any(k.casefold() in ("comment", "designator") for k in params),
        "Comment/designator are managed by the writer.",
    )
    for name, value in params.items():
        add(41, -1, IsHidden="T", FontID=1, Name=name, Text=value)
    for kind, text, name, y in [
        (34, c["designator_prefix"] + "?", "Designator", 30),
        (41, c["name"], "Comment", 15),
    ]:
        d = dict(
            RECORD=kind,
            IndexInSheet=-1,
            OwnerPartId=-1,
            **{"Location.X": 0, "Location.Y": y},
            Color=8388608,
            FontID=1,
            Text=text,
            Name=name,
            UniqueID=uid(c["name"] + name),
        )
        if kind == 34:
            d["ReadOnlyState"] = 1
        rows.append(record(d))
    rows.append(record(dict(RECORD=44)))
    return b"".join(rows), len(rows)


def write(document, output):
    components = prepare(document)
    streams = {}
    weight = 0
    for c in components:
        data, n = component_stream(c)
        streams[(c["name"], "Data")] = data
        weight += n
    header = dict(
        HEADER="Protel for Windows - Schematic Library Editor Binary File Version 5.0",
        Weight=weight,
        MinorVersion=9,
        UniqueID=uid("schlib:" + ",".join(c["name"] for c in components)),
        FontIdCount=3,
        Size1=10,
        FontName1="Times New Roman",
        Size2=8,
        FontName2="Arial",
        Bold2="T",
        Size3=6,
        FontName3="Arial",
        UseMBCS="T",
        IsBOC="T",
        SheetStyle=9,
        BorderOn="T",
        SheetNumberSpaceSize=12,
        AreaColor=16317695,
        SnapGridOn="T",
        SnapGridSize=5,
        VisibleGridOn="T",
        VisibleGridSize=10,
        CustomX=18000,
        CustomY=18000,
        UseCustomSheet="T",
        ReferenceZonesOn="T",
        Display_Unit=0,
        CompCount=len(components),
    )
    for i, c in enumerate(components):
        header.update(
            {
                f"LibRef{i}": c["name"],
                f"CompDescr{i}": c.get("description", ""),
                f"PartCount{i}": len(c["units"]) + 1,
            }
        )
    streams[("FileHeader",)] = record(header)
    streams[("Storage",)] = record(dict(HEADER="Icon storage"))
    output = Path(output)
    require(
        output.suffix.lower() == ".schlib",
        "Output must use .SchLib extension.",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(compound_file(streams))
    return components
