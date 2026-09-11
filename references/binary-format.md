# Native format notes

What the writers emit, for reading a checker message about the native encoding. This is not a
complete Altium format specification, and the encodings below are fixed.

| Item | Encoding |
| --- | --- |
| File container | OLE Compound File Binary, version 3, 512-byte sectors |
| Short strings | Windows-1252 bytes with a one-byte length prefix; 255 bytes maximum |
| Parameter record | Little-endian 32-bit byte length, pipe-prefixed key/value text, trailing NUL |
| SchLib pin record | Length word with high flag byte `0x01`, then binary pin data |
| PCB internal coordinate | `round(mm / 0.0254 * 10000)` |
| PcbLib footprint `Data` | Name string block followed by primitives; no added EOF marker |
| PcbLib footprint `Header` | Exact primitive count, **not** count plus one |
| Empty footprint WideStrings | Framed single NUL byte for the supported special strings |

## SchLib pin record

Pins are the one schematic record stored as packed binary rather than `|KEY=VALUE|` text. Byte
offsets from the start of the record body:

| Offset | Type | Field |
| --- | --- | --- |
| 0 | int32 | Record id, always `2` |
| 4 | uint8 | Reserved, `0` |
| 5 | int16 | Owning part (`OwnerPartId`) |
| 7 | uint8 | Owner part display mode |
| 8..11 | uint8 x4 | Symbol inner edge, outer edge, inside, outside |
| 12 | pascal | Description |
| +0 | uint8 | Formal type, always `1` |
| +1 | uint8 | Electrical type, `0`..`7` |
| +2 | uint8 | Conglomerate: orientation in bits 0-1, then hide, show name, show designator |
| +3 | int16 | Pin length, 10 mil units |
| +5 | int16 | Location X |
| +7 | int16 | Location Y |
| +9 | int32 | Colour |
| +13 | pascal | Name |
| +n | pascal | Designator |
| +n | pascal x3 | Reserved trailing strings, the middle one `\|&\|` |

Orientation `0` points the wire out to the right and `2` to the left; `1` and `3` are the vertical
orientations this toolkit never writes. The description is a plain length-prefixed string at
offset 12 — note that `pyaltiumlib` 0.7 reads it one byte late and swallows the formal-type byte,
which is one reason this toolkit decodes the layout itself.

## Bundled readers

`altium_tools/vendor/` decodes all of the above with no third-party packages:

| Module | Replaces | Scope |
| --- | --- | --- |
| `cfb.py` | `olefile` | Strict CFB v3/v4 reader: DIFAT, FAT, miniFAT, red-black directory |
| `altium.py` | `pyaltiumlib` | SchLib and PcbLib readers built on `cfb` |
| `raster.py`, `strokefont.py` | `Pillow` | Anti-aliased RGB canvas, stroke-font text, PNG encoder |

The readers never call the writers. A readback check therefore compares two independent decodings
of the same published layout, which is the property that makes it worth running — though both now
live in this repository, so a misreading of the format shared by both would escape the comparison.
Only opening the library in Altium settles that.

## CFB container limits

`altium_tools/common.py` writes FAT, miniFAT and directory entries. There is no DIFAT extension
beyond the header's 109 FAT-sector pointers, so very large libraries are out of scope. Directory
children are ordered by UTF-16 name length, then uppercase name — the CFB red-black ordering, not
plain alphabetical.

Component names are limited to 31 UTF-16 units, so no `SectionKeys` indirection is emitted. Only
the legacy string encoding and simple binary records described above are supported.

## Template provenance

Native record templates and metadata come from the generated socket library in `assets/`. File
identities change when a library is regenerated: these commands rebuild outputs, they are not a
lossless editor for arbitrary existing Altium documents.

## Provenance of the encodings

- [embedded-society/altium-designer-mcp — PCBLIB_FORMAT.md](https://github.com/embedded-society/altium-designer-mcp/blob/main/docs/PCBLIB_FORMAT.md)
- [issus/AltiumSharp](https://github.com/issus/AltiumSharp) — binary record layouts
- [wavenumber-eng/altium_monkey](https://github.com/wavenumber-eng/altium_monkey) — mechanical-layer roles, registry/cache fields, mirror-pair encoding
- [Microsoft Compound File Binary specification](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/)
