from __future__ import annotations

import math
import struct
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ...core.errors import FormatError
from .formats import NsirsrcFile

COLLISION_ROOT_TYPE = 0x05

HAVOK_MAGIC_0 = 0x57E0E057
HAVOK_MAGIC_1 = 0x10C0C010
HAVOK_PACKFILE_VERSION = 9
HAVOK_LAYOUT = b"\x04\x01\x01\x01"
HAVOK_CONTENTS_VERSION = "hk_2011.3.0-r1"
HAVOK_SECTION_HEADER_OFFSET = 0x40
HAVOK_SECTION_HEADER_SIZE = 0x30
HAVOK_SECTION_TAGS = (
    "__classnames__",
    "__types__",
    "__data__",
)

RIGID_BODY_CLASS = "hkpRigidBody"
COMPRESSED_MESH_CLASS = "hkpBvCompressedMeshShape"
RIGID_BODY_SHAPE_POINTER_OFFSET = 0x10
RIGID_BODY_TRANSFORM_OFFSET = 0xF0
COMPRESSED_MESH_SIZE = 0xC4
COLLISION_SECTION_SIZE = 0x60

MAX_COLLISION_SECTIONS = 100_000
MAX_COLLISION_VERTICES = 10_000_000
MAX_COLLISION_PRIMITIVES = 10_000_000
MAX_COLLISION_DATA_RUNS = 10_000_000
MAX_COLLISION_PALETTE_ENTRIES = 256

_PACKED_X_MASK = (1 << 11) - 1
_PACKED_Y_MASK = (1 << 11) - 1
_PACKED_Z_MASK = (1 << 10) - 1
_SHARED_X_MASK = (1 << 21) - 1
_SHARED_Y_MASK = (1 << 21) - 1
_SHARED_Z_MASK = (1 << 22) - 1

_RUN_DTYPE = np.dtype(
    [
        ("value", "<u4"),
        ("index", "u1"),
        ("count", "u1"),
        ("reserved", "<u2"),
    ]
)


@dataclass(slots=True)
class DecodedCollision:
    name: str
    positions: NDArray[np.float32]
    indices: NDArray[np.int32]
    user_data: NDArray[np.int32]
    collision_filter_info: NDArray[np.uint32]
    collision_filter_palette: tuple[int, ...]
    section_count: int
    primitive_count: int
    quad_count: int


@dataclass(frozen=True, slots=True)
class _HavokSection:
    index: int
    tag: str
    start: int
    local_fixups: int
    global_fixups: int
    virtual_fixups: int
    exports: int
    imports: int
    end: int

    @property
    def payload_end(self) -> int:
        return self.start + self.local_fixups


class _HavokPackfile:
    def __init__(self, data: bytes, *, label: str) -> None:
        self.data = data
        self.label = label
        _require(
            data,
            0,
            HAVOK_SECTION_HEADER_OFFSET,
            label=label,
            purpose="Havok packfile header",
        )
        magic_0, magic_1, user_tag, version = struct.unpack_from(
            "<4I",
            data,
        )
        if (magic_0, magic_1) != (HAVOK_MAGIC_0, HAVOK_MAGIC_1):
            raise FormatError(f"{label}: missing Havok packfile magic")
        if user_tag:
            raise FormatError(
                f"{label}: unsupported Havok user tag 0x{user_tag:X}"
            )
        if version != HAVOK_PACKFILE_VERSION:
            raise FormatError(
                f"{label}: unsupported Havok packfile version {version}"
            )
        if data[0x10:0x14] != HAVOK_LAYOUT:
            raise FormatError(
                f"{label}: unsupported Havok packfile layout "
                f"{data[0x10:0x14].hex()}"
            )

        (
            section_count,
            contents_section,
            contents_offset,
            class_section,
            class_offset,
        ) = struct.unpack_from("<5i", data, 0x14)
        if section_count != len(HAVOK_SECTION_TAGS):
            raise FormatError(
                f"{label}: expected {len(HAVOK_SECTION_TAGS)} Havok "
                f"sections, found {section_count}"
            )
        raw_version = data[0x28:0x38]
        terminator = raw_version.find(b"\0")
        if terminator < 0:
            raise FormatError(
                f"{label}: unterminated Havok contents version"
            )
        try:
            contents_version = raw_version[:terminator].decode("ascii")
        except UnicodeDecodeError as exc:
            raise FormatError(
                f"{label}: non-ASCII Havok contents version"
            ) from exc
        if contents_version != HAVOK_CONTENTS_VERSION:
            raise FormatError(
                f"{label}: unsupported Havok contents version "
                f"{contents_version!r}"
            )

        self.sections = self._read_sections(section_count)
        if not 0 <= contents_section < len(self.sections):
            raise FormatError(
                f"{label}: invalid Havok contents section {contents_section}"
            )
        if not 0 <= class_section < len(self.sections):
            raise FormatError(
                f"{label}: invalid Havok class-name section {class_section}"
            )
        self.contents_section = self.sections[contents_section]
        self.class_section = self.sections[class_section]
        self.contents_offset = contents_offset
        self.contents_class_name = self.class_name(
            class_section,
            class_offset,
        )

        self.local_fixups = self._read_local_fixups(
            self.contents_section,
        )
        self.global_fixups = self._read_global_fixups(
            self.contents_section,
        )
        self.virtual_classes = self._read_virtual_fixups(
            self.contents_section,
        )

    def _read_sections(self, count: int) -> tuple[_HavokSection, ...]:
        _require(
            self.data,
            HAVOK_SECTION_HEADER_OFFSET,
            count * HAVOK_SECTION_HEADER_SIZE,
            label=self.label,
            purpose="Havok section headers",
        )
        sections: list[_HavokSection] = []
        previous_end = (
            HAVOK_SECTION_HEADER_OFFSET
            + count * HAVOK_SECTION_HEADER_SIZE
        )
        for index in range(count):
            offset = (
                HAVOK_SECTION_HEADER_OFFSET
                + index * HAVOK_SECTION_HEADER_SIZE
            )
            raw_tag = self.data[offset : offset + 0x14]
            terminator = raw_tag.find(b"\0")
            if terminator < 0:
                raise FormatError(
                    f"{self.label}: Havok section {index} tag is "
                    "unterminated"
                )
            try:
                tag = raw_tag[:terminator].decode("ascii")
            except UnicodeDecodeError as exc:
                raise FormatError(
                    f"{self.label}: Havok section {index} tag is not ASCII"
                ) from exc
            if tag != HAVOK_SECTION_TAGS[index]:
                raise FormatError(
                    f"{self.label}: Havok section {index} is {tag!r}, "
                    f"expected {HAVOK_SECTION_TAGS[index]!r}"
                )
            (
                start,
                local_fixups,
                global_fixups,
                virtual_fixups,
                exports,
                imports,
                end,
            ) = struct.unpack_from("<7I", self.data, offset + 0x14)
            offsets = (
                local_fixups,
                global_fixups,
                virtual_fixups,
                exports,
                imports,
                end,
            )
            if tuple(sorted(offsets)) != offsets:
                raise FormatError(
                    f"{self.label}: Havok section {tag!r} ranges are "
                    "unordered"
                )
            _require(
                self.data,
                start,
                end,
                label=self.label,
                purpose=f"Havok section {tag!r}",
            )
            if start < previous_end:
                raise FormatError(
                    f"{self.label}: Havok section {tag!r} overlaps a "
                    "previous section or header"
                )
            previous_end = start + end
            sections.append(
                _HavokSection(
                    index=index,
                    tag=tag,
                    start=start,
                    local_fixups=local_fixups,
                    global_fixups=global_fixups,
                    virtual_fixups=virtual_fixups,
                    exports=exports,
                    imports=imports,
                    end=end,
                )
            )
        return tuple(sections)

    def _read_local_fixups(
        self,
        section: _HavokSection,
    ) -> dict[int, int]:
        result: dict[int, int] = {}
        start = section.start + section.local_fixups
        end = section.start + section.global_fixups
        cursor = start
        while cursor + 8 <= end:
            source, destination = struct.unpack_from("<2i", self.data, cursor)
            if source < 0:
                break
            self._validate_fixup_source(section, source)
            self._validate_fixup_destination(section, destination)
            if source in result:
                raise FormatError(
                    f"{self.label}: duplicate Havok local fixup at "
                    f"0x{source:X}"
                )
            if _u32(self.data, section.start + source):
                raise FormatError(
                    f"{self.label}: Havok local pointer at 0x{source:X} "
                    "is already populated"
                )
            result[source] = destination
            cursor += 8
        self._require_fixup_padding(cursor, end, "local")
        return result

    def _read_global_fixups(
        self,
        section: _HavokSection,
    ) -> dict[int, tuple[int, int]]:
        result: dict[int, tuple[int, int]] = {}
        start = section.start + section.global_fixups
        end = section.start + section.virtual_fixups
        cursor = start
        while cursor + 12 <= end:
            source, section_index, destination = struct.unpack_from(
                "<3i",
                self.data,
                cursor,
            )
            if source < 0:
                break
            self._validate_fixup_source(section, source)
            if not 0 <= section_index < len(self.sections):
                raise FormatError(
                    f"{self.label}: Havok global fixup at 0x{source:X} "
                    f"uses invalid section {section_index}"
                )
            destination_section = self.sections[section_index]
            self._validate_fixup_destination(
                destination_section,
                destination,
            )
            if source in result:
                raise FormatError(
                    f"{self.label}: duplicate Havok global fixup at "
                    f"0x{source:X}"
                )
            if _u32(self.data, section.start + source):
                raise FormatError(
                    f"{self.label}: Havok global pointer at 0x{source:X} "
                    "is already populated"
                )
            result[source] = (section_index, destination)
            cursor += 12
        self._require_fixup_padding(cursor, end, "global")
        return result

    def _read_virtual_fixups(
        self,
        section: _HavokSection,
    ) -> dict[int, str]:
        result: dict[int, str] = {}
        start = section.start + section.virtual_fixups
        end = section.start + section.exports
        cursor = start
        while cursor + 12 <= end:
            source, section_index, class_offset = struct.unpack_from(
                "<3i",
                self.data,
                cursor,
            )
            if source < 0:
                break
            self._validate_fixup_destination(section, source)
            class_name = self.class_name(section_index, class_offset)
            if source in result:
                raise FormatError(
                    f"{self.label}: duplicate Havok virtual fixup at "
                    f"0x{source:X}"
                )
            result[source] = class_name
            cursor += 12
        self._require_fixup_padding(cursor, end, "virtual")
        return result

    def _require_fixup_padding(
        self,
        start: int,
        end: int,
        kind: str,
    ) -> None:
        if any(value != 0xFF for value in self.data[start:end]):
            raise FormatError(
                f"{self.label}: Havok {kind} fixup table has invalid "
                "padding"
            )

    def _validate_fixup_source(
        self,
        section: _HavokSection,
        source: int,
    ) -> None:
        if source < 0 or source > section.local_fixups - 4:
            raise FormatError(
                f"{self.label}: Havok fixup source 0x{source:X} is "
                f"outside section {section.tag!r}"
            )

    def _validate_fixup_destination(
        self,
        section: _HavokSection,
        destination: int,
    ) -> None:
        if destination < 0 or destination >= section.local_fixups:
            raise FormatError(
                f"{self.label}: Havok fixup destination "
                f"0x{destination:X} is outside section {section.tag!r}"
            )

    def class_name(self, section_index: int, offset: int) -> str:
        if not 0 <= section_index < len(self.sections):
            raise FormatError(
                f"{self.label}: invalid Havok class-name section "
                f"{section_index}"
            )
        section = self.sections[section_index]
        if not 0 <= offset < section.local_fixups:
            raise FormatError(
                f"{self.label}: Havok class-name offset 0x{offset:X} "
                f"is outside section {section.tag!r}"
            )
        start = section.start + offset
        end = self.data.find(b"\0", start, section.payload_end)
        if end < 0:
            raise FormatError(
                f"{self.label}: unterminated Havok class name at "
                f"0x{offset:X}"
            )
        try:
            name = self.data[start:end].decode("ascii")
        except UnicodeDecodeError as exc:
            raise FormatError(
                f"{self.label}: non-ASCII Havok class name at "
                f"0x{offset:X}"
            ) from exc
        if not name:
            raise FormatError(
                f"{self.label}: empty Havok class name at 0x{offset:X}"
            )
        return name

    def require_data(
        self,
        relative_offset: int,
        length: int,
        *,
        purpose: str,
    ) -> int:
        section = self.contents_section
        if (
            relative_offset < 0
            or length < 0
            or relative_offset > section.local_fixups
            or length > section.local_fixups - relative_offset
        ):
            raise FormatError(
                f"{self.label}: {purpose} range "
                f"[0x{relative_offset:X}, "
                f"0x{relative_offset + length:X}) exceeds the Havok "
                "data payload"
            )
        return section.start + relative_offset

    def array(
        self,
        object_offset: int,
        field_offset: int,
        *,
        dtype: np.dtype | str,
        stride: int,
        maximum: int,
        purpose: str,
    ) -> np.ndarray:
        header_offset = object_offset + field_offset
        physical = self.require_data(
            header_offset,
            12,
            purpose=f"{purpose} header",
        )
        pointer, count, capacity = struct.unpack_from(
            "<3I",
            self.data,
            physical,
        )
        if pointer:
            raise FormatError(
                f"{self.label}: {purpose} pointer is already populated"
            )
        if not 0 < count <= maximum:
            raise FormatError(
                f"{self.label}: {purpose} count {count} is outside "
                f"1..{maximum}"
            )
        if capacity != 0x80000000 | count:
            raise FormatError(
                f"{self.label}: {purpose} capacity "
                f"0x{capacity:X} does not match count {count}"
            )
        try:
            target = self.local_fixups[header_offset]
        except KeyError as exc:
            raise FormatError(
                f"{self.label}: {purpose} has no local pointer fixup"
            ) from exc
        offset = self.require_data(
            target,
            count * stride,
            purpose=purpose,
        )
        return np.frombuffer(
            self.data,
            dtype=dtype,
            count=count,
            offset=offset,
        )

    def empty_array(
        self,
        object_offset: int,
        field_offset: int,
        *,
        purpose: str,
    ) -> None:
        header_offset = object_offset + field_offset
        physical = self.require_data(
            header_offset,
            12,
            purpose=f"{purpose} header",
        )
        pointer, count, capacity = struct.unpack_from(
            "<3I",
            self.data,
            physical,
        )
        if (pointer, count, capacity) != (0, 0, 0x80000000):
            raise FormatError(
                f"{self.label}: unsupported nonempty {purpose}"
            )
        if header_offset in self.local_fixups:
            raise FormatError(
                f"{self.label}: empty {purpose} has a pointer fixup"
            )


def decode_col(data: bytes, *, label: str) -> DecodedCollision:
    resource = NsirsrcFile(data, label=label)
    if resource.root_type != COLLISION_ROOT_TYPE:
        raise FormatError(
            f"{label}: root type 0x{resource.root_type:X} is not a "
            "collision container"
        )
    packfile = _HavokPackfile(
        data[resource.data_base :],
        label=label,
    )
    return _decode_compressed_mesh(
        packfile,
        name=resource.resource_name,
    )


def _decode_compressed_mesh(
    packfile: _HavokPackfile,
    *,
    name: str,
) -> DecodedCollision:
    data_section = packfile.contents_section
    rigid_body_offset = packfile.contents_offset
    if data_section.index != 2:
        raise FormatError(
            f"{packfile.label}: Havok contents are not in the data section"
        )
    if packfile.contents_class_name != RIGID_BODY_CLASS:
        raise FormatError(
            f"{packfile.label}: Havok contents class is "
            f"{packfile.contents_class_name!r}, expected "
            f"{RIGID_BODY_CLASS!r}"
        )
    if (
        packfile.virtual_classes.get(rigid_body_offset)
        != RIGID_BODY_CLASS
    ):
        raise FormatError(
            f"{packfile.label}: Havok rigid body has no matching "
            "virtual-class fixup"
        )
    transform_offset = packfile.require_data(
        rigid_body_offset + RIGID_BODY_TRANSFORM_OFFSET,
        0x40,
        purpose="Havok rigid-body transform",
    )
    transform = struct.unpack_from(
        "<16f",
        packfile.data,
        transform_offset,
    )
    expected_transform = (
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )
    if transform != expected_transform:
        raise FormatError(
            f"{packfile.label}: unsupported non-identity Havok rigid-body "
            "transform"
        )

    shape_pointer = rigid_body_offset + RIGID_BODY_SHAPE_POINTER_OFFSET
    try:
        shape_section, shape_offset = packfile.global_fixups[shape_pointer]
    except KeyError as exc:
        raise FormatError(
            f"{packfile.label}: Havok rigid body has no shape fixup"
        ) from exc
    if shape_section != data_section.index:
        raise FormatError(
            f"{packfile.label}: Havok shape is outside the data section"
        )
    if (
        packfile.virtual_classes.get(shape_offset)
        != COMPRESSED_MESH_CLASS
    ):
        raise FormatError(
            f"{packfile.label}: Havok rigid body does not own a "
            f"{COMPRESSED_MESH_CLASS}"
        )
    shape_physical = packfile.require_data(
        shape_offset,
        COMPRESSED_MESH_SIZE,
        purpose="Havok compressed mesh shape",
    )
    convex_radius = struct.unpack_from(
        "<f",
        packfile.data,
        shape_physical + 0x18,
    )[0]
    if not math.isfinite(convex_radius) or convex_radius <= 0.0:
        raise FormatError(
            f"{packfile.label}: invalid Havok convex radius "
            f"{convex_radius}"
        )
    shape_modes = packfile.data[
        shape_physical + 0x1C : shape_physical + 0x20
    ]
    if shape_modes != b"\x06\x01\x01\x00":
        raise FormatError(
            f"{packfile.label}: unsupported Havok compressed-mesh modes "
            f"{shape_modes.hex()}"
        )

    palette = packfile.array(
        shape_offset,
        0x20,
        dtype="<u4",
        stride=4,
        maximum=MAX_COLLISION_PALETTE_ENTRIES,
        purpose="collision-filter palette",
    )
    packfile.empty_array(
        shape_offset,
        0x2C,
        purpose="collision user-data palette",
    )
    root_nodes = packfile.array(
        shape_offset,
        0x40,
        dtype=np.dtype(("V", 5)),
        stride=5,
        maximum=MAX_COLLISION_PRIMITIVES,
        purpose="compressed-mesh root nodes",
    )
    domain_min = _vector3(
        packfile,
        shape_offset + 0x50,
        purpose="compressed-mesh domain minimum",
    )
    domain_max = _vector3(
        packfile,
        shape_offset + 0x60,
        purpose="compressed-mesh domain maximum",
    )
    if np.any(domain_min >= domain_max):
        raise FormatError(
            f"{packfile.label}: invalid compressed-mesh domain"
        )
    header_offset = packfile.require_data(
        shape_offset + 0x70,
        12,
        purpose="compressed-mesh key header",
    )
    primitive_key_count, bits_per_key, max_key_value = struct.unpack_from(
        "<3I",
        packfile.data,
        header_offset,
    )
    if not 0 < primitive_key_count <= MAX_COLLISION_PRIMITIVES * 2:
        raise FormatError(
            f"{packfile.label}: invalid collision key count "
            f"{primitive_key_count}"
        )
    if (
        not 1 <= bits_per_key <= 31
        or max_key_value >= 1 << bits_per_key
    ):
        raise FormatError(
            f"{packfile.label}: invalid compressed-mesh key range"
        )

    sections = packfile.array(
        shape_offset,
        0x7C,
        dtype=np.dtype(("V", COLLISION_SECTION_SIZE)),
        stride=COLLISION_SECTION_SIZE,
        maximum=MAX_COLLISION_SECTIONS,
        purpose="compressed-mesh sections",
    )
    primitives = packfile.array(
        shape_offset,
        0x88,
        dtype=np.dtype((np.uint8, 4)),
        stride=4,
        maximum=MAX_COLLISION_PRIMITIVES,
        purpose="compressed-mesh primitives",
    )
    shared_indices = packfile.array(
        shape_offset,
        0x94,
        dtype="<u2",
        stride=2,
        maximum=MAX_COLLISION_VERTICES,
        purpose="compressed-mesh shared-vertex indices",
    )
    packed_vertices = packfile.array(
        shape_offset,
        0xA0,
        dtype="<u4",
        stride=4,
        maximum=MAX_COLLISION_VERTICES,
        purpose="compressed-mesh packed vertices",
    )
    shared_vertices_raw = packfile.array(
        shape_offset,
        0xAC,
        dtype="<u8",
        stride=8,
        maximum=MAX_COLLISION_VERTICES,
        purpose="compressed-mesh shared vertices",
    )
    data_runs = packfile.array(
        shape_offset,
        0xB8,
        dtype=_RUN_DTYPE,
        stride=_RUN_DTYPE.itemsize,
        maximum=MAX_COLLISION_DATA_RUNS,
        purpose="compressed-mesh primitive-data runs",
    )
    if np.any(data_runs["reserved"]):
        raise FormatError(
            f"{packfile.label}: collision data run has nonzero "
            "reserved bytes"
        )

    shared_vertices = _decode_shared_vertices(
        shared_vertices_raw,
        domain_min=domain_min,
        domain_max=domain_max,
    )
    output_vertex_count = len(packed_vertices) + len(shared_indices)
    if output_vertex_count > MAX_COLLISION_VERTICES:
        raise FormatError(
            f"{packfile.label}: decoded collision vertex count "
            f"{output_vertex_count} exceeds {MAX_COLLISION_VERTICES}"
        )
    positions = np.empty(
        (output_vertex_count, 3),
        dtype=np.float32,
    )
    indices = np.empty(
        (primitive_key_count, 3),
        dtype=np.int32,
    )
    user_data = np.empty(primitive_key_count, dtype=np.int32)
    collision_filter_info = np.empty(
        primitive_key_count,
        dtype=np.uint32,
    )

    sections_target = packfile.local_fixups[shape_offset + 0x7C]
    packed_cursor = 0
    shared_cursor = 0
    primitive_cursor = 0
    vertex_cursor = 0
    face_cursor = 0
    quad_count = 0
    for section_index in range(len(sections)):
        section_offset = (
            sections_target + section_index * COLLISION_SECTION_SIZE
        )
        _validate_section_nodes(
            packfile,
            section_offset=section_offset,
            root_node_count=len(root_nodes),
            section_index=section_index,
        )
        (
            local_positions,
            section_primitives,
            section_data,
            section_quad_count,
            packed_count,
            shared_count,
            primitive_count,
        ) = _decode_section(
            packfile,
            section_offset=section_offset,
            section_index=section_index,
            palette=palette,
            shared_vertices=shared_vertices,
            shared_indices=shared_indices,
            packed_vertices=packed_vertices,
            primitives=primitives,
            data_runs=data_runs,
            expected_packed=packed_cursor,
            expected_shared=shared_cursor,
            expected_primitive=primitive_cursor,
        )
        local_vertex_count = len(local_positions)
        local_face_count = primitive_count + section_quad_count
        positions[
            vertex_cursor : vertex_cursor + local_vertex_count
        ] = local_positions

        first_faces = section_primitives[:, :3].astype(
            np.int32,
            copy=False,
        )
        quad_mask = section_primitives[:, 2] != section_primitives[:, 3]
        second_faces = np.column_stack(
            (
                section_primitives[quad_mask, 0],
                section_primitives[quad_mask, 2],
                section_primitives[quad_mask, 3],
            )
        ).astype(np.int32)
        indices[
            face_cursor : face_cursor + primitive_count
        ] = first_faces + vertex_cursor
        indices[
            face_cursor + primitive_count : face_cursor + local_face_count
        ] = second_faces + vertex_cursor

        face_data = np.concatenate(
            (section_data, section_data[quad_mask])
        )
        palette_indices = face_data & 0xFF
        user_data[
            face_cursor : face_cursor + local_face_count
        ] = (face_data >> 8).astype(np.int32)
        collision_filter_info[
            face_cursor : face_cursor + local_face_count
        ] = palette[palette_indices]

        packed_cursor += packed_count
        shared_cursor += shared_count
        primitive_cursor += primitive_count
        vertex_cursor += local_vertex_count
        face_cursor += local_face_count
        quad_count += section_quad_count

    if packed_cursor != len(packed_vertices):
        raise FormatError(
            f"{packfile.label}: collision sections cover "
            f"{packed_cursor} of {len(packed_vertices)} packed vertices"
        )
    if shared_cursor != len(shared_indices):
        raise FormatError(
            f"{packfile.label}: collision sections cover "
            f"{shared_cursor} of {len(shared_indices)} shared indices"
        )
    if primitive_cursor != len(primitives):
        raise FormatError(
            f"{packfile.label}: collision sections cover "
            f"{primitive_cursor} of {len(primitives)} primitives"
        )
    if vertex_cursor != output_vertex_count:
        raise FormatError(
            f"{packfile.label}: decoded collision vertex count is "
            "inconsistent"
        )
    if face_cursor != primitive_key_count:
        raise FormatError(
            f"{packfile.label}: decoded {face_cursor} collision faces, "
            f"expected {primitive_key_count}"
        )
    return DecodedCollision(
        name=name,
        positions=positions,
        indices=indices,
        user_data=user_data,
        collision_filter_info=collision_filter_info,
        collision_filter_palette=tuple(int(value) for value in palette),
        section_count=len(sections),
        primitive_count=len(primitives),
        quad_count=quad_count,
    )


def _validate_section_nodes(
    packfile: _HavokPackfile,
    *,
    section_offset: int,
    root_node_count: int,
    section_index: int,
) -> None:
    packfile.array(
        section_offset,
        0,
        dtype="<u4",
        stride=4,
        maximum=MAX_COLLISION_PRIMITIVES,
        purpose=f"collision section {section_index} nodes",
    )
    physical = packfile.require_data(
        section_offset + 0x5A,
        6,
        purpose=f"collision section {section_index} footer",
    )
    leaf_index, page, flags, layer_data, unused = struct.unpack_from(
        "<H4B",
        packfile.data,
        physical,
    )
    if leaf_index >= root_node_count:
        raise FormatError(
            f"{packfile.label}: collision section {section_index} leaf "
            f"{leaf_index} exceeds {root_node_count} root nodes"
        )
    if page or flags or layer_data or unused:
        raise FormatError(
            f"{packfile.label}: collision section {section_index} has "
            "unsupported footer flags"
        )


def _decode_section(
    packfile: _HavokPackfile,
    *,
    section_offset: int,
    section_index: int,
    palette: NDArray[np.uint32],
    shared_vertices: NDArray[np.float64],
    shared_indices: NDArray[np.uint16],
    packed_vertices: NDArray[np.uint32],
    primitives: NDArray[np.uint8],
    data_runs: np.ndarray,
    expected_packed: int,
    expected_shared: int,
    expected_primitive: int,
) -> tuple[
    NDArray[np.float32],
    NDArray[np.uint8],
    NDArray[np.uint32],
    int,
    int,
    int,
    int,
]:
    physical = packfile.require_data(
        section_offset,
        COLLISION_SECTION_SIZE,
        purpose=f"collision section {section_index}",
    )
    domain_min = _physical_vector3(
        packfile,
        physical + 0x10,
        purpose=f"collision section {section_index} minimum",
    )
    domain_max = _physical_vector3(
        packfile,
        physical + 0x20,
        purpose=f"collision section {section_index} maximum",
    )
    if np.any(domain_min >= domain_max):
        raise FormatError(
            f"{packfile.label}: collision section {section_index} has "
            "an invalid domain"
        )
    first_packed = _u32(packfile.data, physical + 0x48)
    shared_data, primitive_data, run_data = struct.unpack_from(
        "<3I",
        packfile.data,
        physical + 0x4C,
    )
    packed_count, shared_count = struct.unpack_from(
        "<2B",
        packfile.data,
        physical + 0x58,
    )
    first_shared = shared_data >> 8
    first_primitive = primitive_data >> 8
    primitive_count = primitive_data & 0xFF
    first_run = run_data >> 8
    run_count = run_data & 0xFF
    if shared_data & 0xFF != packed_count:
        raise FormatError(
            f"{packfile.label}: collision section {section_index} packed "
            "vertex counts disagree"
        )
    if (
        first_packed,
        first_shared,
        first_primitive,
    ) != (
        expected_packed,
        expected_shared,
        expected_primitive,
    ):
        raise FormatError(
            f"{packfile.label}: collision section {section_index} ranges "
            "are not contiguous"
        )
    if not primitive_count:
        raise FormatError(
            f"{packfile.label}: collision section {section_index} has no "
            "primitives"
        )
    if first_packed + packed_count > len(packed_vertices):
        raise FormatError(
            f"{packfile.label}: collision section {section_index} packed "
            "vertices exceed the global array"
        )
    if first_shared + shared_count > len(shared_indices):
        raise FormatError(
            f"{packfile.label}: collision section {section_index} shared "
            "indices exceed the global array"
        )
    if first_primitive + primitive_count > len(primitives):
        raise FormatError(
            f"{packfile.label}: collision section {section_index} "
            "primitives exceed the global array"
        )
    if not run_count or first_run + run_count > len(data_runs):
        raise FormatError(
            f"{packfile.label}: collision section {section_index} data "
            "runs exceed the global array"
        )

    local_positions = np.empty(
        (packed_count + shared_count, 3),
        dtype=np.float32,
    )
    if packed_count:
        codec_offset = _physical_vector3(
            packfile,
            physical + 0x30,
            purpose=f"collision section {section_index} codec offset",
        )
        codec_scale = _physical_vector3(
            packfile,
            physical + 0x3C,
            purpose=f"collision section {section_index} codec scale",
        )
        if np.any(codec_scale < 0.0):
            raise FormatError(
                f"{packfile.label}: collision section {section_index} "
                "has invalid codec scale"
            )
        packed = packed_vertices[
            first_packed : first_packed + packed_count
        ]
        quantized = np.column_stack(
            (
                packed & _PACKED_X_MASK,
                (packed >> 11) & _PACKED_Y_MASK,
                (packed >> 22) & _PACKED_Z_MASK,
            )
        ).astype(np.float64)
        local_positions[:packed_count] = (
            codec_offset + quantized * codec_scale
        )

    section_shared_indices = shared_indices[
        first_shared : first_shared + shared_count
    ]
    if (
        shared_count
        and int(section_shared_indices.max()) >= len(shared_vertices)
    ):
        raise FormatError(
            f"{packfile.label}: collision section {section_index} shared "
            "vertex index exceeds the global array"
        )
    local_positions[packed_count:] = shared_vertices[
        section_shared_indices
    ]
    if not len(local_positions):
        raise FormatError(
            f"{packfile.label}: collision section {section_index} has no "
            "vertices"
        )
    if (
        np.any(local_positions < domain_min - 1e-3)
        or np.any(local_positions > domain_max + 1e-3)
    ):
        raise FormatError(
            f"{packfile.label}: collision section {section_index} vertices "
            "exceed its domain"
        )

    section_primitives = primitives[
        first_primitive : first_primitive + primitive_count
    ]
    if int(section_primitives.max()) >= len(local_positions):
        raise FormatError(
            f"{packfile.label}: collision section {section_index} has an "
            "out-of-range primitive index"
        )
    section_data = np.empty(primitive_count, dtype=np.uint32)
    cursor = 0
    for run in data_runs[first_run : first_run + run_count]:
        run_index = int(run["index"])
        count = int(run["count"])
        if run_index != cursor or not count:
            raise FormatError(
                f"{packfile.label}: collision section {section_index} "
                "has discontinuous primitive data"
            )
        end = cursor + count
        if end > primitive_count:
            raise FormatError(
                f"{packfile.label}: collision section {section_index} "
                "primitive data exceeds its primitive count"
            )
        value = int(run["value"])
        palette_index = value & 0xFF
        if palette_index >= len(palette):
            raise FormatError(
                f"{packfile.label}: collision section {section_index} "
                f"uses filter palette index {palette_index}"
            )
        section_data[cursor:end] = value
        cursor = end
    if cursor != primitive_count:
        raise FormatError(
            f"{packfile.label}: collision section {section_index} "
            "primitive data is incomplete"
        )
    quad_count = int(
        np.count_nonzero(
            section_primitives[:, 2] != section_primitives[:, 3]
        )
    )
    return (
        local_positions,
        section_primitives,
        section_data,
        quad_count,
        packed_count,
        shared_count,
        primitive_count,
    )


def _decode_shared_vertices(
    packed: NDArray[np.uint64],
    *,
    domain_min: NDArray[np.float64],
    domain_max: NDArray[np.float64],
) -> NDArray[np.float64]:
    quantized = np.column_stack(
        (
            packed & np.uint64(_SHARED_X_MASK),
            (packed >> np.uint64(21)) & np.uint64(_SHARED_Y_MASK),
            (packed >> np.uint64(42)) & np.uint64(_SHARED_Z_MASK),
        )
    ).astype(np.float64)
    scale = (domain_max - domain_min) / np.array(
        (
            _SHARED_X_MASK,
            _SHARED_Y_MASK,
            _SHARED_Z_MASK,
        ),
        dtype=np.float64,
    )
    return domain_min + quantized * scale


def _vector3(
    packfile: _HavokPackfile,
    relative_offset: int,
    *,
    purpose: str,
) -> NDArray[np.float64]:
    physical = packfile.require_data(
        relative_offset,
        12,
        purpose=purpose,
    )
    return _physical_vector3(
        packfile,
        physical,
        purpose=purpose,
    )


def _physical_vector3(
    packfile: _HavokPackfile,
    physical_offset: int,
    *,
    purpose: str,
) -> NDArray[np.float64]:
    values = np.array(
        struct.unpack_from("<3f", packfile.data, physical_offset),
        dtype=np.float64,
    )
    if not np.all(np.isfinite(values)):
        raise FormatError(
            f"{packfile.label}: {purpose} contains non-finite values"
        )
    return values


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
            f"[0x{offset:X}, 0x{offset + length:X}) exceeds the "
            f"0x{len(data):X}-byte container"
        )


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]
