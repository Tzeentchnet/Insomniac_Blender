from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass

from ...core.errors import FormatError
from .formats import NsirsrcFile

RESOURCE_INFO_PACK_MAGIC = b"RESOURCEINFOPCK\x00"
RESOURCE_INFO_PACK_VERSION = 1
STREAM_PACK_MAGIC = b"SPK\0"
STREAM_PACK_VERSION = 2

MAX_PACKED_RESOURCES = 100_000
MAX_STREAM_PACK_NAMES = 100_000
MAX_STREAM_PACK_NAME_LENGTH = 4096
STREAM_PACK_ENTRY_SIZE = 0x54


def _require(
    data: bytes,
    offset: int,
    length: int,
    *,
    label: str,
    purpose: str,
) -> None:
    if (
        offset < 0
        or length < 0
        or offset > len(data)
        or length > len(data) - offset
    ):
        raise FormatError(
            f"{label}: {purpose} range "
            f"[0x{offset:X}, 0x{offset + length:X}) exceeds "
            f"the 0x{len(data):X}-byte container"
        )


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


@dataclass(frozen=True, slots=True)
class PackedResourceRange:
    offset: int
    size: int


class ResourceInfoPack:
    """Bounded view of a RESOURCEINFOPCK metadata bundle."""

    def __init__(self, data: bytes, *, label: str) -> None:
        self.data = data
        self.label = label
        _require(data, 0, 4, label=label, purpose="outer header")
        self.inner_base = _u32(data, 0)
        _require(
            data,
            self.inner_base,
            0x1C,
            label=label,
            purpose="RESOURCEINFOPCK header",
        )
        if (
            data[
                self.inner_base : self.inner_base
                + len(RESOURCE_INFO_PACK_MAGIC)
            ]
            != RESOURCE_INFO_PACK_MAGIC
        ):
            raise FormatError(f"{label}: missing RESOURCEINFOPCK magic")
        version = _u32(data, self.inner_base + 0x10)
        if version != RESOURCE_INFO_PACK_VERSION:
            raise FormatError(
                f"{label}: unsupported RESOURCEINFOPCK version {version}"
            )

        root_pointer = _u32(data, self.inner_base + 0x18)
        root_offset = self._physical(root_pointer)
        _require(
            data,
            root_offset,
            0x0C,
            label=label,
            purpose="resource table header",
        )
        count, table_pointer, resources_end = struct.unpack_from(
            "<3I",
            data,
            root_offset,
        )
        if count > MAX_PACKED_RESOURCES:
            raise FormatError(
                f"{label}: packed resource count {count} exceeds "
                f"{MAX_PACKED_RESOURCES}"
            )
        table_offset = self._physical(table_pointer)
        _require(
            data,
            table_offset,
            count * 4,
            label=label,
            purpose="resource offset table",
        )
        offsets = struct.unpack_from(f"<{count}I", data, table_offset)
        table_end = table_pointer + count * 4
        previous = table_end
        for index, offset in enumerate(offsets):
            if offset < previous or (index > 0 and offset == previous):
                raise FormatError(
                    f"{label}: resource {index} offset 0x{offset:X} "
                    "overlaps the resource table or a previous resource"
                )
            previous = offset
        if offsets and resources_end <= previous:
            raise FormatError(
                f"{label}: resource end 0x{resources_end:X} does not "
                "follow the final resource"
            )
        self._physical(resources_end)

        ranges: list[PackedResourceRange] = []
        for index, offset in enumerate(offsets):
            end = offsets[index + 1] if index + 1 < count else resources_end
            ranges.append(
                PackedResourceRange(
                    offset=self._physical(offset),
                    size=end - offset,
                )
            )
        self.resources = tuple(ranges)

    def _physical(self, virtual_offset: int) -> int:
        physical = self.inner_base + virtual_offset
        if physical < self.inner_base or physical > len(self.data):
            raise FormatError(
                f"{self.label}: virtual offset 0x{virtual_offset:X} "
                "is outside the container"
            )
        return physical

    @property
    def resource_count(self) -> int:
        return len(self.resources)

    def resource(self, index: int) -> NsirsrcFile:
        packed_range = self.resources[index]
        start = packed_range.offset
        end = start + packed_range.size
        return NsirsrcFile(
            self.data[start:end],
            label=f"{self.label} resource {index}",
        )

    def iter_resources(self) -> Iterator[NsirsrcFile]:
        for index in range(self.resource_count):
            yield self.resource(index)


@dataclass(frozen=True, slots=True)
class StreamPackEntry:
    index: int
    name: str
    name_hash: int
    stream_class: int
    primary_size: int
    primary_offset: int
    secondary_size: int
    secondary_offset: int


class StreamPack:
    """Bounded view of an SPK version 2 payload bundle."""

    def __init__(self, data: bytes, *, label: str) -> None:
        self.data = data
        self.label = label
        _require(data, 0, 4, label=label, purpose="outer header")
        self.inner_base = _u32(data, 0)
        _require(
            data,
            self.inner_base,
            0x10,
            label=label,
            purpose="SPK header",
        )
        if data[self.inner_base : self.inner_base + 4] != STREAM_PACK_MAGIC:
            raise FormatError(f"{label}: missing SPK magic")
        version = _u32(data, self.inner_base + 0x04)
        if version != STREAM_PACK_VERSION:
            raise FormatError(f"{label}: unsupported SPK version {version}")

        root_pointer = _u32(data, self.inner_base + 0x0C)
        root_offset = self._physical(root_pointer)
        _require(
            data,
            root_offset,
            0x20,
            label=label,
            purpose="SPK root",
        )
        resource_count = _u32(data, root_offset)
        resource_table_pointer = _u32(data, root_offset + 0x0C)
        name_table_pointer = _u32(data, root_offset + 0x1C)
        if resource_count > MAX_PACKED_RESOURCES:
            raise FormatError(
                f"{label}: stream resource count {resource_count} exceeds "
                f"{MAX_PACKED_RESOURCES}"
            )

        names, name_hashes = self._read_names(name_table_pointer)
        resource_table_offset = self._physical(resource_table_pointer)
        _require(
            data,
            resource_table_offset,
            8 + resource_count * STREAM_PACK_ENTRY_SIZE,
            label=label,
            purpose="stream resource table",
        )
        entries: list[StreamPackEntry] = []
        entries_by_name: dict[str, list[StreamPackEntry]] = {}
        for index in range(resource_count):
            record_offset = (
                resource_table_offset + 8 + index * STREAM_PACK_ENTRY_SIZE
            )
            (
                name_index,
                stream_class,
                stream_count,
                primary_size,
                primary_pointer,
                secondary_size,
                secondary_pointer,
            ) = struct.unpack_from("<7I", data, record_offset)
            if name_index >= len(names):
                raise FormatError(
                    f"{label}: stream resource {index} has invalid "
                    f"name index {name_index}"
                )
            if stream_count != 1:
                raise FormatError(
                    f"{label}: stream resource {index} has unsupported "
                    f"stream count {stream_count}"
                )
            primary_offset = self._stream_offset(
                primary_pointer,
                primary_size,
                purpose=f"stream resource {index} primary payload",
            )
            secondary_offset = self._stream_offset(
                secondary_pointer,
                secondary_size,
                purpose=f"stream resource {index} secondary payload",
            )
            entry = StreamPackEntry(
                index=index,
                name=names[name_index],
                name_hash=name_hashes[name_index],
                stream_class=stream_class,
                primary_size=primary_size,
                primary_offset=primary_offset,
                secondary_size=secondary_size,
                secondary_offset=secondary_offset,
            )
            entries.append(entry)
            entries_by_name.setdefault(entry.name, []).append(entry)
        self.entries = tuple(entries)
        self._entries_by_name = {
            name: tuple(named_entries)
            for name, named_entries in entries_by_name.items()
        }

    def _physical(self, virtual_offset: int) -> int:
        physical = self.inner_base + virtual_offset
        if physical < self.inner_base or physical > len(self.data):
            raise FormatError(
                f"{self.label}: virtual offset 0x{virtual_offset:X} "
                "is outside the stream pack"
            )
        return physical

    def _read_names(
        self,
        table_pointer: int,
    ) -> tuple[tuple[str, ...], tuple[int, ...]]:
        table_offset = self._physical(table_pointer)
        _require(
            self.data,
            table_offset,
            8,
            label=self.label,
            purpose="stream name table header",
        )
        count, records_pointer = struct.unpack_from(
            "<2I",
            self.data,
            table_offset,
        )
        if count > MAX_STREAM_PACK_NAMES:
            raise FormatError(
                f"{self.label}: stream name count {count} exceeds "
                f"{MAX_STREAM_PACK_NAMES}"
            )
        records_offset = self._physical(records_pointer)
        _require(
            self.data,
            records_offset,
            4 + count * 0x0C,
            label=self.label,
            purpose="stream name records",
        )
        names: list[str] = []
        name_hashes: list[int] = []
        for index in range(count):
            record_offset = records_offset + 4 + index * 0x0C
            length, name_pointer, name_hash = struct.unpack_from(
                "<3I",
                self.data,
                record_offset,
            )
            if not 1 <= length <= MAX_STREAM_PACK_NAME_LENGTH:
                raise FormatError(
                    f"{self.label}: stream name {index} has invalid "
                    f"length {length}"
                )
            name_offset = self._physical(name_pointer)
            _require(
                self.data,
                name_offset,
                length,
                label=self.label,
                purpose=f"stream name {index}",
            )
            raw_name = self.data[name_offset : name_offset + length]
            if raw_name[-1] != 0 or b"\0" in raw_name[:-1]:
                raise FormatError(
                    f"{self.label}: stream name {index} is not a single "
                    "null-terminated string"
                )
            try:
                name = raw_name[:-1].decode("ascii")
            except UnicodeDecodeError as exc:
                raise FormatError(
                    f"{self.label}: stream name {index} is not ASCII"
                ) from exc
            names.append(name)
            name_hashes.append(name_hash)
        return tuple(names), tuple(name_hashes)

    def _stream_offset(
        self,
        pointer: int,
        size: int,
        *,
        purpose: str,
    ) -> int:
        if not size:
            if pointer:
                raise FormatError(
                    f"{self.label}: empty {purpose} has nonzero pointer "
                    f"0x{pointer:X}"
                )
            return self.inner_base
        if not pointer:
            raise FormatError(f"{self.label}: {purpose} has a null pointer")
        offset = self._physical(pointer)
        _require(
            self.data,
            offset,
            size,
            label=self.label,
            purpose=purpose,
        )
        return offset

    def entries_named(self, name: str) -> tuple[StreamPackEntry, ...]:
        return self._entries_by_name.get(name, ())

    def primary_data(self, entry: StreamPackEntry) -> bytes:
        start = entry.primary_offset
        return self.data[start : start + entry.primary_size]

    def secondary_data(self, entry: StreamPackEntry) -> bytes:
        start = entry.secondary_offset
        return self.data[start : start + entry.secondary_size]
