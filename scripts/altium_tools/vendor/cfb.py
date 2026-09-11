"""Compound File Binary (OLE2 / CFBF) reader -- the bundled ``olefile`` replacement.

Implemented from the [MS-CFB] structure definitions: a 512-byte header, a DIFAT naming
the FAT sectors, a FAT chaining ordinary sectors, a mini stream for payloads below the
cutoff chained by the miniFAT, and a red-black directory whose 128-byte entries name the
storages and streams.

Reading is strict on purpose. Every structural field is range-checked, chains are walked
with a cycle guard, and anything that does not match the specification is recorded in
``CompoundFile.parsing_issues``. ``strict=True`` (the default) turns those into
exceptions, so a malformed container can never be quietly half-parsed and then reported
as verified.
"""

import io
import struct
from pathlib import Path

MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
FREE = 0xFFFFFFFF
END = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC
EMPTY = 0
STORAGE = 1
STREAM = 2
ROOT = 5


class CompoundFileError(Exception):
    """Raised when a container does not match [MS-CFB] and ``strict`` is set."""


def is_compound_file(source):
    """True when the first eight bytes carry the CFB signature."""
    if isinstance(source, (bytes, bytearray)):
        return bytes(source[:8]) == MAGIC
    try:
        with open(source, "rb") as handle:
            return handle.read(8) == MAGIC
    except OSError:
        return False


class _Entry:
    __slots__ = (
        "name",
        "kind",
        "color",
        "left",
        "right",
        "child",
        "start",
        "size",
        "index",
    )

    def __init__(self, index, raw):
        namelen = struct.unpack_from("<H", raw, 64)[0]
        if namelen % 2 or not 0 <= namelen <= 64:
            raise CompoundFileError(
                f"Directory entry {index} has an invalid name length {namelen}."
            )
        self.index = index
        self.name = raw[: max(0, namelen - 2)].decode("utf-16le") if namelen else ""
        self.kind = raw[66]
        self.color = raw[67]
        self.left, self.right, self.child = struct.unpack_from("<III", raw, 68)
        self.start, self.size = struct.unpack_from("<IQ", raw, 116)

    def __repr__(self):
        kind = "storage" if self.kind == STORAGE else "stream"
        return f"<{kind} {self.name!r} {self.size} bytes>"


class CompoundFile:
    """A parsed CFB container. Use as a context manager, or call ``close()``.

    ``source`` is a path, ``bytes``, or any object with ``.read()``.
    """

    def __init__(self, source, strict=True):
        if isinstance(source, (bytes, bytearray)):
            self._data = bytes(source)
        elif hasattr(source, "read"):
            self._data = source.read()
        else:
            self._data = Path(source).read_bytes()
        self.strict = strict
        self.parsing_issues = []
        self._paths = {}
        self._parse()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        self._data = b""

    def _defect(self, message):
        self.parsing_issues.append(message)
        if self.strict:
            raise CompoundFileError(message)

    def _parse(self):
        data = self._data
        if len(data) < 512 or data[:8] != MAGIC:
            raise CompoundFileError("Not a Compound File Binary container.")
        minor, major, order, sector_shift, mini_shift = struct.unpack_from(
            "<HHHHH", data, 24
        )
        if major not in (3, 4):
            raise CompoundFileError(f"Unsupported CFB major version {major}.")
        if order != 0xFFFE:
            self._defect("Byte-order mark is not little-endian.")
        if sector_shift != (9 if major == 3 else 12):
            raise CompoundFileError(
                f"Sector shift {sector_shift} does not match CFB v{major}."
            )
        if mini_shift != 6:
            self._defect(f"Mini sector shift {mini_shift} is not the required 6.")
        self.minor_version = minor
        self.major_version = major
        self.sectorsize = 1 << sector_shift
        self.minisectorsize = 1 << mini_shift
        if len(data) % self.sectorsize:
            self._defect("File length is not a whole number of sectors.")
        self.nb_sect = max(0, (len(data) - self.sectorsize) // self.sectorsize)

        (
            fat_count,
            dir_start,
            _txn,
            self.mini_cutoff,
            minifat_start,
            minifat_count,
            difat_start,
            difat_count,
        ) = struct.unpack_from("<IIIIIIII", data, 44)
        if self.mini_cutoff != 4096:
            self._defect(
                f"Mini-stream cutoff {self.mini_cutoff} is not the required 4096."
            )
        if major == 3 and struct.unpack_from("<I", data, 40)[0] != 0:
            self._defect("CFB v3 must report zero directory sectors in the header.")

        per_sector = self.sectorsize // 4
        difat = [n for n in struct.unpack_from("<109I", data, 76) if n != FREE]
        sector = difat_start
        seen = set()
        for _ in range(difat_count):
            if sector in (END, FREE):
                break
            if sector in seen:
                self._defect("DIFAT chain loops.")
                break
            seen.add(sector)
            values = struct.unpack_from("<%dI" % per_sector, self._sector(sector))
            difat.extend(n for n in values[:-1] if n != FREE)
            sector = values[-1]
        if len(difat) != fat_count:
            self._defect(
                f"Header lists {fat_count} FAT sectors but the DIFAT names {len(difat)}."
            )

        self.fat = []
        for number in difat:
            self.fat.extend(
                struct.unpack_from("<%dI" % per_sector, self._sector(number))
            )
        for number in difat:
            if number < len(self.fat) and self.fat[number] != FATSECT:
                self._defect(f"FAT sector {number} is not marked FATSECT in the FAT.")

        self.minifat = []
        if minifat_count:
            raw = self._chain_bytes(minifat_start, count=minifat_count)
            self.minifat = list(struct.unpack("<%dI" % (len(raw) // 4), raw))

        directory = self._chain_bytes(dir_start)
        if not directory or len(directory) % 128:
            raise CompoundFileError(
                "Directory stream is empty or not a whole number of entries."
            )
        self.entries = []
        for index in range(len(directory) // 128):
            raw = directory[index * 128 : (index + 1) * 128]
            if raw[66] == EMPTY:
                continue
            entry = _Entry(index, raw)
            if entry.kind not in (STORAGE, STREAM, ROOT):
                self._defect(f"Directory entry {index} has unknown type {entry.kind}.")
            self.entries.append(entry)
        by_index = {e.index: e for e in self.entries}
        if (
            not self.entries
            or self.entries[0].index != 0
            or self.entries[0].kind != ROOT
        ):
            raise CompoundFileError("Directory entry 0 is not the root storage.")
        self.root = self.entries[0]
        self._ministream = (
            self._chain_bytes(self.root.start, limit=self.root.size)
            if self.root.size
            else b""
        )

        def walk(node, prefix, guard):
            if node == FREE or node not in by_index:
                return
            if node in guard:
                self._defect("Directory tree contains a cycle.")
                return
            guard.add(node)
            entry = by_index[node]
            walk(entry.left, prefix, guard)
            path = prefix + (entry.name,)
            if path in self._paths:
                self._defect("Duplicate directory path " + "/".join(path) + ".")
            self._paths[path] = entry
            if entry.kind == STORAGE:
                walk(entry.child, path, set())
            walk(entry.right, prefix, guard)

        walk(self.root.child, (), set())

    def _sector(self, number):
        if not 0 <= number < self.nb_sect:
            raise CompoundFileError(f"Sector {number} is outside the file.")
        offset = (number + 1) * self.sectorsize
        return self._data[offset : offset + self.sectorsize]

    def _chain_bytes(self, start, limit=None, count=None):
        """Concatenate a FAT sector chain, optionally truncated to ``limit`` bytes."""
        out = bytearray()
        sector = start
        seen = set()
        steps = 0
        while sector not in (END, FREE):
            if sector in seen or steps > self.nb_sect + 1:
                raise CompoundFileError(
                    "FAT chain loops or runs past the end of the file."
                )
            seen.add(sector)
            out += self._sector(sector)
            steps += 1
            if count is not None and steps >= count:
                break
            if sector >= len(self.fat):
                raise CompoundFileError(f"Sector {sector} has no FAT entry.")
            sector = self.fat[sector]
        return bytes(out[:limit]) if limit is not None else bytes(out)

    def _mini_bytes(self, start, size):
        out = bytearray()
        sector = start
        seen = set()
        while sector not in (END, FREE) and len(out) < size:
            if sector in seen:
                raise CompoundFileError("MiniFAT chain loops.")
            seen.add(sector)
            offset = sector * self.minisectorsize
            chunk = self._ministream[offset : offset + self.minisectorsize]
            if len(chunk) != self.minisectorsize:
                raise CompoundFileError(
                    "Mini sector runs past the end of the mini stream."
                )
            out += chunk
            if sector >= len(self.minifat):
                raise CompoundFileError(f"Mini sector {sector} has no MiniFAT entry.")
            sector = self.minifat[sector]
        return bytes(out[:size])

    @staticmethod
    def _key(path):
        if isinstance(path, str):
            path = [p for p in path.replace(chr(92), "/").split("/") if p]
        return tuple(path)

    def listdir(self, streams=True, storages=False):
        """Every path in the container, as lists of name components."""
        return [
            list(path)
            for path, entry in sorted(self._paths.items())
            if (entry.kind == STREAM and streams)
            or (entry.kind == STORAGE and storages)
        ]

    def exists(self, path):
        return self._key(path) in self._paths

    def get_size(self, path):
        return self._entry(path).size

    def _entry(self, path):
        key = self._key(path)
        if key not in self._paths:
            raise CompoundFileError("No such entry: " + "/".join(key))
        return self._paths[key]

    def read(self, path):
        """Return a stream's bytes."""
        entry = self._entry(path)
        if entry.kind != STREAM:
            raise CompoundFileError(
                "/".join(self._key(path)) + " is a storage, not a stream."
            )
        if entry.size == 0:
            return b""
        if entry.size < self.mini_cutoff:
            return self._mini_bytes(entry.start, entry.size)
        return self._chain_bytes(entry.start, limit=entry.size)

    def openstream(self, path):
        """``olefile``-compatible spelling: a binary file object over the stream."""
        return io.BytesIO(self.read(path))
