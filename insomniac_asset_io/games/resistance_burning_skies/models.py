from __future__ import annotations

import math
import struct
from dataclasses import dataclass

import numpy as np

from ...core.errors import FormatError, MissingResourceError
from ..resistance_2.formats import Bone, MeshGeometry, Skeleton
from .formats import NsirsrcFile

MODEL_ROOT_TYPE = 0x1C
MATERIAL_DEPENDENCY_TYPE = 0x04
MODEL_MATERIAL_COUNT_OFFSET = 0x00
MODEL_MATERIAL_RECORDS_POINTER_OFFSET = 0x04
MODEL_BONE_RECORD_COUNT_OFFSET = 0x10
MODEL_BONE_RECORDS_POINTER_OFFSET = 0x14
MODEL_BONE_RECORD_SIZE = 0x70
MODEL_BONE_INVERSE_MATRIX_OFFSET = 0x00
MODEL_BONE_PARENT_INDEX_OFFSET = 0x60
MODEL_LOD_COUNT_OFFSET = 0x18
MODEL_LOD_RECORDS_POINTER_OFFSET = 0x1C
MODEL_LOD_RECORD_SIZE = 0x10
MODEL_LOD_MESH_COUNT_OFFSET = 0x00
MODEL_LOD_MESH_ENTRIES_POINTER_OFFSET = 0x04
MODEL_MESH_ENTRY_SIZE = 0x90
MODEL_MESH_GEOMETRY_OFFSET = 0x08
MODEL_MESH_BONE_RANGE_OFFSET = 0x44
MODEL_MESH_BONE_PALETTE_COUNT_OFFSET = 0x46
MODEL_MESH_BONE_PALETTE_OFFSET = 0x70
MODEL_MESH_BONE_PALETTE_CAPACITY = 32
MODEL_GBUFFER_POINTER_OFFSET = 0x0C
MODEL_PHYSICS_POINTER_OFFSET = 0x28
MODEL_TRAUMA_RECORD_SIZE = 0x18
MODEL_TRAUMA_PROPERTY_NAME_OFFSET = 0x04
MODEL_STREAM_HEADER_SIZE = 0x10
EXTERNAL_REFERENCE_TABLE_TAG = 0x00020500
MAX_LODS = 256
MAX_MESHES = 4096
MAX_BONES = 4096
MAX_TRAUMA_PROPERTIES = 64
MAX_VERTICES = 1_000_000
MAX_INDICES = 3_000_000
GBUFFER_FORMAT_RIGID = 0x15
GBUFFER_FORMAT_SKINNED = 0x17
GBUFFER_STRIDES = {
    GBUFFER_FORMAT_RIGID: 36,
    GBUFFER_FORMAT_SKINNED: 44,
}
SKIN_WEIGHT_SUM_TOLERANCE = 0.001


@dataclass(slots=True)
class DecodedSmf:
    name: str
    meshes: list[MeshGeometry]
    material_names: tuple[str, ...]
    material_dependency_count: int
    bounding_sphere: tuple[float, float, float, float]
    material_binding_supported: bool
    skeleton: Skeleton | None


def _bounding_sphere(
    positions: np.ndarray,
) -> tuple[float, float, float, float]:
    minimum = positions.min(axis=0)
    maximum = positions.max(axis=0)
    center = (minimum + maximum) * 0.5
    radius = math.sqrt(float(np.max(np.sum((positions - center) ** 2, axis=1))))
    return (
        float(center[0]),
        float(center[1]),
        float(center[2]),
        radius,
    )


def _decode_skeleton(
    resource: NsirsrcFile,
    *,
    serialized_size: int,
) -> Skeleton | None:
    bone_count = resource.u32(
        resource.physical_offset(MODEL_BONE_RECORD_COUNT_OFFSET)
    )
    if not bone_count:
        return None
    if bone_count > MAX_BONES:
        raise FormatError(
            f"{resource.label}: bone count {bone_count} exceeds {MAX_BONES}"
        )

    records_pointer = resource.u32(
        resource.physical_offset(MODEL_BONE_RECORDS_POINTER_OFFSET)
    )
    records_size = bone_count * MODEL_BONE_RECORD_SIZE
    if not records_pointer or records_pointer + records_size > serialized_size:
        raise FormatError(
            f"{resource.label}: skeleton records are outside the serialized model"
        )
    records_offset = resource.physical_offset(records_pointer)
    resource.require(
        records_offset,
        records_size,
        purpose="model skeleton records",
    )

    bind_world_inverse = np.empty((bone_count, 4, 4), dtype=np.float32)
    parents: list[int] = []
    roots: list[int] = []
    for bone_index in range(bone_count):
        record_offset = records_offset + bone_index * MODEL_BONE_RECORD_SIZE
        bind_world_inverse[bone_index] = (
            np.frombuffer(
                resource.data,
                dtype="<f4",
                count=16,
                offset=record_offset + MODEL_BONE_INVERSE_MATRIX_OFFSET,
            )
            .reshape(4, 4)
            .T
        )
        parent_index = struct.unpack_from(
            "<i",
            resource.data,
            record_offset + MODEL_BONE_PARENT_INDEX_OFFSET,
        )[0]
        if parent_index < -1 or parent_index >= bone_index:
            raise FormatError(
                f"{resource.label}: bone {bone_index} has invalid parent {parent_index}"
            )
        parents.append(parent_index)
        if parent_index < 0:
            roots.append(bone_index)

    if len(roots) != 1:
        raise FormatError(
            f"{resource.label}: expected one skeleton root, found {len(roots)}"
        )
    if not np.all(np.isfinite(bind_world_inverse)):
        raise FormatError(
            f"{resource.label}: skeleton contains non-finite bind matrices"
        )
    try:
        bind_world = np.linalg.inv(bind_world_inverse).astype(np.float32)
    except np.linalg.LinAlgError as exc:
        raise FormatError(
            f"{resource.label}: skeleton contains a singular bind matrix"
        ) from exc

    children: list[list[int]] = [[] for _ in range(bone_count)]
    for bone_index, parent_index in enumerate(parents):
        if parent_index >= 0:
            children[parent_index].append(bone_index)
    sibling_indices = [0] * bone_count
    for siblings in children:
        for index, bone_index in enumerate(siblings[:-1]):
            sibling_indices[bone_index] = siblings[index + 1]

    bones = tuple(
        Bone(
            flags=0,
            parent_index=parent_index,
            child_index=children[bone_index][0] if children[bone_index] else 0,
            sibling_index=sibling_indices[bone_index],
        )
        for bone_index, parent_index in enumerate(parents)
    )
    bind_local = np.empty_like(bind_world)
    for bone_index, parent_index in enumerate(parents):
        bind_local[bone_index] = (
            bind_world_inverse[parent_index] @ bind_world[bone_index]
            if parent_index >= 0
            else bind_world[bone_index]
        )
    return Skeleton(
        root_bone=roots[0],
        bones=bones,
        bind_local=bind_local,
        bind_world=bind_world,
        bind_world_inverse=bind_world_inverse,
        scale_shift=0,
        translation_shift=0,
    )


def _decode_material_indices(
    resource: NsirsrcFile,
    *,
    mesh_entry_offsets: tuple[int, ...],
    material_count: int,
    serialized_size: int,
) -> tuple[int, ...] | None:
    mesh_count = len(mesh_entry_offsets)
    mesh_indices_by_offset = {
        entry_offset: index
        for index, entry_offset in enumerate(mesh_entry_offsets)
    }
    if len(mesh_indices_by_offset) != mesh_count:
        raise FormatError(
            f"{resource.label}: duplicate model mesh entry offsets"
        )
    data = resource.data
    serialized_start = resource.data_base
    serialized_end = resource.physical_offset(serialized_size)
    tag_bytes = struct.pack("<I", EXTERNAL_REFERENCE_TABLE_TAG)
    search_offset = serialized_start
    candidates: list[tuple[int, ...]] = []

    while True:
        tag_offset = data.find(tag_bytes, search_offset, serialized_end)
        if tag_offset < 0:
            break
        search_offset = tag_offset + 1
        if (
            tag_offset < serialized_start + 4
            or (tag_offset - serialized_start) % 4
            or resource.u32(tag_offset - 4) != mesh_count
            or tag_offset + 8 > serialized_end
            or resource.u32(tag_offset + 4) != MATERIAL_DEPENDENCY_TYPE
        ):
            continue

        references_offset = tag_offset + 8
        references_size = mesh_count * 8
        if references_offset + references_size > serialized_end:
            raise FormatError(
                f"{resource.label}: truncated model material reference table"
            )

        material_indices: list[int | None] = [None] * mesh_count
        for reference_index in range(mesh_count):
            entry_virtual_offset, material_index = struct.unpack_from(
                "<2I",
                data,
                references_offset + reference_index * 8,
            )
            mesh_index = mesh_indices_by_offset.get(entry_virtual_offset)
            if mesh_index is None:
                raise FormatError(
                    f"{resource.label}: invalid material reference mesh "
                    f"offset 0x{entry_virtual_offset:X}"
                )
            if material_index >= material_count:
                raise FormatError(
                    f"{resource.label}: mesh {mesh_index} references "
                    f"material {material_index}, but the model has "
                    f"{material_count} materials"
                )
            if material_indices[mesh_index] is not None:
                raise FormatError(
                    f"{resource.label}: duplicate material reference for "
                    f"mesh {mesh_index}"
                )
            material_indices[mesh_index] = material_index

        if any(index is None for index in material_indices):
            raise FormatError(
                f"{resource.label}: incomplete model material reference table"
            )
        candidates.append(
            tuple(index for index in material_indices if index is not None)
        )

    if len(candidates) > 1:
        raise FormatError(
            f"{resource.label}: found multiple model material reference tables"
        )
    if candidates:
        return candidates[0]
    if material_count == 1:
        return (0,) * mesh_count
    return None


def _decode_gib_bone_indices(
    resource: NsirsrcFile,
    *,
    serialized_size: int,
) -> frozenset[int]:
    physics_pointer = resource.u32(
        resource.physical_offset(MODEL_PHYSICS_POINTER_OFFSET)
    )
    if not physics_pointer:
        return frozenset()

    physics_end = resource.u32(
        resource.physical_offset(MODEL_MATERIAL_RECORDS_POINTER_OFFSET)
    )
    if not physics_pointer < physics_end <= serialized_size:
        raise FormatError(
            f"{resource.label}: physics metadata range is invalid"
        )

    bone_count = resource.u32(
        resource.physical_offset(MODEL_BONE_RECORD_COUNT_OFFSET)
    )
    if bone_count > MAX_BONES:
        raise FormatError(
            f"{resource.label}: bone count {bone_count} exceeds {MAX_BONES}"
        )
    if not bone_count:
        return frozenset()

    data = resource.data
    physical_end = resource.physical_offset(physics_end)
    gib_bones: set[int] = set()
    last_record = physics_end - MODEL_TRAUMA_RECORD_SIZE
    for record_pointer in range(
        physics_pointer,
        last_record + 1,
        4,
    ):
        record_offset = resource.physical_offset(record_pointer)
        (
            bone_index,
            properties_pointer,
            property_count,
            sentinel_a,
            sentinel_b,
        ) = struct.unpack_from("<5I", data, record_offset)
        if (
            bone_index >= bone_count
            or not physics_pointer <= properties_pointer < physics_end
            or not 0 < property_count <= MAX_TRAUMA_PROPERTIES
            or sentinel_a != 0xFFFFFFFF
            or sentinel_b != 0xFFFFFFFF
            or properties_pointer
            > physics_end - MODEL_TRAUMA_PROPERTY_NAME_OFFSET - 1
        ):
            continue

        name_offset = resource.physical_offset(
            properties_pointer + MODEL_TRAUMA_PROPERTY_NAME_OFFSET
        )
        name_limit = min(
            physical_end,
            name_offset + 64,
        )
        name_end = data.find(b"\0", name_offset, name_limit)
        if name_end < 0:
            continue
        property_name = data[name_offset:name_end]
        if property_name.startswith(b"prp_traumaBase_") and (
            property_name.endswith(b"_gib")
            or b"_gib_" in property_name
        ):
            gib_bones.add(bone_index)
    return frozenset(gib_bones)


def _decode_lod_mesh_entries(
    resource: NsirsrcFile,
    *,
    serialized_size: int,
) -> tuple[tuple[int, int, int], ...]:
    lod_count = resource.u32(
        resource.physical_offset(MODEL_LOD_COUNT_OFFSET)
    )
    if lod_count > MAX_LODS:
        raise FormatError(
            f"{resource.label}: LOD count {lod_count} exceeds {MAX_LODS}"
        )
    if not lod_count:
        return ()

    lod_records_pointer = resource.u32(
        resource.physical_offset(MODEL_LOD_RECORDS_POINTER_OFFSET)
    )
    lod_records_end = lod_records_pointer + lod_count * MODEL_LOD_RECORD_SIZE
    if not lod_records_pointer or lod_records_end > serialized_size:
        raise FormatError(
            f"{resource.label}: LOD table is outside the serialized model"
        )

    entries: list[tuple[int, int, int]] = []
    for stream_index in reversed(range(lod_count)):
        lod_level = lod_count - stream_index - 1
        record_virtual_offset = (
            lod_records_pointer + stream_index * MODEL_LOD_RECORD_SIZE
        )
        record_offset = resource.physical_offset(record_virtual_offset)
        mesh_count = resource.u32(
            record_offset + MODEL_LOD_MESH_COUNT_OFFSET
        )
        if mesh_count > MAX_MESHES - len(entries):
            raise FormatError(
                f"{resource.label}: total mesh count exceeds {MAX_MESHES}"
            )
        if not mesh_count:
            continue
        mesh_entries_pointer = resource.u32(
            record_offset + MODEL_LOD_MESH_ENTRIES_POINTER_OFFSET
        )
        mesh_entries_end = (
            mesh_entries_pointer + mesh_count * MODEL_MESH_ENTRY_SIZE
        )
        if not mesh_entries_pointer or mesh_entries_end > serialized_size:
            raise FormatError(
                f"{resource.label}: LOD {lod_level} mesh table is outside "
                "the serialized model"
            )
        entries.extend(
            (
                lod_level,
                stream_index,
                mesh_entries_pointer + mesh_index * MODEL_MESH_ENTRY_SIZE,
            )
            for mesh_index in range(mesh_count)
        )
    return tuple(entries)


def _decode_bone_palette(
    resource: NsirsrcFile,
    *,
    entry_offset: int,
    model_bone_count: int,
    mesh_index: int,
) -> np.ndarray:
    if not model_bone_count:
        raise FormatError(
            f"{resource.label}: weighted mesh {mesh_index} has no model bones"
        )

    palette_count = resource.data[
        entry_offset + MODEL_MESH_BONE_PALETTE_COUNT_OFFSET
    ]
    if not 0 < palette_count <= MODEL_MESH_BONE_PALETTE_CAPACITY:
        raise FormatError(
            f"{resource.label}: mesh {mesh_index} has invalid bone palette "
            f"count {palette_count}"
        )
    palette = np.frombuffer(
        resource.data,
        dtype=np.uint8,
        count=palette_count,
        offset=entry_offset + MODEL_MESH_BONE_PALETTE_OFFSET,
    )
    if int(palette.max(initial=0)) >= model_bone_count:
        raise FormatError(
            f"{resource.label}: mesh {mesh_index} bone palette exceeds its "
            f"{model_bone_count} model bones"
        )
    return palette


def _decode_skin_influences(
    resource: NsirsrcFile,
    *,
    entry_offset: int,
    vertex_data_offset: int,
    vertex_count: int,
    stride: int,
    model_bone_count: int,
    mesh_index: int,
) -> tuple[np.ndarray, np.ndarray]:
    palette = _decode_bone_palette(
        resource,
        entry_offset=entry_offset,
        model_bone_count=model_bone_count,
        mesh_index=mesh_index,
    )
    palette_count = len(palette)

    weights = np.ndarray(
        (vertex_count, 4),
        dtype="<f2",
        buffer=resource.data,
        offset=vertex_data_offset + 32,
        strides=(stride, 2),
    ).astype(np.float32)
    local_indices = np.ndarray(
        (vertex_count, 4),
        dtype=np.uint8,
        buffer=resource.data,
        offset=vertex_data_offset + 40,
        strides=(stride, 1),
    )
    if not np.all(np.isfinite(weights)):
        raise FormatError(
            f"{resource.label}: mesh {mesh_index} contains non-finite skin "
            "weights"
        )
    if np.any(weights < 0.0):
        raise FormatError(
            f"{resource.label}: mesh {mesh_index} contains negative skin "
            "weights"
        )

    weight_sums = weights.sum(axis=1)
    if np.any(weight_sums <= 0.0):
        raise FormatError(
            f"{resource.label}: mesh {mesh_index} contains a zero-sum skin "
            "vertex"
        )
    if np.any(np.abs(weight_sums - 1.0) > SKIN_WEIGHT_SUM_TOLERANCE):
        raise FormatError(
            f"{resource.label}: mesh {mesh_index} contains skin weights "
            "outside the verified normalization tolerance"
        )

    active = weights > 0.0
    if np.any(local_indices[active] >= palette_count):
        raise FormatError(
            f"{resource.label}: mesh {mesh_index} skin index exceeds its "
            f"{palette_count}-bone palette"
        )
    bone_indices = np.zeros((vertex_count, 4), dtype=np.uint16)
    bone_indices[active] = palette[local_indices[active]]

    for slot in range(1, 4):
        for previous_slot in range(slot):
            duplicate = (
                (weights[:, slot] > 0.0)
                & (weights[:, previous_slot] > 0.0)
                & (bone_indices[:, slot] == bone_indices[:, previous_slot])
            )
            if np.any(duplicate):
                weights[duplicate, previous_slot] += weights[duplicate, slot]
                weights[duplicate, slot] = 0.0
                bone_indices[duplicate, slot] = 0

    weights /= weight_sums[:, np.newaxis]
    return bone_indices, weights


def _decode_rigid_influences(
    resource: NsirsrcFile,
    *,
    entry_offset: int,
    vertex_count: int,
    model_bone_count: int,
    mesh_index: int,
) -> tuple[np.ndarray, np.ndarray]:
    palette = _decode_bone_palette(
        resource,
        entry_offset=entry_offset,
        model_bone_count=model_bone_count,
        mesh_index=mesh_index,
    )
    if len(palette) != 1:
        raise FormatError(
            f"{resource.label}: rigid mesh {mesh_index} has "
            f"{len(palette)} palette bones"
        )
    bone_indices = np.zeros((vertex_count, 4), dtype=np.uint16)
    bone_indices[:, 0] = palette[0]
    bone_weights = np.zeros((vertex_count, 4), dtype=np.float32)
    bone_weights[:, 0] = 1.0
    return bone_indices, bone_weights


def decode_smf(data: bytes, *, label: str) -> DecodedSmf:
    resource = NsirsrcFile(data, label=label)
    if resource.root_type != MODEL_ROOT_TYPE:
        raise FormatError(f"{label}: root type 0x{resource.root_type:X} is not Model")

    streams = resource.stream_descriptors()
    if not streams:
        raise FormatError(f"{label}: model has no GPU stream descriptors")
    serialized_size = min(stream.primary_offset for stream in streams)
    lod_mesh_entries = _decode_lod_mesh_entries(
        resource,
        serialized_size=serialized_size,
    )
    mesh_count = len(lod_mesh_entries)
    skeleton = _decode_skeleton(
        resource,
        serialized_size=serialized_size,
    )
    model_bone_count = skeleton.bone_count if skeleton is not None else 0
    all_material_names = tuple(
        dependency.name
        for dependency in resource.dependencies()
        if dependency.type_id == MATERIAL_DEPENDENCY_TYPE
    )
    material_count_offset = resource.physical_offset(MODEL_MATERIAL_COUNT_OFFSET)
    material_count = resource.u32(material_count_offset)
    if material_count != len(all_material_names):
        raise FormatError(
            f"{label}: model declares {material_count} materials but has "
            f"{len(all_material_names)} BMX dependencies"
        )
    if not mesh_count:
        if skeleton is None:
            raise MissingResourceError(
                f"{label}: model contains neither renderable GBuffer "
                "geometry nor a recoverable skeleton"
            )
        return DecodedSmf(
            name=resource.resource_name,
            meshes=[],
            material_names=(),
            material_dependency_count=len(all_material_names),
            bounding_sphere=_bounding_sphere(skeleton.bind_world[:, :3, 3]),
            material_binding_supported=True,
            skeleton=skeleton,
        )

    highest_stream_index = max(
        stream_index
        for _lod_level, stream_index, _entry_offset in lod_mesh_entries
    )
    if highest_stream_index >= len(streams):
        raise FormatError(
            f"{label}: model has {highest_stream_index + 1} populated LODs "
            f"but only {len(streams)} GPU streams"
        )

    material_indices = _decode_material_indices(
        resource,
        mesh_entry_offsets=tuple(
            entry_offset
            for _lod_level, _stream_index, entry_offset in lod_mesh_entries
        ),
        material_count=material_count,
        serialized_size=serialized_size,
    )
    material_binding_supported = material_indices is not None
    material_names = all_material_names if material_binding_supported else ()
    gib_bone_indices = _decode_gib_bone_indices(
        resource,
        serialized_size=serialized_size,
    )

    meshes: list[MeshGeometry] = []
    for mesh_index, (
        lod_level,
        stream_index,
        entry_virtual_offset,
    ) in enumerate(
        lod_mesh_entries
    ):
        buffer_origin = (
            resource.physical_offset(streams[stream_index].primary_offset)
            + MODEL_STREAM_HEADER_SIZE
        )
        resource.require(buffer_origin, 1, purpose="model GPU stream")
        entry_offset = resource.physical_offset(entry_virtual_offset)
        record_offset = entry_offset + MODEL_MESH_GEOMETRY_OFFSET
        resource.require(
            record_offset,
            MODEL_MESH_ENTRY_SIZE - MODEL_MESH_GEOMETRY_OFFSET,
            purpose=f"mesh {mesh_index} record",
        )
        summary_pointer = resource.u32(record_offset + MODEL_GBUFFER_POINTER_OFFSET)
        summary_offset = resource.physical_offset(summary_pointer)
        resource.require(
            summary_offset,
            0x20,
            purpose=f"mesh {mesh_index} GBuffer summary",
        )
        (
            stream_count,
            _allocation_size,
            summary_index_count,
            summary_vertex_count,
            format_code,
            reserved,
            descriptor_pointer,
            trailing_reserved,
        ) = struct.unpack_from("<8I", data, summary_offset)
        if stream_count != 1 or reserved or trailing_reserved:
            raise FormatError(
                f"{label}: mesh {mesh_index} has an unsupported GBuffer summary"
            )
        try:
            stride = GBUFFER_STRIDES[format_code]
        except KeyError as exc:
            raise FormatError(
                f"{label}: mesh {mesh_index} uses unsupported Vita "
                f"vertex format 0x{format_code:X}"
            ) from exc

        descriptor_offset = resource.physical_offset(descriptor_pointer)
        resource.require(
            descriptor_offset,
            0x40,
            purpose=f"mesh {mesh_index} geometry descriptor",
        )
        (
            allocation_size,
            resident_size,
            vertex_count,
            vertex_offset,
            triangle_count,
            index_count,
            index_offset,
            descriptor_reserved,
        ) = struct.unpack_from("<8I", data, descriptor_offset)
        if (
            allocation_size != resident_size
            or descriptor_reserved
            or index_count != triangle_count * 3
            or vertex_count != summary_vertex_count
            or index_count != summary_index_count
        ):
            raise FormatError(
                f"{label}: mesh {mesh_index} geometry descriptor is inconsistent"
            )
        if not 0 < vertex_count <= MAX_VERTICES:
            raise FormatError(
                f"{label}: mesh {mesh_index} has invalid vertex count {vertex_count}"
            )
        if not 0 < index_count <= MAX_INDICES:
            raise FormatError(
                f"{label}: mesh {mesh_index} has invalid index count {index_count}"
            )
        vertex_end = vertex_offset + vertex_count * stride
        if not 0 <= index_offset - vertex_end < 16:
            raise FormatError(
                f"{label}: mesh {mesh_index} vertex/index alignment is invalid"
            )

        vertex_data_offset = buffer_origin + vertex_offset
        index_data_offset = buffer_origin + index_offset
        resource.require(
            vertex_data_offset,
            vertex_count * stride,
            purpose=f"mesh {mesh_index} vertices",
        )
        resource.require(
            index_data_offset,
            index_count * 2,
            purpose=f"mesh {mesh_index} indices",
        )
        positions = np.ndarray(
            (vertex_count, 3),
            dtype="<f4",
            buffer=data,
            offset=vertex_data_offset,
            strides=(stride, 4),
        ).copy()
        u_values = np.ndarray(
            (vertex_count,),
            dtype="<f4",
            buffer=data,
            offset=vertex_data_offset + 12,
            strides=(stride,),
        ).copy()
        v_values = (
            np.ndarray(
                (vertex_count,),
                dtype="<f2",
                buffer=data,
                offset=vertex_data_offset + 22,
                strides=(stride,),
            ).astype(np.float32)
            + 1.0
        )
        uvs = np.column_stack((u_values, v_values)).astype(
            np.float32,
            copy=False,
        )
        if not np.all(np.isfinite(positions)):
            raise FormatError(
                f"{label}: mesh {mesh_index} contains non-finite positions"
            )
        if not np.all(np.isfinite(uvs)):
            raise FormatError(f"{label}: mesh {mesh_index} contains non-finite UVs")
        indices = np.frombuffer(
            data,
            dtype="<u2",
            count=index_count,
            offset=index_data_offset,
        ).astype(np.int32)
        if int(indices.max(initial=0)) >= vertex_count:
            raise FormatError(
                f"{label}: mesh {mesh_index} index exceeds its {vertex_count} vertices"
            )
        shader_index = material_indices[mesh_index] if material_indices else 0
        packed_bone_range = resource.u32(
            entry_offset + MODEL_MESH_BONE_RANGE_OFFSET
        )
        mesh_bone_count = (packed_bone_range >> 16) & 0xFF
        mesh_bone_start = packed_bone_range & 0xFFFF
        is_damage_permutation = (
            format_code == GBUFFER_FORMAT_RIGID
            and mesh_bone_count == 1
            and mesh_bone_start in gib_bone_indices
        )
        bone_indices = None
        bone_weights = None
        if format_code == GBUFFER_FORMAT_SKINNED:
            bone_indices, bone_weights = _decode_skin_influences(
                resource,
                entry_offset=entry_offset,
                vertex_data_offset=vertex_data_offset,
                vertex_count=vertex_count,
                stride=stride,
                model_bone_count=model_bone_count,
                mesh_index=mesh_index,
            )
        else:
            bone_indices, bone_weights = _decode_rigid_influences(
                resource,
                entry_offset=entry_offset,
                vertex_count=vertex_count,
                model_bone_count=model_bone_count,
                mesh_index=mesh_index,
            )
        meshes.append(
            MeshGeometry(
                shader_index=shader_index,
                positions=positions,
                uvs=uvs,
                indices=indices,
                bone_indices=bone_indices,
                bone_weights=bone_weights,
                segment_index=(
                    mesh_bone_start if is_damage_permutation else 0
                ),
                is_damage_permutation=is_damage_permutation,
                lod_level=lod_level,
            )
        )

    combined_positions = np.concatenate(
        [
            mesh.positions
            for mesh in meshes
            if mesh.lod_level == 0
        ],
        axis=0,
    )
    return DecodedSmf(
        name=resource.resource_name,
        meshes=meshes,
        material_names=material_names,
        material_dependency_count=len(all_material_names),
        bounding_sphere=_bounding_sphere(combined_positions),
        material_binding_supported=material_binding_supported,
        skeleton=skeleton,
    )
