from __future__ import annotations

import logging
import math
from pathlib import PurePosixPath

import numpy as np

from ...core.binary import IghwFile
from ...core.collision import CollisionInstance, CollisionMesh, CollisionScene
from ...core.errors import FormatError

LOGGER = logging.getLogger(__name__)

SECT_LEVEL_COLLISION = 0x2900

MAX_COLLISION_RECORDS = 1_000_000
MAX_COLLISION_VERTICES = 512
MAX_COLLISION_FACES = 65_535
MAX_COLLISION_PRIMITIVES = 65_535

_INSTANCE_STRIDE = 0x80
_INSTANCE_FLAG_WORLD = 0x00
_INSTANCE_FLAG_TIE = 0x20
_PRIMITIVE_KINDS = (4, 5, 6, 8)
_PRIMITIVE_BATCH_SIZES = {
    4: 0x130,
    5: 0x80,
    6: 0xB0,
    8: 0xB0,
}
_BOX_SIGNS = np.asarray(
    (
        (-1, -1, -1),
        (1, -1, -1),
        (1, 1, -1),
        (-1, 1, -1),
        (-1, -1, 1),
        (1, -1, 1),
        (1, 1, 1),
        (-1, 1, 1),
    ),
    dtype=np.float32,
)
_BOX_INDICES = np.asarray(
    (
        (0, 3, 2),
        (0, 2, 1),
        (4, 5, 6),
        (4, 6, 7),
        (0, 1, 5),
        (0, 5, 4),
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 0, 4),
        (3, 4, 7),
    ),
    dtype=np.int32,
)


def decode_level_collision_resource(
    ighw: IghwFile,
    *,
    resource_path: str,
    quantization_markers: tuple[int, ...] = (256, 257),
    warnings: list[str] | None = None,
) -> CollisionScene:
    if ighw.endian != ">":
        raise FormatError(
            f"{ighw.label}: little-endian level collision is unsupported"
        )

    section = ighw.require_section(SECT_LEVEL_COLLISION)
    ighw.require(
        section.offset,
        section.length,
        purpose="level collision section read",
    )
    if section.length < 0x80:
        raise FormatError(
            f"{ighw.label}: level collision section is shorter than 0x80 bytes"
        )

    base = section.offset
    tuid_table_length = ighw.u32(base)
    record_count_field = ighw.u32(base + 0x04)
    prototype_relative = ighw.u32(base + 0x08)
    prototype_length = ighw.u32(base + 0x0C)

    if tuid_table_length % 0x80:
        raise FormatError(
            f"{ighw.label}: collision TUID table length "
            f"{tuid_table_length:#x} is not 0x80-byte aligned"
        )
    if not record_count_field:
        raise FormatError(
            f"{ighw.label}: collision record count field is zero"
        )
    record_count = record_count_field - 1
    if record_count > MAX_COLLISION_RECORDS:
        raise FormatError(
            f"{ighw.label}: unreasonable collision record count "
            f"{record_count}"
        )

    tuid_table_relative = 0x80
    records_relative = tuid_table_relative + tuid_table_length
    expected_prototype_relative = (
        records_relative + record_count * _INSTANCE_STRIDE
    )
    if prototype_relative != expected_prototype_relative:
        raise FormatError(
            f"{ighw.label}: collision prototype blob begins at "
            f"{prototype_relative:#x}, expected "
            f"{expected_prototype_relative:#x}"
        )
    _require_section_range(
        ighw,
        section.offset,
        section.length,
        tuid_table_relative,
        tuid_table_length,
        purpose="collision TUID table",
    )
    _require_section_range(
        ighw,
        section.offset,
        section.length,
        records_relative,
        record_count * _INSTANCE_STRIDE,
        purpose="collision instance table",
    )
    _require_section_range(
        ighw,
        section.offset,
        section.length,
        prototype_relative,
        prototype_length,
        purpose="collision prototype blob",
    )

    records = base + records_relative
    prototypes = base + prototype_relative
    meshes: dict[int, CollisionMesh | None] = {}
    prototype_extents: dict[int, int] = {}
    prototype_flags: dict[int, int] = {}
    instances: list[CollisionInstance] = []
    skipped_instances = 0
    region_name = PurePosixPath(resource_path).parent.name or "collision"

    for record_index in range(record_count):
        record = records + record_index * _INSTANCE_STRIDE
        aabb_min = _float_array(ighw, record, 3)
        aabb_max = _float_array(ighw, record + 0x10, 3)
        if not np.all(np.isfinite(aabb_min)) or not np.all(
            np.isfinite(aabb_max)
        ):
            raise FormatError(
                f"{ighw.label}: collision record {record_index} has a "
                "non-finite AABB"
            )
        if np.any(aabb_min > aabb_max):
            raise FormatError(
                f"{ighw.label}: collision record {record_index} has an "
                "inverted AABB"
            )

        prototype_offset = ighw.u32(record + 0x20)
        prototype_extent = ighw.u16(record + 0x24) + 0x10
        collision_group = ighw.u16(record + 0x26)
        packed_reference = ighw.u32(record + 0x28)
        instance_flags = packed_reference >> 24
        tuid_index = packed_reference & 0x00FF_FFFF

        if prototype_offset % 0x10 or prototype_extent % 0x10:
            raise FormatError(
                f"{ighw.label}: collision record {record_index} has "
                f"unaligned prototype range {prototype_offset:#x} + "
                f"{prototype_extent:#x}"
            )
        _require_blob_range(
            ighw,
            prototype_offset,
            prototype_extent,
            prototype_length,
            label=f"collision record {record_index} prototype",
        )

        previous_extent = prototype_extents.setdefault(
            prototype_offset,
            prototype_extent,
        )
        if previous_extent != prototype_extent:
            raise FormatError(
                f"{ighw.label}: prototype {prototype_offset:#x} has "
                f"conflicting extents {previous_extent:#x} and "
                f"{prototype_extent:#x}"
            )
        previous_flags = prototype_flags.setdefault(
            prototype_offset,
            instance_flags,
        )
        if previous_flags != instance_flags:
            raise FormatError(
                f"{ighw.label}: prototype {prototype_offset:#x} is shared "
                "between world and Tie collision records"
            )

        matrix, asset_tuid, source_kind, source_group = _instance_transform(
            ighw,
            record,
            record_index=record_index,
            flags=instance_flags,
            tuid_index=tuid_index,
            tuid_table=base + tuid_table_relative,
            tuid_table_length=tuid_table_length,
            region_name=region_name,
        )

        if prototype_offset not in meshes:
            mesh = _decode_prototype(
                ighw,
                offset=prototypes + prototype_offset,
                extent=prototype_extent,
                prototype_offset=prototype_offset,
                resource_path=resource_path,
                region_name=region_name,
                source_kind=source_kind,
                quantization_markers=quantization_markers,
            )
            meshes[prototype_offset] = mesh
        else:
            mesh = meshes[prototype_offset]
        if mesh is None:
            skipped_instances += 1
            continue

        source_id = f"{resource_path}#record-{record_index:06d}"
        if instance_flags == _INSTANCE_FLAG_TIE:
            name = (
                f"{region_name}_Tie_{asset_tuid:016X}_"
                f"{record_index:06d}"
            )
        else:
            name = f"{region_name}_Terrain_{record_index:06d}"
        instances.append(
            CollisionInstance(
                mesh_id=mesh.mesh_id,
                name=name,
                matrix=matrix,
                source_group=source_group,
                source_id=source_id,
                metadata={
                    "asset_index": tuid_index,
                    "asset_tuid": asset_tuid,
                    "collision_group": collision_group,
                    "flags": instance_flags,
                    "prototype_offset": prototype_offset,
                    "record_index": record_index,
                    "resource_path": resource_path,
                },
            )
        )

    skipped_prototypes = sum(mesh is None for mesh in meshes.values())
    if skipped_prototypes:
        message = (
            f"{ighw.label}: skipped {skipped_prototypes} no-op collision "
            f"prototypes referenced by {skipped_instances} records"
        )
        LOGGER.warning(message)
        if warnings is not None:
            warnings.append(message)

    return CollisionScene(
        meshes=tuple(
            mesh
            for offset in sorted(meshes)
            if (mesh := meshes[offset]) is not None
        ),
        instances=tuple(instances),
    )


def _instance_transform(
    ighw: IghwFile,
    record: int,
    *,
    record_index: int,
    flags: int,
    tuid_index: int,
    tuid_table: int,
    tuid_table_length: int,
    region_name: str,
) -> tuple[np.ndarray, int, str, str]:
    if flags == _INSTANCE_FLAG_WORLD:
        if tuid_index:
            raise FormatError(
                f"{ighw.label}: world collision record {record_index} has "
                f"unexpected TUID index {tuid_index:#x}"
            )
        return (
            np.eye(4, dtype=np.float32),
            0,
            "TERRAIN",
            f"Terrain - {region_name}",
        )
    if flags != _INSTANCE_FLAG_TIE:
        raise FormatError(
            f"{ighw.label}: collision record {record_index} uses "
            f"unsupported flags {flags:#x}"
        )
    tuid_count = tuid_table_length // 8
    if tuid_index >= tuid_count:
        raise FormatError(
            f"{ighw.label}: collision record {record_index} TUID index "
            f"{tuid_index:#x} exceeds the {tuid_count}-entry table"
        )

    asset_tuid = ighw.u64(tuid_table + tuid_index * 8)
    if not asset_tuid:
        raise FormatError(
            f"{ighw.label}: collision record {record_index} references a "
            "zero Tie TUID"
        )
    stored = _float_array(ighw, record + 0x30, 12).reshape((3, 4))
    translation = _float_array(ighw, record + 0x60, 3)
    if not np.all(np.isfinite(stored)) or not np.all(
        np.isfinite(translation)
    ):
        raise FormatError(
            f"{ighw.label}: collision record {record_index} has a "
            "non-finite transform"
        )
    if not np.allclose(
        stored[:, 3],
        0.0,
        rtol=0.0,
        atol=1e-6,
    ):
        raise FormatError(
            f"{ighw.label}: collision record {record_index} has non-zero "
            "transform padding"
        )

    matrix = np.eye(4, dtype=np.float32)
    matrix[:3, :3] = stored[:, :3].T
    matrix[:3, 3] = translation
    return (
        matrix,
        asset_tuid,
        "TIE",
        f"Static Props - {region_name}",
    )


def _decode_prototype(
    ighw: IghwFile,
    *,
    offset: int,
    extent: int,
    prototype_offset: int,
    resource_path: str,
    region_name: str,
    source_kind: str,
    quantization_markers: tuple[int, ...],
) -> CollisionMesh | None:
    ighw.require(offset, extent, purpose="collision prototype read")
    quantization_marker = ighw.u16(offset + 0x04)
    metadata: dict[str, bool | int | float | str]
    if quantization_marker in quantization_markers:
        (
            positions,
            indices,
            surface_ids,
            discarded_degenerate_faces,
            discarded_duplicate_faces,
        ) = _decode_triangle_prototype(
            ighw,
            offset,
            extent,
        )
        if not len(indices):
            return None
        prototype_type = "TRIANGLE_MESH"
        metadata = {
            "discarded_degenerate_faces": discarded_degenerate_faces,
            "discarded_duplicate_faces": discarded_duplicate_faces,
            "face_count": len(indices),
            "prototype_type": prototype_type,
            "source_face_count": (
                len(indices)
                + discarded_degenerate_faces
                + discarded_duplicate_faces
            ),
            "vertex_count": len(positions),
        }
    elif quantization_marker == 0 and ighw.u16(offset) == 0x20:
        (
            positions,
            indices,
            surface_ids,
            primitive_counts,
        ) = _decode_primitive_prototype(ighw, offset, extent)
        prototype_type = "PRIMITIVES"
        metadata = {
            "box_count": primitive_counts[0],
            "capsule_count": primitive_counts[2],
            "cylinder_count": primitive_counts[3],
            "primitive_count": sum(primitive_counts),
            "prototype_type": prototype_type,
            "sphere_count": primitive_counts[1],
        }
    else:
        raise FormatError(
            f"{ighw.label}: collision prototype {prototype_offset:#x} uses "
            f"unsupported header 0x{ighw.u16(offset):04X}/"
            f"0x{quantization_marker:04X}"
        )

    mesh_id = (
        f"r23:{resource_path}:prototype:{prototype_offset:08X}"
    )
    return CollisionMesh(
        mesh_id=mesh_id,
        name=f"{region_name}_{prototype_type}_{prototype_offset:08X}",
        positions=positions,
        indices=indices,
        surface_ids=surface_ids,
        source_kind=source_kind,
        source_id=f"{resource_path}@0x{prototype_offset:08X}",
        metadata={
            **metadata,
            "prototype_extent": extent,
            "prototype_offset": prototype_offset,
            "resource_path": resource_path,
        },
    )


def _decode_triangle_prototype(
    ighw: IghwFile,
    offset: int,
    extent: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    _require_prototype_range(ighw, offset, extent, 0, 0x20)
    vertex_count = ighw.u16(offset)
    vertex_group_width = ighw.u16(offset + 0x02)
    quantization_marker = ighw.u16(offset + 0x04)
    face_count = ighw.u16(offset + 0x06)
    vertex_section_length = ighw.u16(offset + 0x08)
    total_data_length = ighw.u16(offset + 0x0C)

    if not vertex_count or vertex_count > MAX_COLLISION_VERTICES:
        raise FormatError(
            f"{ighw.label}: unreasonable level collision vertex count "
            f"{vertex_count}"
        )
    if not face_count or face_count > MAX_COLLISION_FACES:
        raise FormatError(
            f"{ighw.label}: unreasonable level collision face count "
            f"{face_count}"
        )
    if vertex_group_width != 16:
        raise FormatError(
            f"{ighw.label}: unsupported level collision vertex group width "
            f"{vertex_group_width}"
        )
    if not quantization_marker:
        raise FormatError(
            f"{ighw.label}: triangle collision prototype has no "
            "quantization marker"
        )

    group_count = (vertex_count + 7) // 8
    expected_vertex_length = 0x20 + group_count * 0x30
    if vertex_section_length != expected_vertex_length:
        raise FormatError(
            f"{ighw.label}: collision vertex section length "
            f"{vertex_section_length:#x} does not match "
            f"{expected_vertex_length:#x}"
        )
    expected_total_length = _align(
        vertex_section_length + face_count * 4,
        0x10,
    )
    if total_data_length != expected_total_length:
        raise FormatError(
            f"{ighw.label}: collision data length "
            f"{total_data_length:#x} does not match "
            f"{expected_total_length:#x}"
        )
    _require_prototype_range(
        ighw,
        offset,
        extent,
        0,
        total_data_length,
    )

    base_position = np.frombuffer(
        ighw.data,
        dtype=">i4",
        count=3,
        offset=offset + 0x10,
    ).astype(np.int64)
    positions = np.empty((vertex_count, 3), dtype=np.float32)
    for group_index in range(group_count):
        deltas = np.frombuffer(
            ighw.data,
            dtype=">u2",
            count=24,
            offset=offset + 0x20 + group_index * 0x30,
        ).reshape((3, 8)).T
        first = group_index * 8
        group_vertex_count = min(8, vertex_count - first)
        positions[first : first + group_vertex_count] = (
            base_position + deltas[:group_vertex_count]
        ) * np.float32(1.0 / 1024.0)

    face_words = np.frombuffer(
        ighw.data,
        dtype=">u4",
        count=face_count,
        offset=offset + vertex_section_length,
    ).astype(np.uint32)
    indices = np.column_stack(
        (
            (face_words >> np.uint32(18)) & np.uint32(0x1FF),
            (face_words >> np.uint32(9)) & np.uint32(0x1FF),
            face_words & np.uint32(0x1FF),
        )
    ).astype(np.int32)
    maximum_index = int(indices.max())
    if maximum_index >= vertex_count:
        raise FormatError(
            f"{ighw.label}: collision face index {maximum_index} exceeds "
            f"{vertex_count} vertices"
        )
    surface_ids = (face_words >> np.uint32(27)).astype(np.int32)
    valid_faces = (
        (indices[:, 0] != indices[:, 1])
        & (indices[:, 1] != indices[:, 2])
        & (indices[:, 2] != indices[:, 0])
    )
    edge_a = positions[indices[:, 1]] - positions[indices[:, 0]]
    edge_b = positions[indices[:, 2]] - positions[indices[:, 0]]
    valid_faces &= np.any(np.cross(edge_a, edge_b) != 0.0, axis=1)
    discarded_faces = int(np.count_nonzero(~valid_faces))
    indices = indices[valid_faces]
    surface_ids = surface_ids[valid_faces]
    unique_faces: list[int] = []
    seen_faces: set[tuple[int, int, int]] = set()
    for face_index, face in enumerate(indices):
        key = tuple(sorted(int(index) for index in face))
        if key in seen_faces:
            continue
        seen_faces.add(key)
        unique_faces.append(face_index)
    discarded_duplicate_faces = len(indices) - len(unique_faces)
    indices = indices[unique_faces]
    surface_ids = surface_ids[unique_faces]
    return (
        positions,
        indices,
        surface_ids,
        discarded_faces,
        discarded_duplicate_faces,
    )


def _decode_primitive_prototype(
    ighw: IghwFile,
    offset: int,
    extent: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[int, int, int, int]]:
    _require_prototype_range(ighw, offset, extent, 0, 0x20)
    primitive_count = ighw.u16(offset + 0x02)
    counts = tuple(ighw.u8(offset + 0x10 + index) for index in range(4))
    if not primitive_count or primitive_count > MAX_COLLISION_PRIMITIVES:
        raise FormatError(
            f"{ighw.label}: unreasonable collision primitive count "
            f"{primitive_count}"
        )
    if sum(counts) != primitive_count:
        raise FormatError(
            f"{ighw.label}: collision primitive header count "
            f"{primitive_count} does not match group counts {counts}"
        )

    expected_extent = 0x30 + sum(
        math.ceil(count / 4) * _PRIMITIVE_BATCH_SIZES[kind]
        for kind, count in zip(_PRIMITIVE_KINDS, counts, strict=True)
    )
    if extent != expected_extent:
        raise FormatError(
            f"{ighw.label}: collision primitive extent {extent:#x} does "
            f"not match {expected_extent:#x}"
        )

    position_parts: list[np.ndarray] = []
    index_parts: list[np.ndarray] = []
    surface_parts: list[np.ndarray] = []
    vertex_base = 0
    cursor = offset + 0x20
    for kind, count in zip(_PRIMITIVE_KINDS, counts, strict=True):
        batch_size = _PRIMITIVE_BATCH_SIZES[kind]
        value_count = {4: 15, 5: 4, 6: 7, 8: 7}[kind]
        for batch_start in range(0, count, 4):
            lane_count = min(4, count - batch_start)
            values = _primitive_batch_values(
                ighw,
                cursor,
                value_count,
            )
            for lane in range(4):
                descriptor = cursor + lane * 0x10
                expected_kind = kind if lane < lane_count else 0
                actual_kind = ighw.u8(descriptor)
                if actual_kind != expected_kind:
                    raise FormatError(
                        f"{ighw.label}: collision primitive descriptor uses "
                        f"kind {actual_kind}, expected {expected_kind}"
                    )
                if lane >= lane_count:
                    continue
                if ighw.u8(descriptor + 0x03) != 0xFF:
                    raise FormatError(
                        f"{ighw.label}: level collision primitive kind "
                        f"{kind} has an unexpected bone binding"
                    )
                surface_id = ighw.u8(descriptor + 0x01)
                positions, indices = _primitive_geometry(
                    kind,
                    values[:, lane],
                    label=ighw.label,
                )
                position_parts.append(positions)
                index_parts.append(indices + vertex_base)
                surface_parts.append(
                    np.full(
                        len(indices),
                        surface_id,
                        dtype=np.int32,
                    )
                )
                vertex_base += len(positions)
            cursor += batch_size

    if cursor + 0x10 != offset + extent:
        raise FormatError(
            f"{ighw.label}: collision primitive groups end at "
            f"{cursor - offset:#x}, expected {extent - 0x10:#x}"
        )
    return (
        np.concatenate(position_parts).astype(np.float32, copy=False),
        np.concatenate(index_parts).astype(np.int32, copy=False),
        np.concatenate(surface_parts).astype(np.int32, copy=False),
        counts,
    )


def _primitive_batch_values(
    ighw: IghwFile,
    offset: int,
    value_count: int,
) -> np.ndarray:
    values = np.empty((value_count, 4), dtype=np.float32)
    for value_index in range(value_count):
        values[value_index] = _float_array(
            ighw,
            offset + 0x40 + value_index * 0x10,
            4,
        )
    if not np.all(np.isfinite(values)):
        raise FormatError(
            f"{ighw.label}: collision primitive contains non-finite values"
        )
    return values


def _primitive_geometry(
    kind: int,
    values: np.ndarray,
    *,
    label: str,
) -> tuple[np.ndarray, np.ndarray]:
    if kind == 4:
        center = values[:3]
        basis = values[3:12].reshape((3, 3))
        half_extents = values[12:15]
        if np.any(half_extents < 0.0):
            raise FormatError(
                f"{label}: collision box has negative half extents"
            )
        positions = center + (_BOX_SIGNS * half_extents) @ basis
        return positions.astype(np.float32), _BOX_INDICES.copy()

    if kind == 5:
        radius = _positive_radius(values[3], label=label, kind="sphere")
        return _sphere_geometry(values[:3], radius)

    radius = _positive_radius(
        values[6],
        label=label,
        kind="capsule" if kind == 6 else "cylinder",
    )
    if kind == 6:
        return _capsule_geometry(values[:3], values[3:6], radius)
    if kind == 8:
        return _cylinder_geometry(values[:3], values[3:6], radius)
    raise FormatError(f"{label}: unsupported collision primitive kind {kind}")


def _sphere_geometry(
    center: np.ndarray,
    radius: float,
    *,
    segments: int = 12,
    rings: int = 8,
) -> tuple[np.ndarray, np.ndarray]:
    vertices = [np.asarray(center, dtype=np.float32) + (0.0, 0.0, -radius)]
    for ring in range(1, rings):
        latitude = -math.pi / 2.0 + math.pi * ring / rings
        ring_radius = radius * math.cos(latitude)
        z = radius * math.sin(latitude)
        for segment in range(segments):
            longitude = math.tau * segment / segments
            vertices.append(
                np.asarray(center, dtype=np.float32)
                + (
                    ring_radius * math.cos(longitude),
                    ring_radius * math.sin(longitude),
                    z,
                )
            )
    vertices.append(np.asarray(center, dtype=np.float32) + (0.0, 0.0, radius))
    return (
        np.asarray(vertices, dtype=np.float32),
        _triangulated_ring_faces(
            segments,
            rings - 1,
            len(vertices) - 1,
        ),
    )


def _capsule_geometry(
    endpoint_a: np.ndarray,
    endpoint_b: np.ndarray,
    radius: float,
    *,
    segments: int = 12,
) -> tuple[np.ndarray, np.ndarray]:
    endpoint_a = np.asarray(endpoint_a, dtype=np.float32)
    endpoint_b = np.asarray(endpoint_b, dtype=np.float32)
    axis = endpoint_b - endpoint_a
    length = float(np.linalg.norm(axis))
    if length <= 1e-8:
        return _sphere_geometry(
            (endpoint_a + endpoint_b) * np.float32(0.5),
            radius,
            segments=segments,
        )
    direction, tangent, bitangent = _axis_frame(axis)
    vertices = [endpoint_a - direction * radius]
    ring_specs = [
        (endpoint_a, angle)
        for angle in (-3 * math.pi / 8, -math.pi / 4, -math.pi / 8, 0.0)
    ] + [
        (endpoint_b, angle)
        for angle in (0.0, math.pi / 8, math.pi / 4, 3 * math.pi / 8)
    ]
    for center, latitude in ring_specs:
        axial = direction * (radius * math.sin(latitude))
        ring_radius = radius * math.cos(latitude)
        for segment in range(segments):
            longitude = math.tau * segment / segments
            radial = (
                tangent * math.cos(longitude)
                + bitangent * math.sin(longitude)
            )
            vertices.append(center + axial + radial * ring_radius)
    vertices.append(endpoint_b + direction * radius)
    return (
        np.asarray(vertices, dtype=np.float32),
        _triangulated_ring_faces(
            segments,
            len(ring_specs),
            len(vertices) - 1,
        ),
    )


def _cylinder_geometry(
    endpoint_a: np.ndarray,
    endpoint_b: np.ndarray,
    radius: float,
    *,
    segments: int = 12,
) -> tuple[np.ndarray, np.ndarray]:
    endpoint_a = np.asarray(endpoint_a, dtype=np.float32)
    endpoint_b = np.asarray(endpoint_b, dtype=np.float32)
    axis = endpoint_b - endpoint_a
    if float(np.linalg.norm(axis)) <= 1e-8:
        raise FormatError("collision cylinder has coincident endpoints")
    _direction, tangent, bitangent = _axis_frame(axis)
    vertices = [endpoint_a, endpoint_b]
    for endpoint in (endpoint_a, endpoint_b):
        for segment in range(segments):
            longitude = math.tau * segment / segments
            radial = (
                tangent * math.cos(longitude)
                + bitangent * math.sin(longitude)
            )
            vertices.append(endpoint + radial * radius)

    faces: list[tuple[int, int, int]] = []
    first_ring = 2
    second_ring = first_ring + segments
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        a = first_ring + segment
        next_a = first_ring + next_segment
        b = second_ring + segment
        next_b = second_ring + next_segment
        faces.append((0, next_a, a))
        faces.append((1, b, next_b))
        faces.append((a, next_a, next_b))
        faces.append((a, next_b, b))
    return (
        np.asarray(vertices, dtype=np.float32),
        np.asarray(faces, dtype=np.int32),
    )


def _axis_frame(
    axis: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    direction = axis / np.linalg.norm(axis)
    reference = (
        np.asarray((1.0, 0.0, 0.0), dtype=np.float32)
        if abs(float(direction[0])) < 0.9
        else np.asarray((0.0, 1.0, 0.0), dtype=np.float32)
    )
    tangent = np.cross(direction, reference)
    tangent /= np.linalg.norm(tangent)
    bitangent = np.cross(direction, tangent)
    return direction, tangent, bitangent


def _triangulated_ring_faces(
    segments: int,
    ring_count: int,
    top_index: int,
) -> np.ndarray:
    faces: list[tuple[int, int, int]] = []
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        faces.append((0, 1 + next_segment, 1 + segment))
    for ring in range(ring_count - 1):
        first = 1 + ring * segments
        next_first = first + segments
        for segment in range(segments):
            next_segment = (segment + 1) % segments
            a = first + segment
            next_a = first + next_segment
            b = next_first + segment
            next_b = next_first + next_segment
            faces.append((a, next_a, next_b))
            faces.append((a, next_b, b))
    last_ring = 1 + (ring_count - 1) * segments
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        faces.append(
            (
                last_ring + segment,
                last_ring + next_segment,
                top_index,
            )
        )
    return np.asarray(faces, dtype=np.int32)


def _positive_radius(value: float, *, label: str, kind: str) -> float:
    radius = float(value)
    if radius <= 0.0:
        raise FormatError(
            f"{label}: collision {kind} has non-positive radius {radius}"
        )
    return radius


def _float_array(ighw: IghwFile, offset: int, count: int) -> np.ndarray:
    ighw.require(offset, count * 4, purpose="collision float array read")
    return np.frombuffer(
        ighw.data,
        dtype=">f4",
        count=count,
        offset=offset,
    ).astype(np.float32)


def _require_section_range(
    ighw: IghwFile,
    section_offset: int,
    section_length: int,
    relative_offset: int,
    length: int,
    *,
    purpose: str,
) -> None:
    if (
        relative_offset < 0
        or length < 0
        or relative_offset + length > section_length
    ):
        raise FormatError(
            f"{ighw.label}: {purpose} [{relative_offset:#x}, "
            f"{relative_offset + length:#x}) exceeds the "
            f"{section_length:#x}-byte level collision section"
        )
    ighw.require(section_offset + relative_offset, length, purpose=purpose)


def _require_blob_range(
    ighw: IghwFile,
    offset: int,
    length: int,
    blob_length: int,
    *,
    label: str,
) -> None:
    if offset < 0 or length < 0 or offset + length > blob_length:
        raise FormatError(
            f"{ighw.label}: {label} [{offset:#x}, {offset + length:#x}) "
            f"exceeds the {blob_length:#x}-byte prototype blob"
        )


def _require_prototype_range(
    ighw: IghwFile,
    offset: int,
    extent: int,
    relative_offset: int,
    length: int,
) -> None:
    if (
        relative_offset < 0
        or length < 0
        or relative_offset + length > extent
    ):
        raise FormatError(
            f"{ighw.label}: collision prototype range "
            f"[{relative_offset:#x}, {relative_offset + length:#x}) "
            f"exceeds its {extent:#x}-byte extent"
        )
    ighw.require(
        offset + relative_offset,
        length,
        purpose="collision prototype payload",
    )


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)
