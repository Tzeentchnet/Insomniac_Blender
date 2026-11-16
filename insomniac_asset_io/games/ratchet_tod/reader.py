from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from pathlib import PurePosixPath

import numpy as np

from ...core.binary import IghwFile, Section
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
SECT_SHRUB_INSTANCES = 0x7540
SECT_FOLIAGE_INSTANCES = 0x7440
SECT_GAMEPLAY_MOBYS = 0x7340

MOBY_STRIDE = 0xC0
MOBY_BANGLE_STRIDE = 0x08
MOBY_MESH_STRIDE = 0x40
TIE_STRIDE = 0x80
TIE_MESH_STRIDE = 0x40
SHADER_STRIDE = 0x80
TEXTURE_STRIDE = 0x20
TEXSTREAM_STRIDE = 0x10
UFRAG_STRIDE = 0x80
UFRAG_VERTEX_STRIDE = 0x18
TIE_INSTANCE_STRIDE = 0x80
GAMEPLAY_MOBY_STRIDE = 0x48

USE_VERTICES_DAT = 0x8000_0000
YARD_TO_METERS = np.float32(0.9144)
UFRAG_SCALE = np.float32(YARD_TO_METERS / 256.0)


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
    material_surface_channels_by_default = True
    material_base_color_factor_supported = False
    material_emission_uses_albedo = False

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

    def _optional_level_resource(self, filename: str) -> str | None:
        try:
            return self._find_level_resource(filename)
        except MissingResourceError:
            return None

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
        if kind is AssetKind.MOBY:
            return f"Moby_{tuid:04X}"
        if kind is AssetKind.TIE:
            return f"Tie_{tuid:08X}"
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
            name=f"Moby_{tuid:04X}",
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

    def read_shader(self, tuid: int) -> ShaderInfo:
        cached = self._shader_cache.get(tuid)
        if cached is not None:
            return cached
        base = self._pointer(AssetKind.SHADER, tuid).offset
        texture_ids = tuple(
            self.main.u32(base + offset) or None
            for offset in (0, 4, 8, 0x0C)
        )
        material_word = self.main.u32(base + 0x10)
        rendering_mode = self.main.u8(base + 0x11)
        alpha_mode = {
            0: "OPAQUE",
            1: "OPAQUE",
            2: "OPAQUE",
            4: "MASK",
            5: "MASK",
            6: "BLEND",
        }.get(rendering_mode)
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
        )
        self._shader_cache[tuid] = shader
        return shader

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

        terrain = self._read_terrain()
        shader_tuids = self._localized_shaders(terrain)
        shrub_section = self.main.section(SECT_SHRUB_INSTANCES)
        foliage_section = self.main.section(SECT_FOLIAGE_INSTANCES)
        if not tie_instances and not terrain:
            return []
        return [
            Zone(
                tuid=0,
                tie_instances=tie_instances,
                terrain_meshes=terrain,
                shader_tuids=shader_tuids,
                unsupported_shrub_instances=(
                    shrub_section.count if shrub_section else 0
                ),
                unsupported_foliage_instances=(
                    foliage_section.count if foliage_section else 0
                ),
            )
        ]

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
        if section is None:
            return [GameplayRegion(name="art", moby_instances=[])]
        self._validate_stride(
            section,
            GAMEPLAY_MOBY_STRIDE,
            "gameplay Moby",
        )
        instances: list[PlacedInstance] = []
        for index in range(section.count):
            base = section.offset + index * GAMEPLAY_MOBY_STRIDE
            moby_index = gameplay.u16(base + 0x3C)
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
                )
            )
        return [GameplayRegion(name="art", moby_instances=instances)]

    def consume_import_warnings(self) -> tuple[str, ...]:
        warnings = tuple(self.warnings)
        self.warnings.clear()
        return warnings
