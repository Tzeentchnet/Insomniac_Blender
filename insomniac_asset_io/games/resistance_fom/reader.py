from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from pathlib import PurePosixPath

import numpy as np

from ...core.binary import IghwFile
from ...core.collision import CollisionScene as LevelCollisionScene
from ...core.errors import FormatError, InsomniacError, MissingResourceError
from ...core.source import GameSource
from ..ratchet_tod.reader import RatchetTodReader
from ..resistance_2.animations import AnimationProfile
from ..resistance_2.formats import (
    AssetKind,
    AssetPointer,
    DecodedTexture,
    GameplayArea,
    GameplayEffect,
    GameplayMarker,
    GameplayMobyGroup,
    GameplayPath,
    GameplayRegion,
    GameplayTrigger,
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
from ..class_names import (
    ClassNames,
    class_name,
    parse_rfom_class_names,
    read_class_id,
)
from .debug_names import (
    GAME_DEBUG_CACHE_NAME,
    MAX_DEBUG_DAT_SIZE,
    parse_debug_names,
)
from .level_collision import decode_level_collision_resource

LOGGER = logging.getLogger(__name__)

SECT_MOBY = 0xD100
SECT_TIE = 0x3400
SECT_MATERIAL = 0x5001
SECT_TEXTURE = 0x5300
SECT_LEVEL_VERTICES = 0x9000
SECT_LEVEL_INDICES = 0x9100
SECT_REGIONS = 0x6200
SECT_TIE_INSTANCES = 0x9300
SECT_FOLIAGE = 0xC200
SECT_FOLIAGE_INSTANCES = 0x9700
SECT_SHRUB = 0xC700
SECT_SHRUB_INSTANCES = 0xC650
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
FOLIAGE_STRIDE = 0x120
FOLIAGE_INSTANCE_STRIDE = 0xE0
FOLIAGE_BRANCH_VERTEX_STRIDE = 0x14
FOLIAGE_SPRITE_VERTEX_STRIDE = 0x0C
SHRUB_STRIDE = 0x30
SHRUB_CLUSTER_STRIDE = 0x50
SHRUB_INSTANCE_STRIDE = 0x40
SHRUB_VERTEX_STRIDE = 0x10
GAMEPLAY_MOBY_STRIDE = 0x40
GAMEPLAY_PATH_STRIDE = 0x10
GAMEPLAY_TRIGGER_STRIDE = 0x90
GAMEPLAY_EFFECT_STRIDE = 0xA0
GAMEPLAY_MARKER_STRIDE = 0x30
GAMEPLAY_MOBY_GROUP_STRIDE = 0x10
GAMEPLAY_AREA_STRIDE = 0x60

YARD_TO_METERS = np.float32(0.9144)
REGION_SCALE = np.float32(YARD_TO_METERS / 256.0)
MAX_GAMEPLAY_INSTANCES = 1_000_000
MAX_VEGETATION_INSTANCES = 1_000_000
MAX_VEGETATION_INDICES = 10_000_000
FOLIAGE_SHADER_TUID_BASE = 1 << 32


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
    # RFoM has no proven prototype name table; only instances are named.
    provides_source_names = False

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
        self._debug_rejected_tables: set[str] = set()
        self._class_names_loaded = False
        self._class_names_value: ClassNames | None = None

    @property
    def class_names(self) -> ClassNames | None:
        """Class names from the game-wide ``packed/game/ps3debug.dat``.

        Level extraction stores it beside the level files as
        ``game_ps3debug.dat``; an extracted ``GAME.PSARC`` tree may also hold
        it at its archive path.
        """

        if not self._class_names_loaded:
            self._class_names_loaded = True
            matches = [
                path
                for path in self.source.paths
                if path.casefold().endswith("packed/game/ps3debug.dat")
                or path.casefold().endswith("/" + GAME_DEBUG_CACHE_NAME)
                or path.casefold() == GAME_DEBUG_CACHE_NAME
            ]
            try:
                if len(matches) == 1:
                    path = matches[0]
                    if self.source.size(path) > MAX_DEBUG_DAT_SIZE:
                        raise FormatError(
                            f"{path}: ps3debug.dat is unreasonably large"
                        )
                    self._class_names_value = parse_rfom_class_names(
                        self.source.read(path),
                        label=path,
                    )
            except (FormatError, MissingResourceError, OSError) as exc:
                self._warn_once(
                    "game ps3debug.dat",
                    f"Gameplay class names are unavailable: {exc}",
                )
        return self._class_names_value

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
        elif kind is AssetKind.FOLIAGE:
            pointers = self._record_pointers(
                kind,
                SECT_FOLIAGE,
                FOLIAGE_STRIDE,
                identity="offset",
            )
        elif kind is AssetKind.SHRUB:
            pointers = self._record_pointers(
                kind,
                SECT_SHRUB,
                SHRUB_STRIDE,
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
            or self.main.section(SECT_FOLIAGE_INSTANCES) is not None
            or self.main.section(SECT_SHRUB_INSTANCES) is not None
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
        if kind is AssetKind.FOLIAGE:
            return f"Foliage_{tuid:08X}"
        if kind is AssetKind.SHRUB:
            return f"Shrub_{tuid:08X}"
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

    def read_shrub(self, tuid: int) -> TieAsset:
        base = self._pointer(AssetKind.SHRUB, tuid).offset
        vertex_offset = self.main.u32(base)
        index_offset = self.main.u32(base + 0x04)
        index_count = self.main.u32(base + 0x08)
        shader_index = self.main.u16(base + 0x20)
        if index_count > MAX_VEGETATION_INDICES:
            raise FormatError(
                f"Shrub_{tuid:08X}: unreasonable index count {index_count}"
            )
        if not index_count:
            return TieAsset(tuid=tuid, meshes=[], shader_tuids=())

        indices = self._read_level_indices(
            index_offset,
            index_count,
            label=f"Shrub_{tuid:08X}",
        )
        vertex_count = int(indices.max()) + 1
        vertex_length = vertex_count * SHRUB_VERTEX_STRIDE
        vertex_section = self.vertices.require_section(SECT_LEVEL_VERTICES)
        if vertex_offset + vertex_length > vertex_section.length:
            raise FormatError(f"Shrub_{tuid:08X}: vertex buffer overrun")
        vertices = memoryview(
            self.source.read_slice(
                self.vertices_path,
                vertex_section.offset + vertex_offset,
                vertex_length,
            )
        )
        positions = np.ndarray(
            shape=(vertex_count, 3),
            dtype=self.vertices.endian + "f2",
            buffer=vertices,
            strides=(SHRUB_VERTEX_STRIDE, 2),
        ).astype(np.float32)
        positions *= YARD_TO_METERS
        uvs = np.ndarray(
            shape=(vertex_count, 2),
            dtype=self.vertices.endian + "f2",
            buffer=vertices,
            offset=0x0C,
            strides=(SHRUB_VERTEX_STRIDE, 2),
        ).astype(np.float32)
        meshes = [
            MeshGeometry(
                shader_index=shader_index,
                positions=positions,
                uvs=uvs,
                indices=indices,
            )
        ]
        return TieAsset(
            tuid=tuid,
            meshes=meshes,
            shader_tuids=self._localized_shaders(meshes),
        )

    def read_foliage(self, tuid: int) -> TieAsset:
        base = self._pointer(AssetKind.FOLIAGE, tuid).offset
        texture_index = self.main.u32(base + 0x08)
        shader_tuid = self._foliage_shader_tuid(texture_index)
        shader_index = 0
        index_offset = self.main.u32(base + 0x10)
        branch_vertex_offset = self.main.u32(base + 0x18)
        sprite_vertex_offset = self.main.u32(base + 0x40)
        meshes: list[MeshGeometry] = []

        branch_lod: tuple[int, int] | None = None
        for lod_index in range(4):
            lod = base + 0x20 + lod_index * 8
            lod_index_offset = self.main.u32(lod)
            lod_index_count = self.main.u16(lod + 0x04)
            if lod_index_count:
                branch_lod = (lod_index_offset, lod_index_count)
                break
        if branch_lod is not None:
            if sprite_vertex_offset < branch_vertex_offset:
                raise FormatError(
                    f"Foliage_{tuid:08X}: sprite vertices precede branches"
                )
            branch_vertex_bytes = (
                sprite_vertex_offset - branch_vertex_offset
            )
            if branch_vertex_bytes % FOLIAGE_BRANCH_VERTEX_STRIDE:
                raise FormatError(
                    f"Foliage_{tuid:08X}: branch vertex range is not "
                    f"{FOLIAGE_BRANCH_VERTEX_STRIDE:#x}-byte aligned"
                )
            branch_vertex_count = (
                branch_vertex_bytes // FOLIAGE_BRANCH_VERTEX_STRIDE
            )
            if not branch_vertex_count:
                raise FormatError(
                    f"Foliage_{tuid:08X}: branch indices have no vertices"
                )
            vertex_section = self.vertices.require_section(
                SECT_LEVEL_VERTICES
            )
            vertex_length = (
                branch_vertex_count * FOLIAGE_BRANCH_VERTEX_STRIDE
            )
            if branch_vertex_offset + vertex_length > vertex_section.length:
                raise FormatError(
                    f"Foliage_{tuid:08X}: branch vertex buffer overrun"
                )
            vertices = memoryview(
                self.source.read_slice(
                    self.vertices_path,
                    vertex_section.offset + branch_vertex_offset,
                    vertex_length,
                )
            )
            positions = np.ndarray(
                shape=(branch_vertex_count, 3),
                dtype=self.vertices.endian + "f2",
                buffer=vertices,
                strides=(FOLIAGE_BRANCH_VERTEX_STRIDE, 2),
            ).astype(np.float32)
            positions *= YARD_TO_METERS
            uvs = np.ndarray(
                shape=(branch_vertex_count, 2),
                dtype=self.vertices.endian + "f2",
                buffer=vertices,
                offset=0x08,
                strides=(FOLIAGE_BRANCH_VERTEX_STRIDE, 2),
            ).astype(np.float32)
            lod_index_offset, lod_index_count = branch_lod
            indices = self._read_level_indices(
                index_offset + lod_index_offset,
                lod_index_count,
                label=f"Foliage_{tuid:08X} branches",
            )
            if int(indices.max()) >= branch_vertex_count:
                raise FormatError(
                    f"Foliage_{tuid:08X}: branch index exceeds the "
                    "vertex range"
                )
            meshes.append(
                MeshGeometry(
                    shader_index=shader_index,
                    positions=positions,
                    uvs=uvs,
                    indices=indices,
                )
            )

        sprite_mesh = self._read_foliage_sprites(
            base,
            index_offset=index_offset,
            vertex_offset=sprite_vertex_offset,
            shader_index=shader_index,
            label=f"Foliage_{tuid:08X} sprites",
        )
        if sprite_mesh is not None:
            meshes.append(sprite_mesh)
        return TieAsset(
            tuid=tuid,
            meshes=meshes,
            shader_tuids=(shader_tuid,) if meshes else (),
        )

    def _foliage_shader_tuid(self, texture_index: int) -> int:
        section = self.main.require_section(SECT_TEXTURE)
        if texture_index >= section.count:
            raise FormatError(
                f"{self.main_path}: foliage texture index {texture_index} "
                f"is outside the {section.count}-record texture table"
            )
        return FOLIAGE_SHADER_TUID_BASE + texture_index

    def _read_foliage_sprites(
        self,
        base: int,
        *,
        index_offset: int,
        vertex_offset: int,
        shader_index: int,
        label: str,
    ) -> MeshGeometry | None:
        used_lods = self.main.u32(base + 0x44)
        if used_lods > 6:
            raise FormatError(
                f"{label}: unreasonable sprite LOD count {used_lods}"
            )
        if not used_lods:
            return None
        lod_index_begin = self.main.u32(base + 0x48)
        lod_index_end = self.main.u32(base + 0x4C)
        if lod_index_end < lod_index_begin:
            raise FormatError(f"{label}: invalid sprite LOD index range")

        positions_offset = self.main.u32(base + 0xB8)
        used_ranges = self.main.u32(base + 0xBC)
        if used_ranges > 8:
            raise FormatError(
                f"{label}: unreasonable sprite range count {used_ranges}"
            )
        if not used_ranges:
            return None
        if not positions_offset:
            raise FormatError(f"{label}: sprite positions are missing")

        vertex_section = self.vertices.require_section(SECT_LEVEL_VERTICES)
        out_positions: list[tuple[float, float, float]] = []
        out_uvs: list[tuple[float, float]] = []
        for range_index in range(used_ranges):
            item = base + 0xC0 + range_index * 8
            range_index_begin = self.main.u16(item)
            range_index_end = self.main.u16(item + 0x02)
            range_positions_offset = self.main.u16(item + 0x04)
            sprite_count = self.main.u16(item + 0x06)
            if range_index_end < range_index_begin:
                raise FormatError(
                    f"{label}: invalid range {range_index} index window"
                )
            range_index_count = range_index_end - range_index_begin
            if not sprite_count or not range_index_count:
                continue
            if sprite_count * 6 > range_index_count:
                raise FormatError(
                    f"{label}: range {range_index} has {sprite_count} "
                    f"sprites but only {range_index_count} indices"
                )

            indices = self._read_level_indices(
                index_offset + range_index_begin,
                range_index_count,
                label=f"{label} range {range_index}",
            )
            sprite_vertex_count = int(indices.max()) + 1
            vertex_length = (
                sprite_vertex_count * FOLIAGE_SPRITE_VERTEX_STRIDE
            )
            if vertex_offset + vertex_length > vertex_section.length:
                raise FormatError(f"{label}: sprite vertex buffer overrun")
            vertices = memoryview(
                self.source.read_slice(
                    self.vertices_path,
                    vertex_section.offset + vertex_offset,
                    vertex_length,
                )
            )
            sprite_sizes = np.ndarray(
                shape=(sprite_vertex_count, 2),
                dtype=self.vertices.endian + "f2",
                buffer=vertices,
                strides=(FOLIAGE_SPRITE_VERTEX_STRIDE, 2),
            ).astype(np.float32)
            sprite_uvs = np.ndarray(
                shape=(sprite_vertex_count, 2),
                dtype=self.vertices.endian + "f2",
                buffer=vertices,
                offset=0x04,
                strides=(FOLIAGE_SPRITE_VERTEX_STRIDE, 2),
            ).astype(np.float32)

            center_base = positions_offset + range_positions_offset
            self.main.require(
                center_base,
                sprite_count * 0x10,
                purpose=f"{label} range {range_index} centers",
            )
            centers = np.ndarray(
                shape=(sprite_count, 3),
                dtype=self.main.endian + "f4",
                buffer=self.main.data,
                offset=center_base,
                strides=(0x10, 4),
            ).astype(np.float32)
            for sprite_index in range(sprite_count):
                global_sprite_index = (
                    sprite_index * 6 + range_index_begin
                )
                if not (
                    lod_index_begin
                    <= global_sprite_index
                    < lod_index_end
                ):
                    continue
                for corner in range(6):
                    vertex_index = int(indices[sprite_index * 6 + corner])
                    size = sprite_sizes[vertex_index]
                    center = centers[sprite_index]
                    out_positions.append(
                        (
                            float((center[0] + size[0]) * YARD_TO_METERS),
                            float((center[1] + size[1]) * YARD_TO_METERS),
                            float(center[2] * YARD_TO_METERS),
                        )
                    )
                    uv = sprite_uvs[vertex_index]
                    out_uvs.append((float(uv[0]), float(uv[1])))

        if not out_positions:
            return None
        positions = np.asarray(out_positions, dtype=np.float32)
        return MeshGeometry(
            shader_index=shader_index,
            positions=positions,
            uvs=np.asarray(out_uvs, dtype=np.float32),
            indices=np.arange(len(positions), dtype=np.int32),
        )

    def _read_level_indices(
        self,
        offset: int,
        count: int,
        *,
        label: str,
    ) -> np.ndarray:
        if count > MAX_VEGETATION_INDICES:
            raise FormatError(
                f"{label}: unreasonable index count {count}"
            )
        if not count:
            return np.empty(0, dtype=np.int32)
        section = self.vertices.require_section(SECT_LEVEL_INDICES)
        byte_offset = offset * 2
        byte_length = count * 2
        if byte_offset + byte_length > section.length:
            raise FormatError(f"{label}: index buffer overrun")
        return np.frombuffer(
            self.source.read_slice(
                self.vertices_path,
                section.offset + byte_offset,
                byte_length,
            ),
            dtype=self.vertices.endian + "u2",
            count=count,
        ).astype(np.int32)

    def read_shader(self, tuid: int) -> ShaderInfo:
        cached = self._shader_cache.get(tuid)
        if cached is not None:
            return cached
        if tuid >= FOLIAGE_SHADER_TUID_BASE:
            texture_index = tuid - FOLIAGE_SHADER_TUID_BASE
            section = self.main.require_section(SECT_TEXTURE)
            if texture_index >= section.count:
                raise MissingResourceError(
                    f"Foliage shader 0x{tuid:016X} references texture "
                    f"index {texture_index}, outside the {section.count}-"
                    "record texture table"
                )
            texture_tuid = (
                section.offset + texture_index * TEXTURE_STRIDE
            )
            shader = ShaderInfo(
                tuid=tuid,
                albedo_texture_id=texture_tuid,
                normal_texture_id=None,
                specular_texture_id=None,
                detail_texture_id=None,
                lookup_path=f"foliage/texture_{texture_index:04X}",
                alpha_mode_override="MASK",
            )
            self._shader_cache[tuid] = shader
            return shader
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

    def read_level_collision(self) -> LevelCollisionScene:
        path = self._find_level_resource("ps3levelcoll.dat")
        return decode_level_collision_resource(
            self.source.read(path),
            resource_path=path,
            warnings=self.warnings,
        )

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
        shrub_instances = self._read_shrub_instances()
        foliage_instances = self._read_foliage_instances()
        shader_tuids = self._localized_shaders(terrain)
        if (
            not tie_instances
            and not terrain
            and not shrub_instances
            and not foliage_instances
        ):
            return []
        return [
            Zone(
                tuid=0,
                tie_instances=tie_instances,
                terrain_meshes=terrain,
                shader_tuids=shader_tuids,
                shrub_instances=shrub_instances,
                foliage_instances=foliage_instances,
            )
        ]

    def _read_shrub_instances(self) -> list[PlacedInstance]:
        section = self.main.section(SECT_SHRUB_INSTANCES)
        if section is None:
            return []
        if section.count != 1:
            raise FormatError(
                f"{self.main_path}: expected one shrub container, found "
                f"{section.count}"
            )
        if section.length < 0x60:
            raise FormatError(
                f"{self.main_path}: shrub container is too short"
            )
        base = section.offset
        self.main.require(
            base,
            section.length,
            purpose="shrub instance container",
        )
        instance_count = self.main.u32(base)
        instance_offset = self.main.u32(base + 0x04)
        cluster_count = self.main.u32(base + 0x08)
        cluster_offset = self.main.u32(base + 0x0C)
        if (
            instance_count > MAX_VEGETATION_INSTANCES
            or cluster_count > MAX_VEGETATION_INSTANCES
        ):
            raise FormatError(
                f"{self.main_path}: unreasonable shrub placement counts"
            )
        if (
            instance_offset > section.length
            or instance_count * SHRUB_INSTANCE_STRIDE
            > section.length - instance_offset
        ):
            raise FormatError(
                f"{self.main_path}: shrub instance table overruns its "
                "container"
            )
        if (
            cluster_offset > section.length
            or cluster_count * SHRUB_CLUSTER_STRIDE
            > section.length - cluster_offset
        ):
            raise FormatError(
                f"{self.main_path}: shrub cluster table overruns its "
                "container"
            )

        shrub_pointers = self.asset_pointers(AssetKind.SHRUB)
        if len(shrub_pointers) > 16:
            raise FormatError(
                f"{self.main_path}: shrub clusters can address at most "
                f"16 assets, found {len(shrub_pointers)}"
            )
        instances: list[PlacedInstance] = []
        for cluster_index in range(cluster_count):
            cluster = (
                base + cluster_offset + cluster_index * SHRUB_CLUSTER_STRIDE
            )
            local_ranges = tuple(
                (
                    self.main.u8(cluster + 0x40 + range_index * 2),
                    self.main.u8(cluster + 0x41 + range_index * 2),
                )
                for range_index in range(4)
            )
            first_instance = self.main.u32(cluster + 0x48)
            shrub_mask = self.main.u16(cluster + 0x4C)
            local_index = 0
            for mask_index in range(16):
                if not shrub_mask & (0x8000 >> mask_index):
                    continue
                if local_index >= len(local_ranges):
                    raise FormatError(
                        f"{self.main_path}: shrub cluster {cluster_index} "
                        "uses more than four local ranges"
                    )
                count, offset = local_ranges[local_index]
                local_index += 1
                shrub_index = 15 - mask_index
                if shrub_index >= len(shrub_pointers):
                    raise FormatError(
                        f"{self.main_path}: shrub cluster {cluster_index} "
                        f"references missing shrub {shrub_index}"
                    )
                if first_instance + offset + count > instance_count:
                    raise FormatError(
                        f"{self.main_path}: shrub cluster {cluster_index} "
                        "instance range is out of bounds"
                    )
                for local_instance in range(count):
                    instance_index = (
                        first_instance + offset + local_instance
                    )
                    item = (
                        base
                        + instance_offset
                        + instance_index * SHRUB_INSTANCE_STRIDE
                    )
                    position = np.asarray(
                        self.main.vec3(item),
                        dtype=np.float32,
                    )
                    position *= YARD_TO_METERS
                    scale = self.main.f32(item + 0x0C)
                    row_x = np.asarray(
                        self.main.vec3(item + 0x10),
                        dtype=np.float32,
                    )
                    row_y = np.asarray(
                        self.main.vec3(item + 0x20),
                        dtype=np.float32,
                    )
                    row_z = np.cross(row_x, row_y)
                    matrix = np.eye(4, dtype=np.float32)
                    matrix[:3, :3] = (
                        np.stack((row_x, row_y, row_z)).T * scale
                    )
                    matrix[:3, 3] = position
                    instances.append(
                        PlacedInstance(
                            asset_tuid=shrub_pointers[shrub_index].tuid,
                            instance_tuid=item,
                            name=(
                                f"Shrub_{shrub_index:02X}_"
                                f"{cluster_index:04X}_"
                                f"{local_instance:02X}"
                            ),
                            matrix=matrix,
                        )
                    )
        return instances

    def _read_foliage_instances(self) -> list[PlacedInstance]:
        section = self.main.section(SECT_FOLIAGE_INSTANCES)
        if section is None:
            return []
        self._validate_stride(
            section,
            FOLIAGE_INSTANCE_STRIDE,
            "foliage instance",
        )
        foliage_tuids = {
            pointer.tuid
            for pointer in self.asset_pointers(AssetKind.FOLIAGE)
        }
        instances: list[PlacedInstance] = []
        for index in range(section.count):
            base = section.offset + index * FOLIAGE_INSTANCE_STRIDE
            asset_tuid = self.main.u32(base + 0xC4)
            if asset_tuid not in foliage_tuids:
                raise FormatError(
                    f"{self.main_path}: foliage instance {index} "
                    f"references missing descriptor 0x{asset_tuid:08X}"
                )
            matrix = self._read_single_matrix(self.main, base)
            matrix[:3, 3] *= YARD_TO_METERS
            instances.append(
                PlacedInstance(
                    asset_tuid=asset_tuid,
                    instance_tuid=index,
                    name=f"FoliageInstance_{index:04X}",
                    matrix=matrix,
                )
            )
        return instances

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
            0xB0,
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
            try:
                class_id = read_class_id(
                    gameplay,
                    gameplay.u32(base),
                    label=f"{self.gameplay_path} Moby {index}",
                )
            except FormatError as exc:
                self._warn_once("moby class", f"{exc}; class omitted")
                class_id = None
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
                    class_id=class_id,
                )
            )
        paths = self._read_gameplay_paths(gameplay, instances_offset)
        triggers = self._read_gameplay_triggers(gameplay, instances_offset)
        effects = self._read_gameplay_effects(gameplay, instances_offset)
        markers = self._read_gameplay_markers(gameplay, instances_offset)
        moby_groups = self._read_gameplay_moby_groups(
            gameplay,
            instances_offset,
            item_count,
        )
        areas = self._read_gameplay_areas(
            gameplay,
            instances_offset,
            len(paths),
            len(triggers),
        )
        if instances or markers:
            names = self.class_names
            for item in instances:
                item.class_name = class_name(names, item.class_id)
            # Marker payloads carry the same gameplay class number.
            for marker in markers:
                marker.class_name = class_name(names, marker.marker_class)
        self._apply_debug_names(
            group_count=gameplay.u32(section.offset + 0x24),
            families={
                "moby": instances,
                "path": paths,
                "trigger": triggers,
                "effect": effects,
                "marker": markers,
                "moby_group": moby_groups,
                "area": areas,
            },
        )
        return [
            GameplayRegion(
                name=name,
                moby_instances=instances,
                paths=paths,
                triggers=triggers,
                effects=effects,
                markers=markers,
                moby_groups=moby_groups,
                areas=areas,
            )
        ]

    def _apply_debug_names(
        self,
        *,
        group_count: int,
        families: dict[str, list],
    ) -> None:
        """Name gameplay records from the level's ``ps3debug.dat``.

        The table indexes records per family across every gameplay group, so
        it is applied only to single-group levels, where it is unambiguous,
        and only to families whose name count equals the decoded count.
        """

        debug_path = self._optional_level_resource("ps3debug.dat")
        if debug_path is None:
            return
        if group_count != 1:
            message = (
                f"{debug_path}: {group_count} gameplay groups share one "
                "debug name table; using numeric names"
            )
            LOGGER.info(message)
            return
        try:
            if self.source.size(debug_path) > MAX_DEBUG_DAT_SIZE:
                raise FormatError(
                    f"{debug_path}: ps3debug.dat is unreasonably large"
                )
            names = parse_debug_names(
                self.source.read(debug_path),
                label=debug_path,
            )
        except InsomniacError as exc:
            warning = f"Gameplay debug names are unavailable: {exc}"
            LOGGER.warning(warning)
            self.warnings.append(warning)
            return
        for family, records in families.items():
            family_names = names.get(family)
            if family_names is None:
                continue
            if len(family_names) != len(records):
                warning = (
                    f"{debug_path}: {len(family_names)} {family} names for "
                    f"{len(records)} decoded records; using numeric names"
                )
                LOGGER.warning(warning)
                self.warnings.append(warning)
                continue
            for record, source in zip(records, family_names):
                if family == "moby":
                    record.name = source.name
                else:
                    record.source_name = source.name
                record.source_guid = source.guid

    def _gameplay_group(
        self,
        gameplay: IghwFile,
        instances_offset: int,
        descriptor_offset: int,
        stride: int,
        label: str,
    ) -> tuple[int, int]:
        table_offset = gameplay.u32(instances_offset + descriptor_offset)
        count = gameplay.u32(instances_offset + descriptor_offset + 0x04)
        if (
            gameplay.u32(instances_offset + descriptor_offset + 0x08) != 0
            or gameplay.u32(instances_offset + descriptor_offset + 0x0C) != 0
        ):
            raise FormatError(
                f"{self.gameplay_path}: gameplay {label} descriptor has "
                "non-zero reserved fields"
            )
        if count > MAX_GAMEPLAY_INSTANCES:
            raise FormatError(
                f"{self.gameplay_path}: unreasonable gameplay {label} count {count}"
            )
        if count and not table_offset:
            raise FormatError(
                f"{self.gameplay_path}: gameplay {label} table is missing"
            )
        gameplay.require(
            table_offset,
            count * stride,
            purpose=f"gameplay {label} table",
        )
        return table_offset, count

    def _require_finite(
        self,
        values: np.ndarray,
        label: str,
    ) -> None:
        if not np.all(np.isfinite(values)):
            raise FormatError(
                f"{self.gameplay_path}: gameplay {label} contains non-finite values"
            )

    def _read_gameplay_paths(
        self,
        gameplay: IghwFile,
        instances_offset: int,
    ) -> list[GameplayPath]:
        table_offset, count = self._gameplay_group(
            gameplay,
            instances_offset,
            0x50,
            GAMEPLAY_PATH_STRIDE,
            "path",
        )
        paths: list[GameplayPath] = []
        for index in range(count):
            base = table_offset + index * GAMEPLAY_PATH_STRIDE
            point_offset = gameplay.u32(base)
            point_count = gameplay.u32(base + 0x04)
            if (
                gameplay.u32(base + 0x08) != 0
                or gameplay.u32(base + 0x0C) != 0
            ):
                raise FormatError(
                    f"{self.gameplay_path}: gameplay path {index} has "
                    "non-zero reserved fields"
                )
            if point_count > MAX_GAMEPLAY_INSTANCES:
                raise FormatError(
                    f"{self.gameplay_path}: unreasonable gameplay path "
                    f"{index} point count {point_count}"
                )
            if point_count and not point_offset:
                raise FormatError(
                    f"{self.gameplay_path}: gameplay path {index} point "
                    "table is missing"
                )
            gameplay.require(
                point_offset,
                point_count * 0x10,
                purpose=f"gameplay path {index} points",
            )
            points = np.frombuffer(
                gameplay.data,
                dtype=gameplay.endian + "f4",
                count=point_count * 4,
                offset=point_offset,
            ).reshape((point_count, 4))
            self._require_finite(points, f"path {index}")
            paths.append(
                GameplayPath(
                    index=index,
                    points=(points[:, :3] * YARD_TO_METERS).astype(
                        np.float32, copy=True
                    ),
                )
            )
        return paths

    def _read_gameplay_triggers(
        self,
        gameplay: IghwFile,
        instances_offset: int,
    ) -> list[GameplayTrigger]:
        table_offset, count = self._gameplay_group(
            gameplay,
            instances_offset,
            0x60,
            GAMEPLAY_TRIGGER_STRIDE,
            "trigger",
        )
        triggers: list[GameplayTrigger] = []
        for index in range(count):
            base = table_offset + index * GAMEPLAY_TRIGGER_STRIDE
            values = np.frombuffer(
                gameplay.data,
                dtype=gameplay.endian + "f4",
                count=32,
                offset=base,
            )
            self._require_finite(values, f"trigger {index}")
            matrix = self._read_single_matrix(gameplay, base)
            matrix[:3, :3] *= YARD_TO_METERS
            matrix[:3, 3] *= YARD_TO_METERS
            shape_kind = gameplay.u32(base + 0x80)
            ordinal = gameplay.u32(base + 0x84)
            if shape_kind > 3:
                raise FormatError(
                    f"{self.gameplay_path}: gameplay trigger {index} has "
                    f"unknown shape kind {shape_kind}"
                )
            if ordinal != index:
                raise FormatError(
                    f"{self.gameplay_path}: gameplay trigger {index} stores "
                    f"ordinal {ordinal}"
                )
            if (
                gameplay.u32(base + 0x88) != 0
                or gameplay.u32(base + 0x8C) != 0
            ):
                raise FormatError(
                    f"{self.gameplay_path}: gameplay trigger {index} has "
                    "non-zero reserved fields"
                )
            triggers.append(
                GameplayTrigger(
                    index=index,
                    shape_kind=shape_kind,
                    matrix=matrix,
                )
            )
        return triggers

    def _read_gameplay_effects(
        self,
        gameplay: IghwFile,
        instances_offset: int,
    ) -> list[GameplayEffect]:
        table_offset, count = self._gameplay_group(
            gameplay,
            instances_offset,
            0x70,
            GAMEPLAY_EFFECT_STRIDE,
            "effect",
        )
        effects: list[GameplayEffect] = []
        for index in range(count):
            base = table_offset + index * GAMEPLAY_EFFECT_STRIDE
            payload_offset = gameplay.u32(base)
            payload_size = gameplay.u32(base + 0x9C)
            if bool(payload_offset) != bool(payload_size):
                raise FormatError(
                    f"{self.gameplay_path}: gameplay effect {index} has "
                    "an incomplete payload reference"
                )
            if payload_size:
                self._read_gameplay_payload_class(
                    gameplay,
                    payload_offset,
                    payload_size,
                    f"effect {index}",
                )
            matrix = self._read_single_matrix(gameplay, base + 0x04)
            inverse = self._read_single_matrix(gameplay, base + 0x44)
            self._require_finite(matrix, f"effect {index}")
            self._require_finite(inverse, f"effect {index} inverse")
            if not np.allclose(
                matrix @ inverse,
                np.identity(4, dtype=np.float32),
                rtol=1e-3,
                atol=1e-3,
            ):
                raise FormatError(
                    f"{self.gameplay_path}: gameplay effect {index} "
                    "matrices are not inverses"
                )
            matrix[:3, :3] *= YARD_TO_METERS
            matrix[:3, 3] *= YARD_TO_METERS
            if gameplay.u32(base + 0x90) != 0:
                raise FormatError(
                    f"{self.gameplay_path}: gameplay effect {index} has "
                    "a non-zero reserved field"
                )
            effects.append(
                GameplayEffect(
                    index=index,
                    effect_class=gameplay.u32(base + 0x98),
                    flags=gameplay.u32(base + 0x94),
                    matrix=matrix,
                    payload_size=payload_size,
                )
            )
        return effects

    def _read_gameplay_markers(
        self,
        gameplay: IghwFile,
        instances_offset: int,
    ) -> list[GameplayMarker]:
        table_offset, count = self._gameplay_group(
            gameplay,
            instances_offset,
            0x80,
            GAMEPLAY_MARKER_STRIDE,
            "marker",
        )
        markers: list[GameplayMarker] = []
        for index in range(count):
            base = table_offset + index * GAMEPLAY_MARKER_STRIDE
            payload_offset = gameplay.u32(base)
            payload_size = gameplay.u32(base + 0x28)
            if not payload_offset or not payload_size:
                raise FormatError(
                    f"{self.gameplay_path}: gameplay marker {index} has "
                    "no embedded payload"
                )
            marker_class = self._read_gameplay_payload_class(
                gameplay,
                payload_offset,
                payload_size,
                f"marker {index}",
            )
            if gameplay.u32(base + 0x2C) != 0:
                raise FormatError(
                    f"{self.gameplay_path}: gameplay marker {index} has "
                    "a non-zero reserved field"
                )
            position = gameplay.vec3(base + 0x04)
            rotation = gameplay.vec3(base + 0x10)
            scale = gameplay.vec3(base + 0x1C)
            self._require_finite(
                np.asarray(position + rotation + scale, dtype=np.float32),
                f"marker {index}",
            )
            matrix = _matrix_from_zyx(
                tuple(value * float(YARD_TO_METERS) for value in position),
                rotation,
                1.0,
            )
            matrix[:3, :3] *= np.asarray(scale, dtype=np.float32)
            markers.append(
                GameplayMarker(
                    index=index,
                    marker_class=marker_class,
                    matrix=matrix,
                    scale=scale,
                    payload_size=payload_size,
                )
            )
        return markers

    def _read_gameplay_payload_class(
        self,
        gameplay: IghwFile,
        payload_offset: int,
        payload_size: int,
        label: str,
    ) -> int:
        gameplay.require(
            payload_offset,
            payload_size,
            purpose=f"gameplay {label} payload",
        )
        payload = IghwFile(
            gameplay.bytes(payload_offset, payload_size),
            label=f"{self.gameplay_path} {label} payload",
        )
        section = payload.require_section(0x25200)
        payload.require(
            section.offset,
            4,
            purpose=f"gameplay {label} payload class",
        )
        return payload.u32(section.offset)

    def _read_gameplay_moby_groups(
        self,
        gameplay: IghwFile,
        instances_offset: int,
        moby_count: int,
    ) -> list[GameplayMobyGroup]:
        table_offset, count = self._gameplay_group(
            gameplay,
            instances_offset,
            0x90,
            GAMEPLAY_MOBY_GROUP_STRIDE,
            "Moby group",
        )
        groups: list[GameplayMobyGroup] = []
        for index in range(count):
            base = table_offset + index * GAMEPLAY_MOBY_GROUP_STRIDE
            member_offset = gameplay.u32(base)
            member_count = gameplay.u32(base + 0x04)
            if (
                gameplay.u32(base + 0x08) != 0
                or gameplay.u32(base + 0x0C) != 0
            ):
                raise FormatError(
                    f"{self.gameplay_path}: gameplay Moby group {index} "
                    "has non-zero reserved fields"
                )
            if member_count > MAX_GAMEPLAY_INSTANCES:
                raise FormatError(
                    f"{self.gameplay_path}: unreasonable gameplay Moby "
                    f"group {index} member count {member_count}"
                )
            if member_count and not member_offset:
                raise FormatError(
                    f"{self.gameplay_path}: gameplay Moby group {index} "
                    "member table is missing"
                )
            gameplay.require(
                member_offset,
                member_count * 4,
                purpose=f"gameplay Moby group {index} members",
            )
            members: list[int] = []
            for member_index in range(member_count):
                range_offset = member_offset + member_index * 4
                range_end = gameplay.u16(range_offset)
                range_start = gameplay.u16(range_offset + 2)
                if range_end != range_start + 1 or range_end > moby_count:
                    raise FormatError(
                        f"{self.gameplay_path}: gameplay Moby group "
                        f"{index} member {member_index} has invalid range "
                        f"[{range_start}, {range_end}) for {moby_count} Mobys"
                    )
                members.append(range_start)
            groups.append(
                GameplayMobyGroup(
                    index=index,
                    moby_indices=tuple(members),
                )
            )
        return groups

    def _read_gameplay_areas(
        self,
        gameplay: IghwFile,
        instances_offset: int,
        path_count: int,
        trigger_count: int,
    ) -> list[GameplayArea]:
        table_offset, count = self._gameplay_group(
            gameplay,
            instances_offset,
            0xA0,
            GAMEPLAY_AREA_STRIDE,
            "area",
        )
        areas: list[GameplayArea] = []
        for index in range(count):
            base = table_offset + index * GAMEPLAY_AREA_STRIDE
            path_offset = gameplay.u32(base)
            trigger_offset = gameplay.u32(base + 0x04)
            path_member_count = gameplay.u32(base + 0x48)
            trigger_member_count = gameplay.u32(base + 0x4C)
            for member_count, member_offset, member_label in (
                (path_member_count, path_offset, "path"),
                (trigger_member_count, trigger_offset, "trigger"),
            ):
                if member_count > MAX_GAMEPLAY_INSTANCES:
                    raise FormatError(
                        f"{self.gameplay_path}: unreasonable gameplay area "
                        f"{index} {member_label} count {member_count}"
                    )
                if member_count and not member_offset:
                    raise FormatError(
                        f"{self.gameplay_path}: gameplay area {index} "
                        f"{member_label} table is missing"
                    )
                gameplay.require(
                    member_offset,
                    member_count * 2,
                    purpose=(f"gameplay area {index} {member_label} members"),
                )
            if any(
                gameplay.u32(base + offset) != 0
                for offset in (0x54, 0x58, 0x5C)
            ):
                raise FormatError(
                    f"{self.gameplay_path}: gameplay area {index} has "
                    "non-zero reserved fields"
                )
            path_indices = tuple(
                gameplay.u16(path_offset + member_index * 2)
                for member_index in range(path_member_count)
            )
            trigger_indices = tuple(
                gameplay.u16(trigger_offset + member_index * 2)
                for member_index in range(trigger_member_count)
            )
            for member_index, path_index in enumerate(path_indices):
                if path_index >= path_count:
                    raise FormatError(
                        f"{self.gameplay_path}: gameplay area {index} "
                        f"path member {member_index} references path "
                        f"{path_index}, but only {path_count} paths exist"
                    )
            for member_index, trigger_index in enumerate(trigger_indices):
                if trigger_index >= trigger_count:
                    raise FormatError(
                        f"{self.gameplay_path}: gameplay area {index} "
                        f"trigger member {member_index} references trigger "
                        f"{trigger_index}, but only {trigger_count} "
                        "triggers exist"
                    )
            raw_name = bytes(gameplay.data[base + 0x08 : base + 0x48]).split(
                b"\0",
                1,
            )[0]
            areas.append(
                GameplayArea(
                    index=index,
                    name=raw_name.decode("ascii", errors="replace"),
                    path_indices=path_indices,
                    trigger_indices=trigger_indices,
                    flags=gameplay.u32(base + 0x50),
                )
            )
        return areas
