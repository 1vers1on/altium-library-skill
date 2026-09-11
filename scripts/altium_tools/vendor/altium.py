"""Altium .SchLib and .PcbLib readers -- the bundled ``pyaltiumlib`` replacement.

Decoded from the container and record layouts in ``references/binary-format.md``:

* a ``.SchLib`` is a CFB whose ``FileHeader`` stream is one ``|KEY=VALUE|`` record naming
  the fonts and the components, and whose ``<Component>/Data`` stream is a run of
  length-prefixed records -- parameter records in the same ``|KEY=VALUE|`` spelling, plus
  one packed binary layout for pins;
* a ``.PcbLib`` is a CFB whose ``Library/Data`` stream holds the library parameters and
  the footprint name list, and whose ``<Footprint>/Data`` stream is a run of typed,
  block-framed primitive records.

Nothing here consults the writer.  Both sides decode the same published byte offsets
independently, which is what makes a readback comparison worth running.  Framing is
checked exactly: a record that does not consume its declared length, a stream with
trailing bytes, or an unknown record type raises :class:`AltiumFormatError`.
"""

import struct
from pathlib import Path
from .cfb import CompoundFile, CompoundFileError

MILS_PER_UNIT = 10  # schematic parameter coordinates are stored in 10 mil units
PCB_UNITS_PER_MM = 1e4 / 0.0254  # PCB coordinates are stored in 1/10000 mil


class AltiumFormatError(Exception):
    """Raised when a library's bytes do not match the documented native layout."""


def _need(condition, message):
    if not condition:
        raise AltiumFormatError(message)


def parse_parameters(data, where="record"):
    """Decode one ``|KEY=VALUE|...`` block into a flat dict, keeping each key's own case.

    Altium writes these blocks as a single flat record; ``RECORD`` is an ordinary key
    inside one, not a separator, so a duplicate key is a genuine format error rather than
    a second record. Case is preserved because a library rewritten from a template has to
    spell its parameters exactly as Altium did; lookups go through :class:`Params`, which
    is case-insensitive.
    """
    _need(
        isinstance(data, (bytes, bytearray)),
        f"{where}: parameter block must be bytes.",
    )
    text = bytes(data).decode("cp1252")
    _need(
        text == "" or text.endswith(chr(0)),
        f"{where}: parameter block is not NUL terminated.",
    )
    fields = {}
    seen = set()
    for entry in text.rstrip(chr(0)).split("|"):
        if "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        _need(key.lower() not in seen, f"{where}: duplicate key {key!r}.")
        seen.add(key.lower())
        fields[key] = value
    return fields


class Params(dict):
    """A read view over a parameter record: original keys, case-insensitive lookup.

    Treat it as immutable. Code that rewrites parameters back into a library works on the
    plain dict from :func:`parse_parameters` instead, so key case survives the round trip.
    """

    def __init__(self, mapping=()):
        super().__init__(mapping)
        self._index = {str(key).lower(): key for key in self}

    def get(self, key, default=None):
        actual = self._index.get(str(key).lower())
        return default if actual is None else dict.__getitem__(self, actual)

    def __contains__(self, key):
        return str(key).lower() in self._index

    def text(self, key, default=""):
        value = self.get(key)
        return default if value is None else value

    def integer(self, key, default=0):
        value = self.get(key)
        if value is None or value == "":
            return default
        try:
            return int(value)
        except ValueError:
            raise AltiumFormatError(f"Parameter {key!r} is not an integer: {value!r}")

    def flag(self, key, default=False):
        value = self.get(key)
        if value is None:
            return default
        return str(value).strip().upper() in ("T", "TRUE", "1")

    def coordinate(self, key, default=0.0):
        """A schematic coordinate in 10 mil units, folding in any ``_FRAC`` companion."""
        if key not in self and f"{key}_frac" not in self:
            return default
        return self.integer(key) + self.integer(f"{key}_frac") / 100000.0


# --- schematic ----------------------------------------------------------------------------------

ELECTRICAL_NAMES = (
    "input",
    "bidirectional",
    "output",
    "open_collector",
    "passive",
    "tri_state",
    "open_emitter",
    "power",
)
ORIENTATION_NAMES = {0: "right", 1: "up", 2: "left", 3: "down"}


class SchRecord:
    """One parameter-style schematic record.

    ``x``/``y`` are in 10 mil units with Y increasing upward, exactly as stored -- this
    reader applies no sign flip of its own.
    """

    def __init__(self, params):
        self.params = params
        self.record = params.integer("record", -1)
        self.owner_part_id = params.integer("ownerpartid", -1)
        self.index_in_sheet = params.integer("indexinsheet", -1)
        self.x = params.coordinate("location.x")
        self.y = params.coordinate("location.y")
        self.text = params.text("text")
        self.name = params.text("name")
        self.font_id = params.integer("fontid", 0)
        self.justification = params.integer("justification", 0)
        self.unique_id = params.text("uniqueid")

    def __repr__(self):
        return f"<SchRecord {self.record} owner={self.owner_part_id}>"


class SchRectangle(SchRecord):
    """RECORD=14. ``location`` is one corner, ``corner`` the opposite one."""

    def __init__(self, params):
        super().__init__(params)
        self.corner_x = params.coordinate("corner.x")
        self.corner_y = params.coordinate("corner.y")

    @property
    def width(self):
        return abs(self.corner_x - self.x)

    @property
    def height(self):
        return abs(self.corner_y - self.y)


class SchPin:
    """RECORD=2, stored as a packed binary block rather than ``|KEY=VALUE|`` text.

    Layout, from offset 0: int32 record, uint8 reserved, int16 owner part, uint8 display
    mode, four uint8 symbol fields, Pascal description, uint8 formal type, uint8
    electrical type, uint8 conglomerate (orientation in bits 0-1, then flags), int16
    length, int16 x, int16 y, int32 colour, Pascal name, Pascal designator, then three
    trailing Pascal strings Altium reserves.
    """

    record = 2

    def __init__(self, data):
        _need(len(data) >= 12, "Pin record is shorter than its fixed header.")
        (
            kind,
            reserved,
            self.owner_part_id,
            self.owner_part_display_mode,
            self.symbol_inner_edge,
            self.symbol_outer_edge,
            self.symbol_inside,
            self.symbol_outside,
        ) = struct.unpack_from("<iBhBBBBB", data)
        _need(kind == 2, f"Pin record carries record id {kind}, not 2.")
        self.reserved = reserved
        pos = 12
        self.description, pos = _pascal(data, pos, "pin description")
        tail = "<BBBhhhI"
        _need(
            pos + struct.calcsize(tail) <= len(data),
            "Pin record is truncated after its description.",
        )
        (
            self.formal_type,
            electrical,
            conglomerate,
            self.length,
            self.x,
            self.y,
            self.color,
        ) = struct.unpack_from(tail, data, pos)
        pos += struct.calcsize(tail)
        _need(
            0 <= electrical < len(ELECTRICAL_NAMES),
            f"Unknown pin electrical type {electrical}.",
        )
        self.electrical = electrical
        self.electrical_name = ELECTRICAL_NAMES[electrical]
        self.conglomerate = conglomerate
        self.orientation = conglomerate & 0x03
        self.orientation_name = ORIENTATION_NAMES[self.orientation]
        self.rotated = bool(conglomerate & 0x01)
        self.flipped = bool(conglomerate & 0x02)
        self.hidden = bool(conglomerate & 0x04)
        self.show_name = bool(conglomerate & 0x08)
        self.show_designator = bool(conglomerate & 0x10)
        self.graphically_locked = bool(conglomerate & 0x40)
        self.name, pos = _pascal(data, pos, "pin name")
        self.designator, pos = _pascal(data, pos, "pin designator")
        self.extras = []
        while pos < len(data):
            value, pos = _pascal(data, pos, "pin trailing string")
            self.extras.append(value)
        _need(pos == len(data), "Pin record has bytes past its last string.")

    @property
    def end_x(self):
        return self.x + {0: self.length, 2: -self.length}.get(self.orientation, 0)

    @property
    def end_y(self):
        return self.y + {1: self.length, 3: -self.length}.get(self.orientation, 0)

    @property
    def horizontal(self):
        return self.orientation in (0, 2)

    def __repr__(self):
        return f"<SchPin {self.designator}:{self.name} at ({self.x},{self.y}) {self.orientation_name}>"


def _pascal(data, pos, where):
    _need(pos < len(data), f"{where}: length byte past end of record.")
    n = data[pos]
    pos += 1
    _need(
        pos + n <= len(data),
        f"{where}: string of {n} bytes runs past end of record.",
    )
    return data[pos : pos + n].decode("cp1252"), pos + n


class SchComponent:
    """One library component: its records in file order, plus indexed views of them."""

    def __init__(self, name, description, declared_part_count, data):
        self.name = name
        self.description = description
        self.records = []
        pos = 0
        while pos < len(data):
            _need(pos + 4 <= len(data), f"{name}: truncated record header.")
            head = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            length = head & 0x00FFFFFF
            kind = head >> 24
            _need(
                length <= 0xFFFF,
                f"{name}: record length {length} exceeds the 16 bit field Altium writes.",
            )
            _need(
                pos + length <= len(data),
                f"{name}: record body of {length} bytes runs past the stream.",
            )
            body = data[pos : pos + length]
            pos += length
            if kind == 0:
                self.records.append(
                    _schematic_record(Params(parse_parameters(body, name)))
                )
            elif kind == 1:
                self.records.append(SchPin(body))
            else:
                raise AltiumFormatError(f"{name}: unsupported record type {kind}.")
        _need(pos == len(data), f"{name}: trailing bytes after the last record.")

        header = [r for r in self.records if getattr(r, "record", None) == 1]
        _need(
            len(header) == 1,
            f"{name}: expected exactly one RECORD=1 component header.",
        )
        self.header = header[0]
        # Altium stores part count as parts+1; the header stream repeats the same figure.
        self.part_count = self.header.params.integer("partcount", 1) - 1
        _need(
            self.part_count >= 1,
            f"{name}: component header declares no parts.",
        )
        _need(
            declared_part_count is None or declared_part_count - 1 == self.part_count,
            f"{name}: FileHeader part count disagrees with the component record.",
        )
        self.declared_pin_count = self.header.params.integer("allpincount", -1)
        self.pins = [r for r in self.records if isinstance(r, SchPin)]
        self.rectangles = [r for r in self.records if isinstance(r, SchRectangle)]
        self.labels = [r for r in self.records if getattr(r, "record", None) == 4]
        self.designator = next(
            (r.text for r in self.records if getattr(r, "record", None) == 34),
            "",
        )
        self.parameters = {
            r.name: r.text for r in self.records if getattr(r, "record", None) == 41
        }
        _need(
            self.declared_pin_count in (-1, len(self.pins)),
            f"{name}: AllPinCount is {self.declared_pin_count} but {len(self.pins)} pin records are present.",
        )

    def records_for_part(self, part):
        """Records owned by ``part``, plus the component-wide ones."""
        return [
            r for r in self.records if getattr(r, "owner_part_id", -1) in (-1, part)
        ]

    def __repr__(self):
        return f"<SchComponent {self.name!r} {len(self.pins)} pins {self.part_count} parts>"


def _schematic_record(params):
    return (
        SchRectangle(params)
        if params.integer("record", -1) == 14
        else SchRecord(params)
    )


class SchLibrary:
    """A parsed ``.SchLib``."""

    def __init__(self, path, container):
        self.path = Path(path)
        self.header = Params(
            parse_parameters(_block(container.read("FileHeader")), "FileHeader")
        )
        banner = self.header.text("header")
        _need(
            "Schematic" in banner and "Binary File" in banner,
            f"{self.path.name}: FileHeader is not a schematic binary library ({banner!r}).",
        )
        self.fonts = {}
        for index in range(1, self.header.integer("fontidcount") + 1):
            self.fonts[index] = dict(
                name=self.header.text(f"fontname{index}"),
                size=self.header.integer(f"size{index}"),
                bold=self.header.flag(f"bold{index}"),
                italic=self.header.flag(f"italic{index}"),
            )
        self.component_count = self.header.integer("compcount")
        self.components = []
        for index in range(self.component_count):
            name = self.header.text(f"libref{index}")
            _need(bool(name), f"FileHeader entry {index} has no LibRef.")
            _need(
                container.exists([name, "Data"]),
                f"FileHeader lists {name!r} but the stream is missing.",
            )
            self.components.append(
                SchComponent(
                    name,
                    self.header.text(f"compdescr{index}"),
                    self.header.integer(f"partcount{index}", 0) or None,
                    container.read([name, "Data"]),
                )
            )
        names = {c.name for c in self.components}
        streams = {
            p[0] for p in container.listdir() if len(p) == 2 and p[1].lower() == "data"
        }
        extra = streams - names
        _need(
            not extra,
            f"{self.path.name}: component streams {sorted(extra)} are not listed in FileHeader.",
        )

    def component(self, name):
        for c in self.components:
            if c.name == name:
                return c
        raise KeyError(name)

    def __repr__(self):
        return f"<SchLibrary {self.path.name} {len(self.components)} components>"


def _block(data):
    """Unwrap a stream whose payload is a single u32 length-prefixed block."""
    _need(len(data) >= 4, "Block stream is shorter than its length prefix.")
    n = struct.unpack_from("<I", data)[0]
    _need(4 + n <= len(data), "Block length runs past the stream.")
    return data[4 : 4 + n]


# --- PCB ----------------------------------------------------------------------------------------

# Primitive type -> (block count, name). This toolkit writes only these; anything else in a
# library it produced means the file was not written by this toolkit.
PCB_BLOCKS = {1: 1, 2: 6, 4: 1, 5: 2, 6: 1, 11: 1, 12: 1}
PCB_NAMES = {
    1: "Arc",
    2: "Pad",
    4: "Track",
    5: "Text",
    6: "Fill",
    11: "Region",
    12: "ComponentBody",
}


def decode_pcb_parameters(data):
    """Split ``Library/Data`` into its parameter dict and the trailing footprint list."""
    _need(len(data) >= 4, "Library/Data is shorter than its length prefix.")
    n = struct.unpack_from("<I", data)[0]
    _need(
        4 + n <= len(data),
        "Library/Data parameter block runs past the stream.",
    )
    # A plain dict: the writer rewrites these keys verbatim when it saves a library.
    return parse_parameters(data[4 : 4 + n], "Library/Data"), data[4 + n :]


def parse_pcb_records(data):
    """Return ``(footprint name, [(type, [block, ...]), ...])`` for one footprint stream.

    Strict framing for the types this toolkit writes; this is not a general PcbLib importer.
    """
    _need(len(data) >= 4, "Footprint stream is shorter than its name prefix.")
    n = struct.unpack_from("<I", data)[0]
    _need(
        4 + n <= len(data) and n >= 1,
        "Footprint name block runs past the stream.",
    )
    _need(
        data[4] == n - 1,
        "Footprint name length byte disagrees with its block size.",
    )
    name = data[5 : 4 + n].decode("cp1252")
    pos = 4 + n
    records = []
    while pos < len(data):
        kind = data[pos]
        pos += 1
        _need(
            kind in PCB_BLOCKS,
            f"{name}: unsupported primitive type {kind} in library data.",
        )
        blocks = []
        for _ in range(PCB_BLOCKS[kind]):
            _need(pos + 4 <= len(data), f"{name}: truncated record header.")
            size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            _need(pos + size <= len(data), f"{name}: truncated record body.")
            blocks.append(data[pos : pos + size])
            pos += size
        records.append((kind, blocks))
    _need(pos == len(data), f"{name}: trailing bytes after the last primitive.")
    return name, records


class PcbFootprint:
    def __init__(self, name, records):
        self.name = name
        self.records = records
        self.pads = [blocks for kind, blocks in records if kind == 2]
        self.counts = {
            PCB_NAMES[k]: sum(1 for t, _ in records if t == k)
            for k in sorted({t for t, _ in records})
        }

    def __repr__(self):
        return f"<PcbFootprint {self.name!r} {len(self.pads)} pads>"


class PcbLibrary:
    """A parsed ``.PcbLib``."""

    def __init__(self, path, container):
        self.path = Path(path)
        raw, tail = decode_pcb_parameters(container.read("Library/Data"))
        self.parameters = Params(raw)
        _need(len(tail) >= 4, "Library/Data has no footprint list.")
        count = struct.unpack_from("<I", tail)[0]
        pos = 4
        self.footprint_names = []
        for _ in range(count):
            _need(pos + 4 <= len(tail), "Footprint list is truncated.")
            n = struct.unpack_from("<I", tail, pos)[0]
            pos += 4
            _need(
                pos + n <= len(tail) and n >= 1,
                "Footprint name runs past the list.",
            )
            _need(
                tail[pos] == n - 1,
                "Footprint name length byte disagrees with its block size.",
            )
            self.footprint_names.append(tail[pos + 1 : pos + n].decode("cp1252"))
            pos += n
        _need(pos == len(tail), "Trailing bytes after the footprint list.")
        self.footprints = []
        for name in self.footprint_names:
            _need(
                container.exists([name, "Data"]),
                f"Library/Data lists {name!r} but the stream is missing.",
            )
            stored, records = parse_pcb_records(container.read([name, "Data"]))
            _need(
                stored == name,
                f"Footprint stream is named {stored!r} but the library lists {name!r}.",
            )
            self.footprints.append(PcbFootprint(name, records))
        self.layer_kinds = self._layer_kinds(container)

    @staticmethod
    def _layer_kinds(container):
        raw = container.read("Library/LayerKindMapping/Data")
        _need(len(raw) >= 20, "LayerKindMapping stream is truncated.")
        count = struct.unpack_from("<I", raw, 16)[0]
        _need(
            20 + 8 * count <= len(raw),
            "LayerKindMapping declares more entries than it holds.",
        )
        return dict(struct.unpack_from("<II", raw, 20 + 8 * i) for i in range(count))

    def footprint(self, name):
        for f in self.footprints:
            if f.name == name:
                return f
        raise KeyError(name)

    def __repr__(self):
        return f"<PcbLibrary {self.path.name} {len(self.footprints)} footprints>"


# --- entry points -------------------------------------------------------------------------------


def open_container(path, strict=True):
    """Open an Altium library's CFB container, rejecting anything that is not clean CFB v3."""
    path = Path(path)
    if not path.is_file():
        raise AltiumFormatError(f"No such library: {path}")
    try:
        container = CompoundFile(path, strict=strict)
    except CompoundFileError as error:
        raise AltiumFormatError(f"{path.name}: {error}") from None
    if container.sectorsize != 512 or container.major_version != 3:
        raise AltiumFormatError(f"{path.name}: native container is not clean CFB v3.")
    if container.parsing_issues:
        raise AltiumFormatError(
            f"{path.name}: container defects: {container.parsing_issues}"
        )
    return container


def read(path):
    """Open a ``.SchLib`` or ``.PcbLib`` and return the matching library object."""
    path = Path(path)
    suffix = path.suffix.lower()
    with open_container(path) as container:
        if suffix == ".schlib":
            return SchLibrary(path, container)
        if suffix == ".pcblib":
            return PcbLibrary(path, container)
    raise AltiumFormatError(f"{path.name}: expected a .SchLib or .PcbLib file.")
