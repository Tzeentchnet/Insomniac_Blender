from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from ...core.errors import FormatError

NSIRSRC_MAGIC = b"NSIRSRC\0"
NSIRSRC_VERSION = 7
MAX_DEPENDENCIES = 4096


@dataclass(frozen=True, slots=True)
class ResourceDependency:
    type_id: int
    name: str


@dataclass(frozen=True, slots=True)
class StreamDescriptor:
    primary_size: int
    primary_offset: int
    secondary_size: int
    secondary_offset: int


class NsirsrcFile:
    def __init__(self, data: bytes, *, label: str) -> None:
        self.data = data
        self.label = label
        self.require(0, 0x28, purpose="NSIRSRC header")
        if data[:8] != NSIRSRC_MAGIC:
            raise FormatError(f"{label}: missing NSIRSRC magic")
        version = self.u32(0x08)
        if version != NSIRSRC_VERSION:
            raise FormatError(
                f"{label}: unsupported NSIRSRC version {version}"
            )
        self.data_base = self.u32(0x0C)
        if not 0x28 <= self.data_base <= len(data):
            raise FormatError(
                f"{label}: invalid serialized data base 0x{self.data_base:X}"
            )
        self.require(0x5C, 4, purpose="root resource type")

    @property
    def root_type(self) -> int:
        return self.u32(0x5C)

    @property
    def resource_name(self) -> str:
        name_offset = self.u32(0x28)
        return self.c_string(name_offset, limit=64)

    def require(self, offset: int, length: int, *, purpose: str) -> None:
        if (
            offset < 0
            or length < 0
            or offset > len(self.data)
            or length > len(self.data) - offset
        ):
            raise FormatError(
                f"{self.label}: {purpose} range "
                f"[0x{offset:X}, 0x{offset + length:X}) exceeds "
                f"the 0x{len(self.data):X}-byte resource"
            )

    def u32(self, offset: int) -> int:
        self.require(offset, 4, purpose="32-bit value")
        return struct.unpack_from("<I", self.data, offset)[0]

    def c_string(self, offset: int, *, limit: int) -> str:
        self.require(offset, 1, purpose="string")
        end_limit = min(len(self.data), offset + limit)
        end = self.data.find(b"\0", offset, end_limit)
        if end < 0:
            raise FormatError(
                f"{self.label}: unterminated string at 0x{offset:X}"
            )
        try:
            return self.data[offset:end].decode("ascii")
        except UnicodeDecodeError as exc:
            raise FormatError(
                f"{self.label}: non-ASCII string at 0x{offset:X}"
            ) from exc

    def physical_offset(self, virtual_offset: int) -> int:
        if virtual_offset < 0:
            raise FormatError(
                f"{self.label}: negative virtual offset {virtual_offset}"
            )
        physical = self.data_base + virtual_offset
        if physical > len(self.data):
            raise FormatError(
                f"{self.label}: virtual offset 0x{virtual_offset:X} "
                "is outside the resource"
            )
        return physical

    def dependencies(self) -> tuple[ResourceDependency, ...]:
        self.require(0x7C, 8, purpose="dependency table header")
        count = self.u32(0x7C)
        table_offset = self.u32(0x80)
        if count > MAX_DEPENDENCIES:
            raise FormatError(
                f"{self.label}: dependency count {count} exceeds "
                f"{MAX_DEPENDENCIES}"
            )
        if not count:
            return ()
        if table_offset >= self.data_base:
            raise FormatError(
                f"{self.label}: dependency table offset "
                f"0x{table_offset:X} is outside the metadata"
            )
        self.require(
            table_offset,
            count * 4,
            purpose="dependency pointer table",
        )
        dependencies: list[ResourceDependency] = []
        for index in range(count):
            record_offset = self.u32(table_offset + index * 4)
            if record_offset + 8 > self.data_base:
                raise FormatError(
                    f"{self.label}: dependency {index} record "
                    f"0x{record_offset:X} is outside the metadata"
                )
            dependencies.append(
                ResourceDependency(
                    type_id=self.u32(record_offset),
                    name=self.c_string(record_offset + 4, limit=64),
                )
            )
        return tuple(dependencies)

    def stream_descriptors(self) -> tuple[StreamDescriptor, ...]:
        candidates: list[StreamDescriptor] = []
        for offset in range(0x28, self.data_base - 0x20 + 1, 4):
            scale = struct.unpack_from("<f", self.data, offset)[0]
            if (
                not math.isfinite(scale)
                or not (1.0 / 256.0) <= scale <= 1.0
            ):
                continue
            (
                primary_size,
                primary_offset,
                primary_reserved,
                secondary_size,
                secondary_offset,
                secondary_reserved,
                trailing_reserved,
            ) = struct.unpack_from("<7I", self.data, offset + 4)
            if (
                primary_reserved
                or secondary_reserved
                or trailing_reserved
                or not primary_size
                or not primary_offset
            ):
                continue
            primary_physical = self.data_base + primary_offset
            if primary_physical >= len(self.data):
                continue
            if secondary_size:
                secondary_physical = self.data_base + secondary_offset
                if (
                    not secondary_offset
                    or secondary_physical >= len(self.data)
                ):
                    continue
            candidates.append(
                StreamDescriptor(
                    primary_size=primary_size,
                    primary_offset=primary_offset,
                    secondary_size=secondary_size,
                    secondary_offset=secondary_offset,
                )
            )
        return tuple(candidates)

    def stream_descriptor(self) -> StreamDescriptor:
        candidates = self.stream_descriptors()
        if len(candidates) != 1:
            raise FormatError(
                f"{self.label}: expected one stream descriptor, found "
                f"{len(candidates)}"
            )
        return candidates[0]
