from __future__ import annotations

import logging
from pathlib import PurePosixPath

import numpy as np

from ...core.binary import BinaryView
from ...core.collision import CollisionInstance, CollisionMesh, CollisionScene
from ...core.errors import FormatError

LOGGER = logging.getLogger(__name__)

MAX_COLLISION_RECORDS = 1_000_000
MAX_COLLISION_VERTICES = 512
MAX_COLLISION_FACES = 65_535

_HEADER_SIZE = 0x80
_INSTANCE_STRIDE = 0x80
_INSTANCE_FLAG_WORLD = 0x00
_INSTANCE_FLAG_TIE = 0x20
_PROTOTYPE_BASE_OFFSET = 0x60
_PROTOTYPE_VERTEX_OFFSET = 0x70
_QUANTIZATION_MARKER = 1
_YARD_TO_METERS = np.float32(0.9144)
_POSITION_SCALE = np.float32(_YARD_TO_METERS / 1024.0)
# The face word's top five bits (stored as ``insomniac_surface_id``) are
# triangle flags. Bit 0 marks a surface the player cannot wall-jump from:
# ReLunacy traced it to the TOD wall-jump check, from the layout first
# decoded by @sowelilili. The other four flag bits remain unnamed.
_SURFACE_FLAG_NO_WALL_JUMP = 0x01
NO_WALL_JUMP_ATTRIBUTE = "insomniac_no_wall_jump"


def decode_level_collision_resource(
    data: bytes | bytearray | memoryview,
    *,
    resource_path: str,
    warnings: list[str] | None = None,
) -> CollisionScene:
    view = BinaryView(data, endian=">", label=resource_path)
    view.require(0, _HEADER_SIZE, purpose="level collision header")

    records_offset = view.u32(0)
    record_count = view.u32(0x04)
    prototypes_offset = view.u32(0x08)
    prototypes_length = view.u32(0x0C)
    prototypes_end = view.u32(0x30)

    if records_offset != _HEADER_SIZE:
        raise FormatError(
            f"{view.label}: collision records begin at "
            f"{records_offset:#x}, expected {_HEADER_SIZE:#x}"
        )
    if not record_count or record_count > MAX_COLLISION_RECORDS:
        raise FormatError(
            f"{view.label}: unreasonable collision record count "
            f"{record_count}"
        )
    expected_prototypes_offset = (
        records_offset + record_count * _INSTANCE_STRIDE
    )
    if prototypes_offset != expected_prototypes_offset:
        raise FormatError(
            f"{view.label}: collision prototype blob begins at "
            f"{prototypes_offset:#x}, expected "
            f"{expected_prototypes_offset:#x}"
        )
    if not prototypes_length:
        raise FormatError(
            f"{view.label}: collision prototype blob is empty"
        )
    if prototypes_end != prototypes_offset + prototypes_length:
        raise FormatError(
            f"{view.label}: collision prototype end {prototypes_end:#x} "
            f"does not match {prototypes_offset + prototypes_length:#x}"
        )
    view.require(
        records_offset,
        record_count * _INSTANCE_STRIDE,
        purpose="collision instance table",
    )
    view.require(
        prototypes_offset,
        prototypes_length,
        purpose="collision prototype blob",
    )

    meshes: dict[int, CollisionMesh | None] = {}
    prototype_extents: dict[int, int] = {}
    prototype_flags: dict[int, int] = {}
    instances: list[CollisionInstance] = []
    skipped_instances = 0
    level_name = PurePosixPath(resource_path).parent.name or "collision"

    for record_index in range(record_count):
        record = records_offset + record_index * _INSTANCE_STRIDE
        stored = _float_array(view, record, 12).reshape((3, 4))
        translation = _float_array(view, record + 0x30, 4)
        sphere = _float_array(view, record + 0x40, 4)
        aabb_min = _float_array(view, record + 0x50, 3)
        aabb_max = _float_array(view, record + 0x60, 3)
        _validate_record_values(
            view,
            record_index=record_index,
            stored=stored,
            translation=translation,
            sphere=sphere,
            aabb_min=aabb_min,
            aabb_max=aabb_max,
        )

        packed_reference = view.u32(record + 0x70)
        instance_flags = packed_reference >> 24
        component_index = packed_reference & 0x00FF_FFFF
        prototype_offset = view.u32(record + 0x74)
        collision_group = view.u16(record + 0x78)
        prototype_extent = view.u16(record + 0x7A)
        if view.u32(record + 0x7C):
            raise FormatError(
                f"{view.label}: collision record {record_index} has "
                "non-zero reserved data"
            )
        if instance_flags not in (
            _INSTANCE_FLAG_WORLD,
            _INSTANCE_FLAG_TIE,
        ):
            raise FormatError(
                f"{view.label}: collision record {record_index} uses "
                f"unsupported flags {instance_flags:#x}"
            )
        if instance_flags == _INSTANCE_FLAG_WORLD and component_index:
            raise FormatError(
                f"{view.label}: world collision record {record_index} has "
                f"unexpected component index {component_index:#x}"
            )
        if (
            prototype_offset % 0x10
            or not prototype_extent
            or prototype_extent % 0x10
        ):
            raise FormatError(
                f"{view.label}: collision record {record_index} has "
                f"unaligned prototype range {prototype_offset:#x} + "
                f"{prototype_extent:#x}"
            )
        _require_blob_range(
            view,
            prototype_offset,
            prototype_extent,
            prototypes_length,
            label=f"collision record {record_index} prototype",
        )

        previous_extent = prototype_extents.setdefault(
            prototype_offset,
            prototype_extent,
        )
        if previous_extent != prototype_extent:
            raise FormatError(
                f"{view.label}: prototype {prototype_offset:#x} has "
                f"conflicting extents {previous_extent:#x} and "
                f"{prototype_extent:#x}"
            )
        previous_flags = prototype_flags.setdefault(
            prototype_offset,
            instance_flags,
        )
        if previous_flags != instance_flags:
            raise FormatError(
                f"{view.label}: prototype {prototype_offset:#x} is shared "
                "between world and Tie collision records"
            )

        source_kind = (
            "TERRAIN"
            if instance_flags == _INSTANCE_FLAG_WORLD
            else "TIE"
        )
        source_group = (
            f"Terrain - {level_name}"
            if instance_flags == _INSTANCE_FLAG_WORLD
            else f"Static Props - {level_name}"
        )
        if prototype_offset not in meshes:
            mesh = _decode_prototype(
                view,
                offset=prototypes_offset + prototype_offset,
                extent=prototype_extent,
                prototype_offset=prototype_offset,
                resource_path=resource_path,
                level_name=level_name,
                source_kind=source_kind,
            )
            meshes[prototype_offset] = mesh
        else:
            mesh = meshes[prototype_offset]
        if mesh is None:
            skipped_instances += 1
            continue

        matrix = np.eye(4, dtype=np.float32)
        matrix[:3, :3] = stored[:, :3].T
        matrix[:3, 3] = translation[:3] * _YARD_TO_METERS
        family = (
            "Terrain"
            if instance_flags == _INSTANCE_FLAG_WORLD
            else "Tie"
        )
        instances.append(
            CollisionInstance(
                mesh_id=mesh.mesh_id,
                name=f"{level_name}_{family}_{record_index:06d}",
                matrix=matrix,
                source_group=source_group,
                source_id=(
                    f"{resource_path}#record-{record_index:06d}"
                ),
                metadata={
                    "collision_group": collision_group,
                    "component_index": component_index,
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
            f"{view.label}: skipped {skipped_prototypes} no-op collision "
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


def _validate_record_values(
    view: BinaryView,
    *,
    record_index: int,
    stored: np.ndarray,
    translation: np.ndarray,
    sphere: np.ndarray,
    aabb_min: np.ndarray,
    aabb_max: np.ndarray,
) -> None:
    values = (stored, translation, sphere, aabb_min, aabb_max)
    if not all(np.all(np.isfinite(value)) for value in values):
        raise FormatError(
            f"{view.label}: collision record {record_index} contains "
            "non-finite values"
        )
    if not np.allclose(
        stored[:, 3],
        0.0,
        rtol=0.0,
        atol=1e-6,
    ):
        raise FormatError(
            f"{view.label}: collision record {record_index} has non-zero "
            "transform padding"
        )
    if not np.isclose(
        translation[3],
        1.0,
        rtol=0.0,
        atol=1e-6,
    ):
        raise FormatError(
            f"{view.label}: collision record {record_index} has invalid "
            f"translation W {translation[3]}"
        )
    if sphere[3] < 0.0:
        raise FormatError(
            f"{view.label}: collision record {record_index} has a "
            "negative bounding-sphere radius"
        )
    if np.any(aabb_min > aabb_max):
        raise FormatError(
            f"{view.label}: collision record {record_index} has an "
            "inverted AABB"
        )


def _decode_prototype(
    view: BinaryView,
    *,
    offset: int,
    extent: int,
    prototype_offset: int,
    resource_path: str,
    level_name: str,
    source_kind: str,
) -> CollisionMesh | None:
    view.require(offset, extent, purpose="collision prototype read")
    (
        positions,
        indices,
        surface_ids,
        discarded_degenerate_faces,
        discarded_duplicate_faces,
    ) = _decode_triangle_prototype(view, offset, extent)
    if not len(indices):
        return None

    mesh_id = (
        f"tod:{resource_path}:prototype:{prototype_offset:08X}"
    )
    return CollisionMesh(
        mesh_id=mesh_id,
        name=f"{level_name}_TRIANGLE_MESH_{prototype_offset:08X}",
        positions=positions,
        indices=indices,
        surface_ids=surface_ids,
        face_flags={
            NO_WALL_JUMP_ATTRIBUTE: (
                surface_ids & _SURFACE_FLAG_NO_WALL_JUMP
            ).astype(np.bool_),
        },
        source_kind=source_kind,
        source_id=f"{resource_path}@0x{prototype_offset:08X}",
        metadata={
            "discarded_degenerate_faces": discarded_degenerate_faces,
            "discarded_duplicate_faces": discarded_duplicate_faces,
            "face_count": len(indices),
            "prototype_extent": extent,
            "prototype_offset": prototype_offset,
            "prototype_type": "TRIANGLE_MESH",
            "quantization_marker": _QUANTIZATION_MARKER,
            "resource_path": resource_path,
            "source_face_count": (
                len(indices)
                + discarded_degenerate_faces
                + discarded_duplicate_faces
            ),
            "vertex_count": len(positions),
        },
    )


def _decode_triangle_prototype(
    view: BinaryView,
    offset: int,
    extent: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    _require_prototype_range(view, offset, extent, 0, _PROTOTYPE_VERTEX_OFFSET)
    vertex_count = view.u16(offset)
    base_offset = view.u16(offset + 0x02)
    quantization_marker = view.u16(offset + 0x04)
    face_count = view.u16(offset + 0x06)
    face_offset = view.u16(offset + 0x08)
    reserved_a = view.u16(offset + 0x0A)
    total_data_length = view.u16(offset + 0x0C)
    reserved_b = view.u16(offset + 0x0E)

    if not vertex_count or vertex_count > MAX_COLLISION_VERTICES:
        raise FormatError(
            f"{view.label}: unreasonable level collision vertex count "
            f"{vertex_count}"
        )
    if not face_count or face_count > MAX_COLLISION_FACES:
        raise FormatError(
            f"{view.label}: unreasonable level collision face count "
            f"{face_count}"
        )
    if base_offset != _PROTOTYPE_BASE_OFFSET:
        raise FormatError(
            f"{view.label}: unsupported collision prototype base offset "
            f"{base_offset:#x}"
        )
    if quantization_marker != _QUANTIZATION_MARKER:
        raise FormatError(
            f"{view.label}: unsupported collision quantization marker "
            f"{quantization_marker}"
        )
    if reserved_a or reserved_b:
        raise FormatError(
            f"{view.label}: collision prototype has non-zero reserved "
            "header data"
        )
    if view.u32(offset + 0x6C):
        raise FormatError(
            f"{view.label}: collision prototype has non-zero base padding"
        )

    group_count = (vertex_count + 7) // 8
    expected_face_offset = (
        _PROTOTYPE_VERTEX_OFFSET + group_count * 0x30
    )
    if face_offset != expected_face_offset:
        raise FormatError(
            f"{view.label}: collision face data begins at "
            f"{face_offset:#x}, expected {expected_face_offset:#x}"
        )
    face_end = face_offset + face_count * 4
    expected_total_length = (face_end + 0x0F) & ~0x0F
    if total_data_length != expected_total_length:
        raise FormatError(
            f"{view.label}: collision data length "
            f"{total_data_length:#x} does not match "
            f"{expected_total_length:#x}"
        )
    if total_data_length > extent:
        raise FormatError(
            f"{view.label}: collision data length "
            f"{total_data_length:#x} exceeds prototype extent "
            f"{extent:#x}"
        )
    _require_prototype_range(
        view,
        offset,
        extent,
        _PROTOTYPE_BASE_OFFSET,
        0x10,
    )
    _require_prototype_range(
        view,
        offset,
        extent,
        _PROTOTYPE_VERTEX_OFFSET,
        group_count * 0x30,
    )
    _require_prototype_range(
        view,
        offset,
        extent,
        face_offset,
        face_count * 4,
    )

    base_position = np.frombuffer(
        view.data,
        dtype=">i4",
        count=3,
        offset=offset + _PROTOTYPE_BASE_OFFSET,
    ).astype(np.int64)
    positions = np.empty((vertex_count, 3), dtype=np.float32)
    for group_index in range(group_count):
        first = group_index * 8
        group_vertex_count = min(8, vertex_count - first)
        deltas = np.frombuffer(
            view.data,
            dtype=">u2",
            count=24,
            offset=offset + _PROTOTYPE_VERTEX_OFFSET + group_index * 0x30,
        ).reshape((3, 8)).T.astype(np.int64)
        positions[first : first + group_vertex_count] = (
            base_position + deltas[:group_vertex_count]
        ) * _POSITION_SCALE

    face_words = np.frombuffer(
        view.data,
        dtype=">u4",
        count=face_count,
        offset=offset + face_offset,
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
            f"{view.label}: collision face index {maximum_index} exceeds "
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
    discarded_degenerate_faces = int(np.count_nonzero(~valid_faces))
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
        discarded_degenerate_faces,
        discarded_duplicate_faces,
    )


def _float_array(
    view: BinaryView,
    offset: int,
    count: int,
) -> np.ndarray:
    view.require(offset, count * 4, purpose="collision float array")
    return np.frombuffer(
        view.data,
        dtype=">f4",
        count=count,
        offset=offset,
    ).astype(np.float32)


def _require_blob_range(
    view: BinaryView,
    relative_offset: int,
    length: int,
    blob_length: int,
    *,
    label: str,
) -> None:
    if relative_offset + length > blob_length:
        raise FormatError(
            f"{view.label}: {label} [{relative_offset:#x}, "
            f"{relative_offset + length:#x}) exceeds the "
            f"{blob_length:#x}-byte prototype blob"
        )


def _require_prototype_range(
    view: BinaryView,
    prototype_offset: int,
    prototype_extent: int,
    relative_offset: int,
    length: int,
) -> None:
    if relative_offset + length > prototype_extent:
        raise FormatError(
            f"{view.label}: collision prototype read "
            f"[{relative_offset:#x}, {relative_offset + length:#x}) "
            f"exceeds its {prototype_extent:#x}-byte extent"
        )
    view.require(
        prototype_offset + relative_offset,
        length,
        purpose="collision prototype field",
    )
