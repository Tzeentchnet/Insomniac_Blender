from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from insomniac_asset_io.core.psarc import PsarcArchive


def _u40(value: int) -> bytes:
    return value.to_bytes(5, "big")


def _blocks(data: bytes, block_size: int):
    stored = []
    lengths = []
    for offset in range(0, len(data), block_size):
        raw = data[offset : offset + block_size]
        compressed = zlib.compress(raw)
        if len(compressed) < len(raw):
            stored.append(compressed)
            lengths.append(len(compressed))
        else:
            stored.append(raw)
            lengths.append(0)
    return stored, lengths


def _build_psarc(path: Path) -> bytes:
    block_size = 16
    name = "folder/test.bin"
    manifest = (name + "\n").encode()
    payload = b"0123456789abcdefghijklmnopqrstuvwxyz"
    manifest_blocks, manifest_lengths = _blocks(manifest, block_size)
    payload_blocks, payload_lengths = _blocks(payload, block_size)
    lengths = manifest_lengths + payload_lengths
    toc_size = 32 + 2 * 30 + len(lengths)
    manifest_offset = toc_size
    payload_offset = manifest_offset + sum(map(len, manifest_blocks))

    def entry(digest, block_index, size, offset):
        return (
            digest
            + struct.pack(">I", block_index)
            + _u40(size)
            + _u40(offset)
        )

    header = struct.pack(
        ">4sHH4sIIIII",
        b"PSAR",
        1,
        2,
        b"zlib",
        toc_size,
        30,
        2,
        block_size,
        0,
    )
    toc = (
        entry(bytes(16), 0, len(manifest), manifest_offset)
        + entry(
            hashlib.md5(name.encode(), usedforsecurity=False).digest(),
            len(manifest_blocks),
            len(payload),
            payload_offset,
        )
        + bytes(lengths)
    )
    path.write_bytes(
        header
        + toc
        + b"".join(manifest_blocks)
        + b"".join(payload_blocks)
    )
    return payload


class PsarcTests(unittest.TestCase):
    def test_reads_entry_and_cross_block_slice(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.psarc"
            payload = _build_psarc(path)
            with PsarcArchive(path) as archive:
                self.assertEqual(len(archive.entries), 1)
                self.assertEqual(archive.read("folder/test.bin"), payload)
                self.assertEqual(
                    archive.read_slice("FOLDER\\TEST.BIN", 10, 19),
                    payload[10:29],
                )


if __name__ == "__main__":
    unittest.main()
