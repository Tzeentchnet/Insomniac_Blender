from __future__ import annotations

import hashlib
import struct
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .errors import FormatError, MissingResourceError, UnsupportedFormatError


@dataclass(frozen=True, slots=True)
class PsarcEntry:
    name: str
    uncompressed_size: int
    file_offset: int
    block_index: int


@dataclass(frozen=True, slots=True)
class _RawEntry:
    digest: bytes
    uncompressed_size: int
    file_offset: int
    block_index: int


class PsarcArchive:
    """Random-access reader for PS3 zlib PSARC archives."""

    HEADER_SIZE = 32
    ENTRY_SIZE = 30

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._file = self.path.open("rb")
        try:
            self._read_toc()
        except Exception:
            self._file.close()
            raise

    @classmethod
    def from_stream(
        cls,
        stream: BinaryIO,
        *,
        label: str,
    ) -> PsarcArchive:
        archive = cls.__new__(cls)
        archive.path = label
        archive._lock = threading.RLock()
        archive._file = stream
        try:
            archive._read_toc()
        except Exception:
            stream.close()
            raise
        return archive

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.close()

    def __enter__(self) -> "PsarcArchive":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    @staticmethod
    def normalize(name: str) -> str:
        return name.replace("\\", "/").lstrip("/").casefold()

    def _read_exact(self, length: int) -> bytes:
        data = self._file.read(length)
        if len(data) != length:
            raise FormatError(
                f"{self.path}: expected {length} bytes, got {len(data)}"
            )
        return data

    @staticmethod
    def _u40(data: bytes) -> int:
        if len(data) != 5:
            raise ValueError("u40 requires five bytes")
        return int.from_bytes(data, "big")

    def _read_toc(self) -> None:
        with self._lock:
            self._file.seek(0)
            header = self._read_exact(self.HEADER_SIZE)
            magic = header[:4]
            if magic == b"DSAR":
                raise UnsupportedFormatError(
                    f"{self.path}: DSAR-wrapped archives are not supported"
                )
            if magic != b"PSAR":
                raise FormatError(f"{self.path}: bad PSARC magic {magic!r}")

            (
                major,
                minor,
                compression,
                toc_size,
                entry_size,
                entry_count,
                block_size,
                flags,
            ) = struct.unpack(">HH4sIIIII", header[4:])
            if major != 1:
                raise UnsupportedFormatError(
                    f"{self.path}: unsupported PSARC version {major}.{minor}"
                )
            if compression != b"zlib":
                raise UnsupportedFormatError(
                    f"{self.path}: unsupported PSARC compression "
                    f"{compression.decode('ascii', errors='replace')!r}"
                )
            if entry_count == 0:
                raise FormatError(f"{self.path}: PSARC has no TOC entries")
            if block_size <= 0:
                raise FormatError(f"{self.path}: invalid block size {block_size}")

            entry_size = entry_size or self.ENTRY_SIZE
            if entry_size < self.ENTRY_SIZE:
                raise FormatError(
                    f"{self.path}: invalid TOC entry size {entry_size}"
                )
            raw_entries: list[_RawEntry] = []
            for _ in range(entry_count):
                raw = self._read_exact(entry_size)
                raw_entries.append(
                    _RawEntry(
                        digest=raw[:16],
                        block_index=struct.unpack_from(">I", raw, 16)[0],
                        uncompressed_size=self._u40(raw[20:25]),
                        file_offset=self._u40(raw[25:30]),
                    )
                )

            block_table_bytes = (
                toc_size - self.HEADER_SIZE - entry_size * entry_count
            )
            if block_table_bytes < 0:
                raise FormatError(f"{self.path}: TOC size is smaller than its entries")

            block_length_width = max(1, ((block_size - 1).bit_length() + 7) // 8)
            if block_table_bytes % block_length_width:
                raise FormatError(
                    f"{self.path}: malformed PSARC block-length table"
                )
            block_count = block_table_bytes // block_length_width
            block_raw = self._read_exact(block_table_bytes)
            block_lengths = [
                int.from_bytes(
                    block_raw[
                        index * block_length_width : (index + 1)
                        * block_length_width
                    ],
                    "big",
                )
                for index in range(block_count)
            ]

            self.major = major
            self.minor = minor
            self.block_size = block_size
            self.flags = flags
            self._block_lengths = tuple(block_lengths)

            manifest = self._read_raw(raw_entries[0], 0, None)
            names = [
                name
                for name in manifest.decode("utf-8", errors="replace")
                .replace("\0", "\n")
                .splitlines()
                if name
            ]
            by_hash: dict[bytes, str] = {}
            for raw_name in names:
                stored_name = (
                    raw_name[1:]
                    if flags & 0x02 and raw_name.startswith("/")
                    else raw_name
                )
                variants = {raw_name, raw_name.upper(), raw_name.lower()}
                if flags & 0x01:
                    variants.add(raw_name.upper())
                for variant in variants:
                    by_hash.setdefault(
                        hashlib.md5(
                            variant.encode("utf-8"), usedforsecurity=False
                        ).digest(),
                        stored_name,
                    )

            entries: list[PsarcEntry] = []
            by_name: dict[str, PsarcEntry] = {}
            for raw in raw_entries[1:]:
                name = by_hash.get(raw.digest)
                if name is None:
                    continue
                entry = PsarcEntry(
                    name=name,
                    uncompressed_size=raw.uncompressed_size,
                    file_offset=raw.file_offset,
                    block_index=raw.block_index,
                )
                entries.append(entry)
                by_name.setdefault(self.normalize(name), entry)
            self.entries = tuple(entries)
            self._by_name = by_name

    def get(self, name: str) -> PsarcEntry:
        try:
            return self._by_name[self.normalize(name)]
        except KeyError as exc:
            raise MissingResourceError(
                f"{self.path}: PSARC entry {name!r} was not found"
            ) from exc

    def read(self, name_or_entry: str | PsarcEntry) -> bytes:
        entry = (
            self.get(name_or_entry)
            if isinstance(name_or_entry, str)
            else name_or_entry
        )
        return self._read_raw(
            _RawEntry(
                digest=b"",
                uncompressed_size=entry.uncompressed_size,
                file_offset=entry.file_offset,
                block_index=entry.block_index,
            ),
            0,
            None,
        )

    def read_slice(
        self,
        name_or_entry: str | PsarcEntry,
        offset: int,
        length: int,
    ) -> bytes:
        entry = (
            self.get(name_or_entry)
            if isinstance(name_or_entry, str)
            else name_or_entry
        )
        return self._read_raw(
            _RawEntry(
                digest=b"",
                uncompressed_size=entry.uncompressed_size,
                file_offset=entry.file_offset,
                block_index=entry.block_index,
            ),
            offset,
            length,
        )

    def _read_raw(
        self,
        entry: _RawEntry,
        offset: int,
        length: int | None,
    ) -> bytes:
        if offset < 0 or offset > entry.uncompressed_size:
            raise FormatError(
                f"{self.path}: slice offset {offset} exceeds entry size "
                f"{entry.uncompressed_size}"
            )
        if length is None:
            length = entry.uncompressed_size - offset
        if length < 0 or offset + length > entry.uncompressed_size:
            raise FormatError(
                f"{self.path}: invalid entry slice [{offset}, {offset + length}) "
                f"for {entry.uncompressed_size} bytes"
            )
        if length == 0:
            return b""

        first_block = offset // self.block_size
        last_block = (offset + length - 1) // self.block_size
        physical_offset = entry.file_offset
        logical_remaining = entry.uncompressed_size

        for relative_index in range(first_block):
            table_index = entry.block_index + relative_index
            encoded = self._block_length(table_index)
            raw_length = min(self.block_size, logical_remaining)
            physical_offset += encoded or raw_length
            logical_remaining -= raw_length

        output = bytearray()
        with self._lock:
            self._file.seek(physical_offset)
            for relative_index in range(first_block, last_block + 1):
                table_index = entry.block_index + relative_index
                encoded = self._block_length(table_index)
                block_logical_start = relative_index * self.block_size
                raw_length = min(
                    self.block_size,
                    entry.uncompressed_size - block_logical_start,
                )
                stored_length = encoded or raw_length
                raw = self._read_exact(stored_length)
                if encoded == 0 or encoded == raw_length:
                    decoded = raw
                else:
                    try:
                        decoded = zlib.decompress(raw)
                    except zlib.error as exc:
                        raise FormatError(
                            f"{self.path}: failed to decompress PSARC block "
                            f"{table_index}: {exc}"
                        ) from exc
                if len(decoded) != raw_length:
                    raise FormatError(
                        f"{self.path}: PSARC block {table_index} decoded to "
                        f"{len(decoded)} bytes, expected {raw_length}"
                    )

                take_start = max(offset, block_logical_start) - block_logical_start
                take_end = (
                    min(offset + length, block_logical_start + raw_length)
                    - block_logical_start
                )
                output.extend(decoded[take_start:take_end])

        if len(output) != length:
            raise FormatError(
                f"{self.path}: entry slice produced {len(output)} bytes, "
                f"expected {length}"
            )
        return bytes(output)

    def _block_length(self, index: int) -> int:
        try:
            return self._block_lengths[index]
        except IndexError as exc:
            raise FormatError(
                f"{self.path}: PSARC block index {index} is outside the table"
            ) from exc
