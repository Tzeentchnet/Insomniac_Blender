from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from pathlib import PurePosixPath

import numpy as np

from ...core.binary import IghwFile
from ...core.errors import FormatError
from ...core.source import GameSource
from ..ratchet_tod.reader import RatchetTodReader
from ..resistance_2.animations import AnimationProfile
from ..resistance_2.formats import (
    AssetKind,
    AssetPointer,
    DecodedTexture,
    GameplayRegion,
    MeshGeometry,
    MobyAsset,
    PlacedInstance,
    ShaderInfo,
    TerrainMesh,
    TextureInfo,
    TieAsset,
    Zone,
)
from ..resistance_2.reader import MAX_BONES, MAX_MESHES, _matrix_from_zyx
from ..resistance_2.textures import texture_mip_size

LOGGER = logging.getLogger(__name__)

SECT_MOBY = 0xD100
SECT_TIE = 0x3400
SECT_MATERIAL = 0x5001
SECT_TEXTURE = 0x5300
SECT_LEVEL_VERTICES = 0x9000
SECT_LEVEL_INDICES = 0x9100
SECT_REGIONS = 0x6200
SECT_TIE_INSTANCES = 0x9300
SECT_GAMEPLAY = 0x25000

MOBY_STRIDE = 0xC0
MOBY_BANGLE_STRIDE = 0x08
MOBY_PRIMITIVE_STRIDE = 0x20
TIE_STRIDE = 0x40
TIE_PRIMITIVE_STRIDE = 0x20
MATERIAL_STRIDE = 0x80
TEXTURE_STRIDE = 0x20
REGION_STRIDE = 0x80
REGION_VERTEX_STRIDE = 0x1A
TIE_INSTANCE_STRIDE = 0xC0
GAMEPLAY_MOBY_STRIDE = 0x40

YARD_TO_METERS = np.float32(0.9144)
REGION_SCALE = np.float32(YARD_TO_METERS / 256.0)
MAX_GAMEPLAY_INSTANCES = 1_000_000


@dataclass(frozen=True, slots=True)
class _TextureLocation:
    info: TextureInfo
    path: str


class ResistanceFomReader(RatchetTodReader):
    """Reader for Resistance: Fall of Man retail PS3 level data."""

    game_id = "RESISTANCE_FOM"
    game_label = "Resistance: Fall of Man"
    engine_id = "V1"
    asset_prefix = "RFOM"
    animation_profile = AnimationProfile.RESISTANCE_FOM
    level_local_asset_ids = True
    material_surface_channels_by_default = True
    material_base_color_factor_supported = False
    material_emission_uses_albedo = False

    def __init__(
        self,
        source: GameSource,
        *,
        metadata_only: bool = False,
    ) -> None:
        self.source = source
        self.main_path = self._find_level_resource("ps3levelmain.dat")
        parent = str(PurePosixPath(self.main_path).parent)
        self.resource_prefix = "" if parent == "." else parent
        self.assetlookup_path = self.main_path
        self._main_data = source.read(self.main_path)
        self.main = IghwFile(self._main_data, label=self.main_path)
        self.assetlookup = self.main

        if metadata_only:
            self.vertices_path = self._optional_level_resource(
                "ps3levelverts.dat"
            )
            self.vertices = (
                self._read_ighw_index(self.vertices_path)
                if self.vertices_path is not None
                else None
            )
            self.textures_path = self._optional_level_resource(
                "ps3leveltexs.dat"
            )
        else:
            self.vertices_path = self._find_level_resource(
                "ps3levelverts.dat"
            )
            self.vertices = self._read_ighw_index(self.vertices_path)
            self.textures_path = self._find_level_resource(
                "ps3leveltexs.dat"
            )
        self.texstream_path = None
        self.gameplay_path = self._optional_level_resource(
            "ps3gameplay.dat"
        )

        self._pointer_cache: dict[
            AssetKind,
            tuple[AssetPointer, ...],
        ] = {}
        self._pointer_maps: dict[
            AssetKind,
            dict[int, AssetPointer],
        ] = {}
        self._shader_cache: dict[int, ShaderInfo] = {}
        self._texture_cache: dict[int, DecodedTexture] = {}
        self._animation_offsets: dict[int, tuple[int, ...]] = {}
        self.warnings: list[str] = []

    def asset_pointers(
        self,
        kind: AssetKind,
    ) -> tuple[AssetPointer, ...]:
        cached = self._pointer_cache.get(kind)
        if cached is not None:
            return cached
        if kind is AssetKind.MOBY:
            section = self.main.section(SECT_MOBY)
            if section is None:
                pointers = ()
            else:
                self._validate_stride(section, MOBY_STRIDE, "Moby")
                pointers = tuple(
                    AssetPointer(
                        tuid=self.main.u16(
                            section.offset + index * MOBY_STRIDE + 0x1A
                        ),
                        offset=section.offset + index * MOBY_STRIDE,
                        length=MOBY_STRIDE,
                    )
                    for index in range(section.count)
                )
        elif kind is AssetKind.TIE:
            pointers = self._record_pointers(
                kind,
                SECT_TIE,
                TIE_STRIDE,
                identity="offset",
            )
        elif kind is AssetKind.SHADER:
            pointers = self._record_pointers(
                kind,
                SECT_MATERIAL,
                MATERIAL_STRIDE,
                identity="index",
            )
        elif kind in {AssetKind.TEXTURE, AssetKind.HIGH_MIP}:
            pointers = self._record_pointers(
                kind,
                SECT_TEXTURE,
                TEXTURE_STRIDE,
                identity="offset",
            )
        elif kind is AssetKind.ZONE and (
            self.main.section(SECT_REGIONS) is not None
            or self.main.section(SECT_TIE_INSTANCES) is not None
        ):
            pointers = (AssetPointer(tuid=0, offset=0, length=0),)
        else:
            pointers = ()

        pointer_map = {pointer.tuid: pointer for pointer in pointers}
        if len(pointer_map) != len(pointers):
            raise FormatError(
                f"{self.main_path}: duplicate {kind.name} identifiers"
            )
        self._pointer_cache[kind] = pointers
        self._pointer_maps[kind] = pointer_map
        return pointers

    def read_asset_name(
        self,
        kind: AssetKind,
        tuid: int,
    ) -> str | None:
        if kind is AssetKind.MOBY:
            return f"Moby_{tuid:04X}"
        if kind is AssetKind.TIE:
            return f"Tie_{tuid:08X}"
        if kind is AssetKind.SHADER:
            return f"Material_{tuid:04X}"
        return None

    @property
    def _shader_count(self) -> int:
        section = self.main.section(SECT_MATERIAL)
        return section.count if section is not None else 0

    def read_moby(self, tuid: int) -> MobyAsset:
        pointer = self._pointer(AssetKind.MOBY, tuid)
        base = pointer.offset
        bounding_sphere = (
            *self.main.vec3(base),
            self.main.f32(base + 0x0C),
        )
        animation_count = self.main.u16(base + 0x16)
        bangle_count = self.main.u16(base + 0x18)
        skeleton_offset = self.main.u32(base + 0x20)
        animations_offset = self.main.u32(base + 0x24)
        bangles_offset = self.main.u32(base + 0x28)
        index_offset = self.main.u32(base + 0x34)
        vertex_offset = self.main.u32(base + 0x38)
        mesh_scale = self.main.f32(base + 0x3C)

        if bangle_count and not bangles_offset:
            raise FormatError(
                f"Moby_{tuid:04X}: {bangle_count} bangles but no table"
            )
        if bangle_count > MAX_MESHES:
            raise FormatError(
                f"Moby_{tuid:04X}: unreasonable bangle count "
                f"{bangle_count}"
            )

        descriptors: list[
            tuple[int, int, int, int, int, int, np.ndarray, int]
        ] = []
        for bangle_index in range(bangle_count):
            bangle = bangles_offset + bangle_index * MOBY_BANGLE_STRIDE
            mesh_table = self.main.u32(bangle)
            mesh_count = self.main.u32(bangle + 4)
            if mesh_count > MAX_MESHES - len(descriptors):
                raise FormatError(
                    f"Moby_{tuid:04X}: unreasonable mesh count "
                    f"{mesh_count}"
                )
            if mesh_count and not mesh_table:
                raise FormatError(
                    f"Moby_{tuid:04X}: mesh table is missing"
                )
            for mesh_index in range(mesh_count):
                mesh = mesh_table + mesh_index * MOBY_PRIMITIVE_STRIDE
                shader_index = self.main.u16(mesh)
                vertex_count = self.main.u16(mesh + 0x02)
                index_count = self.main.u16(mesh + 0x04)
                bone_count = self.main.u8(mesh + 0x06)
                vertex_type = self.main.u8(mesh + 0x07)
                index_index = self.main.u32(mesh + 0x08)
                local_vertex_offset = self.main.u32(mesh + 0x0C)
                bone_map = self._read_bone_map(
                    self.main,
                    self.main.u32(mesh + 0x10),
                    bone_count,
                )
                if not vertex_count or not index_count:
                    continue
                descriptors.append(
                    (
                        index_index,
                        index_count,
                        local_vertex_offset,
                        vertex_count,
                        vertex_type,
                        shader_index,
                        bone_map,
                        bangle_index,
                    )
                )

        max_index_end = max(
            (
                (index_index + index_count) * 2
                for (
                    index_index,
                    index_count,
                    _vertex_offset,
                    _vertex_count,
                    _vertex_type,
                    _shader_index,
                    _bone_map,
                    _bangle_index,
                ) in descriptors
            ),
            default=0,
        )
        max_vertex_end = max(
            (
                local_vertex_offset
                + vertex_count * (0x1C if vertex_type == 1 else 0x14)
                for (
                    _index_index,
                    _index_count,
                    local_vertex_offset,
                    vertex_count,
                    vertex_type,
                    _shader_index,
                    _bone_map,
                    _bangle_index,
                ) in descriptors
            ),
            default=0,
        )
        index_path, index_base, index_available = self._geometry_base(
            index_offset,
            SECT_LEVEL_INDICES,
        )
        vertex_path, vertex_base, vertex_available = self._geometry_base(
            vertex_offset,
            SECT_LEVEL_VERTICES,
        )
        if max_index_end > index_available:
            raise FormatError(f"Moby_{tuid:04X}: index buffer overrun")
        if max_vertex_end > vertex_available:
            raise FormatError(f"Moby_{tuid:04X}: vertex buffer overrun")
        index_buffer = memoryview(
            self.source.read_slice(index_path, index_base, max_index_end)
        )
        vertex_buffer = memoryview(
            self.source.read_slice(vertex_path, vertex_base, max_vertex_end)
        )

        meshes = [
            self._decode_moby_mesh(
                index_buffer=index_buffer,
                vertex_buffer=vertex_buffer,
                index_index=index_index,
                index_count=index_count,
                vertex_offset=local_vertex_offset,
                vertex_count=vertex_count,
                stride=0x1C if vertex_type == 1 else 0x14,
                shader_index=shader_index,
                scale=mesh_scale,
                bone_map=bone_map,
                endian=self.main.endian,
                label=f"Moby_{tuid:04X}",
                segment_index=bangle_index,
                is_damage_permutation=False,
            )
            for (
                index_index,
                index_count,
                local_vertex_offset,
                vertex_count,
                vertex_type,
                shader_index,
                bone_map,
                bangle_index,
            ) in descriptors
        ]
        shader_tuids = self._localized_shaders(meshes)

        skeleton = (
            self._read_skeleton_at(self.main, skeleton_offset)
            if skeleton_offset
            else None
        )
        if skeleton is not None and skeleton.bone_count > MAX_BONES:
            raise FormatError(
                f"Moby_{tuid:04X}: unreasonable skeleton size "
                f"{skeleton.bone_count}"
            )
        animation_offsets: tuple[int, ...] = ()
        if animation_count:
            if not animations_offset:
                raise FormatError(
                    f"Moby_{tuid:04X}: animation table is missing"
                )
            self.main.require(
                animations_offset,
                animation_count * 4,
                purpose="inline animation pointer table",
            )
            animation_offsets = tuple(
                offset
                for index in range(animation_count)
                if (
                    offset := self.main.u32(
                        animations_offset + index * 4
                    )
                )
            )
        self._animation_offsets[tuid] = animation_offsets
        return MobyAsset(
            tuid=tuid,
            name=f"Moby_{tuid:04X}",
            meshes=meshes,
            bounding_sphere=bounding_sphere,
            shader_tuids=shader_tuids,
            skeleton=skeleton,
            animset_hash=(tuid + 1 if animation_offsets else None),
            bind_pose_inverse_offset=0,
        )

    def read_tie(self, tuid: int) -> TieAsset:
        pointer = self._pointer(AssetKind.TIE, tuid)
        base = pointer.offset
        mesh_table = self.main.u32(base)
        mesh_count = self.main.u16(base + 0x08)
        vertex_start = self.main.u32(base + 0x10)
        scale = np.asarray(
            self.main.vec3(base + 0x20),
            dtype=np.float32,
        )
        if mesh_count > MAX_MESHES:
            raise FormatError(
                f"Tie_{tuid:08X}: unreasonable mesh count {mesh_count}"
            )
        if mesh_count and not mesh_table:
            raise FormatError(f"Tie_{tuid:08X}: mesh table is missing")

        descriptors: list[tuple[int, int, int, int, int]] = []
        max_vertex_end = 0
        for mesh_index in range(mesh_count):
            mesh = mesh_table + mesh_index * TIE_PRIMITIVE_STRIDE
            descriptor = (
                self.main.u16(mesh),
                self.main.u32(mesh + 0x04),
                self.main.u16(mesh + 0x08),
                self.main.u16(mesh + 0x0C),
                self.main.u16(mesh + 0x0A),
            )
            descriptors.append(descriptor)
            max_vertex_end = max(
                max_vertex_end,
                (descriptor[3] + descriptor[4]) * 0x14,
            )

        vertex_section = self.vertices.require_section(SECT_LEVEL_VERTICES)
        index_section = self.vertices.require_section(SECT_LEVEL_INDICES)
        if vertex_start + max_vertex_end > vertex_section.length:
            raise FormatError(f"Tie_{tuid:08X}: vertex buffer overrun")
        vertex_buffer = memoryview(
            self.source.read_slice(
                self.vertices_path,
                vertex_section.offset + vertex_start,
                max_vertex_end,
            )
        )

        meshes: list[MeshGeometry] = []
        shader_tuids: list[int] = []
        for (
            shader_index,
            index_index,
            index_count,
            vertex_index,
            vertex_count,
        ) in descriptors:
            index_offset = index_index * 2
            index_length = index_count * 2
            if index_offset + index_length > index_section.length:
                raise FormatError(f"Tie_{tuid:08X}: index buffer overrun")
            index_buffer = memoryview(
                self.source.read_slice(
                    self.vertices_path,
                    index_section.offset + index_offset,
                    index_length,
                )
            )
            mesh = self._decode_tie_mesh(
                vertex_buffer,
                index_buffer,
                vertex_index,
                vertex_count,
                0,
                index_count,
                scale,
                len(shader_tuids),
                self.main.endian,
                f"Tie_{tuid:08X}",
            )
            if shader_index >= self._shader_count:
                raise FormatError(
                    f"Tie_{tuid:08X}: shader index {shader_index} is "
                    "outside the material table"
                )
            meshes.append(mesh)
            shader_tuids.append(shader_index)
        return TieAsset(
            tuid=tuid,
            meshes=meshes,
            shader_tuids=tuple(shader_tuids),
        )

    def read_shader(self, tuid: int) -> ShaderInfo:
        cached = self._shader_cache.get(tuid)
        if cached is not None:
            return cached
        base = self._pointer(AssetKind.SHADER, tuid).offset
        texture_ids = tuple(
            self.main.u32(base + offset) or None
            for offset in (0x20, 0x24, 0x28, 0x2C)
        )
        shader = ShaderInfo(
            tuid=tuid,
            albedo_texture_id=texture_ids[0],
            normal_texture_id=texture_ids[1],
            specular_texture_id=texture_ids[2],
            detail_texture_id=texture_ids[3],
            material_word=self.main.u32(base + 0x1C),
            material_values=tuple(
                tuple(
                    self.main.f32(
                        base
                        + 0x30
                        + vector_index * 0x10
                        + component_index * 4
                    )
                    for component_index in range(4)
                )
                for vector_index in range(5)
            ),
        )
        self._shader_cache[tuid] = shader
        return shader

    @cached_property
    def _texture_locations(self) -> dict[int, _TextureLocation]:
        section = self.main.section(SECT_TEXTURE)
        if section is None:
            return {}
        self._validate_stride(section, TEXTURE_STRIDE, "texture")
        locations: dict[int, _TextureLocation] = {}
        resource_size = self.source.size(self.textures_path)
        for index in range(section.count):
            base = section.offset + index * TEXTURE_STRIDE
            pixel_offset = self.main.u32(base)
            mip_count = self.main.u16(base + 0x04)
            format_code = self.main.u8(base + 0x06)
            width = self.main.u16(base + 0x18)
            height = self.main.u16(base + 0x1A)
            length = texture_mip_size(width, height, format_code)
            if pixel_offset + length > resource_size:
                raise FormatError(
                    f"{self.textures_path}: texture 0x{base:08X} "
                    "payload overruns the resource"
                )
            locations[base] = _TextureLocation(
                info=TextureInfo(
                    id=base,
                    tuid=base,
                    format_code=format_code,
                    mip_count=mip_count,
                    width=width,
                    height=height,
                    offset=pixel_offset,
                    length=length,
                ),
                path=self.textures_path,
            )
        return locations

    def read_zones(self) -> list[Zone]:
        tie_instances: list[PlacedInstance] = []
        section = self.main.section(SECT_TIE_INSTANCES)
        if section is not None:
            self._validate_stride(
                section,
                TIE_INSTANCE_STRIDE,
                "tie instance",
            )
            for index in range(section.count):
                base = section.offset + index * TIE_INSTANCE_STRIDE
                matrix = self._read_single_matrix(self.main, base)
                matrix[:3, 3] *= YARD_TO_METERS
                tie_instances.append(
                    PlacedInstance(
                        asset_tuid=self.main.u32(base + 0x8C),
                        instance_tuid=index,
                        name=f"TieInstance_{index:04X}",
                        matrix=matrix,
                    )
                )

        terrain = self._read_terrain()
        shader_tuids = self._localized_shaders(terrain)
        if not tie_instances and not terrain:
            return []
        return [
            Zone(
                tuid=0,
                tie_instances=tie_instances,
                terrain_meshes=terrain,
                shader_tuids=shader_tuids,
            )
        ]

    def _read_terrain(self) -> list[TerrainMesh]:
        section = self.main.section(SECT_REGIONS)
        if section is None:
            return []
        self._validate_stride(section, REGION_STRIDE, "region")
        vertex_section = self.vertices.require_section(SECT_LEVEL_VERTICES)
        index_section = self.vertices.require_section(SECT_LEVEL_INDICES)
        terrain: list[TerrainMesh] = []
        for index in range(section.count):
            base = section.offset + index * REGION_STRIDE
            index_offset = self.main.u32(base + 0x54) * 2
            vertex_offset = self.main.u32(base + 0x58)
            index_count = self.main.u16(base + 0x5C)
            vertex_count = self.main.u16(base + 0x5E)
            if not index_count or not vertex_count:
                continue
            vertex_length = vertex_count * REGION_VERTEX_STRIDE
            index_length = index_count * 2
            if vertex_offset + vertex_length > vertex_section.length:
                warning = (
                    f"Region {index} skipped: vertex buffer overrun"
                )
                LOGGER.warning(warning)
                self.warnings.append(warning)
                continue
            if index_offset + index_length > index_section.length:
                warning = f"Region {index} skipped: index buffer overrun"
                LOGGER.warning(warning)
                self.warnings.append(warning)
                continue
            vertices = memoryview(
                self.source.read_slice(
                    self.vertices_path,
                    vertex_section.offset + vertex_offset,
                    vertex_length,
                )
            )
            positions = np.ndarray(
                shape=(vertex_count, 3),
                dtype=self.main.endian + "i2",
                buffer=vertices,
                strides=(REGION_VERTEX_STRIDE, 2),
            ).astype(np.float32)
            positions += np.asarray(
                self.main.vec3(base + 0x40),
                dtype=np.float32,
            )
            positions *= REGION_SCALE
            uvs = np.ndarray(
                shape=(vertex_count, 2),
                dtype=self.main.endian + "f2",
                buffer=vertices,
                offset=8,
                strides=(REGION_VERTEX_STRIDE, 2),
            ).astype(np.float32)
            indices = np.frombuffer(
                self.source.read_slice(
                    self.vertices_path,
                    index_section.offset + index_offset,
                    index_length,
                ),
                dtype=self.main.endian + "u2",
                count=index_count,
            ).astype(np.int32)
            terrain.append(
                TerrainMesh(
                    tuid=base,
                    shader_index=self.main.u16(base + 0x4C),
                    positions=positions,
                    uvs=uvs,
                    indices=indices,
                )
            )
        return terrain

    def read_gameplay_regions(self) -> list[GameplayRegion]:
        if self.gameplay_path is None:
            return []
        gameplay = IghwFile(
            self.source.read(self.gameplay_path),
            label=self.gameplay_path,
        )
        section = gameplay.section(SECT_GAMEPLAY)
        if section is None:
            return [GameplayRegion(name="art", moby_instances=[])]
        gameplay.require(
            section.offset,
            0x24,
            purpose="Gameplay structure",
        )
        instances_offset = gameplay.u32(section.offset + 0x20)
        if not instances_offset:
            return [GameplayRegion(name="art", moby_instances=[])]
        gameplay.require(
            instances_offset,
            0x48,
            purpose="GameplayInstances structure",
        )
        raw_name = bytes(
            gameplay.data[instances_offset : instances_offset + 0x40]
        ).split(b"\0", 1)[0]
        name = raw_name.decode("ascii", errors="replace") or "art"
        items_offset = gameplay.u32(instances_offset + 0x40)
        item_count = gameplay.u32(instances_offset + 0x44)
        if item_count > MAX_GAMEPLAY_INSTANCES:
            raise FormatError(
                f"{self.gameplay_path}: unreasonable gameplay Moby "
                f"count {item_count}"
            )
        if item_count and not items_offset:
            raise FormatError(
                f"{self.gameplay_path}: gameplay Moby table is missing"
            )
        gameplay.require(
            items_offset,
            item_count * GAMEPLAY_MOBY_STRIDE,
            purpose="gameplay Moby table",
        )
        instances: list[PlacedInstance] = []
        for index in range(item_count):
            base = items_offset + index * GAMEPLAY_MOBY_STRIDE
            moby_id = gameplay.u16(base + 0x3C)
            position = tuple(
                value * float(YARD_TO_METERS)
                for value in gameplay.vec3(base + 0x10)
            )
            instances.append(
                PlacedInstance(
                    asset_tuid=moby_id,
                    instance_tuid=index,
                    name=f"Moby_{moby_id:04X}_Instance_{index:04X}",
                    matrix=_matrix_from_zyx(
                        position,
                        gameplay.vec3(base + 0x1C),
                        1.0,
                    ),
                )
            )
        return [GameplayRegion(name=name, moby_instances=instances)]
