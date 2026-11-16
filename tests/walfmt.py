"""The log file format, implemented a second time in Python.

Independent of the C code on purpose: the tests use it to write logs
the C core must accept, to read logs the C core wrote, and to decide
on their own where the valid part of a damaged log ends.

File:   8 bytes magic "LGWAL001", then batches.
Batch:  one or more data records, then one commit record.
Record: len u32 | crc u32 | type u8 | body        (little-endian)
        crc = CRC-32 (zlib) of the 4 len bytes followed by type + body
        type 1 create:   id u64, initial i64, flags u32      (len 21)
        type 2 transfer: id u64, from u64, to u64, amount i64 (len 33)
        type 3 commit:   start u64                            (len 9)
                         start = file offset where its batch began

Data records count only once the commit record of their batch is
there. What follows the last complete batch is either the one batch a
crash interrupted (to be cut off) or damage in acknowledged data (the
log must be refused); `verdict` decides which, independently of the C
code.
"""

import struct
import zlib

MAGIC = b"LGWAL001"
CREATE, XFER, COMMIT = 1, 2, 3
BODY = {CREATE: struct.Struct("<QqI"), XFER: struct.Struct("<QQQq"),
        COMMIT: struct.Struct("<Q")}
LEN = {CREATE: 21, XFER: 33, COMMIT: 9}
COMMIT_SIZE = 17
MAX_BATCH = 1 << 20     # LG_WAL_BUF of the default build


def record(rtype, *fields):
    payload = bytes([rtype]) + BODY[rtype].pack(*fields)
    head = struct.pack("<I", len(payload))
    crc = zlib.crc32(payload, zlib.crc32(head))
    return head + struct.pack("<I", crc) + payload


def create(acc, initial, flags=0):
    return record(CREATE, acc, initial, flags)


def transfer(tid, src, dst, amount):
    return record(XFER, tid, src, dst, amount)


def commit(start):
    return record(COMMIT, start)


def seal(prefix, records):
    """`prefix` (a whole log so far) plus one more complete batch."""
    return prefix + b"".join(records) + commit(len(prefix))


def log(batches):
    """A whole log file from a list of batches (lists of records)."""
    data = MAGIC
    for recs in batches:
        data = seal(data, recs)
    return data


def one(data, off):
    """The intact record at `off` as (tuple, size), or None."""
    if len(data) - off < 8:
        return None
    length, crc = struct.unpack_from("<II", data, off)
    if length not in (9, 21, 33) or len(data) - off < 8 + length:
        return None
    payload = data[off + 8:off + 8 + length]
    if zlib.crc32(payload, zlib.crc32(data[off:off + 4])) != crc:
        return None
    rtype = payload[0]
    if rtype not in LEN or LEN[rtype] != length:
        return None
    return (rtype,) + BODY[rtype].unpack(payload[1:]), 8 + length


def scan(data, start=len(MAGIC)):
    """Returns (items, end): the data records of every complete batch
    as (record tuple, offset just past the record), and the offset
    where the last complete batch ends."""
    items, pending = [], []
    off = end = start
    while True:
        got = one(data, off)
        if got is None:
            break
        rec, size = got
        off += size
        if rec[0] == COMMIT:
            if rec[1] != end:
                break
            items += pending
            pending = []
            end = off
        else:
            pending.append((rec, off))
    return items, end


def parse(data, start=len(MAGIC)):
    """Returns (records, end): the data records of every complete
    batch, as tuples (type, fields...), and the offset where the last
    complete batch ends."""
    items, end = scan(data, start)
    return [rec for rec, _ in items], end


def verdict(data, max_batch=MAX_BATCH):
    """None if recovery must accept `data` (keeping parse(data) and
    cutting the rest), else the reason it must refuse. Assumes the
    header is good."""
    end = scan(data)[1]
    if len(data) - end > max_batch:
        return "long tail"
    pos = data.find(b"\x09\0\0\0", end)
    while pos >= 0:
        got = one(data, pos)
        if got and got[0][0] == COMMIT:
            if got[0][1] != end or pos + COMMIT_SIZE != len(data):
                return "behind"
            return None
        pos = data.find(b"\x09\0\0\0", pos + 1)
    return None
