from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Final

from .errors import FormatError


_FORMATS: Final[dict[str, str]] = {
    "u8": "B",
    "i8": "b",
    "u16": "H",
    "i16": "h",
    "u32": "I",
    "i32": "i",
    "u64": "Q",
    "i64": "q",
    "f32": "f",
}


@dataclass(frozen=True, slots=True)
class Section:
    id: int
    offset: int
    count: int
    length: int


class BinaryView:
    """Bounds-checked random access over an immutable byte buffer."""

    __slots__ = ("_data", "endian", "label")

    def __init__(
        self,
        data: bytes | bytearray | memoryview,
        *,
        endian: str = ">",
        label: str = "buffer",
    ) -> None:
        if endian not in (">", "<"):
            raise ValueError(f"Unsupported byte order {endian!r}")
        self._data = memoryview(data)
        self.endian = endian
        self.label = label

    @property
    def data(self) -> memoryview:
        return self._data

    @property
    def size(self) -> int:
        return len(self._data)

    def require(self, offset: int, length: int, *, purpose: str = "read") -> None:
        if offset < 0 or length < 0 or offset + length > self.size:
            raise FormatError(
                f"{self.label}: {purpose} [{offset:#x}, {offset + length:#x}) "
                f"is outside a {self.size:#x}-byte buffer"
            )

    def bytes(self, offset: int, length: int) -> bytes:
        self.require(offset, length)
        return self._data[offset : offset + length].tobytes()

    def view(self, offset: int, length: int) -> memoryview:
        self.require(offset, length)
        return self._data[offset : offset + length]

    def unpack(self, kind: str, offset: int):
        try:
            fmt = _FORMATS[kind]
        except KeyError as exc:
            raise ValueError(f"Unknown binary scalar {kind!r}") from exc
        size = struct.calcsize(fmt)
        self.require(offset, size, purpose=f"{kind} read")
        return struct.unpack_from(self.endian + fmt, self._data, offset)[0]

    def u8(self, offset: int) -> int:
        return self.unpack("u8", offset)

    def i8(self, offset: int) -> int:
        return self.unpack("i8", offset)

    def u16(self, offset: int) -> int:
        return self.unpack("u16", offset)

    def i16(self, offset: int) -> int:
        return self.unpack("i16", offset)

    def u32(self, offset: int) -> int:
        return self.unpack("u32", offset)

    def i32(self, offset: int) -> int:
        return self.unpack("i32", offset)

    def u64(self, offset: int) -> int:
        return self.unpack("u64", offset)

    def f32(self, offset: int) -> float:
        return self.unpack("f32", offset)

    def vec3(self, offset: int) -> tuple[float, float, float]:
        self.require(offset, 12, purpose="vec3 read")
        return struct.unpack_from(self.endian + "3f", self._data, offset)

    def cstring(self, offset: int, *, max_length: int = 1 << 20) -> str:
        self.require(offset, 1, purpose="string read")
        end_limit = min(self.size, offset + max_length)
        end = self._data[offset:end_limit].tobytes().find(b"\0")
        if end < 0:
            raise FormatError(
                f"{self.label}: unterminated string at {offset:#x} "
                f"within {end_limit - offset} bytes"
            )
        return self._data[offset : offset + end].tobytes().decode(
            "utf-8", errors="replace"
        )


class IghwFile(BinaryView):
    """Reader for Insomniac's sectioned IGHW container."""

    MAGIC_BIG = b"IGHW"
    MAGIC_LITTLE = b"WHGI"
    MAX_SECTIONS = 1_000_000

    __slots__ = ("version", "sections", "_by_id")

    def __init__(
        self,
        data: bytes | bytearray | memoryview,
        *,
        label: str = "IGHW",
    ) -> None:
        raw = memoryview(data)
        if len(raw) < 16:
            raise FormatError(f"{label}: file is too short for an IGHW header")
        magic = raw[:4].tobytes()
        if magic == self.MAGIC_BIG:
            endian = ">"
        elif magic == self.MAGIC_LITTLE:
            endian = "<"
        else:
            raise FormatError(f"{label}: bad IGHW magic {magic!r}")

        super().__init__(raw, endian=endian, label=label)
        major = self.u16(4)
        minor = self.u16(6)
        self.version = (major, minor)
        section_count = self.u32(8)
        if section_count > self.MAX_SECTIONS:
            raise FormatError(
                f"{label}: unreasonable section count {section_count}"
            )

        if self.version == (1, 1):
            table_offset = 0x20
        elif self.version == (0, 2):
            table_offset = 0x10
        else:
            raise FormatError(
                f"{label}: unsupported IGHW version {major}.{minor}"
            )

        self.require(
            table_offset,
            section_count * 16,
            purpose="section table read",
        )
        sections: list[Section] = []
        by_id: dict[int, list[Section]] = {}
        for index in range(section_count):
            offset = table_offset + index * 16
            section = Section(
                id=self.u32(offset),
                offset=self.u32(offset + 4),
                count=self.u32(offset + 8) & ~0x1000_0000,
                length=self.u32(offset + 12),
            )
            sections.append(section)
            by_id.setdefault(section.id, []).append(section)
        self.sections = tuple(sections)
        self._by_id = by_id

    def section(self, section_id: int) -> Section | None:
        matches = self._by_id.get(section_id)
        return matches[0] if matches else None

    def sections_for(self, section_id: int) -> tuple[Section, ...]:
        return tuple(self._by_id.get(section_id, ()))

    def require_section(self, section_id: int) -> Section:
        section = self.section(section_id)
        if section is None:
            raise FormatError(
                f"{self.label}: required section 0x{section_id:X} is missing"
            )
        return section
