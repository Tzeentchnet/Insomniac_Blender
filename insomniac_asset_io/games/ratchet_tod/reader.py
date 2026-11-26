from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from pathlib import PurePosixPath

import numpy as np

from ...core.binary import IghwFile, Section
from ...core.collision import CollisionScene as LevelCollisionScene
from ...core.errors import FormatError, MissingResourceError
from ...core.source import GameSource
from ..resistance_2.animations import (
    AnimationProfile,
    decode_inline_animations,
)
from ..resistance_2.formats import (
    AssetKind,
    AssetPointer,
    DecodedClip,
    DecodedTexture,
    GameplayRegion,
    MeshGeometry,
    MobyAsset,
    PlacedInstance,
    ShaderInfo,
    SourceSummary,
    TerrainMesh,
    TextureInfo,
    TieAsset,
    Zone,
)
from ..resistance_2.reader import (
    MAX_BONES,
    MAX_MESHES,
    Resistance2Reader,
    _matrix_from_zyx,
)
from ..resistance_2.textures import decode_texture, texture_mip_size
from ..class_names import (
    ClassNames,
    class_name,
    parse_tod_class_names,
    parse_tod_collision_material_names,
    read_class_id,
)
from .gameplay_context import read_tod_gameplay_context
from .debug_names import (
    GAME_DEBUG_CACHE_NAME,
    MAX_DEBUG_DAT_SIZE,
    DebugInstanceName,
    TodDebugNames,
    parse_debug_names,
)
from .level_collision import decode_level_collision_resource

LOGGER = logging.getLogger(__name__)

SECT_MOBY = 0xD100
SECT_TIE = 0x3400
SECT_SHADER = 0x5000
SECT_TEXTURE = 0x5200
SECT_TEXSTREAM = 0x9800
SECT_LEVEL_VERTICES = 0x9000
SECT_LEVEL_INDICES = 0x9100
SECT_UFRAGS = 0x6200
SECT_TIE_INSTANCES = 0x9240
SECT_FOLIAGE = 0xA200
SECT_SHRUB = 0xB100
SECT_SHRUB_INSTANCES = 0x9540
SECT_FOLIAGE_INSTANCES = 0x9340
SECT_GAMEPLAY_MOBYS = 0x7340

MOBY_STRIDE = 0xC0
MOBY_BANGLE_STRIDE = 0x08
MOBY_MESH_STRIDE = 0x40
TIE_STRIDE = 0x80
TIE_MESH_STRIDE = 0x40
FOLIAGE_STRIDE = 0xB0
SHRUB_STRIDE = 0x30
SHADER_STRIDE = 0x80
TEXTURE_STRIDE = 0x20
TEXSTREAM_STRIDE = 0x10
UFRAG_STRIDE = 0x80
UFRAG_VERTEX_STRIDE = 0x18
TIE_INSTANCE_STRIDE = 0x80
FOLIAGE_INSTANCE_STRIDE = 0xE0
FOLIAGE_CENTER_STRIDE = 0x08
SHRUB_CLUSTER_STRIDE = 0x60
SHRUB_INSTANCE_STRIDE = 0x40
SHRUB_VERTEX_STRIDE = 0x0E
GAMEPLAY_MOBY_STRIDE = 0x48

# Shader record +0x11 rendering-mode byte. Byte names follow ReLunacy's
# RenderingMode enum. Overlay (1) and Scunge (3) use an unsorted alpha-blend
# pass and Additive (2) uses SRC_ALPHA, ONE. Cutout (4) and SoftEdge (5) draw
# in the opaque pass and are clipped. Bytes 7 and 8 (BakedOnly/LitOnly) never
# occur in the retail TOD shader tables.
TOD_RENDERING_MODES = {
    0: "OPAQUE",
    1: "BLEND",
    2: "ADDITIVE",
    3: "BLEND",
    4: "MASK",
    5: "MASK",
    6: "BLEND",
}

USE_VERTICES_DAT = 0x8000_0000
YARD_TO_METERS = np.float32(0.9144)
UFRAG_SCALE = np.float32(YARD_TO_METERS / 256.0)
FOLIAGE_SHADER_TUID_BASE = 1 << 32
MAX_VEGETATION_INSTANCES = 1_000_000
MAX_VEGETATION_INDICES = 10_000_000
MAX_TOD_SHRUB_CLASSES = 32


@dataclass(frozen=True, slots=True)
class _TextureLocation:
    info: TextureInfo
    path: str


class RatchetTodReader(Resistance2Reader):
    """Reader for the retail Tools of Destruction level layout."""

    game_id = "RATCHET_TOD"
    game_label = "Ratchet & Clank Future: Tools of Destruction"
    engine_id = "V1"
    asset_prefix = "TOD"
    animation_profile = AnimationProfile.RATCHET_TOD
    level_local_asset_ids = True
    material_surface_channels_by_default = True
    material_base_color_factor_supported = False
    material_emission_uses_albedo = False
    provides_source_names = True

    def __init__(self, source: GameSource) -> None:
        self.source = source
        self.main_path = self._find_level_resource("main.dat")
        parent = str(PurePosixPath(self.main_path).parent)
        self.resource_prefix = "" if parent == "." else parent
        self.assetlookup_path = self.main_path
        self._main_data = source.read(self.main_path)
        self.main = IghwFile(self._main_data, label=self.main_path)
        self.assetlookup = self.main

        self.vertices_path = self._find_level_resource("vertices.dat")
        self.vertices = self._read_ighw_index(self.vertices_path)
        self.textures_path = self._find_level_resource("textures.dat")
        self.texstream_path = self._optional_level_resource("texstream.dat")
        self.gameplay_path = self._optional_level_resource("gameplay.dat")

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
        self._debug_names_loaded = False
        self._debug_names_value: TodDebugNames | None = None
        self._debug_rejected_tables: set[str] = set()
        self._class_names_loaded = False
        self._class_names_value: ClassNames | None = None
        self._collision_material_names_loaded = False
        self._collision_material_names_value: ClassNames | None = None

    def _optional_level_resource(self, filename: str) -> str | None:
        try:
            return self._find_level_resource(filename)
        except MissingResourceError:
            return None

    def _read_debug_dat(self) -> tuple[str, bytes] | None:
        """Return the level's loose retail DEBUG.DAT, when available.

        The disc stores it outside the level PSARCs; extraction places it
        beside ``level_cached.psarc``. Directory sources may also contain
        it under ``levels/<level>/``. The game-wide ``built/game/debug.dat``
        is a different file and is never matched.
        """

        level_paths = [
            path
            for path in self.source.paths
            if path.casefold().endswith("/debug.dat")
            and "/levels/" in path.casefold()
        ]
        if len(level_paths) == 1:
            path = level_paths[0]
            if self.source.size(path) > MAX_DEBUG_DAT_SIZE:
                raise FormatError(f"{path}: DEBUG.DAT is unreasonably large")
            return path, self.source.read(path)
        if len(level_paths) > 1:
            return None
        selected = self.source.selected_path
        if selected is None:
            return None
        folder = selected if selected.is_dir() else selected.parent
        try:
            candidates = [
                item
                for item in folder.iterdir()
                if item.is_file() and item.name.casefold() == "debug.dat"
            ]
        except OSError:
            return None
        if len(candidates) != 1:
            return None
        candidate = candidates[0]
        if candidate.stat().st_size > MAX_DEBUG_DAT_SIZE:
            raise FormatError(f"{candidate}: DEBUG.DAT is unreasonably large")
        return str(candidate), candidate.read_bytes()

    @property
    def debug_names(self) -> TodDebugNames | None:
        if not self._debug_names_loaded:
            self._debug_names_loaded = True
            try:
                located = self._read_debug_dat()
                if located is not None:
                    path, data = located
                    self._debug_names_value = parse_debug_names(
                        data,
                        label=path,
                    )
            except (FormatError, MissingResourceError, OSError) as exc:
                self._warn_once(
                    "debug.dat",
                    f"Level DEBUG.DAT names are unavailable: {exc}",
                )
        return self._debug_names_value

    def _read_game_debug_dat(self) -> tuple[str, bytes] | None:
        """Return the game-wide ``BUILT/GAME/DEBUG.DAT``, when available.

        Level extraction copies it beside ``level_cached.psarc`` as
        ``game_debug.dat``; a directory source may hold it at its disc path.
        """

        matches = [
            path
            for path in self.source.paths
            if path.casefold().endswith("built/game/debug.dat")
            or path.casefold().endswith("/" + GAME_DEBUG_CACHE_NAME)
            or path.casefold() == GAME_DEBUG_CACHE_NAME
        ]
        if len(matches) == 1:
            path = matches[0]
            if self.source.size(path) > MAX_DEBUG_DAT_SIZE:
                raise FormatError(f"{path}: DEBUG.DAT is unreasonably large")
            return path, self.source.read(path)
        if matches:
            return None
        selected = self.source.selected_path
        if selected is None:
            return None
        candidate = (
            selected if selected.is_dir() else selected.parent
        ) / GAME_DEBUG_CACHE_NAME
        if not candidate.is_file():
            return None
        if candidate.stat().st_size > MAX_DEBUG_DAT_SIZE:
            raise FormatError(f"{candidate}: DEBUG.DAT is unreasonably large")
        return str(candidate), candidate.read_bytes()

    @property
    def class_names(self) -> ClassNames | None:
        if not self._class_names_loaded:
            self._class_names_loaded = True
            try:
                located = self._read_game_debug_dat()
                if located is not None:
                    path, data = located
                    self._class_names_value = parse_tod_class_names(
                        data,
                        label=path,
                    )
            except (FormatError, MissingResourceError, OSError) as exc:
                self._warn_once(
                    "game debug.dat",
                    f"Gameplay class names are unavailable: {exc}",
                )
        return self._class_names_value

    @property
    def collision_material_names(self) -> ClassNames | None:
        """``kMaterialID`` names from the game-wide DEBUG.DAT, if present."""

        if not self._collision_material_names_loaded:
            self._collision_material_names_loaded = True
            try:
                located = self._read_game_debug_dat()
                if located is not None:
                    path, data = located
                    self._collision_material_names_value = (
                        parse_tod_collision_material_names(data, label=path)
                    )
            except (FormatError, MissingResourceError, OSError) as exc:
                self._warn_once(
                    "game debug.dat collision materials",
                    f"Collision material names are unavailable: {exc}",
                )
        return self._collision_material_names_value

    def _warn_once(self, key: str, message: str) -> None:
        if key in self._debug_rejected_tables:
            return
        self._debug_rejected_tables.add(key)
        LOGGER.warning(message)
        self.warnings.append(message)

    def _checked_names(self, key: str, names, expected: int):
        """Return a debug table only when it covers every decoded record."""

        if names is None:
            return None
        if len(names) != expected:
            self._warn_once(
                key,
                f"DEBUG.DAT {key} names cover {len(names)} records but the "
                f"level decodes {expected}; using numeric names",
            )
            return None
        return names

    def _prototype_source_name(
        self,
        kind: AssetKind,
        tuid: int,
    ) -> str | None:
        names = self.debug_names
        if names is None:
            return None
        pointers = self.asset_pointers(kind)
        table = self._checked_names(
            f"{kind.name.lower()} prototype",
            names.prototypes.get(kind),
            len(pointers),
        )
        if table is None:
            return None
        if kind is AssetKind.MOBY:
            index = tuid
        else:
            # Tie, Foliage, and Shrub identities are main.dat record offsets.
            first = pointers[0].offset if pointers else 0
            index = (tuid - first) // pointers[0].length if pointers else -1
        if not 0 <= index < len(table):
            return None
        return table[index]

    def _instance_source_names(
        self,
        family: str,
        expected: int,
    ) -> tuple[DebugInstanceName, ...] | None:
        names = self.debug_names
        if names is None:
            return None
        return self._checked_names(
            f"{family} instance",
            names.instances.get(family),
            expected,
        )

    def _apply_instance_names(
        self,
        family: str,
        instances: list[PlacedInstance],
    ) -> None:
        names = self._instance_source_names(family, len(instances))
        if names is None:
            return
        for instance, source in zip(instances, names):
            instance.name = source.name
            instance.source_guid = source.guid

    def _apply_source_names(self, family: str, items: list) -> None:
        """Attach DEBUG.DAT names to gameplay context records."""

        names = self._instance_source_names(family, len(items))
        if names is None:
            return
        for item, source in zip(items, names):
            item.source_name = source.name
            item.source_guid = source.guid

    def _read_ighw_index(self, path: str) -> IghwFile:
        header = self.source.read_slice(path, 0, 16)
        magic = header[:4]
        if magic == IghwFile.MAGIC_BIG:
            byteorder = "big"
        elif magic == IghwFile.MAGIC_LITTLE:
            byteorder = "little"
        else:
            raise FormatError(f"{path}: bad IGHW magic {magic!r}")
        version = (
            int.from_bytes(header[4:6], byteorder),
            int.from_bytes(header[6:8], byteorder),
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
        count = int.from_bytes(header[8:12], byteorder)
        if count > IghwFile.MAX_SECTIONS:
            raise FormatError(f"{path}: unreasonable section count {count}")
        return IghwFile(
            self.source.read_slice(
                path,
                0,
                table_offset + count * 16,
            ),
            label=path,
        )

    def inspect(self) -> SourceSummary:
        return SourceSummary(
            counts={
                kind: len(self.asset_pointers(kind))
                for kind in AssetKind
            },
            resource_prefix=self.resource_prefix,
        )

    def summary_text(self) -> str:
        return (
            f"{len(self.asset_pointers(AssetKind.MOBY))} mobys, "
            f"{len(self.asset_pointers(AssetKind.TIE))} ties, "
            f"{len(self.asset_pointers(AssetKind.SHRUB))} shrubs, "
            f"{len(self.asset_pointers(AssetKind.FOLIAGE))} foliage assets, "
            f"{len(self.asset_pointers(AssetKind.SHADER))} shaders, "
            f"{len(self.asset_pointers(AssetKind.TEXTURE))} textures, "
            f"{len(self.asset_pointers(AssetKind.ZONE))} zone"
        )

    def _record_pointers(
        self,
        kind: AssetKind,
        section_id: int,
        stride: int,
        *,
        identity: str,
    ) -> tuple[AssetPointer, ...]:
        section = self.main.section(section_id)
        if section is None:
            return ()
        if section.length != stride:
            raise FormatError(
                f"{self.main_path}: section 0x{section_id:X} has stride "
                f"{section.length:#x}; expected {stride:#x}"
            )
        self.main.require(
            section.offset,
            section.count * stride,
            purpose=f"{kind.name} record table",
        )
        return tuple(
            AssetPointer(
                tuid=(
                    index
                    if identity == "index"
                    else section.offset + index * stride
                ),
                offset=section.offset + index * stride,
                length=stride,
            )
            for index in range(section.count)
        )

    def asset_pointers(
        self,
        kind: AssetKind,
    ) -> tuple[AssetPointer, ...]:
        cached = self._pointer_cache.get(kind)
        if cached is not None:
            return cached
        if kind is AssetKind.MOBY:
            pointers = self._record_pointers(
                kind,
                SECT_MOBY,
                MOBY_STRIDE,
                identity="index",
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
                SECT_SHADER,
                SHADER_STRIDE,
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
            self.main.section(SECT_UFRAGS) is not None
            or self.main.section(SECT_TIE_INSTANCES) is not None
            or self.main.section(SECT_FOLIAGE_INSTANCES) is not None
            or self.main.section(SECT_SHRUB_INSTANCES) is not None
        ):
            pointers = (AssetPointer(tuid=0, offset=0, length=0),)
        else:
            pointers = ()
        self._pointer_cache[kind] = pointers
        self._pointer_maps[kind] = {
            pointer.tuid: pointer for pointer in pointers
        }
        return pointers

    def _pointer(self, kind: AssetKind, tuid: int) -> AssetPointer:
        self.asset_pointers(kind)
        try:
            return self._pointer_maps[kind][tuid]
        except KeyError as exc:
            raise MissingResourceError(
                f"{kind.name.title()} asset 0x{tuid:016X} is not "
                f"present in {self.main_path}"
            ) from exc

    def read_asset_name(
        self,
        kind: AssetKind,
        tuid: int,
    ) -> str | None:
        self._pointer(kind, tuid)
        if kind is AssetKind.SHADER:
            # Shader identities are 0x5000 record indices, parallel to
            # DEBUG.DAT 0x5D00. Without the table they stay unnamed.
            return self._shader_source_name(tuid)
        source_name = self._prototype_source_name(kind, tuid)
        if source_name is not None:
            return source_name
        if kind is AssetKind.MOBY:
            return f"Moby_{tuid:04X}"
        if kind is AssetKind.TIE:
            return f"Tie_{tuid:08X}"
        if kind is AssetKind.FOLIAGE:
            return f"Foliage_{tuid:08X}"
        if kind is AssetKind.SHRUB:
            return f"Shrub_{tuid:08X}"
        return None

    @property
    def _shader_count(self) -> int:
        return len(self.asset_pointers(AssetKind.SHADER))

    def _localized_shaders(
        self,
        meshes: list[MeshGeometry] | list[TerrainMesh],
    ) -> tuple[int, ...]:
        shader_ids: list[int] = []
        local_indices: dict[int, int] = {}
        for mesh in meshes:
            global_index = mesh.shader_index
            if global_index >= self._shader_count:
                raise FormatError(
                    f"{self.main_path}: shader index {global_index} is "
                    f"outside the {self._shader_count}-record shader table"
                )
            local_index = local_indices.get(global_index)
            if local_index is None:
                local_index = len(shader_ids)
                local_indices[global_index] = local_index
                shader_ids.append(global_index)
            mesh.shader_index = local_index
        return tuple(shader_ids)

    def _geometry_base(
        self,
        raw_offset: int,
        section_id: int,
    ) -> tuple[str, int, int]:
        relative = raw_offset & ~USE_VERTICES_DAT
        if raw_offset & USE_VERTICES_DAT:
            section = self.vertices.require_section(section_id)
            if relative > section.length:
                raise FormatError(
                    f"{self.vertices_path}: geometry offset "
                    f"{relative:#x} exceeds section 0x{section_id:X}"
                )
            return (
                self.vertices_path,
                section.offset + relative,
                section.length - relative,
            )
        size = self.source.size(self.textures_path)
        if relative > size:
            raise FormatError(
                f"{self.textures_path}: geometry offset {relative:#x} "
                f"exceeds the resource"
            )
        return self.textures_path, relative, size - relative

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
            tuple[int, int, int, int, int, int, np.ndarray]
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
            for mesh_index in range(mesh_count):
                mesh = mesh_table + mesh_index * MOBY_MESH_STRIDE
                bone_count = self.main.u8(mesh + 0x0C)
                descriptors.append(
                    (
                        self.main.u32(mesh),
                        self.main.u16(mesh + 0x12),
                        self.main.u32(mesh + 4),
                        self.main.u16(mesh + 0x0A),
                        self.main.u8(mesh + 0x0D),
                        self.main.u16(mesh + 8),
                        self._read_bone_map(
                            self.main,
                            self.main.u32(mesh + 0x20),
                            bone_count,
                        ),
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

        meshes: list[MeshGeometry] = []
        descriptor_index = 0
        for bangle_index in range(bangle_count):
            bangle = bangles_offset + bangle_index * MOBY_BANGLE_STRIDE
            mesh_count = self.main.u32(bangle + 4)
            for _mesh_index in range(mesh_count):
                (
                    index_index,
                    index_count,
                    local_vertex_offset,
                    vertex_count,
                    vertex_type,
                    shader_index,
                    bone_map,
                ) = descriptors[descriptor_index]
                descriptor_index += 1
                meshes.append(
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
                )
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
            name=self.read_asset_name(AssetKind.MOBY, tuid) or f"Moby_{tuid:04X}",
            meshes=meshes,
            bounding_sphere=bounding_sphere,
            shader_tuids=shader_tuids,
            skeleton=skeleton,
            animset_hash=(tuid + 1 if animation_offsets else None),
            bind_pose_inverse_offset=0,
        )

    def read_animations(self, moby: MobyAsset) -> list[DecodedClip]:
        if moby.skeleton is None or moby.animset_hash is None:
            return []
        offsets = self._animation_offsets.get(moby.tuid)
        if offsets is None:
            self.read_moby(moby.tuid)
            offsets = self._animation_offsets.get(moby.tuid, ())
        return decode_inline_animations(
            self._main_data,
            offsets,
            moby.skeleton,
            profile=self.animation_profile,
            label=f"{self.main_path}: Moby_{moby.tuid:04X} animations",
        )

    def read_tie(self, tuid: int) -> TieAsset:
        pointer = self._pointer(AssetKind.TIE, tuid)
        base = pointer.offset
        mesh_table = self.main.u32(base)
        mesh_count = self.main.u8(base + 0x0F)
        vertex_start = self.main.u32(base + 0x14)
        scale = np.asarray(
            self.main.vec3(base + 0x20),
            dtype=np.float32,
        )
        if mesh_count > MAX_MESHES:
            raise FormatError(
                f"Tie_{tuid:08X}: unreasonable mesh count {mesh_count}"
            )

        vertex_section = self.vertices.require_section(SECT_LEVEL_VERTICES)
        index_section = self.vertices.require_section(SECT_LEVEL_INDICES)
        descriptors: list[tuple[int, int, int, int, int]] = []
        max_vertex_end = 0
        for mesh_index in range(mesh_count):
            mesh = mesh_table + mesh_index * TIE_MESH_STRIDE
            descriptor = (
                self.main.u32(mesh),
                self.main.u16(mesh + 0x12),
                self.main.u16(mesh + 4),
                self.main.u16(mesh + 8),
                self.main.u16(mesh + 0x28),
            )
            descriptors.append(descriptor)
            max_vertex_end = max(
                max_vertex_end,
                (descriptor[2] + descriptor[3]) * 0x14,
            )
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
            index_index,
            index_count,
            vertex_index,
            vertex_count,
            shader_index,
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
                    f"outside the shader table"
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
        if index_count % 3:
            raise FormatError(
                f"Shrub_{tuid:08X}: index count {index_count} is not "
                "a triangle list"
            )
        if not index_count:
            return TieAsset(tuid=tuid, meshes=[], shader_tuids=())

        index_section = self.vertices.require_section(SECT_LEVEL_INDICES)
        index_byte_offset = index_offset * 2
        index_length = index_count * 2
        if (
            index_byte_offset > index_section.length
            or index_length > index_section.length - index_byte_offset
        ):
            raise FormatError(f"Shrub_{tuid:08X}: index buffer overrun")
        indices = np.frombuffer(
            self.source.read_slice(
                self.vertices_path,
                index_section.offset + index_byte_offset,
                index_length,
            ),
            dtype=self.vertices.endian + "u2",
            count=index_count,
        ).astype(np.int32)

        vertex_count = int(indices.max()) + 1
        vertex_length = vertex_count * SHRUB_VERTEX_STRIDE
        vertex_section = self.vertices.require_section(SECT_LEVEL_VERTICES)
        if (
            vertex_offset > vertex_section.length
            or vertex_length > vertex_section.length - vertex_offset
        ):
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
            dtype=self.vertices.endian + "i2",
            buffer=vertices,
            strides=(SHRUB_VERTEX_STRIDE, 2),
        ).astype(np.float32)
        positions *= UFRAG_SCALE
        uvs = np.ndarray(
            shape=(vertex_count, 2),
            dtype=self.vertices.endian + "f2",
            buffer=vertices,
            offset=0x08,
            strides=(SHRUB_VERTEX_STRIDE, 2),
        ).astype(np.float32)
        if not np.isfinite(uvs).all():
            raise FormatError(
                f"Shrub_{tuid:08X}: texture coordinates are not finite"
            )
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
        pointer = self._pointer(AssetKind.FOLIAGE, tuid)
        base = pointer.offset
        vertex_section = self.vertices.require_section(SECT_LEVEL_VERTICES)
        index_section = self.vertices.require_section(SECT_LEVEL_INDICES)
        branch_offset = self.main.u32(base + 0x18)
        sprite_offset = self.main.u32(base + 0x40)
        meshes: list[MeshGeometry] = []

        if branch_offset and sprite_offset < branch_offset:
            raise FormatError(
                f"Foliage_{tuid:08X}: sprite offset precedes branch data"
            )
        if branch_offset and sprite_offset > branch_offset:
            branch_length = sprite_offset - branch_offset
            if branch_length % 0x14:
                raise FormatError(
                    f"Foliage_{tuid:08X}: branch vertex range is not "
                    "0x14-byte aligned"
                )
            if branch_offset + branch_length > vertex_section.length:
                raise FormatError(
                    f"Foliage_{tuid:08X}: branch vertex buffer overrun"
                )
            branch_lod: tuple[int, int] | None = None
            for lod_index in range(4):
                lod = base + 0x20 + lod_index * 8
                index_count = self.main.u16(lod + 4)
                if index_count:
                    branch_lod = (
                        self.main.u32(lod),
                        index_count,
                    )
                    break
            if branch_lod is not None:
                index_index, index_count = branch_lod
                index_offset = index_index * 2
                index_length = index_count * 2
                if index_offset + index_length > index_section.length:
                    raise FormatError(
                        f"Foliage_{tuid:08X}: branch index buffer overrun"
                    )
                vertex_count = branch_length // 0x14
                if vertex_count > MAX_MESHES:
                    raise FormatError(
                        f"Foliage_{tuid:08X}: unreasonable branch vertex "
                        f"count {vertex_count}"
                    )
                vertex_buffer = memoryview(
                    self.source.read_slice(
                        self.vertices_path,
                        vertex_section.offset + branch_offset,
                        branch_length,
                    )
                )
                positions = np.ndarray(
                    shape=(vertex_count, 3),
                    dtype=self.main.endian + "f2",
                    buffer=vertex_buffer,
                    strides=(0x14, 2),
                ).astype(np.float32)
                positions *= YARD_TO_METERS
                uvs = np.ndarray(
                    shape=(vertex_count, 2),
                    dtype=self.main.endian + "f2",
                    buffer=vertex_buffer,
                    offset=8,
                    strides=(0x14, 2),
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
                if (
                    not np.isfinite(positions).all()
                    or not np.isfinite(uvs).all()
                ):
                    raise FormatError(
                        f"Foliage_{tuid:08X}: branch vertices are not finite"
                    )
                if indices.size and int(indices.max()) >= vertex_count:
                    raise FormatError(
                        f"Foliage_{tuid:08X}: branch index exceeds "
                        f"{vertex_count} vertices"
                    )
                meshes.append(
                    MeshGeometry(
                        shader_index=0,
                        positions=positions,
                        uvs=uvs,
                        indices=indices,
                    )
                )

        used_lods = self.main.u32(base + 0x48)
        if used_lods:
            corner_begin = self.main.u16(base + 0x50)
            corner_end = self.main.u16(base + 0x52)
            if corner_begin != corner_end:
                center_offset = self.main.u32(base + 0x44)
                corner_length = corner_end * 0x08
                center_length = (
                    (corner_end // 4) * FOLIAGE_CENTER_STRIDE
                )
                if sprite_offset + corner_length > vertex_section.length:
                    raise FormatError(
                        f"Foliage_{tuid:08X}: corner buffer overrun"
                    )
                if center_offset + center_length > vertex_section.length:
                    raise FormatError(
                        f"Foliage_{tuid:08X}: center buffer overrun"
                    )
                corner_buffer = memoryview(
                    self.source.read_slice(
                        self.vertices_path,
                        vertex_section.offset + sprite_offset,
                        corner_length,
                    )
                )
                center_buffer = memoryview(
                    self.source.read_slice(
                        self.vertices_path,
                        vertex_section.offset + center_offset,
                        center_length,
                    )
                )
                meshes.append(
                    self._decode_foliage_sprite_mesh(
                        corner_buffer=corner_buffer,
                        center_buffer=center_buffer,
                        corner_begin=corner_begin,
                        corner_end=corner_end,
                        center_stride=FOLIAGE_CENTER_STRIDE,
                        endian=self.main.endian,
                        label=f"Foliage_{tuid:08X}",
                        position_scale=YARD_TO_METERS,
                    )
                )

        if not meshes:
            return TieAsset(tuid=tuid, meshes=[], shader_tuids=())
        texture_index = self.main.u32(base + 0x08)
        texture_section = self.main.require_section(SECT_TEXTURE)
        if texture_index >= texture_section.count:
            raise FormatError(
                f"Foliage_{tuid:08X}: texture index {texture_index} is "
                f"outside the {texture_section.count}-record table"
            )
        return TieAsset(
            tuid=tuid,
            meshes=meshes,
            shader_tuids=(
                FOLIAGE_SHADER_TUID_BASE + texture_index,
            ),
        )

    def read_shader(self, tuid: int) -> ShaderInfo:
        cached = self._shader_cache.get(tuid)
        if cached is not None:
            return cached
        if tuid >= FOLIAGE_SHADER_TUID_BASE:
            texture_index = tuid - FOLIAGE_SHADER_TUID_BASE
            texture_section = self.main.require_section(SECT_TEXTURE)
            if texture_index >= texture_section.count:
                raise MissingResourceError(
                    f"Foliage shader 0x{tuid:016X} references texture "
                    f"index {texture_index}, outside the "
                    f"{texture_section.count}-record texture table"
                )
            shader = ShaderInfo(
                tuid=tuid,
                albedo_texture_id=(
                    texture_section.offset + texture_index * TEXTURE_STRIDE
                ),
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
            for offset in (0, 4, 8, 0x0C)
        )
        material_word = self.main.u32(base + 0x10)
        rendering_mode = self.main.u8(base + 0x11)
        alpha_mode = TOD_RENDERING_MODES.get(rendering_mode)
        if alpha_mode is None:
            warning = (
                f"Shader {tuid} uses unknown TOD rendering mode "
                f"{rendering_mode}; treating it as opaque"
            )
            LOGGER.warning(warning)
            self.warnings.append(warning)
            alpha_mode = "OPAQUE"
        shader = ShaderInfo(
            tuid=tuid,
            albedo_texture_id=texture_ids[0],
            normal_texture_id=texture_ids[1],
            specular_texture_id=texture_ids[2],
            detail_texture_id=texture_ids[3],
            material_word=material_word,
            material_values=tuple(
                tuple(
                    self.main.f32(
                        base
                        + 0x20
                        + vector_index * 0x10
                        + component_index * 4
                    )
                    for component_index in range(4)
                )
                for vector_index in range(6)
            ),
            alpha_mode_override=alpha_mode,
            lookup_path=self._shader_source_name(tuid),
        )
        self._shader_cache[tuid] = shader
        return shader

    def _shader_source_name(self, index: int) -> str | None:
        names = self.debug_names
        if names is None or not names.shaders:
            return None
        table = self._checked_names(
            "shader",
            names.shaders,
            self._shader_count,
        )
        if table is None or not 0 <= index < len(table):
            return None
        return table[index]

    @cached_property
    def _texture_locations(self) -> dict[int, _TextureLocation]:
        texture_section = self.main.section(SECT_TEXTURE)
        if texture_section is None:
            return {}
        if texture_section.length != TEXTURE_STRIDE:
            raise FormatError(
                f"{self.main_path}: texture record stride is "
                f"{texture_section.length:#x}; expected {TEXTURE_STRIDE:#x}"
            )
        stream_offsets: dict[int, int] = {}
        stream_section = self.main.section(SECT_TEXSTREAM)
        if stream_section is not None:
            if stream_section.length != TEXSTREAM_STRIDE:
                raise FormatError(
                    f"{self.main_path}: texstream record stride is "
                    f"{stream_section.length:#x}; "
                    f"expected {TEXSTREAM_STRIDE:#x}"
                )
            for index in range(stream_section.count):
                base = stream_section.offset + index * TEXSTREAM_STRIDE
                stream_offsets[self.main.u16(base + 6)] = self.main.u32(base)

        locations: dict[int, _TextureLocation] = {}
        warned_missing_stream = False
        for index in range(texture_section.count):
            base = texture_section.offset + index * TEXTURE_STRIDE
            pixel_offset = self.main.u32(base)
            mip_count = self.main.u16(base + 4)
            format_code = (self.main.u16(base + 6) >> 8) & 0xF
            width = self.main.u16(base + 0x18)
            height = self.main.u16(base + 0x1A)
            streamed_offset = stream_offsets.get(index)
            if streamed_offset is not None and self.texstream_path is not None:
                pixel_offset = streamed_offset
                width *= 2
                height *= 2
                mip_count += 1
                path = self.texstream_path
            else:
                path = self.textures_path
                if (
                    streamed_offset is not None
                    and self.texstream_path is None
                    and not warned_missing_stream
                ):
                    warning = (
                        "texstream.dat is unavailable; using lower-resolution "
                        "textures.dat payloads"
                    )
                    LOGGER.warning(warning)
                    self.warnings.append(warning)
                    warned_missing_stream = True
            length = texture_mip_size(width, height, format_code)
            if pixel_offset + length > self.source.size(path):
                raise FormatError(
                    f"{path}: texture 0x{base:08X} payload overruns "
                    f"the resource"
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
                path=path,
            )
        return locations

    @property
    def texture_infos(self) -> dict[int, TextureInfo]:
        return {
            texture_id: location.info
            for texture_id, location in self._texture_locations.items()
        }

    def read_texture(
        self,
        texture_id: int,
        *,
        lookup_path: str | None = None,
    ) -> DecodedTexture:
        del lookup_path
        cached = self._texture_cache.get(texture_id)
        if cached is not None:
            return cached
        try:
            location = self._texture_locations[texture_id]
        except KeyError as exc:
            raise MissingResourceError(
                f"Texture 0x{texture_id:08X} is not present in "
                f"{self.main_path}"
            ) from exc
        texture = decode_texture(
            location.info,
            self.source.read_slice(
                location.path,
                location.info.offset,
                location.info.length,
            ),
        )
        self._texture_cache[texture_id] = texture
        return texture

    def clear_texture_cache(self) -> None:
        self._texture_cache.clear()

    def read_level_collision(self) -> LevelCollisionScene:
        path = self._find_level_resource("collision.dat")
        scene = decode_level_collision_resource(
            self.source.read(path),
            resource_path=path,
            warnings=self.warnings,
        )
        names = self.collision_material_names
        if names:
            used: set[int] = set()
            for mesh in scene.meshes:
                if mesh.material_ids is not None:
                    used.update(np.unique(mesh.material_ids).tolist())
            scene.material_names = {
                material_id: name
                for material_id in sorted(used)
                if material_id < len(names)
                and (name := names[material_id]) is not None
            }
        return scene

    def read_zones(self) -> list[Zone]:
        tie_instances: list[PlacedInstance] = []
        tie_section = self.main.section(SECT_TIE_INSTANCES)
        if tie_section is not None:
            self._validate_stride(
                tie_section,
                TIE_INSTANCE_STRIDE,
                "tie instance",
            )
            for index in range(tie_section.count):
                base = tie_section.offset + index * TIE_INSTANCE_STRIDE
                matrix = self._read_single_matrix(self.main, base)
                matrix[:3, 3] *= YARD_TO_METERS
                tie_instances.append(
                    PlacedInstance(
                        asset_tuid=self.main.u32(base + 0x50),
                        instance_tuid=index,
                        name=f"TieInstance_{index:04X}",
                        matrix=matrix,
                    )
                )

        foliage_instances: list[PlacedInstance] = []
        foliage_section = self.main.section(SECT_FOLIAGE_INSTANCES)
        if foliage_section is not None:
            self._validate_stride(
                foliage_section,
                FOLIAGE_INSTANCE_STRIDE,
                "foliage instance",
            )
            if foliage_section.count > MAX_MESHES:
                raise FormatError(
                    f"{self.main_path}: unreasonable foliage instance count "
                    f"{foliage_section.count}"
                )
            foliage_tuids = {
                pointer.tuid
                for pointer in self.asset_pointers(AssetKind.FOLIAGE)
            }
            for index in range(foliage_section.count):
                base = (
                    foliage_section.offset
                    + index * FOLIAGE_INSTANCE_STRIDE
                )
                foliage_tuid = self.main.u32(base + 0xC4)
                if foliage_tuid not in foliage_tuids:
                    raise FormatError(
                        f"Foliage instance {index} references unknown "
                        f"descriptor 0x{foliage_tuid:08X}"
                    )
                matrix = self._read_single_matrix(self.main, base)
                matrix[:3, 3] *= YARD_TO_METERS
                foliage_instances.append(
                    PlacedInstance(
                        asset_tuid=foliage_tuid,
                        instance_tuid=index,
                        name=f"FoliageInstance_{index:04X}",
                        matrix=matrix,
                    )
                )

        shrub_instances = self._read_shrub_instances()
        self._apply_instance_names("tie", tie_instances)
        self._apply_instance_names("foliage", foliage_instances)
        self._apply_instance_names("shrub", shrub_instances)
        terrain = self._read_terrain()
        shader_tuids = self._localized_shaders(terrain)
        if (
            not tie_instances
            and not foliage_instances
            and not shrub_instances
            and not terrain
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
        if len(shrub_pointers) > MAX_TOD_SHRUB_CLASSES:
            raise FormatError(
                f"{self.main_path}: shrub clusters can address at most "
                f"{MAX_TOD_SHRUB_CLASSES} assets, found "
                f"{len(shrub_pointers)}"
            )
        referenced = np.zeros(instance_count, dtype=np.bool_)
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
            active_ranges = tuple(
                item for item in local_ranges if item[0]
            )
            shrub_mask = self.main.u32(cluster + 0x4C)
            shrub_indices = tuple(
                index
                for index in range(MAX_TOD_SHRUB_CLASSES - 1, -1, -1)
                if shrub_mask & (1 << index)
            )
            if len(active_ranges) != len(shrub_indices):
                raise FormatError(
                    f"{self.main_path}: shrub cluster {cluster_index} has "
                    f"{len(active_ranges)} populated ranges for "
                    f"{len(shrub_indices)} assets"
                )
            first_instance = self.main.u16(cluster + 0x54)
            for shrub_index, (count, offset) in zip(
                shrub_indices,
                active_ranges,
                strict=True,
            ):
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
                    if referenced[instance_index]:
                        raise FormatError(
                            f"{self.main_path}: shrub instance "
                            f"{instance_index} is referenced more than once"
                        )
                    referenced[instance_index] = True
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
                    if (
                        not np.isfinite(position).all()
                        or not np.isfinite(scale)
                        or not np.isfinite(row_x).all()
                        or not np.isfinite(row_y).all()
                    ):
                        raise FormatError(
                            f"{self.main_path}: shrub instance "
                            f"{instance_index} transform is not finite"
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
        if instance_count and not referenced.all():
            missing = int(np.flatnonzero(~referenced)[0])
            raise FormatError(
                f"{self.main_path}: shrub instance {missing} is not "
                "referenced by a cluster"
            )
        return instances

    @staticmethod
    def _validate_stride(
        section: Section,
        stride: int,
        label: str,
    ) -> None:
        if section.length != stride:
            raise FormatError(
                f"{label} section has stride {section.length:#x}; "
                f"expected {stride:#x}"
            )

    def _read_terrain(self) -> list[TerrainMesh]:
        section = self.main.section(SECT_UFRAGS)
        if section is None:
            return []
        self._validate_stride(section, UFRAG_STRIDE, "UFrag")
        vertex_section = self.vertices.require_section(SECT_LEVEL_VERTICES)
        index_section = self.vertices.require_section(SECT_LEVEL_INDICES)
        terrain: list[TerrainMesh] = []
        for index in range(section.count):
            base = section.offset + index * UFRAG_STRIDE
            index_offset = self.main.u32(base + 0x40) * 2
            vertex_offset = self.main.u32(base + 0x44)
            index_count = self.main.u16(base + 0x48)
            vertex_count = self.main.u16(base + 0x4A)
            if not index_count or not vertex_count:
                continue
            vertex_length = vertex_count * UFRAG_VERTEX_STRIDE
            index_length = index_count * 2
            if vertex_offset + vertex_length > vertex_section.length:
                warning = f"UFrag {index} skipped: vertex buffer overrun"
                LOGGER.warning(warning)
                self.warnings.append(warning)
                continue
            if index_offset + index_length > index_section.length:
                warning = f"UFrag {index} skipped: index buffer overrun"
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
                strides=(UFRAG_VERTEX_STRIDE, 2),
            ).astype(np.float32)
            positions *= UFRAG_SCALE
            positions += (
                np.asarray(
                    self.main.vec3(base + 0x60),
                    dtype=np.float32,
                )
                * UFRAG_SCALE
            )
            uvs = np.ndarray(
                shape=(vertex_count, 2),
                dtype=self.main.endian + "f2",
                buffer=vertices,
                offset=8,
                strides=(UFRAG_VERTEX_STRIDE, 2),
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
                    tuid=self.main.u64(base),
                    shader_index=self.main.u16(base + 0x50),
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
        section = gameplay.section(SECT_GAMEPLAY_MOBYS)
        if section is not None:
            self._validate_stride(
                section,
                GAMEPLAY_MOBY_STRIDE,
                "gameplay Moby",
            )
        instances: list[PlacedInstance] = []
        for index in range(section.count if section is not None else 0):
            base = section.offset + index * GAMEPLAY_MOBY_STRIDE
            moby_index = gameplay.u16(base + 0x3C)
            try:
                class_id = read_class_id(
                    gameplay,
                    gameplay.u32(base),
                    label=f"{self.gameplay_path} Moby {index}",
                )
            except FormatError as exc:
                self._warn_once("moby class", f"{exc}; class omitted")
                class_id = None
            instances.append(
                PlacedInstance(
                    asset_tuid=moby_index,
                    instance_tuid=index,
                    name=(
                        f"Moby_{moby_index:04X}_Instance_{index:04X}"
                    ),
                    matrix=_matrix_from_zyx(
                        gameplay.vec3(base + 0x18),
                        gameplay.vec3(base + 0x24),
                        gameplay.f32(base + 0x30),
                    ),
                    class_id=class_id,
                )
            )
        self._apply_instance_names("moby", instances)
        region = GameplayRegion(name="art", moby_instances=instances)
        try:
            context = read_tod_gameplay_context(
                gameplay,
                moby_count=len(instances),
                label=self.gameplay_path,
            )
        except FormatError as exc:
            self._warn_once(
                "gameplay context",
                f"Gameplay volumes, paths, and Clues were skipped: {exc}",
            )
        else:
            for family, items in (
                ("path", context.paths),
                ("volume", context.volumes),
                ("clue", context.clues),
                ("controller", context.controllers),
                ("pod", context.pods),
                ("area", context.areas),
                ("scent", context.scents),
            ):
                self._apply_source_names(family, items)
            for area in context.areas:
                area.name = area.source_name or ""
            region.paths = context.paths
            region.triggers = context.volumes
            region.clues = context.clues
            region.controllers = context.controllers
            region.moby_groups = context.pods
            region.areas = context.areas
            region.clue_groups = context.scents
            class_ids = [item.class_id for item in instances]
            class_ids += [clue.class_id for clue in context.clues]
            class_ids += context.controller_class_ids
            if any(class_id is not None for class_id in class_ids):
                names = self.class_names
                for clue in context.clues:
                    clue.class_name = class_name(names, clue.class_id)
                for controller, class_id in zip(
                    context.controllers,
                    context.controller_class_ids,
                ):
                    controller.class_name = class_name(names, class_id)
        if any(item.class_id is not None for item in instances):
            names = self.class_names
            for item in instances:
                item.class_name = class_name(names, item.class_id)
        return [region]

    def consume_import_warnings(self) -> tuple[str, ...]:
        warnings = tuple(self.warnings)
        self.warnings.clear()
        return warnings
