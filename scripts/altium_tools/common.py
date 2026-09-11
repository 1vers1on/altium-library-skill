"""Small native Altium framing and CFB v3 writer. No third-party imports."""

import hashlib
import math
import struct


def uid(seed):
    return hashlib.sha256(seed.encode()).hexdigest()[:8].upper()


def record(d):
    for k, v in d.items():
        assert "|" not in str(v), (k, v)
    b = ("|" + "|".join(f"{k}={v}" for k, v in d.items()) + "\0").encode("cp1252")
    return struct.pack("<I", len(b)) + b


def ps(s):
    b = s.encode("cp1252")
    assert len(b) <= 255
    return bytes([len(b)]) + b


FREE = 0xFFFFFFFF
END = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD


def compound_file(streams):
    """Small standards-compliant CFB v3 writer, with FAT and miniFAT streams."""
    entries = [dict(name="Root Entry", type=5, children=[], data=b"")]
    paths = {(): 0}
    for path, data in streams.items():
        for depth in range(1, len(path) + 1):
            key = path[:depth]
            if key in paths:
                continue
            i = len(entries)
            paths[key] = i
            parent = paths[key[:-1]]
            is_stream = depth == len(path)
            entries.append(
                dict(
                    name=key[-1],
                    type=2 if is_stream else 1,
                    children=[],
                    data=data if is_stream else b"",
                )
            )
            entries[parent]["children"].append(i)
    for e in entries:
        e.update(left=FREE, right=FREE, child=FREE, color=1, start=END, size=0)
    # CFB directory children are ordered by UTF-16 name length then uppercase name.
    for e in entries:
        ids = sorted(
            e["children"],
            key=lambda i: (
                len(entries[i]["name"].encode("utf-16le")),
                entries[i]["name"].upper(),
            ),
        )
        maxdepth = int(math.floor(math.log2(len(ids)))) if ids else 0

        def tree(seq, depth=0):
            if not seq:
                return FREE
            mid = len(seq) // 2
            i = seq[mid]
            entries[i]["color"] = 0 if depth == maxdepth and depth else 1
            entries[i]["left"] = tree(seq[:mid], depth + 1)
            entries[i]["right"] = tree(seq[mid + 1 :], depth + 1)
            return i

        e["child"] = tree(ids)
    minidata = bytearray()
    minifat = []
    for e in entries:
        if e["type"] != 2:
            continue
        e["size"] = len(e["data"])
        if 0 < e["size"] < 4096:
            start = len(minifat)
            count = math.ceil(e["size"] / 64)
            e["start"] = start
            minidata.extend(e["data"].ljust(count * 64, b"\0"))
            minifat.extend(list(range(start + 1, start + count)) + [END])
    sectors = []
    fat = []

    def allocate(data):
        if not data:
            return END, 0
        start = len(sectors)
        count = math.ceil(len(data) / 512)
        padded = data.ljust(count * 512, b"\0")
        sectors.extend(padded[i * 512 : (i + 1) * 512] for i in range(count))
        fat.extend(list(range(start + 1, start + count)) + [END])
        return start, count

    for e in entries:
        if e["type"] == 2 and e["size"] >= 4096:
            e["start"], _ = allocate(e["data"])
    entries[0]["start"], _ = allocate(bytes(minidata))
    entries[0]["size"] = len(minidata)
    mfdata = struct.pack("<" + "I" * len(minifat), *minifat) if minifat else b""
    if mfdata:
        mfdata = mfdata.ljust(math.ceil(len(mfdata) / 512) * 512, b"\xff")
    mfstart, mfcount = allocate(mfdata)
    db = bytearray()
    for e in entries:
        name = (e["name"] + "\0").encode("utf-16le")
        assert len(name) <= 64
        d = bytearray(128)
        d[: len(name)] = name
        struct.pack_into(
            "<HBBIII",
            d,
            64,
            len(name),
            e["type"],
            e["color"],
            e["left"],
            e["right"],
            e["child"],
        )
        struct.pack_into("<IQ", d, 116, e["start"], e["size"])
        db.extend(d)
    dstart, _ = allocate(bytes(db))
    nf = math.ceil(len(sectors) / 128)
    while nf != math.ceil((len(sectors) + nf) / 128):
        nf = math.ceil((len(sectors) + nf) / 128)
    assert nf <= 109
    fatids = list(range(len(sectors), len(sectors) + nf))
    fat.extend([FATSECT] * nf)
    fat.extend([FREE] * (nf * 128 - len(fat)))
    fatdata = struct.pack("<" + "I" * len(fat), *fat)
    sectors.extend(fatdata[i * 512 : (i + 1) * 512] for i in range(nf))
    h = bytearray(512)
    h[:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<HHHHH", h, 24, 0x003E, 3, 0xFFFE, 9, 6)
    struct.pack_into(
        "<IIIIIIIII", h, 40, 0, nf, dstart, 0, 4096, mfstart, mfcount, END, 0
    )
    struct.pack_into("<109I", h, 76, *(fatids + [FREE] * (109 - nf)))
    return bytes(h) + b"".join(sectors)


def require(condition, message):
    if not condition:
        raise ValueError(message)
