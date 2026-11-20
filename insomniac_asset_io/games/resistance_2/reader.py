from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath

import numpy as np

from ...core.binary import IghwFile
from ...core.errors import (
    AmbiguousResourceError,
    FormatError,
    InsomniacError,
    MissingResourceError,
)
from ...core.source import GameSource
from .animations import AnimationProfile, decode_animset
from .formats import (
    AssetKind,
    AssetPointer,
    Bone,
    CollisionMesh,
    CollisionPrimitive,
    DecodedClip,
    DecodedTexture,
    GameplayRegion,
    MeshGeometry,
    MobyAsset,
    PlacedInstance,
    ShaderInfo,
    Skeleton,
    SourceSummary,
    TerrainMesh,
    TextureInfo,
    TieAsset,
    Zone,
)
from .textures import decode_texture, texture_mip_size

LOGGER = logging.getLogger(__name__)

SECT_MOBY_HEADER = 0xD100
SECT_MOBY_NAME = 0xD200
SECT_MOBY_SKELETON = 0xD300
SECT_MOBY_SHADER_TABLE = 0x5600
SECT_MOBY_INDICES = 0xE100
SECT_MOBY_VERTICES = 0xE200

SECT_TIE_VERTICES = 0x3000
SECT_TIE_INDICES = 0x3200
SECT_TIE_HEADER = 0x3400
SECT_TIE_NAME = 0x3410
SECT_TIE_SHADER_TABLE = 0x5600

SECT_SHADER_MATERIAL = 0x5000
SECT_SHADER_REFS = 0x5D00
SECT_TEXTURE_META = 0x1D140
SECT_HIGHMIP_POINTERS = 0x1D1C0
SECT_GLOBAL_TEXTURE = 0x5200
SECT_GLOBAL_TEXTURE_DATA_V1 = 0x5300
SECT_GLOBAL_TEXTURE_DATA = 0x5400
SECT_GLOBAL_TEXTURE_RESOURCE = 0x5A00

SECT_UFRAG_VERTICES = 0x6000
SECT_UFRAG_INDICES = 0x6100
SECT_UFRAGS = 0x6200
SECT_UFRAG_SHADER_TABLE = 0x71A0
SECT_TIE_TUID_TABLE = 0x7200
SECT_TIE_INSTANCES = 0x7240
SECT_TIE_NAME_POINTERS = 0x72C0
SECT_FOLIAGE_INSTANCES = 0x7440
SECT_SHRUB_INSTANCES = 0x7540

SECT_GAMEPLAY_STRINGS = 0x25000
SECT_PRIUS_MOBY_INSTANCES = 0x25048
SECT_PRIUS_MOBY_METADATA = 0x2504C
SECT_REGION_MOBY_TUIDS = 0x1C600

MAX_ASSET_SIZE = 512 * 1024 * 1024
MAX_MESHES = 1_000_000
MAX_BONES = 4096
MAX_COLLISION_PRIMITIVES = 65_535
MAX_COLLISION_VERTICES = 512
MAX_COLLISION_FACES = 65_535


@dataclass(frozen=True, slots=True)
class _GlobalTextureLocation:
    tuid: int
    descriptor_index: int
    header_path: str
    texel_path: str


class Resistance2Reader:
    """Direct reader for the Resistance 2 Insomniac Engine v2 chain."""

    game_id = "RESISTANCE_2"
    game_label = "Resistance 2"
    engine_id = "V2"
    asset_prefix = "R2"
    shader_texture_slot_count = 4
    shader_material_word_offset = 0x10
    shader_material_values_offset = 0x20
    shader_material_vector_count = 6
    shader_material_tail_count = 0
    material_surface_channels_by_default = True
    material_emission_uses_albedo = True
    material_emission_disabled_control_mask = 0
    material_luminance_alpha_shader_tuids: frozenset[int] = frozenset()
    collision_quantization_markers = (256, 257)
    animation_profile = AnimationProfile.RESISTANCE_2

    def __init__(self, source: GameSource) -> None:
        self.source = source
        self.assetlookup_path = self._find_level_resource("assetlookup.dat")
        parent = str(PurePosixPath(self.assetlookup_path).parent)
        self.resource_prefix = "" if parent == "." else parent
        self.assetlookup = IghwFile(
            source.read(self.assetlookup_path),
            label=self.assetlookup_path,
        )
        self._pointer_cache: dict[AssetKind, tuple[AssetPointer, ...]] = {}
        self._pointer_maps: dict[AssetKind, dict[int, AssetPointer]] = {}
        self._shader_cache: dict[int, ShaderInfo] = {}
        self._texture_cache: dict[int, DecodedTexture] = {}
        self._texture_fallback_cache: dict[
            tuple[int, str],
            DecodedTexture,
        ] = {}
        self._texture_path_index: dict[str, tuple[int, ...]] | None = None
        self._sibling_texture_index: dict[int, tuple[Path, ...]] | None = None
        self.warnings: list[str] = []

    def _find_level_resource(self, filename: str) -> str:
        suffix = "/" + filename.casefold()
        candidates = [
            path
            for path in self.source.paths
            if path.casefold() == filename.casefold()
            or path.casefold().endswith(suffix)
        ]
        if not candidates:
            raise MissingResourceError(
                f"{self.game_label} source has no {filename}"
            )
        level_candidates = [
            path for path in candidates if "/levels/" in path.casefold()
        ]
        if len(level_candidates) == 1:
            return level_candidates[0]
        if len(candidates) == 1:
            return candidates[0]
        choices = ", ".join(candidates[:8])
        raise FormatError(
            f"{self.game_label} source contains multiple {filename} "
            f"resources: "
            f"{choices}. Open a specific level_cached.psarc instead."
        )

    def resource_path(self, relative_path: str) -> str:
        relative = relative_path.replace("\\", "/").lstrip("/")
        if not self.resource_prefix:
            return relative
        return f"{self.resource_prefix}/{relative}"

    def inspect(self) -> SourceSummary:
        return SourceSummary(
            counts={kind: len(self.asset_pointers(kind)) for kind in AssetKind},
            resource_prefix=self.resource_prefix,
        )

    def asset_pointers(self, kind: AssetKind) -> tuple[AssetPointer, ...]:
        cached = self._pointer_cache.get(kind)
        if cached is not None:
            return cached
        section = self.assetlookup.section(kind.section_id)
        if section is None:
            pointers: tuple[AssetPointer, ...] = ()
        else:
            if section.length % 16:
                raise FormatError(
                    f"{self.assetlookup_path}: section 0x{section.id:X} has "
                    f"a non-integral pointer-table length {section.length}"
                )
            count = section.length // 16
            self.assetlookup.require(
                section.offset,
                section.length,
                purpose=f"{kind.name} pointer table",
            )
            dtype = np.dtype(
                [
                    ("tuid", self.assetlookup.endian + "u8"),
                    ("offset", self.assetlookup.endian + "u4"),
                    ("length", self.assetlookup.endian + "u4"),
                ]
            )
            rows = np.frombuffer(
                self.assetlookup.data,
                dtype=dtype,
                count=count,
                offset=section.offset,
            )
            pointers = tuple(
                AssetPointer(
                    tuid=int(row["tuid"]),
                    offset=int(row["offset"]),
                    length=int(row["length"]),
                )
                for row in rows
            )
        self._pointer_cache[kind] = pointers
        self._pointer_maps[kind] = {pointer.tuid: pointer for pointer in pointers}
        return pointers

    def _pointer(self, kind: AssetKind, tuid: int) -> AssetPointer:
        self.asset_pointers(kind)
        try:
            return self._pointer_maps[kind][tuid]
        except KeyError as exc:
            raise MissingResourceError(
                f"{kind.name.title()} asset 0x{tuid:016X} is not present "
                f"in {self.assetlookup_path}"
            ) from exc

    def _asset_bytes(self, kind: AssetKind, tuid: int) -> bytes:
        pointer = self._pointer(kind, tuid)
        if pointer.length > MAX_ASSET_SIZE:
            raise FormatError(
                f"{kind.name} 0x{tuid:016X} claims {pointer.length} bytes"
            )
        return self.source.read_slice(
            self.resource_path(kind.filename),
            pointer.offset,
            pointer.length,
        )

    def read_asset_name(
        self,
        kind: AssetKind,
        tuid: int,
    ) -> str | None:
        section_id = {
            AssetKind.MOBY: SECT_MOBY_NAME,
            AssetKind.TIE: SECT_TIE_NAME,
        }.get(kind)
        if section_id is None:
            return None

        pointer = self._pointer(kind, tuid)
        label = f"{kind.name.lower()} 0x{tuid:016X}"
        header_length = min(pointer.length, 0x20)
        header = self.source.read_slice(
            self.resource_path(kind.filename),
            pointer.offset,
            header_length,
        )
        if len(header) < 0x10:
            raise FormatError(f"{label}: file is too short for an IGHW header")
        if header[:4] == IghwFile.MAGIC_BIG:
            byte_order = "big"
        elif header[:4] == IghwFile.MAGIC_LITTLE:
            byte_order = "little"
        else:
            raise FormatError(f"{label}: bad IGHW magic {header[:4]!r}")
        version = (
            int.from_bytes(header[4:6], byte_order),
            int.from_bytes(header[6:8], byte_order),
        )
        table_offset = 0x20 if version == (1, 1) else 0x10
        if version not in {(1, 1), (0, 2)}:
            raise FormatError(
                f"{label}: unsupported IGHW version {version[0]}.{version[1]}"
            )
        section_count = int.from_bytes(header[8:12], byte_order)
        if section_count > IghwFile.MAX_SECTIONS:
            raise FormatError(
                f"{label}: unreasonable section count {section_count}"
            )
        table_length = table_offset + section_count * 16
        if table_length > pointer.length:
            raise FormatError(f"{label}: section table exceeds the asset")
        table = self.source.read_slice(
            self.resource_path(kind.filename),
            pointer.offset,
            table_length,
        )
        section = IghwFile(table, label=label).section(section_id)
        if section is None or not section.length:
            return None
        if section.length > 64 * 1024:
            raise FormatError(
                f"{label}: unreasonable name length {section.length}"
            )
        if section.offset + section.length > pointer.length:
            raise FormatError(f"{label}: name section exceeds the asset")
        raw_name = self.source.read_slice(
            self.resource_path(kind.filename),
            pointer.offset + section.offset,
            section.length,
        )
        name = raw_name.partition(b"\0")[0].decode(
            "utf-8",
            errors="replace",
        )
        return name or None

    def read_moby(self, tuid: int) -> MobyAsset:
        data = self._asset_bytes(AssetKind.MOBY, tuid)
        ighw = IghwFile(data, label=f"moby 0x{tuid:016X}")
        return self._parse_moby(ighw, tuid)

    def _parse_moby(self, ighw: IghwFile, tuid_hint: int) -> MobyAsset:
        header = ighw.require_section(SECT_MOBY_HEADER)
        base = header.offset
        bounding_sphere = (
            *ighw.vec3(base),
            ighw.f32(base + 0x0C),
        )
        bind_pose_inverse_offset = ighw.i16(base + 0x10)
        segment_count = ighw.u16(base + 0x18)
        segments_offset = ighw.u32(base + 0x24)
        collision_primitive_offset = ighw.u32(base + 0x30)
        collision_mesh_offset = ighw.u32(base + 0x34)
        animset_raw = ighw.u64(base + 0x50)
        mesh_scale = ighw.f32(base + 0x70)
        bangle_count = ighw.u32(base + 0x90)
        default_draw_mask = ighw.u64(base + 0x98)
        header_tuid = ighw.u64(base + 0xB0)
        tuid = header_tuid or tuid_hint
        if bangle_count > segment_count:
            raise FormatError(
                f"{ighw.label}: bangle count {bangle_count} exceeds segment "
                f"count {segment_count}"
            )
        customization_segments = (
            tuple(range(1, segment_count)) if bangle_count else ()
        )
        customization_segment_set = frozenset(customization_segments)

        name_section = ighw.section(SECT_MOBY_NAME)
        name = (
            ighw.cstring(name_section.offset)
            if name_section is not None
            else f"moby_{tuid:016X}"
        )
        shader_tuids = self._read_shader_table(ighw, SECT_MOBY_SHADER_TABLE)
        skeleton = self._read_skeleton(ighw)

        index_section = ighw.section(SECT_MOBY_INDICES)
        vertex_section = ighw.section(SECT_MOBY_VERTICES)
        meshes: list[MeshGeometry] = []
        if index_section is not None and vertex_section is not None:
            index_buffer = ighw.view(index_section.offset, index_section.length)
            vertex_buffer = ighw.view(
                vertex_section.offset, vertex_section.length
            )
            for segment_index in range(segment_count):
                segment = segments_offset + segment_index * 8
                mesh_table = ighw.u32(segment)
                mesh_count = ighw.u32(segment + 4)
                if mesh_count > MAX_MESHES:
                    raise FormatError(
                        f"{ighw.label}: unreasonable mesh count {mesh_count}"
                    )
                for mesh_index in range(mesh_count):
                    mesh_offset = mesh_table + mesh_index * 0x40
                    index_index = ighw.u32(mesh_offset)
                    vertex_offset = ighw.u32(mesh_offset + 4)
                    shader_index = ighw.u16(mesh_offset + 8)
                    vertex_count = ighw.u16(mesh_offset + 0x0A)
                    bone_map_count = ighw.u8(mesh_offset + 0x0C)
                    vertex_type = ighw.u8(mesh_offset + 0x0D)
                    index_count = ighw.u32(mesh_offset + 0x10)
                    bone_map_offset = ighw.u32(mesh_offset + 0x20)
                    bone_map = self._read_bone_map(
                        ighw, bone_map_offset, bone_map_count
                    )
                    meshes.append(
                        self._decode_moby_mesh(
                            index_buffer=index_buffer,
                            vertex_buffer=vertex_buffer,
                            index_index=index_index,
                            index_count=index_count,
                            vertex_offset=vertex_offset,
                            vertex_count=vertex_count,
                            stride=0x1C if vertex_type == 1 else 0x14,
                            shader_index=shader_index,
                            scale=mesh_scale,
                            bone_map=bone_map,
                            endian=ighw.endian,
                            label=ighw.label,
                            segment_index=segment_index,
                            is_damage_permutation=(
                                segment_index not in customization_segment_set
                                and
                                bool(default_draw_mask)
                                and segment_index < 64
                                and not (
                                    default_draw_mask & (1 << segment_index)
                                )
                            ),
                        )
                    )
        collision_primitives = (
            self._read_collision_primitives(
                ighw,
                collision_primitive_offset,
            )
            if collision_primitive_offset
            else ()
        )
        collision_meshes = (
            (self._read_collision_mesh(ighw, collision_mesh_offset),)
            if collision_mesh_offset
            else ()
        )
        return MobyAsset(
            tuid=tuid,
            name=name,
            meshes=meshes,
            bounding_sphere=bounding_sphere,
            shader_tuids=shader_tuids,
            skeleton=skeleton,
            animset_hash=animset_raw or None,
            bind_pose_inverse_offset=bind_pose_inverse_offset,
            collision_primitives=collision_primitives,
            collision_meshes=collision_meshes,
            customization_segments=customization_segments,
            default_draw_mask=default_draw_mask,
        )

    @staticmethod
    def _decode_moby_mesh(
        *,
        index_buffer: memoryview,
        vertex_buffer: memoryview,
        index_index: int,
        index_count: int,
        vertex_offset: int,
        vertex_count: int,
        stride: int,
        shader_index: int,
        scale: float,
        bone_map: np.ndarray,
        endian: str,
        label: str,
        segment_index: int,
        is_damage_permutation: bool,
    ) -> MeshGeometry:
        index_byte_offset = index_index * 2
        index_byte_length = index_count * 2
        if index_byte_offset + index_byte_length > len(index_buffer):
            raise FormatError(f"{label}: moby index buffer overrun")
        indices = np.frombuffer(
            index_buffer,
            dtype=endian + "u2",
            count=index_count,
            offset=index_byte_offset,
        ).astype(np.int32)

        vertex_length = vertex_count * stride
        if vertex_offset + vertex_length > len(vertex_buffer):
            raise FormatError(f"{label}: moby vertex buffer overrun")
        positions = np.ndarray(
            shape=(vertex_count, 3),
            dtype=endian + "i2",
            buffer=vertex_buffer,
            offset=vertex_offset,
            strides=(stride, 2),
        ).astype(np.float32)
        positions *= np.float32(scale)
        uv_offset = 0x10 if stride == 0x1C else 0x08
        uvs = np.ndarray(
            shape=(vertex_count, 2),
            dtype=endian + "f2",
            buffer=vertex_buffer,
            offset=vertex_offset + uv_offset,
            strides=(stride, 2),
        ).astype(np.float32)

        bone_indices: np.ndarray | None = None
        bone_weights: np.ndarray | None = None
        if len(bone_map):
            bone_indices = np.zeros((vertex_count, 4), dtype=np.uint16)
            bone_weights = np.zeros((vertex_count, 4), dtype=np.uint8)
            if stride == 0x1C:
                local_indices = np.ndarray(
                    shape=(vertex_count, 4),
                    dtype=np.uint8,
                    buffer=vertex_buffer,
                    offset=vertex_offset + 0x08,
                    strides=(stride, 1),
                )
                bone_weights[:] = np.ndarray(
                    shape=(vertex_count, 4),
                    dtype=np.uint8,
                    buffer=vertex_buffer,
                    offset=vertex_offset + 0x0C,
                    strides=(stride, 1),
                )
                valid = local_indices < len(bone_map)
                bone_indices[valid] = bone_map[local_indices[valid]]
                bone_indices[bone_weights == 0] = 0
            else:
                purpose = np.ndarray(
                    shape=(vertex_count,),
                    dtype=endian + "i2",
                    buffer=vertex_buffer,
                    offset=vertex_offset + 6,
                    strides=(stride,),
                ).astype(np.int32)
                local = np.abs((purpose + 1) // 3)
                valid = local < len(bone_map)
                bone_indices[valid, 0] = bone_map[local[valid]]
                bone_weights[:, 0] = 255
        return MeshGeometry(
            shader_index=shader_index,
            positions=positions,
            uvs=uvs,
            indices=indices,
            bone_indices=bone_indices,
            bone_weights=bone_weights,
            segment_index=segment_index,
            is_damage_permutation=is_damage_permutation,
        )

    @staticmethod
    def _read_collision_primitives(
        ighw: IghwFile,
        offset: int,
    ) -> tuple[CollisionPrimitive, ...]:
        ighw.require(offset, 0x90)
        record_alignment = ighw.u16(offset + 0x80)
        count = ighw.u16(offset + 0x82)
        record_size = 0x50
        if record_alignment != 0x10:
            raise FormatError(
                f"{ighw.label}: unsupported collision primitive layout "
                f"alignment={record_alignment}"
            )
        if count > MAX_COLLISION_PRIMITIVES:
            raise FormatError(
                f"{ighw.label}: unreasonable collision primitive count {count}"
            )

        records_offset = offset + 0x90
        ighw.require(records_offset, count * record_size)
        primitives: list[CollisionPrimitive] = []
        for index in range(count):
            record_offset = records_offset + index * record_size
            kind = ighw.u8(record_offset)
            source_bone = ighw.u8(record_offset + 0x03)
            payload = np.frombuffer(
                ighw.data,
                dtype=ighw.endian + "f4",
                count=16,
                offset=record_offset + 0x10,
            ).astype(np.float32).reshape((4, 4))
            primitives.append(
                CollisionPrimitive(
                    kind=kind,
                    bone_index=None if source_bone == 0xFF else source_bone,
                    data=payload,
                )
            )
        return tuple(primitives)

    @classmethod
    def _read_collision_mesh(
        cls,
        ighw: IghwFile,
        offset: int,
    ) -> CollisionMesh:
        ighw.require(offset, 0xA0)
        vertex_count = ighw.u16(offset + 0x80)
        vertex_group_width = ighw.u16(offset + 0x82)
        quantization_scale = ighw.u16(offset + 0x84)
        face_count = ighw.u16(offset + 0x86)
        vertex_section_length = ighw.u16(offset + 0x88)
        total_data_length = ighw.u16(offset + 0x8C)

        if (
            vertex_group_width != 16
            or quantization_scale
            not in cls.collision_quantization_markers
        ):
            raise FormatError(
                f"{ighw.label}: unsupported collision mesh layout "
                f"group_width={vertex_group_width}, "
                f"quantization={quantization_scale}"
            )
        if vertex_count > MAX_COLLISION_VERTICES:
            raise FormatError(
                f"{ighw.label}: unreasonable collision vertex count "
                f"{vertex_count}"
            )
        if face_count > MAX_COLLISION_FACES:
            raise FormatError(
                f"{ighw.label}: unreasonable collision face count "
                f"{face_count}"
            )

        group_count = (vertex_count + 7) // 8
        header_relative_vertex_length = 0x20 + group_count * 0x30
        payload_relative_vertex_length = 0x10 + group_count * 0x30
        if vertex_section_length == header_relative_vertex_length:
            collision_data_offset = offset + 0x80
        elif vertex_section_length == payload_relative_vertex_length:
            collision_data_offset = offset + 0x90
        else:
            raise FormatError(
                f"{ighw.label}: collision vertex section is "
                f"{vertex_section_length} bytes, expected "
                f"{header_relative_vertex_length} or "
                f"{payload_relative_vertex_length}"
            )
        required_data_length = vertex_section_length + face_count * 4
        aligned_data_length = (required_data_length + 0x0F) & ~0x0F
        if total_data_length != aligned_data_length:
            raise FormatError(
                f"{ighw.label}: collision data is {total_data_length} bytes, "
                f"expected {aligned_data_length}"
            )
        ighw.require(collision_data_offset, total_data_length)

        base_position = np.frombuffer(
            ighw.data,
            dtype=ighw.endian + "i4",
            count=3,
            offset=offset + 0x90,
        ).astype(np.int64)
        positions = np.empty((vertex_count, 3), dtype=np.float32)
        vertices_offset = offset + 0xA0
        for group_index in range(group_count):
            group = np.frombuffer(
                ighw.data,
                dtype=ighw.endian + "u2",
                count=24,
                offset=vertices_offset + group_index * 0x30,
            ).reshape((3, 8)).T
            start = group_index * 8
            end = min(start + 8, vertex_count)
            positions[start:end] = (
                base_position + group[: end - start].astype(np.int64)
            ) / 1024.0

        face_offset = collision_data_offset + vertex_section_length
        face_words = np.frombuffer(
            ighw.data,
            dtype=ighw.endian + "u4",
            count=face_count,
            offset=face_offset,
        ).astype(np.uint32)
        indices = np.column_stack(
            (
                (face_words >> 18) & 0x1FF,
                (face_words >> 9) & 0x1FF,
                face_words & 0x1FF,
            )
        ).astype(np.int32)
        if face_count and int(indices.max()) >= vertex_count:
            raise FormatError(
                f"{ighw.label}: collision face references vertex "
                f"{int(indices.max())}, but only {vertex_count} exist"
            )
        return CollisionMesh(
            positions=positions,
            indices=indices,
            face_types=(face_words >> 27).astype(np.uint8),
        )

    @staticmethod
    def _read_bone_map(
        ighw: IghwFile,
        offset: int,
        count: int,
    ) -> np.ndarray:
        if not offset or not count:
            return np.empty(0, dtype=np.uint16)
        ighw.require(offset, count * 2, purpose="bone palette")
        return np.frombuffer(
            ighw.data,
            dtype=ighw.endian + "u2",
            count=count,
            offset=offset,
        ).astype(np.uint16)

    @staticmethod
    def _read_shader_table(
        ighw: IghwFile,
        section_id: int,
    ) -> tuple[int, ...]:
        section = ighw.section(section_id)
        if section is None or section.count == 0:
            return ()
        ighw.require(
            section.offset, section.count * 8, purpose="shader TUID table"
        )
        values = np.frombuffer(
            ighw.data,
            dtype=ighw.endian + "u8",
            count=section.count,
            offset=section.offset,
        )
        return tuple(int(value) for value in values)

    @staticmethod
    def _recover_shift(raw: int) -> int:
        if raw <= 15:
            return raw
        swapped = ((raw & 0xFF) << 8) | (raw >> 8)
        if swapped <= 15:
            return swapped
        return swapped & 0x1F

    def _read_skeleton(self, ighw: IghwFile) -> Skeleton | None:
        section = ighw.section(SECT_MOBY_SKELETON)
        if section is None:
            return None
        return self._read_skeleton_at(ighw, section.offset)

    def _read_skeleton_at(
        self,
        ighw: IghwFile,
        base: int,
    ) -> Skeleton | None:
        bone_count = ighw.u16(base)
        root_bone = ighw.u16(base + 2)
        bones_offset = ighw.u32(base + 4)
        world_offset = ighw.u32(base + 8)
        inverse_offset = ighw.u32(base + 0x0C)
        scale_shift = self._recover_shift(ighw.u16(base + 0x10))
        translation_shift = self._recover_shift(ighw.u16(base + 0x12))
        if not bone_count or not bones_offset:
            return None
        if bone_count > MAX_BONES:
            raise FormatError(
                f"{ighw.label}: unreasonable skeleton size {bone_count}"
            )

        bones: list[Bone] = []
        for index in range(bone_count):
            offset = bones_offset + index * 8
            bones.append(
                Bone(
                    flags=ighw.u16(offset),
                    parent_index=ighw.i16(offset + 2),
                    child_index=ighw.u16(offset + 4),
                    sibling_index=ighw.u16(offset + 6),
                )
            )
        bind_world = self._read_matrix_array(
            ighw, world_offset, bone_count, "world bind"
        )
        if inverse_offset:
            bind_inverse = self._read_matrix_array(
                ighw, inverse_offset, bone_count, "inverse world bind"
            )
        else:
            bind_inverse = np.linalg.inv(bind_world).astype(np.float32)

        bind_local = np.empty_like(bind_world)
        for index, bone in enumerate(bones):
            if bone.has_parent(index, bone_count):
                bind_local[index] = (
                    bind_inverse[bone.parent_index] @ bind_world[index]
                )
            else:
                bind_local[index] = bind_world[index]
        self._clean_matrices(bind_local)
        self._clean_matrices(bind_world)
        self._clean_matrices(bind_inverse)
        return Skeleton(
            root_bone=root_bone,
            bones=tuple(bones),
            bind_local=bind_local,
            bind_world=bind_world,
            bind_world_inverse=bind_inverse,
            scale_shift=scale_shift,
            translation_shift=translation_shift,
        )

    @staticmethod
    def _read_matrix_array(
        ighw: IghwFile,
        offset: int,
        count: int,
        label: str,
    ) -> np.ndarray:
        if not offset:
            raise FormatError(f"{ighw.label}: missing {label} matrix pointer")
        ighw.require(offset, count * 64, purpose=f"{label} matrices")
        flat = np.frombuffer(
            ighw.data,
            dtype=ighw.endian + "f4",
            count=count * 16,
            offset=offset,
        ).astype(np.float32)
        return flat.reshape(count, 4, 4).transpose(0, 2, 1).copy()

    @staticmethod
    def _clean_matrices(matrices: np.ndarray) -> None:
        matrices[~np.isfinite(matrices)] = 0.0
        matrices[:, 3, :] = (0.0, 0.0, 0.0, 1.0)

    def read_tie(self, tuid: int) -> TieAsset:
        data = self._asset_bytes(AssetKind.TIE, tuid)
        ighw = IghwFile(data, label=f"tie 0x{tuid:016X}")
        header = ighw.require_section(SECT_TIE_HEADER)
        base = header.offset
        mesh_table = ighw.u32(base)
        mesh_count = ighw.u8(base + 0x0F)
        vertex_start = ighw.u32(base + 0x14)
        vertex_size = ighw.u32(base + 0x18)
        scale = np.asarray(ighw.vec3(base + 0x20), dtype=np.float32)
        header_tuid = ighw.u64(base + 0x68)
        actual_tuid = header_tuid or tuid

        vertex_section = ighw.require_section(SECT_TIE_VERTICES)
        index_section = ighw.require_section(SECT_TIE_INDICES)
        vertex_buffer = ighw.view(
            vertex_section.offset + vertex_start, vertex_size
        )
        index_buffer = ighw.view(index_section.offset, index_section.length)
        shader_tuids = self._read_shader_table(ighw, SECT_TIE_SHADER_TABLE)
        meshes: list[MeshGeometry] = []
        for mesh_index in range(mesh_count):
            mesh = mesh_table + mesh_index * 0x40
            index_index = ighw.u32(mesh)
            vertex_index = ighw.u16(mesh + 4)
            vertex_count = ighw.u16(mesh + 8)
            index_count = ighw.u16(mesh + 0x12)
            candidate = ighw.u16(mesh + 0x28)
            if candidate >= len(shader_tuids):
                fallback = ighw.u16(mesh + 0x0C)
                shader_index = (
                    fallback if fallback < len(shader_tuids) else candidate
                )
            else:
                shader_index = candidate
            meshes.append(
                self._decode_tie_mesh(
                    vertex_buffer,
                    index_buffer,
                    vertex_index,
                    vertex_count,
                    index_index,
                    index_count,
                    scale,
                    shader_index,
                    ighw.endian,
                    ighw.label,
                )
            )
        return TieAsset(
            tuid=actual_tuid,
            meshes=meshes,
            shader_tuids=shader_tuids,
        )

    @staticmethod
    def _decode_tie_mesh(
        vertex_buffer: memoryview,
        index_buffer: memoryview,
        vertex_index: int,
        vertex_count: int,
        index_index: int,
        index_count: int,
        scale: np.ndarray,
        shader_index: int,
        endian: str,
        label: str,
    ) -> MeshGeometry:
        stride = 0x14
        vertex_offset = vertex_index * stride
        if vertex_offset + vertex_count * stride > len(vertex_buffer):
            raise FormatError(f"{label}: tie vertex buffer overrun")
        index_offset = index_index * 2
        if index_offset + index_count * 2 > len(index_buffer):
            raise FormatError(f"{label}: tie index buffer overrun")
        positions = np.ndarray(
            shape=(vertex_count, 3),
            dtype=endian + "i2",
            buffer=vertex_buffer,
            offset=vertex_offset,
            strides=(stride, 2),
        ).astype(np.float32)
        positions *= scale
        uvs = np.ndarray(
            shape=(vertex_count, 2),
            dtype=endian + "f2",
            buffer=vertex_buffer,
            offset=vertex_offset + 8,
            strides=(stride, 2),
        ).astype(np.float32)
        indices = np.frombuffer(
            index_buffer,
            dtype=endian + "u2",
            count=index_count,
            offset=index_offset,
        ).astype(np.int32)
        return MeshGeometry(
            shader_index=shader_index,
            positions=positions,
            uvs=uvs,
            indices=indices,
        )

    def read_shader(self, tuid: int) -> ShaderInfo:
        cached = self._shader_cache.get(tuid)
        if cached is not None:
            return cached
        data = self._asset_bytes(AssetKind.SHADER, tuid)
        ighw = IghwFile(data, label=f"shader 0x{tuid:016X}")
        section = ighw.section(SECT_SHADER_REFS)
        texture_id_end = 0x10 + self.shader_texture_slot_count * 4
        texture_path_end = (
            texture_id_end + self.shader_texture_slot_count * 4
        )
        if section is None or section.length < texture_id_end:
            shader = ShaderInfo(tuid, None, None, None, None)
        else:
            texture_id_base = section.offset + 0x10
            texture_ids = [
                ighw.u32(texture_id_base + index * 4)
                for index in range(self.shader_texture_slot_count)
            ]
            lookup_pointer = ighw.u32(section.offset + 0x08)
            lookup_path = (
                ighw.cstring(lookup_pointer) if lookup_pointer else None
            )
            texture_paths = [None] * self.shader_texture_slot_count
            if section.length >= texture_path_end:
                pointers = [
                    ighw.u32(
                        section.offset + texture_id_end + index * 4
                    )
                    for index in range(self.shader_texture_slot_count)
                ]
                texture_paths = [
                    ighw.cstring(pointer) if pointer else None
                    for pointer in pointers
                ]

            material_word = None
            texture_map_indices: tuple[int, ...] = ()
            material_pre_control_values: tuple[int, ...] = ()
            material_values: tuple[
                tuple[float, float, float, float],
                ...,
            ] = ()
            material_tail_values: tuple[float, ...] = ()
            material = ighw.section(SECT_SHADER_MATERIAL)
            if material is not None:
                map_index_length = self.shader_texture_slot_count * 4
                pre_control_length = (
                    self.shader_material_word_offset - map_index_length
                )
                if pre_control_length < 0 or pre_control_length % 4:
                    raise FormatError(
                        f"{ighw.label}: invalid configured material layout"
                    )
                required_material_length = max(
                    self.shader_material_word_offset + 4,
                    self.shader_material_values_offset
                    + self.shader_material_vector_count * 0x10
                    + self.shader_material_tail_count * 4,
                )
                if material.length < required_material_length:
                    raise FormatError(
                        f"{ighw.label}: material section is only "
                        f"{material.length:#x} bytes"
                    )
                ighw.require(
                    material.offset,
                    required_material_length,
                    purpose="shader material read",
                )
                texture_map_indices = tuple(
                    ighw.i32(material.offset + index * 4)
                    for index in range(self.shader_texture_slot_count)
                )
                material_pre_control_values = tuple(
                    ighw.u32(
                        material.offset + map_index_length + index * 4
                    )
                    for index in range(pre_control_length // 4)
                )
                material_word = ighw.u32(
                    material.offset + self.shader_material_word_offset
                )
                material_values = tuple(
                    tuple(
                        ighw.f32(
                            material.offset
                            + self.shader_material_values_offset
                            + vector_index * 0x10
                            + component_index * 4
                        )
                        for component_index in range(4)
                    )
                    for vector_index in range(
                        self.shader_material_vector_count
                    )
                )
                tail_offset = (
                    material.offset
                    + self.shader_material_values_offset
                    + self.shader_material_vector_count * 0x10
                )
                material_tail_values = tuple(
                    ighw.f32(tail_offset + index * 4)
                    for index in range(self.shader_material_tail_count)
                )
            shader = ShaderInfo(
                tuid=tuid,
                albedo_texture_id=texture_ids[0] or None,
                normal_texture_id=texture_ids[1] or None,
                specular_texture_id=texture_ids[2] or None,
                detail_texture_id=texture_ids[3] or None,
                lookup_path=lookup_path,
                texture_lookup_paths=tuple(texture_paths[:4]),
                extra_texture_ids=tuple(
                    texture_id or None for texture_id in texture_ids[4:]
                ),
                extra_texture_lookup_paths=tuple(texture_paths[4:]),
                texture_map_indices=texture_map_indices,
                material_pre_control_values=material_pre_control_values,
                material_word=material_word,
                material_values=material_values,
                material_tail_values=material_tail_values,
            )
        self._shader_cache[tuid] = shader
        return shader

    @cached_property
    def texture_infos(self) -> dict[int, TextureInfo]:
        meta = self.assetlookup.require_section(SECT_TEXTURE_META)
        pointers = self.assetlookup.require_section(SECT_HIGHMIP_POINTERS)
        if meta.length % 4 or pointers.length % 16:
            raise FormatError(
                f"{self.assetlookup_path}: malformed texture metadata tables"
            )
        count = pointers.length // 16
        if meta.length // 4 != count:
            raise FormatError(
                f"{self.assetlookup_path}: texture metadata count does not "
                "match high-mip pointers"
            )
        infos: dict[int, TextureInfo] = {}
        for index in range(count):
            meta_offset = meta.offset + index * 4
            pointer_offset = pointers.offset + index * 16
            tuid = self.assetlookup.u64(pointer_offset)
            texture_id = tuid & 0xFFFF_FFFF
            infos[texture_id] = TextureInfo(
                id=texture_id,
                tuid=tuid,
                format_code=self.assetlookup.u8(meta_offset),
                mip_count=self.assetlookup.u8(meta_offset + 1),
                width=1 << self.assetlookup.u8(meta_offset + 2),
                height=1 << self.assetlookup.u8(meta_offset + 3),
                offset=self.assetlookup.u32(pointer_offset + 8),
                length=self.assetlookup.u32(pointer_offset + 12),
            )
        return infos

    def clear_texture_cache(self) -> None:
        """Drop all decoded textures held by this reader.

        Each decoded texture is a full RGBA array, so the cache grows
        without bound. Callers should clear it once they have built
        everything that needs decoded pixels.
        """
        self._texture_cache.clear()
        self._texture_fallback_cache.clear()

    def read_texture(
        self,
        texture_id: int,
        *,
        lookup_path: str | None = None,
    ) -> DecodedTexture:
        try:
            return self._read_texture_exact(texture_id)
        except MissingResourceError:
            normalized = self._normalize_texture_lookup_path(lookup_path)
            if not normalized:
                raise
            cache_key = (texture_id, normalized)
            cached = self._texture_fallback_cache.get(cache_key)
            if cached is not None:
                return cached
            resolved = self._read_texture_by_lookup_path(
                texture_id,
                normalized,
            )
            if resolved is None:
                raise
            resolved_id, texture = resolved
            self._texture_fallback_cache[cache_key] = texture
            LOGGER.info(
                "Texture 0x%08X resolved as 0x%08X through authored "
                "lookup path %s",
                texture_id,
                resolved_id,
                lookup_path,
            )
            return texture

    def _read_texture_exact(self, texture_id: int) -> DecodedTexture:
        cached = self._texture_cache.get(texture_id)
        if cached is not None:
            return cached
        info = self.texture_infos.get(texture_id)
        if info is None:
            try:
                texture = self._read_global_texture(texture_id)
            except MissingResourceError:
                texture = self._read_sibling_texture(texture_id)
            self._texture_cache[texture_id] = texture
            return texture
        if not info.length:
            try:
                raw = self._asset_bytes(AssetKind.TEXTURE, info.tuid)
            except MissingResourceError as exc:
                raise MissingResourceError(
                    f"Texture 0x{texture_id:08X} has neither a high-mip "
                    "nor an embedded level payload"
                ) from exc
            required = texture_mip_size(
                info.width,
                info.height,
                info.format_code,
            )
            if len(raw) < required:
                raise FormatError(
                    f"Texture 0x{texture_id:08X} embedded payload is "
                    f"{len(raw)} bytes, but its {info.width}x{info.height} "
                    f"format requires at least {required}"
                )
            info = TextureInfo(
                id=info.id,
                tuid=info.tuid,
                format_code=info.format_code,
                mip_count=info.mip_count,
                width=info.width,
                height=info.height,
                offset=0,
                length=len(raw),
            )
            texture = decode_texture(info, raw)
            self._texture_cache[texture_id] = texture
            return texture
        raw = self.source.read_slice(
            self.resource_path("highmips.dat"),
            info.offset,
            info.length,
        )
        texture = decode_texture(info, raw)
        self._texture_cache[texture_id] = texture
        return texture

    @staticmethod
    def _normalize_texture_lookup_path(value: str | None) -> str:
        return (value or "").replace("\\", "/").strip("/").casefold()

    def _texture_ids_by_lookup_path(self) -> dict[str, tuple[int, ...]]:
        if self._texture_path_index is not None:
            return self._texture_path_index
        by_path: dict[str, set[int]] = {}
        for pointer in self.asset_pointers(AssetKind.SHADER):
            try:
                shader = self.read_shader(pointer.tuid)
            except InsomniacError as exc:
                warning = (
                    f"Texture path lookup skipped shader "
                    f"0x{pointer.tuid:016X}: {exc}"
                )
                LOGGER.warning(warning)
                self.warnings.append(warning)
                continue
            texture_ids = (
                shader.albedo_texture_id,
                shader.normal_texture_id,
                shader.expensive_texture_id,
                shader.detail_texture_id,
            )
            for texture_id, path in zip(
                texture_ids,
                shader.texture_lookup_paths,
            ):
                normalized = self._normalize_texture_lookup_path(path)
                if texture_id is not None and normalized:
                    by_path.setdefault(normalized, set()).add(texture_id)
        self._texture_path_index = {
            path: tuple(sorted(texture_ids))
            for path, texture_ids in by_path.items()
        }
        return self._texture_path_index

    def _read_texture_by_lookup_path(
        self,
        texture_id: int,
        normalized_path: str,
    ) -> tuple[int, DecodedTexture] | None:
        candidates: list[tuple[int, DecodedTexture]] = []
        for candidate_id in self._texture_ids_by_lookup_path().get(
            normalized_path,
            (),
        ):
            if candidate_id == texture_id:
                continue
            try:
                texture = self._read_texture_exact(candidate_id)
            except MissingResourceError:
                continue
            candidates.append((candidate_id, texture))
        if not candidates:
            return None

        first_id, first = candidates[0]
        if any(
            texture.info.format_code != first.info.format_code
            or texture.info.width != first.info.width
            or texture.info.height != first.info.height
            or not np.array_equal(texture.rgba, first.rgba)
            for _candidate_id, texture in candidates[1:]
        ):
            choices = ", ".join(
                f"0x{candidate_id:08X}"
                for candidate_id, _texture in candidates
            )
            raise AmbiguousResourceError(
                f"Texture 0x{texture_id:08X} lookup path "
                f"{normalized_path!r} matches multiple different "
                f"available payloads: {choices}"
            )
        return first_id, first

    @cached_property
    def global_texture_locations(
        self,
    ) -> dict[int, tuple[_GlobalTextureLocation, ...]]:
        by_id: dict[int, list[_GlobalTextureLocation]] = {}
        for header_path in sorted(set(self.source.paths)):
            normalized = "/" + header_path.replace("\\", "/").casefold()
            if (
                "/built/tuids/" not in normalized
                or not normalized.endswith("/header.dat")
            ):
                continue
            folder = PurePosixPath(header_path).parent
            try:
                tuid = int(folder.name, 16)
            except ValueError:
                continue
            if len(folder.name) != 16:
                continue
            texel_path = str(folder / "texel.dat")
            if not self.source.exists(texel_path):
                continue
            header = IghwFile(
                self.source.read(header_path),
                label=header_path,
            )
            descriptors = header.section(SECT_GLOBAL_TEXTURE)
            texel = self._read_ighw_metadata(texel_path)
            resources = texel.section(SECT_GLOBAL_TEXTURE_RESOURCE)
            data_section = texel.section(SECT_GLOBAL_TEXTURE_DATA_V1)
            if data_section is None:
                data_section = texel.section(SECT_GLOBAL_TEXTURE_DATA)
            if (
                descriptors is None
                or resources is None
                or data_section is None
            ):
                continue
            if descriptors.count != resources.count:
                raise FormatError(
                    f"{header_path}: {descriptors.count} texture "
                    f"descriptors do not match {resources.count} resource "
                    "records"
                )
            if descriptors.length < 0x1C or resources.length < 8:
                raise FormatError(
                    f"{header_path}: invalid global texture record sizes"
                )
            header.require(
                descriptors.offset,
                descriptors.count * descriptors.length,
                purpose="global texture descriptors",
            )
            resource_data = self.source.read_slice(
                texel_path,
                resources.offset,
                resources.count * resources.length,
            )
            byteorder = "big" if texel.endian == ">" else "little"
            for index in range(resources.count):
                record = index * resources.length
                texture_id = int.from_bytes(
                    resource_data[record : record + 4],
                    byteorder,
                )
                by_id.setdefault(texture_id, []).append(
                    _GlobalTextureLocation(
                        tuid,
                        index,
                        header_path,
                        texel_path,
                    )
                )
        return {
            texture_id: tuple(locations)
            for texture_id, locations in by_id.items()
        }

    def _read_ighw_metadata(self, path: str) -> IghwFile:
        size = self.source.size(path)
        if size < 16:
            raise FormatError(f"{path}: file is too short for an IGHW header")
        prefix = self.source.read_slice(path, 0, min(size, 0x20))
        if prefix[:4] == IghwFile.MAGIC_BIG:
            byteorder = "big"
        elif prefix[:4] == IghwFile.MAGIC_LITTLE:
            byteorder = "little"
        else:
            raise FormatError(f"{path}: bad IGHW magic {prefix[:4]!r}")
        version = (
            int.from_bytes(prefix[4:6], byteorder),
            int.from_bytes(prefix[6:8], byteorder),
        )
        if version == (1, 1):
            table_offset = 0x20
        elif version == (0, 2):
            table_offset = 0x10
        else:
            raise FormatError(
                f"{path}: unsupported IGHW version "
                f"{version[0]}.{version[1]}"
            )
        section_count = int.from_bytes(prefix[8:12], byteorder)
        if section_count > IghwFile.MAX_SECTIONS:
            raise FormatError(
                f"{path}: unreasonable section count {section_count}"
            )
        table_end = table_offset + section_count * 16
        if table_end > size:
            raise FormatError(
                f"{path}: section table ends at {table_end}, beyond "
                f"the {size}-byte file"
            )
        return IghwFile(
            self.source.read_slice(path, 0, table_end),
            label=path,
        )

    def _read_global_texture(self, texture_id: int) -> DecodedTexture:
        locations = self.global_texture_locations.get(texture_id, ())
        if not locations:
            raise MissingResourceError(
                f"Texture 0x{texture_id:08X} is not in the level lookup "
                "or loaded global archives"
            )
        textures = [
            self._read_global_texture_at(texture_id, location)
            for location in locations
        ]
        first = textures[0]
        if any(
            texture.info.format_code != first.info.format_code
            or texture.info.width != first.info.width
            or texture.info.height != first.info.height
            or not np.array_equal(texture.rgba, first.rgba)
            for texture in textures[1:]
        ):
            choices = ", ".join(
                f"0x{location.tuid:016X}[{location.descriptor_index}]"
                for location in locations
            )
            raise AmbiguousResourceError(
                f"Global texture ID 0x{texture_id:08X} matches multiple "
                f"different payloads: {choices}"
            )
        return first

    def _read_global_texture_at(
        self,
        texture_id: int,
        location: _GlobalTextureLocation,
    ) -> DecodedTexture:
        header = IghwFile(
            self.source.read(location.header_path),
            label=location.header_path,
        )
        descriptor = header.require_section(SECT_GLOBAL_TEXTURE)
        if location.descriptor_index >= descriptor.count:
            raise FormatError(
                f"{location.header_path}: texture descriptor index "
                f"{location.descriptor_index} is out of range"
            )
        base = (
            descriptor.offset + location.descriptor_index * descriptor.length
        )
        offset = header.u32(base)
        mip_count = header.u16(base + 4)
        format_code = header.u8(base + 6)
        width = header.u16(base + 0x18)
        height = header.u16(base + 0x1A)
        length = texture_mip_size(width, height, format_code)

        texel = self._read_ighw_metadata(location.texel_path)
        data_section = texel.section(SECT_GLOBAL_TEXTURE_DATA_V1)
        if data_section is None:
            data_section = texel.require_section(SECT_GLOBAL_TEXTURE_DATA)
        if offset + length > data_section.length:
            raise FormatError(
                f"{location.texel_path}: mip 0 range "
                f"[{offset}, {offset + length}) exceeds the texture data "
                f"section length {data_section.length}"
            )
        raw = self.source.read_slice(
            location.texel_path,
            data_section.offset + offset,
            length,
        )
        info = TextureInfo(
            id=texture_id,
            tuid=location.tuid,
            format_code=format_code,
            mip_count=mip_count,
            width=width,
            height=height,
            offset=offset,
            length=length,
        )
        return decode_texture(info, raw)

    def _sibling_textures(self) -> dict[int, tuple[Path, ...]]:
        if self._sibling_texture_index is not None:
            return self._sibling_texture_index
        by_id: dict[int, list[Path]] = {}
        for archive_path in self.source.sibling_level_archives():
            try:
                with GameSource.open(archive_path) as source:
                    sibling = Resistance2Reader(source)
                    for texture_id in sibling.texture_infos:
                        by_id.setdefault(texture_id, []).append(archive_path)
            except InsomniacError as exc:
                warning = (
                    f"Sibling level texture lookup skipped "
                    f"{archive_path}: {exc}"
                )
                LOGGER.warning(warning)
                self.warnings.append(warning)
        self._sibling_texture_index = {
            texture_id: tuple(paths)
            for texture_id, paths in by_id.items()
        }
        return self._sibling_texture_index

    def _read_sibling_texture(self, texture_id: int) -> DecodedTexture:
        archive_paths = self._sibling_textures().get(texture_id, ())
        if not archive_paths:
            raise MissingResourceError(
                f"Texture 0x{texture_id:08X} is not in the level lookup, "
                "loaded global archives, or extracted sibling levels"
            )

        textures: list[DecodedTexture] = []
        locations: list[Path] = []
        for archive_path in archive_paths:
            try:
                with GameSource.open(archive_path) as source:
                    sibling = Resistance2Reader(source)
                    textures.append(sibling.read_texture(texture_id))
                    locations.append(archive_path)
            except InsomniacError as exc:
                warning = (
                    f"Sibling texture 0x{texture_id:08X} could not be read "
                    f"from {archive_path}: {exc}"
                )
                LOGGER.warning(warning)
                self.warnings.append(warning)
        if not textures:
            raise MissingResourceError(
                f"Texture 0x{texture_id:08X} was indexed in sibling levels "
                "but none of its payloads could be read"
            )

        first = textures[0]
        if any(
            texture.info.tuid != first.info.tuid
            or texture.info.format_code != first.info.format_code
            or texture.info.width != first.info.width
            or texture.info.height != first.info.height
            or not np.array_equal(texture.rgba, first.rgba)
            for texture in textures[1:]
        ):
            choices = ", ".join(str(path) for path in locations)
            raise AmbiguousResourceError(
                f"Sibling texture ID 0x{texture_id:08X} has different "
                f"payloads in: {choices}"
            )
        return first

    def read_animations(self, moby: MobyAsset) -> list[DecodedClip]:
        if moby.skeleton is None or moby.animset_hash is None:
            return []
        data = self._asset_bytes(AssetKind.ANIMSET, moby.animset_hash)
        return decode_animset(
            data,
            moby.skeleton,
            profile=self.animation_profile,
        )

    def read_zones(self) -> list[Zone]:
        zones: list[Zone] = []
        for pointer in self.asset_pointers(AssetKind.ZONE):
            data = self.source.read_slice(
                self.resource_path(AssetKind.ZONE.filename),
                pointer.offset,
                pointer.length,
            )
            zones.append(
                self._parse_zone(
                    IghwFile(data, label=f"zone 0x{pointer.tuid:016X}"),
                    pointer.tuid,
                )
            )
        return zones

    def _parse_zone(self, ighw: IghwFile, tuid: int) -> Zone:
        shader_tuids = self._read_shader_table(ighw, SECT_UFRAG_SHADER_TABLE)
        terrain = self._parse_terrain(ighw)
        instances: list[PlacedInstance] = []
        instance_section = ighw.section(SECT_TIE_INSTANCES)
        if instance_section is not None and instance_section.count:
            name_section = ighw.require_section(SECT_TIE_NAME_POINTERS)
            tuid_section = ighw.require_section(SECT_TIE_TUID_TABLE)
            for index in range(instance_section.count):
                base = instance_section.offset + index * 0x80
                matrix = self._read_single_matrix(ighw, base)
                tie_index = ighw.u32(base + 0x50)
                asset_tuid = ighw.u64(tuid_section.offset + tie_index * 8)

                meta = name_section.offset + index * 0x10
                instance_tuid = ighw.u64(meta)
                name_offset = ighw.u32(meta + 8)
                name = (
                    ighw.cstring(name_offset)
                    if name_offset
                    else f"tie_{instance_tuid:016X}"
                )
                instances.append(
                    PlacedInstance(
                        asset_tuid=asset_tuid,
                        instance_tuid=instance_tuid,
                        name=name,
                        matrix=matrix,
                    )
                )
        shrub_section = ighw.section(SECT_SHRUB_INSTANCES)
        foliage_section = ighw.section(SECT_FOLIAGE_INSTANCES)
        return Zone(
            tuid=tuid,
            tie_instances=instances,
            terrain_meshes=terrain,
            shader_tuids=shader_tuids,
            unsupported_shrub_instances=(
                shrub_section.count if shrub_section else 0
            ),
            unsupported_foliage_instances=(
                foliage_section.count if foliage_section else 0
            ),
        )

    @staticmethod
    def _read_single_matrix(ighw: IghwFile, offset: int) -> np.ndarray:
        ighw.require(offset, 64, purpose="instance matrix")
        flat = np.frombuffer(
            ighw.data,
            dtype=ighw.endian + "f4",
            count=16,
            offset=offset,
        ).astype(np.float32)
        matrix = flat.reshape(4, 4).T.copy()
        matrix[3] = (0.0, 0.0, 0.0, 1.0)
        return matrix

    def _parse_terrain(self, ighw: IghwFile) -> list[TerrainMesh]:
        section = ighw.section(SECT_UFRAGS)
        vertex_section = ighw.section(SECT_UFRAG_VERTICES)
        index_section = ighw.section(SECT_UFRAG_INDICES)
        if section is None or vertex_section is None or index_section is None:
            return []
        vertices = ighw.view(vertex_section.offset, vertex_section.length)
        indices = ighw.view(index_section.offset, index_section.length)
        terrain: list[TerrainMesh] = []
        for index in range(section.count):
            base = section.offset + index * 0x80
            fragment_tuid = ighw.u64(base)
            index_offset = ighw.u32(base + 0x40)
            vertex_offset = ighw.u32(base + 0x44)
            index_count = ighw.u16(base + 0x48)
            vertex_count = ighw.u16(base + 0x4A)
            shader_index = ighw.u16(base + 0x50)
            position = np.asarray(ighw.vec3(base + 0x60), dtype=np.float32)
            position *= np.float32(1.0 / 256.0)
            stride = 0x18
            if vertex_offset + vertex_count * stride > len(vertices):
                raise FormatError(f"{ighw.label}: terrain vertex buffer overrun")
            if index_offset + index_count * 2 > len(indices):
                raise FormatError(f"{ighw.label}: terrain index buffer overrun")
            mesh_positions = np.ndarray(
                shape=(vertex_count, 3),
                dtype=ighw.endian + "i2",
                buffer=vertices,
                offset=vertex_offset,
                strides=(stride, 2),
            ).astype(np.float32)
            mesh_positions *= np.float32(1.0 / 256.0)
            mesh_positions += position
            uvs = np.ndarray(
                shape=(vertex_count, 2),
                dtype=ighw.endian + "f2",
                buffer=vertices,
                offset=vertex_offset + 8,
                strides=(stride, 2),
            ).astype(np.float32)
            mesh_indices = np.frombuffer(
                indices,
                dtype=ighw.endian + "u2",
                count=index_count,
                offset=index_offset,
            ).astype(np.int32)
            terrain.append(
                TerrainMesh(
                    tuid=fragment_tuid,
                    shader_index=shader_index,
                    positions=mesh_positions,
                    uvs=uvs,
                    indices=mesh_indices,
                )
            )
        return terrain

    def read_gameplay_regions(self) -> list[GameplayRegion]:
        gameplay_path = self.resource_path("gameplay.dat")
        if not self.source.exists(gameplay_path):
            return []
        gameplay = IghwFile(
            self.source.read(gameplay_path),
            label=gameplay_path,
        )
        names = self._read_region_names(gameplay)
        regions: list[GameplayRegion] = []
        for name in names:
            try:
                regions.append(self._read_gameplay_region(name))
            except MissingResourceError as exc:
                warning = f"Gameplay region {name!r} skipped: {exc}"
                LOGGER.warning(warning)
                self.warnings.append(warning)
        return regions

    @staticmethod
    def _read_region_names(gameplay: IghwFile) -> list[str]:
        section = gameplay.require_section(SECT_GAMEPLAY_STRINGS)
        byte_count = section.count
        if byte_count < 0x10:
            raise FormatError(
                f"{gameplay.label}: gameplay string section is too short"
            )
        descriptor = section.offset + byte_count - 0x10
        count = gameplay.u32(descriptor)
        table = gameplay.u32(descriptor + 4)
        return [
            gameplay.cstring(gameplay.u32(table + index * 4))
            for index in range(count)
        ]

    def _read_gameplay_region(self, name: str) -> GameplayRegion:
        prius_path = self.resource_path(f"{name}/gp_prius.dat")
        region_path = self.resource_path(f"{name}/region.dat")
        prius = IghwFile(self.source.read(prius_path), label=prius_path)
        region = IghwFile(self.source.read(region_path), label=region_path)
        instances = prius.require_section(SECT_PRIUS_MOBY_INSTANCES)
        metadata = prius.require_section(SECT_PRIUS_MOBY_METADATA)
        tuids = region.require_section(SECT_REGION_MOBY_TUIDS)

        placed: list[PlacedInstance] = []
        for index in range(instances.count):
            instance_offset = instances.offset + index * 0x50
            metadata_offset = metadata.offset + index * 0x10
            moby_index = prius.u16(instance_offset)
            position = prius.vec3(instance_offset + 0x14)
            rotation = prius.vec3(instance_offset + 0x20)
            scale = prius.f32(instance_offset + 0x2C)
            instance_tuid = prius.u64(metadata_offset)
            name_offset = prius.u32(metadata_offset + 8)
            group = prius.u16(metadata_offset + 0x0C)
            asset_tuid = region.u64(tuids.offset + moby_index * 8)
            instance_name = (
                prius.cstring(name_offset)
                if name_offset
                else f"moby_{instance_tuid:016X}"
            )
            matrix = _matrix_from_zyx(position, rotation, scale)
            placed.append(
                PlacedInstance(
                    asset_tuid=asset_tuid,
                    instance_tuid=instance_tuid,
                    name=instance_name,
                    matrix=matrix,
                    group=group,
                )
            )
        return GameplayRegion(name=name, moby_instances=placed)


def _matrix_from_zyx(
    position: tuple[float, float, float],
    angles: tuple[float, float, float],
    scale: float,
) -> np.ndarray:
    ax, ay, az = angles
    cx, sx = np.cos(ax), np.sin(ax)
    cy, sy = np.cos(ay), np.sin(ay)
    cz, sz = np.cos(az), np.sin(az)
    rotation_x = np.array(
        [[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float32
    )
    rotation_y = np.array(
        [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float32
    )
    rotation_z = np.array(
        [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float32
    )
    matrix = np.identity(4, dtype=np.float32)
    matrix[:3, :3] = (rotation_z @ rotation_y @ rotation_x) * scale
    matrix[:3, 3] = position
    return matrix
