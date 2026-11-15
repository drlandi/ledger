"""The log file format, implemented a second time in Python.

Independent of the C code on purpose: the tests use it to write logs
the C core must accept, to read logs the C core wrote, and to decide
on their own where the valid part of a damaged log ends.

File:   8 bytes magic "LGWAL001", then records.
Record: len u32 | crc u32 | type u8 | body        (little-endian)
        crc = CRC-32 (zlib) of the 4 len bytes followed by type + body
        type 1 create:   id u64, initial i64, flags u32      (len 21)
        type 2 transfer: id u64, from u64, to u64, amount i64 (len 33)
"""

import struct
import zlib

MAGIC = b"LGWAL001"
CREATE, XFER = 1, 2
BODY = {CREATE: struct.Struct("<QqI"), XFER: struct.Struct("<QQQq")}
LEN = {CREATE: 21, XFER: 33}


def record(rtype, *fields):
    payload = bytes([rtype]) + BODY[rtype].pack(*fields)
    head = struct.pack("<I", len(payload))
    crc = zlib.crc32(payload, zlib.crc32(head))
    return head + struct.pack("<I", crc) + payload


def create(acc, initial, flags=0):
    return record(CREATE, acc, initial, flags)


def transfer(tid, src, dst, amount):
    return record(XFER, tid, src, dst, amount)


def parse(data, start=len(MAGIC)):
    """Returns (records, end): every intact record from `start` on, as
    tuples (type, fields...), and the offset where the valid part of
    the log ends."""
    out = []
    off = start
    size = len(data)
    while size - off >= 8:
        length, crc = struct.unpack_from("<II", data, off)
        if length not in (21, 33) or size - off < 8 + length:
            break
        payload = data[off + 8:off + 8 + length]
        if zlib.crc32(payload, zlib.crc32(data[off:off + 4])) != crc:
            break
        rtype = payload[0]
        if rtype not in LEN or LEN[rtype] != length:
            break
        out.append((rtype,) + BODY[rtype].unpack(payload[1:]))
        off += 8 + length
    return out, off
