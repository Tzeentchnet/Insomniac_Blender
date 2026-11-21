from __future__ import annotations

import json
import logging
import math
import re
import zlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from ..core.collision import (
    CollisionMesh as LevelCollisionMesh,
)
from ..core.collision import (
    CollisionScene,
    validate_collision_scene,
)
from ..core.errors import InsomniacError
from ..games.resistance_2.formats import (
    AssetKind,
    CollisionMesh,
    CollisionPrimitive,
    DecodedClip,
    DecodedTexture,
    MeshGeometry,
    MobyAsset,
    PlacedInstance,
    ShaderInfo,
    Skeleton,
    TextureInfo,
    TieAsset,
    Zone,
)
from ..games.resistance_2.materials import (
    EMISSIVE_CHANNEL_NAME,
    can_reconstruct_normal_map,
    has_emissive_mask,
    reconstruct_normal_map,
)
from ..games.resistance_2.reader import Resistance2Reader

LOGGER = logging.getLogger(__name__)

_IMAGE_HAS_ALPHA_KEY = "insomniac_has_alpha"
_LINEAR_INTERPOLATION = (
    bpy.types.Keyframe.bl_rna.properties["interpolation"]
    .enum_items["LINEAR"]
    .value
)
_ACTION_VALUE_EPSILON = 1e-6
_TIE_LIGHTMAP_ATLAS_DOWNSAMPLE = 4
_TIE_LIGHTMAP_ATLAS_PADDING = 2
_TIE_LIGHTMAP_ATLAS_WIDTHS = (4096, 8192)
_TIE_LIGHTMAP_ATLAS_MAX_HEIGHT = 8192
_TIE_LIGHTMAP_SCALE_ATTRIBUTE = "insomniac_lightmap_uv_scale"
_TIE_LIGHTMAP_OFFSET_ATTRIBUTE = "insomniac_lightmap_uv_offset"
_RNM_BASIS = (
    (math.sqrt(2.0 / 3.0), 0.0, math.sqrt(1.0 / 3.0)),
    (-math.sqrt(1.0 / 6.0), math.sqrt(1.0 / 2.0), math.sqrt(1.0 / 3.0)),
    (-math.sqrt(1.0 / 6.0), -math.sqrt(1.0 / 2.0), math.sqrt(1.0 / 3.0)),
)


def _reader_game_id(reader: object | None) -> str:
    return str(getattr(reader, "game_id", "RESISTANCE_2"))


def _reader_game_label(reader: object | None) -> str:
    return str(getattr(reader, "game_label", "Resistance 2"))


def _reader_engine_id(reader: object | None) -> str:
    return str(getattr(reader, "engine_id", "V2"))


def _reader_asset_prefix(reader: object | None) -> str:
    return str(getattr(reader, "asset_prefix", "R2"))


def _reader_axis_conversion_x_degrees(reader: object | None) -> float:
    return float(getattr(reader, "axis_conversion_x_degrees", 90.0))


def _reader_asset_scope(reader: object | None) -> str:
    level_local = getattr(reader, "level_local_asset_ids", None)
    if level_local is None:
        level_local = _reader_game_id(reader) == "RATCHET_TOD"
    if not bool(level_local):
        return ""
    prefix = str(getattr(reader, "resource_prefix", "")).replace("\\", "/")
    if not prefix:
        source = getattr(reader, "source", None)
        root = getattr(source, "root", None)
        prefix = str(getattr(root, "name", ""))
    marker = "/levels/"
    normalized = "/" + prefix.lstrip("/")
    if marker in normalized.casefold():
        marker_index = normalized.casefold().index(marker) + len(marker)
        prefix = normalized[marker_index:]
    return _safe_name(prefix.replace("/", "_")) if prefix else ""


def _normalized_texture_lookup_path(path: str | None) -> str:
    return (path or "").replace("\\", "/").strip("/").casefold()


@dataclass(frozen=True, slots=True)
class AssetImportOptions:
    materials: bool = True
    animations: bool = True
    animation_clip_limit: int = 0
    convert_axes: bool = True
    cycles_materials: bool = False
    damage_permutations: bool = False
    lower_lods: bool = False
    collision_geometry: bool = False
    source_default_parts: bool = False


@dataclass(frozen=True, slots=True)
class LevelImportOptions:
    materials: bool = True
    terrain: bool = True
    ties: bool = True
    vegetation: bool = True
    gameplay: bool = True
    collision_geometry: bool = False
    convert_axes: bool = True
    cycles_materials: bool = False


@dataclass(slots=True)
class ImportResult:
    root: bpy.types.Object
    objects: int = 0
    meshes: int = 0
    materials: int = 0
    images: int = 0
    actions: int = 0
    warnings: list[str] | None = None

    def summary(self) -> str:
        warning_count = len(self.warnings or ())
        return (
            f"{self.objects} objects, {self.meshes} meshes, "
            f"{self.materials} materials, {self.images} images, "
            f"{self.actions} actions, {warning_count} warnings"
        )


@dataclass(slots=True)
class MaterialImportResult:
    materials: tuple[bpy.types.Material, ...]
    images: tuple[bpy.types.Image, ...]
    warnings: list[str]

    def summary(self) -> str:
        return (
            f"{len(self.materials)} materials, {len(self.images)} images, "
            f"{len(self.warnings)} warnings"
        )


@dataclass(slots=True)
class _CombinedMesh:
    mesh: bpy.types.Mesh
    bone_indices: np.ndarray | None
    bone_weights: np.ndarray | None


class _MaterialFactory:
    def __init__(
        self,
        reader: Resistance2Reader,
        enabled: bool,
        warnings: list[str],
        *,
        cycles_materials: bool = False,
    ) -> None:
        self.reader = reader
        self.enabled = enabled
        self.warnings = warnings
        self.cycles_materials = cycles_materials
        self.surface_material_channels = cycles_materials or bool(
            getattr(
                reader,
                "material_surface_channels_by_default",
                False,
            )
        )
        self.base_color_factor_supported = bool(
            getattr(reader, "material_base_color_factor_supported", True)
        )
        self.use_base_color_factor = self.base_color_factor_supported and (
            cycles_materials
            or bool(
                getattr(
                    reader,
                    "material_uses_base_color_factor_by_default",
                    False,
                )
            )
        )
        self.materials: dict[int, bpy.types.Material] = {}
        self.terrain_materials: dict[
            tuple[int, int, int | None],
            bpy.types.Material,
        ] = {}
        self.vertex_lit_materials: dict[
            tuple[str, int, bool],
            bpy.types.Material,
        ] = {}
        self.tie_lightmap_atlas: bpy.types.Image | None = None
        self.tie_directional_lightmap_atlas: bpy.types.Image | None = None
        self.tie_lightmap_transforms: dict[
            tuple[int, int],
            tuple[float, float, float, float],
        ] = {}
        self.images: dict[
            tuple[int, str, str],
            bpy.types.Image,
        ] = {}
        self._normal_map_support: dict[
            tuple[int, str, str],
            tuple[bool, int],
        ] = {}
        self._emissive_masks: dict[
            tuple[int, str, str],
            bool,
        ] = {}

    @property
    def material_count(self) -> int:
        return (
            len(self.materials)
            + len(self.terrain_materials)
            + len(self.vertex_lit_materials)
        )

    def prepare_tie_lightmap_atlas(
        self,
        zones: Iterable[Zone],
    ) -> None:
        if (
            not self.enabled
            or not bool(
                getattr(self.reader, "tie_instance_lightmaps", False)
            )
        ):
            return
        references = {
            (instance.lightmap_zone_tuid, instance.lightmap_index)
            for zone in zones
            for instance in zone.tie_instances
            if instance.lightmap_zone_tuid is not None
            and instance.lightmap_index is not None
        }
        if not references:
            return
        info_reader = getattr(self.reader, "zone_lightmap_infos", None)
        texture_reader = getattr(self.reader, "read_zone_lightmap", None)
        if not callable(info_reader) or not callable(texture_reader):
            message = (
                "Tie instances reference baked lightmaps, but the reader "
                "does not expose zone lightmap data"
            )
            LOGGER.warning(message)
            self.warnings.append(message)
            return

        directional_enabled = bool(
            getattr(self.reader, "zone_directional_lightmaps", False)
        )
        directional_info_reader = getattr(
            self.reader,
            "zone_directional_lightmap_infos",
            None,
        )
        directional_texture_reader = getattr(
            self.reader,
            "read_zone_directional_lightmap",
            None,
        )
        if directional_enabled and (
            not callable(directional_info_reader)
            or not callable(directional_texture_reader)
        ):
            message = (
                "Tie instances reference paired directional lightmaps, but "
                "the reader does not expose their zone texture data"
            )
            LOGGER.warning(message)
            self.warnings.append(message)
            return

        records = []
        infos_by_zone: dict[int, dict[int, TextureInfo]] = {}
        directional_infos_by_zone: dict[int, dict[int, TextureInfo]] = {}
        for zone_tuid, lightmap_index in sorted(references):
            infos = infos_by_zone.get(zone_tuid)
            if infos is None:
                infos = info_reader(zone_tuid)
                infos_by_zone[zone_tuid] = infos
            info = infos.get(lightmap_index)
            if info is None:
                message = (
                    f"Zone 0x{zone_tuid:016X} has no Tie lightmap "
                    f"{lightmap_index}"
                )
                LOGGER.warning(message)
                self.warnings.append(message)
                continue
            width = max(
                1,
                info.width // _TIE_LIGHTMAP_ATLAS_DOWNSAMPLE,
            )
            height = max(
                1,
                info.height // _TIE_LIGHTMAP_ATLAS_DOWNSAMPLE,
            )
            if directional_enabled:
                directional_infos = directional_infos_by_zone.get(zone_tuid)
                if directional_infos is None:
                    directional_infos = directional_info_reader(zone_tuid)
                    directional_infos_by_zone[zone_tuid] = directional_infos
                directional_info = directional_infos.get(lightmap_index)
                if directional_info is None:
                    message = (
                        f"Zone 0x{zone_tuid:016X} has no paired directional "
                        f"Tie lightmap {lightmap_index}"
                    )
                    LOGGER.warning(message)
                    self.warnings.append(message)
                    continue
                can_reduce = (
                    directional_info.width % width == 0
                    and directional_info.height % height == 0
                )
                can_expand = (
                    width % directional_info.width == 0
                    and height % directional_info.height == 0
                )
                if not (can_reduce or can_expand):
                    raise InsomniacError(
                        f"Directional Tie lightmap "
                        f"{directional_info.width}x{directional_info.height} "
                        f"cannot be resized to paired atlas tile "
                        f"{width}x{height}"
                    )
            records.append(
                ((zone_tuid, lightmap_index), info, width, height)
            )
        if not records:
            return

        atlas_width, atlas_height, placements = (
            self._pack_tie_lightmap_atlas(records)
        )
        scope = _safe_name(
            str(getattr(self.reader, "resource_prefix", "level"))
            .replace("\\", "_")
            .replace("/", "_")
        )
        base_name = "_".join(
            part
            for part in (
                _reader_asset_prefix(self.reader),
                scope,
            )
            if part
        )
        name = f"{base_name}_Tie_Lightmap_Atlas"
        image = self._create_tie_lightmap_atlas_image(
            name=name,
            scope=scope,
            texture_role="tie_lightmap_atlas",
            colorspace_role="lightmap",
            label="Tie lightmap",
            atlas_width=atlas_width,
            atlas_height=atlas_height,
            records=records,
            placements=placements,
            texture_reader=texture_reader,
            resize=self._downsample_tie_lightmap,
            is_data=False,
        )
        self.images[(0, "tie_lightmap_atlas", scope)] = image
        self.tie_lightmap_atlas = image
        if directional_enabled:
            directional_name = (
                f"{base_name}_Tie_Directional_Lightmap_Atlas"
            )
            directional_image = self._create_tie_lightmap_atlas_image(
                name=directional_name,
                scope=scope,
                texture_role="tie_directional_lightmap_atlas",
                colorspace_role="directional_lightmap",
                label="Directional Tie lightmap",
                atlas_width=atlas_width,
                atlas_height=atlas_height,
                records=records,
                placements=placements,
                texture_reader=directional_texture_reader,
                resize=self._resize_directional_lightmap,
                is_data=True,
            )
            self.images[
                (0, "tie_directional_lightmap_atlas", scope)
            ] = directional_image
            self.tie_directional_lightmap_atlas = directional_image
        self.tie_lightmap_transforms = {
            reference: (
                width / atlas_width,
                height / atlas_height,
                (packed_x + _TIE_LIGHTMAP_ATLAS_PADDING) / atlas_width,
                (packed_y + _TIE_LIGHTMAP_ATLAS_PADDING) / atlas_height,
            )
            for reference, (
                packed_x,
                packed_y,
                width,
                height,
            ) in placements.items()
        }

    def _create_tie_lightmap_atlas_image(
        self,
        *,
        name: str,
        scope: str,
        texture_role: str,
        colorspace_role: str,
        label: str,
        atlas_width: int,
        atlas_height: int,
        records: list[tuple[tuple[int, int], TextureInfo, int, int]],
        placements: dict[
            tuple[int, int],
            tuple[int, int, int, int],
        ],
        texture_reader: Callable[[int, int], DecodedTexture],
        resize: Callable[[np.ndarray, int, int], np.ndarray],
        is_data: bool,
    ) -> bpy.types.Image:
        image = next(
            (
                candidate
                for candidate in bpy.data.images
                if candidate.name == name
                and candidate.get("insomniac_texture_role")
                == texture_role
                and candidate.size[0] == atlas_width
                and candidate.size[1] == atlas_height
            ),
            None,
        )
        if image is None:
            image = bpy.data.images.new(
                name,
                width=atlas_width,
                height=atlas_height,
                alpha=True,
                float_buffer=False,
                is_data=is_data,
            )
            atlas = np.full(
                (atlas_height, atlas_width, 4),
                255,
                dtype=np.uint8,
            )
            for key, _info, width, height in records:
                placement = placements[key]
                try:
                    texture = texture_reader(*key)
                except InsomniacError as exc:
                    message = (
                        f"{label} {key[1]} in zone "
                        f"0x{key[0]:016X} skipped: {exc}"
                    )
                    LOGGER.warning(message)
                    self.warnings.append(message)
                    continue
                reduced = resize(
                    texture.rgba,
                    width,
                    height,
                )
                tile = np.pad(
                    np.flipud(reduced),
                    (
                        (
                            _TIE_LIGHTMAP_ATLAS_PADDING,
                            _TIE_LIGHTMAP_ATLAS_PADDING,
                        ),
                        (
                            _TIE_LIGHTMAP_ATLAS_PADDING,
                            _TIE_LIGHTMAP_ATLAS_PADDING,
                        ),
                        (0, 0),
                    ),
                    mode="edge",
                )
                packed_x, packed_y = placement[:2]
                atlas[
                    packed_y : packed_y + tile.shape[0],
                    packed_x : packed_x + tile.shape[1],
                ] = tile
            pixels = np.multiply(
                atlas,
                np.float32(1.0 / 255.0),
                dtype=np.float32,
            ).ravel()
            image.pixels.foreach_set(pixels)
            image.pack()
            image.update()
        self._apply_colorspace(image, colorspace_role)
        image["insomniac_texture_role"] = texture_role
        image["insomniac_asset_scope"] = scope
        image["insomniac_lightmap_count"] = len(placements)
        image["insomniac_lightmap_downsample"] = (
            _TIE_LIGHTMAP_ATLAS_DOWNSAMPLE
        )
        image[_IMAGE_HAS_ALPHA_KEY] = False
        return image

    @staticmethod
    def _pack_tie_lightmap_atlas(
        records: list[
            tuple[tuple[int, int], TextureInfo, int, int]
        ],
    ) -> tuple[
        int,
        int,
        dict[tuple[int, int], tuple[int, int, int, int]],
    ]:
        padding = _TIE_LIGHTMAP_ATLAS_PADDING
        ordered = sorted(
            records,
            key=lambda record: (record[3], record[2], record[0]),
            reverse=True,
        )
        for atlas_width in _TIE_LIGHTMAP_ATLAS_WIDTHS:
            placements: dict[
                tuple[int, int],
                tuple[int, int, int, int],
            ] = {}
            x = 4
            y = 0
            row_height = 4
            for key, _info, width, height in ordered:
                packed_width = width + padding * 2
                packed_height = height + padding * 2
                if x + packed_width > atlas_width:
                    y += row_height
                    x = 0
                    row_height = 0
                placements[key] = (x, y, width, height)
                x += packed_width
                row_height = max(row_height, packed_height)
            atlas_height = (y + row_height + 3) & ~3
            if atlas_height <= _TIE_LIGHTMAP_ATLAS_MAX_HEIGHT:
                return atlas_width, max(4, atlas_height), placements
        raise InsomniacError(
            "Tie lightmaps exceed the supported 8192x8192 compact atlas"
        )

    @staticmethod
    def _downsample_tie_lightmap(
        rgba: np.ndarray,
        width: int,
        height: int,
    ) -> np.ndarray:
        source_height, source_width = rgba.shape[:2]
        if source_width == width and source_height == height:
            return rgba
        if source_width % width or source_height % height:
            raise InsomniacError(
                f"Tie lightmap {source_width}x{source_height} cannot be "
                f"reduced to {width}x{height} with integral blocks"
            )
        block_width = source_width // width
        block_height = source_height // height
        encoded = rgba.astype(np.float32) * np.float32(1.0 / 255.0)
        rgb = encoded[..., :3]
        linear_rgb = np.where(
            rgb <= 0.04045,
            rgb / 12.92,
            ((rgb + 0.055) / 1.055) ** 2.4,
        )
        reduced_rgb = linear_rgb.reshape(
            height,
            block_height,
            width,
            block_width,
            3,
        ).mean(axis=(1, 3), dtype=np.float32)
        encoded_rgb = np.where(
            reduced_rgb <= 0.0031308,
            reduced_rgb * 12.92,
            1.055 * (reduced_rgb ** (1.0 / 2.4)) - 0.055,
        )
        reduced_alpha = encoded[..., 3].reshape(
            height,
            block_height,
            width,
            block_width,
        ).mean(axis=(1, 3), dtype=np.float32)
        reduced = np.concatenate(
            (encoded_rgb, reduced_alpha[..., np.newaxis]),
            axis=2,
        )
        return np.rint(reduced * 255.0).astype(np.uint8)

    @staticmethod
    def _resize_directional_lightmap(
        rgba: np.ndarray,
        width: int,
        height: int,
    ) -> np.ndarray:
        source_height, source_width = rgba.shape[:2]
        if source_width == width and source_height == height:
            return rgba
        if source_width % width == 0 and source_height % height == 0:
            block_width = source_width // width
            block_height = source_height // height
            reduced = rgba.astype(np.float32).reshape(
                height,
                block_height,
                width,
                block_width,
                4,
            ).mean(axis=(1, 3), dtype=np.float32)
            return np.rint(reduced).astype(np.uint8)
        if width % source_width == 0 and height % source_height == 0:
            return np.repeat(
                np.repeat(rgba, height // source_height, axis=0),
                width // source_width,
                axis=1,
            )
        raise InsomniacError(
            f"Directional Tie lightmap {source_width}x{source_height} "
            f"cannot be resized to {width}x{height} with integral blocks"
        )

    def material(self, shader_tuid: int) -> bpy.types.Material:
        cached = self.materials.get(shader_tuid)
        if cached is not None:
            return cached
        scope = _reader_asset_scope(self.reader)
        name = "_".join(
            part
            for part in (
                _reader_asset_prefix(self.reader),
                scope,
                "Shader",
                f"{shader_tuid:016X}",
            )
            if part
        )
        material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        material.use_nodes = True
        material["insomniac_game"] = _reader_game_id(self.reader)
        if scope:
            material["insomniac_asset_scope"] = scope
        material["insomniac_shader_tuid"] = f"0x{shader_tuid:016X}"
        color = _stable_color(shader_tuid)
        material.diffuse_color = (*color, 1.0)
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = (*color, 1.0)

        if self.enabled:
            try:
                shader = self.reader.read_shader(shader_tuid)
            except InsomniacError as exc:
                self._warn(
                    f"Shader 0x{shader_tuid:016X} metadata fallback: {exc}"
                )
            else:
                self._store_shader_metadata(material, shader)
                if principled is not None:
                    self._apply_base_color_factor(
                        material,
                        principled,
                        shader,
                    )
                    diffuse_color = self._add_diffuse(
                        material,
                        principled,
                        shader,
                    )
                    if self.surface_material_channels:
                        self._add_normal(material, principled, shader)
                    self._add_packed(
                        material,
                        principled,
                        shader,
                        diffuse_color=diffuse_color,
                    )
                    self._add_detail(material, shader)
                    self._add_dedicated_emission(
                        material,
                        principled,
                        shader,
                    )
                    self._add_dedicated_alpha(
                        material,
                        principled,
                        shader,
                    )
                    self._apply_specular_power(
                        material,
                        principled,
                        shader,
                    )
        material["insomniac_cycles_material"] = self.cycles_materials
        material["insomniac_surface_material_channels"] = (
            self.surface_material_channels
        )
        material["insomniac_material_mode"] = (
            "ENHANCED"
            if self.cycles_materials
            else (
                f"{_reader_asset_prefix(self.reader)}_STANDARD"
                if self.surface_material_channels
                else "STANDARD"
            )
        )
        self.materials[shader_tuid] = material
        self.release_decoded_textures()
        return material

    def terrain_material(
        self,
        shader_tuid: int,
        zone_tuid: int,
        lightmap_index: int | None,
        lightmap: DecodedTexture | None,
        directional_lightmap: DecodedTexture | None = None,
        *,
        vertex_colors: bool,
    ) -> bpy.types.Material:
        base_material = self.material(shader_tuid)
        if not self.enabled or (
            not vertex_colors
            and lightmap is None
            and directional_lightmap is None
        ):
            return base_material
        key = (zone_tuid, shader_tuid, lightmap_index)
        cached = self.terrain_materials.get(key)
        if cached is not None:
            return cached

        suffix = (
            f"Lightmap_{lightmap_index:04X}"
            if lightmap_index is not None
            else "Vertex_Lighting"
        )
        material = base_material.copy()
        material.name = (
            f"{base_material.name}_Zone_{zone_tuid:016X}_{suffix}"
        )
        material["insomniac_terrain_material"] = True
        material["insomniac_zone_tuid"] = f"0x{zone_tuid:016X}"
        material["insomniac_vertex_lighting"] = vertex_colors
        if lightmap_index is not None:
            material["insomniac_lightmap_index"] = lightmap_index

        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            color_socket = principled.inputs["Base Color"]
            color_output = self._disconnect_color_input(material, color_socket)
            if vertex_colors:
                color_output = self._multiply_vertex_color(
                    material,
                    principled,
                    color_output,
                    prefix="R3_Terrain",
                    node_label="R3",
                )
            lightmap_uv: bpy.types.NodeSocket | None = None
            if (
                lightmap is not None or directional_lightmap is not None
            ) and lightmap_index is not None:
                uv_map = material.node_tree.nodes.new("ShaderNodeUVMap")
                uv_map.name = "R3_Terrain_Lightmap_UV"
                uv_map.label = "R3 lightmap UV1"
                uv_map.uv_map = "LightmapUV"
                uv_map.location = (-600.0, -100.0)
                lightmap_uv = uv_map.outputs["UV"]
            if lightmap is not None and lightmap_index is not None:
                image = self.terrain_lightmap_image(
                    zone_tuid,
                    lightmap_index,
                    lightmap,
                )
                texture = material.node_tree.nodes.new("ShaderNodeTexImage")
                texture.name = (
                    f"R3_Terrain_Lightmap_{lightmap_index:04X}"
                )
                texture.label = "R3 baked zone lightmap"
                texture.image = image
                texture.extension = "EXTEND"
                texture.location = (-350.0, -100.0)
                material.node_tree.links.new(
                    lightmap_uv,
                    texture.inputs["Vector"],
                )
                multiply = material.node_tree.nodes.new("ShaderNodeMixRGB")
                multiply.name = "R3_Terrain_Lightmap_Multiply"
                multiply.label = "R3 lit albedo x zone lightmap"
                multiply.blend_type = "MULTIPLY"
                multiply.inputs[0].default_value = 1.0
                multiply.location = (200.0, 175.0)
                material.node_tree.links.new(
                    color_output,
                    multiply.inputs[1],
                )
                material.node_tree.links.new(
                    texture.outputs["Color"],
                    multiply.inputs[2],
                )
                color_output = multiply.outputs["Color"]
                material["insomniac_lightmap_image"] = image.name
            if (
                directional_lightmap is not None
                and lightmap_index is not None
            ):
                directional_image = (
                    self.terrain_directional_lightmap_image(
                        zone_tuid,
                        lightmap_index,
                        directional_lightmap,
                    )
                )
                directional_texture = material.node_tree.nodes.new(
                    "ShaderNodeTexImage"
                )
                directional_texture.name = (
                    f"R3_Terrain_Directional_Lightmap_"
                    f"{lightmap_index:04X}"
                )
                directional_texture.label = (
                    "R3 radiosity-normal-map coefficients"
                )
                directional_texture.image = directional_image
                directional_texture.extension = "EXTEND"
                directional_texture.location = (-350.0, -275.0)
                material.node_tree.links.new(
                    lightmap_uv,
                    directional_texture.inputs["Vector"],
                )
                color_output = self._multiply_directional_lightmap(
                    material,
                    principled,
                    color_output,
                    directional_texture.outputs["Color"],
                    prefix="R3_Terrain",
                )
                material["insomniac_directional_lightmap_image"] = (
                    directional_image.name
                )
            material.node_tree.links.new(
                color_output,
                color_socket,
            )

        self.terrain_materials[key] = material
        return material

    def vertex_lit_material(
        self,
        shader_tuid: int,
        *,
        variant_kind: str,
        lightmapped: bool = False,
    ) -> bpy.types.Material:
        base_material = self.material(shader_tuid)
        if not self.enabled:
            return base_material
        key = (variant_kind, shader_tuid, lightmapped)
        cached = self.vertex_lit_materials.get(key)
        if cached is not None:
            return cached
        material = base_material.copy()
        suffix = "_Lightmap" if lightmapped else ""
        material.name = (
            f"{base_material.name}_{variant_kind}_Vertex_Lighting{suffix}"
        )
        material["insomniac_vertex_lit_material"] = True
        material["insomniac_vertex_lit_kind"] = variant_kind
        material["insomniac_tie_lightmap_material"] = lightmapped
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            color_socket = principled.inputs["Base Color"]
            color_output = self._disconnect_color_input(material, color_socket)
            color_output = self._multiply_vertex_color(
                material,
                principled,
                color_output,
                prefix=f"R3_{variant_kind}",
                node_label=f"R3 {variant_kind.lower()}",
            )
            if (
                variant_kind == "TIE"
                and lightmapped
                and self.tie_lightmap_atlas is not None
            ):
                color_output = self._multiply_tie_lightmap(
                    material,
                    color_output,
                )
            material.node_tree.links.new(color_output, color_socket)
        self.vertex_lit_materials[key] = material
        return material

    def _multiply_vertex_color(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        color_output: bpy.types.NodeSocket,
        *,
        prefix: str,
        node_label: str,
    ) -> bpy.types.NodeSocket:
        vertex_color = material.node_tree.nodes.new("ShaderNodeVertexColor")
        vertex_color.name = f"{prefix}_Vertex_Color"
        vertex_color.label = f"{node_label} per-vertex light"
        vertex_color.layer_name = "InsomniacVertexColor"
        vertex_color.location = (-350.0, 250.0)
        multiply = material.node_tree.nodes.new("ShaderNodeMixRGB")
        multiply.name = f"{prefix}_Vertex_Color_Multiply"
        multiply.label = f"{node_label} albedo x per-vertex light"
        multiply.blend_type = "MULTIPLY"
        multiply.inputs[0].default_value = 1.0
        multiply.location = (-50.0, 225.0)
        material.node_tree.links.new(color_output, multiply.inputs[1])
        material.node_tree.links.new(
            vertex_color.outputs["Color"],
            multiply.inputs[2],
        )
        self._multiply_vertex_alpha(
            material,
            principled,
            vertex_color,
            prefix=prefix,
            node_label=node_label,
        )
        return multiply.outputs["Color"]

    def _multiply_tie_lightmap(
        self,
        material: bpy.types.Material,
        color_output: bpy.types.NodeSocket,
    ) -> bpy.types.NodeSocket:
        uv_map = material.node_tree.nodes.new("ShaderNodeUVMap")
        uv_map.name = "R3_TIE_Lightmap_UV"
        uv_map.label = "R3 Tie lightmap UV2"
        uv_map.uv_map = "LightmapUV"
        uv_map.location = (-350.0, -75.0)
        scale = material.node_tree.nodes.new("ShaderNodeAttribute")
        scale.name = "R3_TIE_Lightmap_Scale"
        scale.label = "Per-instance atlas scale"
        scale.attribute_type = "OBJECT"
        scale.attribute_name = _TIE_LIGHTMAP_SCALE_ATTRIBUTE
        scale.location = (-350.0, -225.0)
        offset = material.node_tree.nodes.new("ShaderNodeAttribute")
        offset.name = "R3_TIE_Lightmap_Offset"
        offset.label = "Per-instance atlas offset"
        offset.attribute_type = "OBJECT"
        offset.attribute_name = _TIE_LIGHTMAP_OFFSET_ATTRIBUTE
        offset.location = (-350.0, -375.0)
        scale_uv = material.node_tree.nodes.new("ShaderNodeVectorMath")
        scale_uv.name = "R3_TIE_Lightmap_UV_Scale"
        scale_uv.operation = "MULTIPLY"
        scale_uv.location = (-75.0, -100.0)
        material.node_tree.links.new(
            uv_map.outputs["UV"],
            scale_uv.inputs[0],
        )
        material.node_tree.links.new(
            scale.outputs["Vector"],
            scale_uv.inputs[1],
        )
        offset_uv = material.node_tree.nodes.new("ShaderNodeVectorMath")
        offset_uv.name = "R3_TIE_Lightmap_UV_Offset"
        offset_uv.operation = "ADD"
        offset_uv.location = (125.0, -100.0)
        material.node_tree.links.new(
            scale_uv.outputs["Vector"],
            offset_uv.inputs[0],
        )
        material.node_tree.links.new(
            offset.outputs["Vector"],
            offset_uv.inputs[1],
        )
        texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture.name = "R3_TIE_Lightmap_Atlas"
        texture.label = "R3 compact Tie lightmap atlas"
        texture.image = self.tie_lightmap_atlas
        texture.extension = "EXTEND"
        texture.location = (350.0, -100.0)
        material.node_tree.links.new(
            offset_uv.outputs["Vector"],
            texture.inputs["Vector"],
        )
        multiply = material.node_tree.nodes.new("ShaderNodeMixRGB")
        multiply.name = "R3_TIE_Lightmap_Multiply"
        multiply.label = "R3 lit albedo x Tie lightmap"
        multiply.blend_type = "MULTIPLY"
        multiply.inputs[0].default_value = 1.0
        multiply.location = (575.0, 175.0)
        material.node_tree.links.new(color_output, multiply.inputs[1])
        material.node_tree.links.new(
            texture.outputs["Color"],
            multiply.inputs[2],
        )
        material["insomniac_lightmap_image"] = self.tie_lightmap_atlas.name
        color_output = multiply.outputs["Color"]
        if self.tie_directional_lightmap_atlas is None:
            return color_output
        directional_texture = material.node_tree.nodes.new(
            "ShaderNodeTexImage"
        )
        directional_texture.name = "R3_TIE_Directional_Lightmap_Atlas"
        directional_texture.label = (
            "R3 compact radiosity-normal-map coefficient atlas"
        )
        directional_texture.image = self.tie_directional_lightmap_atlas
        directional_texture.extension = "EXTEND"
        directional_texture.location = (350.0, -275.0)
        material.node_tree.links.new(
            offset_uv.outputs["Vector"],
            directional_texture.inputs["Vector"],
        )
        material["insomniac_directional_lightmap_image"] = (
            self.tie_directional_lightmap_atlas.name
        )
        return self._multiply_directional_lightmap(
            material,
            material.node_tree.nodes["Principled BSDF"],
            color_output,
            directional_texture.outputs["Color"],
            prefix="R3_TIE",
        )

    def _multiply_directional_lightmap(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        color_output: bpy.types.NodeSocket,
        directional_output: bpy.types.NodeSocket,
        *,
        prefix: str,
    ) -> bpy.types.NodeSocket:
        tangent_normal = self._tangent_normal_output(
            material,
            principled,
            prefix=prefix,
        )
        separate = material.node_tree.nodes.new("ShaderNodeSeparateColor")
        separate.name = f"{prefix}_RNM_Coefficients"
        separate.label = "R3 directional basis coefficients"
        separate.mode = "RGB"
        material.node_tree.links.new(
            directional_output,
            separate.inputs["Color"],
        )

        weighted: list[bpy.types.NodeSocket] = []
        for index, (basis, channel) in enumerate(
            zip(_RNM_BASIS, ("Red", "Green", "Blue"))
        ):
            dot = material.node_tree.nodes.new("ShaderNodeVectorMath")
            dot.name = f"{prefix}_RNM_Basis_{index}_Dot"
            dot.operation = "DOT_PRODUCT"
            dot.inputs[1].default_value = basis
            material.node_tree.links.new(tangent_normal, dot.inputs[0])
            clamp = material.node_tree.nodes.new("ShaderNodeMath")
            clamp.name = f"{prefix}_RNM_Basis_{index}_Clamp"
            clamp.operation = "MAXIMUM"
            clamp.inputs[1].default_value = 0.0
            material.node_tree.links.new(
                dot.outputs["Value"],
                clamp.inputs[0],
            )
            square = material.node_tree.nodes.new("ShaderNodeMath")
            square.name = f"{prefix}_RNM_Basis_{index}_Square"
            square.operation = "MULTIPLY"
            material.node_tree.links.new(
                clamp.outputs["Value"],
                square.inputs[0],
            )
            material.node_tree.links.new(
                clamp.outputs["Value"],
                square.inputs[1],
            )
            weight = material.node_tree.nodes.new("ShaderNodeMath")
            weight.name = f"{prefix}_RNM_Basis_{index}_Weight"
            weight.operation = "MULTIPLY"
            material.node_tree.links.new(
                separate.outputs[channel],
                weight.inputs[0],
            )
            material.node_tree.links.new(
                square.outputs["Value"],
                weight.inputs[1],
            )
            weighted.append(weight.outputs["Value"])

        add_first = material.node_tree.nodes.new("ShaderNodeMath")
        add_first.name = f"{prefix}_RNM_Add_01"
        add_first.operation = "ADD"
        material.node_tree.links.new(weighted[0], add_first.inputs[0])
        material.node_tree.links.new(weighted[1], add_first.inputs[1])
        factor = material.node_tree.nodes.new("ShaderNodeMath")
        factor.name = f"{prefix}_RNM_Factor"
        factor.label = "R3 radiosity-normal-map factor"
        factor.operation = "ADD"
        material.node_tree.links.new(
            add_first.outputs["Value"],
            factor.inputs[0],
        )
        material.node_tree.links.new(weighted[2], factor.inputs[1])

        multiply = material.node_tree.nodes.new("ShaderNodeMixRGB")
        multiply.name = f"{prefix}_Directional_Multiply"
        multiply.label = "R3 baked light x directional response"
        multiply.blend_type = "MULTIPLY"
        multiply.inputs[0].default_value = 1.0
        material.node_tree.links.new(color_output, multiply.inputs[1])
        material.node_tree.links.new(
            factor.outputs["Value"],
            multiply.inputs[2],
        )
        material["insomniac_directional_lightmap"] = True
        material["insomniac_directional_lightmap_model"] = (
            "RADIOSITY_NORMAL_MAPPING"
        )
        return multiply.outputs["Color"]

    @staticmethod
    def _tangent_normal_output(
        material: bpy.types.Material,
        principled: bpy.types.Node,
        *,
        prefix: str,
    ) -> bpy.types.NodeSocket:
        normal_input = principled.inputs["Normal"]
        encoded_normal: bpy.types.NodeSocket | None = None
        if normal_input.is_linked:
            normal_node = normal_input.links[0].from_node
            color_input = normal_node.inputs.get("Color")
            if (
                normal_node.bl_idname == "ShaderNodeNormalMap"
                and color_input is not None
                and color_input.is_linked
            ):
                encoded_normal = color_input.links[0].from_socket
        if encoded_normal is None:
            flat = material.node_tree.nodes.new("ShaderNodeCombineXYZ")
            flat.name = f"{prefix}_RNM_Flat_Normal"
            flat.inputs["Z"].default_value = 1.0
            return flat.outputs["Vector"]

        scale = material.node_tree.nodes.new("ShaderNodeVectorMath")
        scale.name = f"{prefix}_RNM_Normal_Scale"
        scale.operation = "MULTIPLY"
        scale.inputs[1].default_value = (2.0, 2.0, 2.0)
        material.node_tree.links.new(encoded_normal, scale.inputs[0])
        bias = material.node_tree.nodes.new("ShaderNodeVectorMath")
        bias.name = f"{prefix}_RNM_Normal_Bias"
        bias.operation = "ADD"
        bias.inputs[1].default_value = (-1.0, -1.0, -1.0)
        material.node_tree.links.new(
            scale.outputs["Vector"],
            bias.inputs[0],
        )
        normalize = material.node_tree.nodes.new("ShaderNodeVectorMath")
        normalize.name = f"{prefix}_RNM_Normal"
        normalize.operation = "NORMALIZE"
        material.node_tree.links.new(
            bias.outputs["Vector"],
            normalize.inputs[0],
        )
        return normalize.outputs["Vector"]

    def apply_tie_lightmap_transform(
        self,
        obj: bpy.types.Object,
        instance: PlacedInstance,
    ) -> None:
        if (
            instance.lightmap_zone_tuid is None
            or instance.lightmap_index is None
        ):
            return
        transform = self.tie_lightmap_transforms.get(
            (instance.lightmap_zone_tuid, instance.lightmap_index)
        )
        if transform is None:
            return
        scale_x, scale_y, offset_x, offset_y = transform
        obj[_TIE_LIGHTMAP_SCALE_ATTRIBUTE] = (scale_x, scale_y, 1.0)
        obj[_TIE_LIGHTMAP_OFFSET_ATTRIBUTE] = (offset_x, offset_y, 0.0)
        obj["insomniac_lightmap_zone_tuid"] = (
            f"0x{instance.lightmap_zone_tuid:016X}"
        )
        obj["insomniac_lightmap_atlas"] = self.tie_lightmap_atlas.name

    @staticmethod
    def _disconnect_color_input(
        material: bpy.types.Material,
        socket: bpy.types.NodeSocket,
    ) -> bpy.types.NodeSocket:
        if socket.is_linked:
            link = socket.links[0]
            source = link.from_socket
            material.node_tree.links.remove(link)
            return source
        color = material.node_tree.nodes.new("ShaderNodeRGB")
        color.name = "R3_Terrain_Base_Color"
        color.label = "Source base color"
        color.outputs["Color"].default_value = socket.default_value
        color.location = (-600.0, 350.0)
        return color.outputs["Color"]

    @staticmethod
    def _multiply_vertex_alpha(
        material: bpy.types.Material,
        principled: bpy.types.Node,
        vertex_color: bpy.types.Node,
        *,
        prefix: str,
        node_label: str,
    ) -> None:
        alpha_socket = principled.inputs["Alpha"]
        if not alpha_socket.is_linked:
            return
        alpha_link = alpha_socket.links[0]
        source = alpha_link.from_socket
        material.node_tree.links.remove(alpha_link)
        multiply = material.node_tree.nodes.new("ShaderNodeMath")
        multiply.name = f"{prefix}_Vertex_Alpha_Multiply"
        multiply.label = f"{node_label} material alpha x vertex alpha"
        multiply.operation = "MULTIPLY"
        multiply.use_clamp = True
        multiply.location = (200.0, -225.0)
        material.node_tree.links.new(source, multiply.inputs[0])
        material.node_tree.links.new(
            vertex_color.outputs["Alpha"],
            multiply.inputs[1],
        )
        material.node_tree.links.new(
            multiply.outputs["Value"],
            alpha_socket,
        )

    def _store_shader_metadata(
        self,
        material: bpy.types.Material,
        shader: ShaderInfo,
    ) -> None:
        if shader.lookup_path is not None:
            material["insomniac_shader_lookup_path"] = shader.lookup_path
        placeholder_kind = shader.source_placeholder_kind
        material["insomniac_source_placeholder"] = (
            placeholder_kind is not None
        )
        if placeholder_kind is not None:
            material["insomniac_source_placeholder_kind"] = placeholder_kind
        if shader.material_word is not None:
            material["insomniac_material_word"] = (
                f"0x{shader.material_word:08X}"
            )
            material["insomniac_material_flags"] = shader.material_flags
            material["insomniac_blend_mode"] = shader.blend_mode
            material["insomniac_material_unknown_0"] = (
                shader.material_unknown_0
            )
            material["insomniac_material_unknown_1"] = (
                shader.material_unknown_1
            )
            material["insomniac_alpha_mode"] = shader.alpha_mode
            material["insomniac_alpha_override"] = (
                shader.uses_implicit_albedo_alpha
            )
        if shader.material_values:
            material["insomniac_material_values"] = [
                value
                for vector in shader.material_values
                for value in vector
            ]
        if shader.material_tail_values:
            material["insomniac_material_tail_values"] = list(
                shader.material_tail_values
            )
        if shader.texture_map_indices:
            material["insomniac_texture_map_indices"] = list(
                shader.texture_map_indices
            )
        if shader.material_pre_control_values:
            material["insomniac_material_pre_control_values"] = list(
                shader.material_pre_control_values
            )
        specular_name = {
            "GLOSS": "gloss",
            "SPECULAR": "specular",
        }.get(shader.specular_texture_semantics, "packed")
        texture_names = ("diffuse", "normal", specular_name, "detail")
        texture_ids = (
            shader.albedo_texture_id,
            shader.normal_texture_id,
            shader.expensive_texture_id,
            shader.detail_texture_id,
        )
        for index, (texture_name, texture_id, texture_path) in enumerate(
            zip(
                texture_names,
                texture_ids,
                shader.texture_lookup_paths,
            )
        ):
            if index < len(shader.texture_map_indices):
                material[f"insomniac_{texture_name}_map_index"] = (
                    shader.texture_map_indices[index]
                )
                material[f"insomniac_{texture_name}_texture_bound"] = (
                    shader.is_texture_slot_bound(index)
                )
            if texture_id is not None:
                material[f"insomniac_{texture_name}_texture_id"] = (
                    f"0x{texture_id:08X}"
                )
            if texture_path is not None:
                material[f"insomniac_{texture_name}_texture_path"] = (
                    texture_path
                )
        if shader.emissive_texture_id is not None:
            material["insomniac_emissive_texture_id"] = (
                f"0x{shader.emissive_texture_id:08X}"
            )
        if shader.emissive_texture_lookup_path is not None:
            material["insomniac_emissive_texture_path"] = (
                shader.emissive_texture_lookup_path
            )
        material["insomniac_emissive_enabled"] = shader.emissive_enabled
        if shader.use_emissive_texture is not None:
            material["insomniac_use_emissive_texture"] = (
                shader.use_emissive_texture
            )
        if shader.alpha_texture_id is not None:
            material["insomniac_alpha_texture_id"] = (
                f"0x{shader.alpha_texture_id:08X}"
            )
        if shader.alpha_texture_lookup_path is not None:
            material["insomniac_alpha_texture_path"] = (
                shader.alpha_texture_lookup_path
            )
        if shader.specular_power is not None:
            material["insomniac_specular_power"] = shader.specular_power
        material["insomniac_normal_texture_encoding"] = (
            shader.normal_texture_encoding
        )
        material["insomniac_specular_texture_semantics"] = (
            shader.specular_texture_semantics
        )
        for index, (texture_id, texture_path) in enumerate(
            zip(
                shader.extra_texture_ids,
                shader.extra_texture_lookup_paths,
            ),
            start=1,
        ):
            slot = index + 3
            if slot < len(shader.texture_map_indices):
                material[f"insomniac_extra_{index}_map_index"] = (
                    shader.texture_map_indices[slot]
                )
                material[f"insomniac_extra_{index}_texture_bound"] = (
                    shader.is_texture_slot_bound(slot)
                )
            if texture_id is not None:
                material[f"insomniac_extra_{index}_texture_id"] = (
                    f"0x{texture_id:08X}"
                )
            if texture_path is not None:
                material[f"insomniac_extra_{index}_texture_path"] = (
                    texture_path
                )

    def _apply_base_color_factor(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        shader: ShaderInfo,
    ) -> None:
        factor = (
            self._base_color_factor(shader)
            if self.base_color_factor_supported
            else (1.0, 1.0, 1.0, 1.0)
        )
        material["insomniac_base_color_factor"] = list(factor)
        valid = (
            self.base_color_factor_supported
            and self._has_base_color_factor(shader)
        )
        material["insomniac_base_color_factor_valid"] = valid
        if self.use_base_color_factor and not valid:
            self._warn(
                f"Shader 0x{shader.tuid:016X} has an unsupported, "
                "non-finite, or out-of-range base color factor; the enhanced "
                "material uses neutral white"
            )
        if not self.use_base_color_factor:
            return
        material.diffuse_color = factor
        principled.inputs["Base Color"].default_value = factor

    @staticmethod
    def _base_color_factor(
        shader: ShaderInfo,
    ) -> tuple[float, float, float, float]:
        if not _MaterialFactory._has_base_color_factor(shader):
            return (1.0, 1.0, 1.0, 1.0)
        return shader.base_color_factor

    @staticmethod
    def _has_base_color_factor(shader: ShaderInfo) -> bool:
        factor = shader.base_color_factor
        return shader.material_unknown_0 != 14 and all(
            math.isfinite(value) and 0.0 <= value <= 1.0
            for value in factor
        )

    def _add_diffuse(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        shader: ShaderInfo,
    ) -> bpy.types.NodeSocket | None:
        texture_id = shader.albedo_texture_id
        if texture_id is None or not shader.is_texture_slot_bound(0):
            return None
        try:
            image = self.image(
                texture_id,
                lookup_path=shader.texture_lookup_paths[0],
            )
        except InsomniacError as exc:
            self._warn(
                f"Shader 0x{shader.tuid:016X} diffuse fallback: {exc}"
            )
            return None
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.name = f"Diffuse_{texture_id:08X}"
        node.label = (
            f"{_reader_game_label(self.reader)} source placeholder: "
            "default pink"
            if shader.source_placeholder_kind == "DEFAULT_PINK"
            else f"{_reader_game_label(self.reader)} diffuse"
        )
        node.image = image
        color_output = node.outputs["Color"]
        factor = self._base_color_factor(shader)
        if self.use_base_color_factor and factor[:3] != (1.0, 1.0, 1.0):
            tint = material.node_tree.nodes.new("ShaderNodeVectorMath")
            tint.name = f"Base_Color_Tint_{shader.tuid:016X}"
            tint.label = (
                f"{_reader_game_label(self.reader)} base color multiplier"
            )
            tint.operation = "MULTIPLY"
            tint.inputs[1].default_value = factor[:3]
            material.node_tree.links.new(color_output, tint.inputs[0])
            color_output = tint.outputs["Vector"]
        material.node_tree.links.new(
            color_output,
            principled.inputs["Base Color"],
        )

        self._add_alpha(material, principled, node, shader)
        return node.outputs["Color"]

    def _add_alpha(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        texture_node: bpy.types.Node,
        shader: ShaderInfo,
    ) -> None:
        alpha_mode = shader.alpha_mode
        if alpha_mode == "OPAQUE":
            return
        material.surface_render_method = "DITHERED"
        if self._uses_luminance_alpha(shader):
            coverage = material.node_tree.nodes.new("ShaderNodeRGBToBW")
            coverage.name = f"Alpha_Coverage_{shader.tuid:016X}"
            coverage.label = (
                f"{_reader_game_label(self.reader)} albedo-luminance coverage"
            )
            strength = material.node_tree.nodes.new("ShaderNodeMath")
            strength.name = f"Alpha_Strength_{shader.tuid:016X}"
            strength.label = (
                f"{_reader_game_label(self.reader)} authored overlay strength"
            )
            strength.operation = "MULTIPLY"
            strength.use_clamp = True
            alpha_factor = self._material_alpha_factor(shader)
            strength.inputs[1].default_value = alpha_factor
            material.node_tree.links.new(
                texture_node.outputs["Color"],
                coverage.inputs["Color"],
            )
            material.node_tree.links.new(
                coverage.outputs["Val"],
                strength.inputs[0],
            )
            material.node_tree.links.new(
                strength.outputs["Value"],
                principled.inputs["Alpha"],
            )
            material["insomniac_alpha_source"] = (
                "ALBEDO_LUMINANCE_X_MATERIAL_ALPHA"
            )
            material["insomniac_alpha_approximation"] = (
                "R3_MODE_1_BC1_LUMINANCE"
            )
            material["insomniac_alpha_factor"] = alpha_factor
            return
        if alpha_mode == "MASK":
            clip = material.node_tree.nodes.new("ShaderNodeMath")
            clip.name = f"Alpha_Clip_{shader.tuid:016X}"
            clip.label = f"{_reader_game_label(self.reader)} alpha mask"
            clip.operation = "GREATER_THAN"
            clip.inputs[1].default_value = 0.5
            material.node_tree.links.new(
                texture_node.outputs["Alpha"],
                clip.inputs[0],
            )
            material.node_tree.links.new(
                clip.outputs["Value"],
                principled.inputs["Alpha"],
            )
            material.alpha_threshold = 0.5
            return
        material.node_tree.links.new(
            texture_node.outputs["Alpha"],
            principled.inputs["Alpha"],
        )

    def _uses_luminance_alpha(self, shader: ShaderInfo) -> bool:
        shader_tuids = getattr(
            self.reader,
            "material_luminance_alpha_shader_tuids",
            (),
        )
        return shader.tuid in shader_tuids

    @staticmethod
    def _material_alpha_factor(shader: ShaderInfo) -> float:
        factor = shader.base_color_factor[3]
        if not math.isfinite(factor) or factor < 0.0:
            return 1.0
        return factor

    def _add_normal(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        shader: ShaderInfo,
    ) -> None:
        texture_id = shader.normal_texture_id
        if texture_id is None or not shader.is_texture_slot_bound(1):
            return
        lookup_path = shader.texture_lookup_paths[1]
        try:
            if shader.normal_texture_encoding != "DIRECT":
                key = self._texture_key(
                    texture_id,
                    "normal",
                    lookup_path,
                )
                support = self._normal_map_support.get(key)
                if support is None:
                    texture = self.reader.read_texture(
                        texture_id,
                        lookup_path=lookup_path,
                    )
                    support = (
                        can_reconstruct_normal_map(texture),
                        texture.info.format_code,
                    )
                    self._normal_map_support[key] = support
                if not support[0]:
                    material["insomniac_normal_map_supported"] = False
                    self._warn(
                        f"Shader 0x{shader.tuid:016X} normal map uses "
                        f"unsupported texture format "
                        f"0x{support[1]:02X}"
                    )
                    return
            image = self.image(
                texture_id,
                role="normal",
                lookup_path=lookup_path,
            )
        except InsomniacError as exc:
            self._warn(
                f"Shader 0x{shader.tuid:016X} normal map unavailable: {exc}"
            )
            return
        material["insomniac_normal_map_supported"] = True
        texture_node = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture_node.name = f"Normal_{texture_id:08X}"
        texture_node.label = (
            f"{_reader_game_label(self.reader)} "
            + (
                "direct RGB normal"
                if shader.normal_texture_encoding == "DIRECT"
                else "reconstructed normal"
            )
        )
        texture_node.image = image
        normal = material.node_tree.nodes.new("ShaderNodeNormalMap")
        normal.name = f"Normal_Map_{texture_id:08X}"
        normal.label = f"{_reader_game_label(self.reader)} tangent normal"
        normal.space = "TANGENT"
        material.node_tree.links.new(
            texture_node.outputs["Color"],
            normal.inputs["Color"],
        )
        material.node_tree.links.new(
            normal.outputs["Normal"],
            principled.inputs["Normal"],
        )

    def _add_detail(
        self,
        material: bpy.types.Material,
        shader: ShaderInfo,
    ) -> None:
        texture_id = shader.detail_texture_id
        if texture_id is None or not shader.is_texture_slot_bound(3):
            return
        try:
            image = self.image(
                texture_id,
                role="detail",
                lookup_path=shader.texture_lookup_paths[3],
            )
        except InsomniacError as exc:
            self._warn(
                f"Shader 0x{shader.tuid:016X} detail map unavailable: {exc}"
            )
            return
        node = self._texture_node(
            material,
            image,
            name=f"Detail_{texture_id:08X}",
            label=(
                f"{_reader_game_label(self.reader)} "
                "detail data (uncomposed)"
            ),
        )
        node.location = (-600.0, -600.0)
        material["insomniac_detail_data_only"] = True

    def _add_packed(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        shader: ShaderInfo,
        *,
        diffuse_color: bpy.types.NodeSocket | None = None,
    ) -> None:
        texture_id = shader.expensive_texture_id
        if shader.specular_texture_semantics in {"GLOSS", "SPECULAR"}:
            material["insomniac_emissive_mask"] = False
            material["insomniac_emissive"] = False
            if (
                texture_id is not None
                and shader.is_texture_slot_bound(2)
                and self.surface_material_channels
            ):
                add_surface_map = (
                    self._add_gloss
                    if shader.specular_texture_semantics == "GLOSS"
                    else self._add_specular
                )
                add_surface_map(
                    material,
                    principled,
                    shader,
                    texture_id,
                )
            return
        if texture_id is None or not shader.is_texture_slot_bound(2):
            material["insomniac_emissive_mask"] = False
            material["insomniac_emissive"] = False
            return
        material["insomniac_packed_texture_id"] = f"0x{texture_id:08X}"
        material["insomniac_emissive_channel"] = "B"
        lookup_path = shader.texture_lookup_paths[2]
        try:
            key = self._texture_key(
                texture_id,
                "packed",
                lookup_path,
            )
            emissive_mask = self._emissive_masks.get(key)
            if emissive_mask is None:
                texture = self.reader.read_texture(
                    texture_id,
                    lookup_path=lookup_path,
                )
                emissive_mask = has_emissive_mask(texture)
                self._emissive_masks[key] = emissive_mask
        except InsomniacError as exc:
            self._warn(
                f"Shader 0x{shader.tuid:016X} emission unavailable: {exc}"
            )
            return
        disabled_control_mask = int(
            getattr(
                self.reader,
                "material_emission_disabled_control_mask",
                0,
            )
        )
        control_enabled = (
            shader.material_word is None
            or not (
                (shader.material_unknown_1 or 0)
                & disabled_control_mask
            )
        )
        emissive = emissive_mask and control_enabled
        material["insomniac_emissive_mask"] = emissive_mask
        material["insomniac_emissive_control_enabled"] = control_enabled
        material["insomniac_emissive"] = emissive
        use_surface_channels = self.surface_material_channels and (
            shader.use_glossiness or shader.use_specular
        )
        if not emissive and not use_surface_channels:
            return

        image = self.image(
            texture_id,
            role="packed",
            lookup_path=lookup_path,
        )
        texture_node = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture_node.name = f"Packed_{texture_id:08X}"
        texture_node.label = (
            f"{_reader_game_label(self.reader)} packed material map"
        )
        texture_node.image = image
        separate = material.node_tree.nodes.new("ShaderNodeSeparateColor")
        separate.name = f"Packed_Channels_{texture_id:08X}"
        separate.label = "R=gloss G=specular B=emission"
        separate.mode = "RGB"
        material.node_tree.links.new(
            texture_node.outputs["Color"],
            separate.inputs["Color"],
        )
        if self.surface_material_channels and shader.use_glossiness:
            roughness = material.node_tree.nodes.new("ShaderNodeMath")
            roughness.name = f"Roughness_{texture_id:08X}"
            roughness.label = (
                f"{_reader_game_label(self.reader)} inverted red gloss"
            )
            roughness.operation = "SUBTRACT"
            roughness.use_clamp = True
            roughness.inputs[0].default_value = 1.0
            material.node_tree.links.new(
                separate.outputs["Red"],
                roughness.inputs[1],
            )
            material.node_tree.links.new(
                roughness.outputs["Value"],
                principled.inputs["Roughness"],
            )
        if self.surface_material_channels and shader.use_specular:
            material.node_tree.links.new(
                separate.outputs["Green"],
                principled.inputs["Specular IOR Level"],
            )
        if emissive:
            emission_color = separate.outputs[EMISSIVE_CHANNEL_NAME]
            if bool(
                getattr(
                    self.reader,
                    "material_emission_uses_albedo",
                    False,
                )
            ):
                emission_color = self._add_albedo_colored_emission(
                    material,
                    shader,
                    diffuse_color,
                    emission_color,
                )
                material["insomniac_emissive_color_source"] = (
                    "ALBEDO_X_BASE_COLOR_FACTOR_X_PACKED_BLUE"
                )
            else:
                material["insomniac_emissive_color_source"] = "PACKED_BLUE"
            material.node_tree.links.new(
                emission_color,
                principled.inputs["Emission Color"],
            )
            principled.inputs["Emission Strength"].default_value = 1.0
            material["insomniac_emissive_strength"] = 1.0

    def _add_gloss(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        shader: ShaderInfo,
        texture_id: int,
    ) -> None:
        lookup_path = shader.texture_lookup_paths[2]
        try:
            image = self.image(
                texture_id,
                role="gloss",
                lookup_path=lookup_path,
            )
        except InsomniacError as exc:
            self._warn(
                f"Shader 0x{shader.tuid:016X} gloss map unavailable: {exc}"
            )
            return
        texture_node = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture_node.name = f"Gloss_{texture_id:08X}"
        texture_node.label = f"{_reader_game_label(self.reader)} gloss"
        texture_node.image = image
        separate = material.node_tree.nodes.new("ShaderNodeSeparateColor")
        separate.name = f"Gloss_Channel_{texture_id:08X}"
        separate.label = "R=gloss"
        separate.mode = "RGB"
        roughness = material.node_tree.nodes.new("ShaderNodeMath")
        roughness.name = f"Roughness_{texture_id:08X}"
        roughness.label = (
            f"{_reader_game_label(self.reader)} inverted red gloss"
        )
        roughness.operation = "SUBTRACT"
        roughness.use_clamp = True
        roughness.inputs[0].default_value = 1.0
        material.node_tree.links.new(
            texture_node.outputs["Color"],
            separate.inputs["Color"],
        )
        material.node_tree.links.new(
            separate.outputs["Red"],
            roughness.inputs[1],
        )
        material.node_tree.links.new(
            roughness.outputs["Value"],
            principled.inputs["Roughness"],
        )

    def _add_specular(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        shader: ShaderInfo,
        texture_id: int,
    ) -> None:
        lookup_path = shader.texture_lookup_paths[2]
        try:
            image = self.image(
                texture_id,
                role="specular",
                lookup_path=lookup_path,
            )
        except InsomniacError as exc:
            self._warn(
                f"Shader 0x{shader.tuid:016X} specular map unavailable: "
                f"{exc}"
            )
            return
        texture_node = self._texture_node(
            material,
            image,
            name=f"Specular_{texture_id:08X}",
            label=f"{_reader_game_label(self.reader)} specular",
        )
        material.node_tree.links.new(
            texture_node.outputs["Color"],
            principled.inputs["Specular IOR Level"],
        )
        material["insomniac_specular_map_source"] = "DEDICATED_COLOR"

    def _add_dedicated_emission(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        shader: ShaderInfo,
    ) -> None:
        texture_id = shader.emissive_texture_id
        color_source = "AUTHORED_COLOR"
        if texture_id is None:
            if not shader.emissive_enabled:
                return
            if shader.use_emissive_texture is True:
                self._warn(
                    f"Shader 0x{shader.tuid:016X} emissive map unavailable: "
                    "texture is enabled but no map was resolved"
                )
                return
            principled.inputs["Emission Color"].default_value = (
                *shader.emissive_color,
                1.0,
            )
        else:
            try:
                image = self.image(
                    texture_id,
                    role="color",
                    lookup_path=shader.emissive_texture_lookup_path,
                )
            except InsomniacError as exc:
                self._warn(
                    f"Shader 0x{shader.tuid:016X} emissive map unavailable: "
                    f"{exc}"
                )
                return
            texture_node = material.node_tree.nodes.new(
                "ShaderNodeTexImage"
            )
            texture_node.name = f"Emissive_{texture_id:08X}"
            texture_node.label = (
                f"{_reader_game_label(self.reader)} emissive color"
            )
            texture_node.image = image
            emission_color = texture_node.outputs["Color"]
            if shader.emissive_color != (1.0, 1.0, 1.0):
                tint = material.node_tree.nodes.new(
                    "ShaderNodeVectorMath"
                )
                tint.name = f"Emissive_Tint_{shader.tuid:016X}"
                tint.label = (
                    f"{_reader_game_label(self.reader)} emissive multiplier"
                )
                tint.operation = "MULTIPLY"
                tint.inputs[1].default_value = shader.emissive_color
                material.node_tree.links.new(
                    emission_color,
                    tint.inputs[0],
                )
                emission_color = tint.outputs["Vector"]
            material.node_tree.links.new(
                emission_color,
                principled.inputs["Emission Color"],
            )
            color_source = "DEDICATED_TEXTURE_X_COLOR"
        strength = (
            shader.emissive_strength
            if math.isfinite(shader.emissive_strength)
            and shader.emissive_strength >= 0.0
            else 1.0
        )
        principled.inputs["Emission Strength"].default_value = strength
        material["insomniac_emissive_mask"] = False
        material["insomniac_emissive"] = strength > 0.0
        material["insomniac_emissive_color_source"] = color_source
        material["insomniac_emissive_color"] = list(
            shader.emissive_color
        )
        material["insomniac_emissive_strength"] = strength

    def _add_dedicated_alpha(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        shader: ShaderInfo,
    ) -> None:
        texture_id = shader.alpha_texture_id
        if texture_id is None or shader.alpha_mode == "OPAQUE":
            return
        try:
            image = self.image(
                texture_id,
                role="alpha",
                lookup_path=shader.alpha_texture_lookup_path,
            )
        except InsomniacError as exc:
            self._warn(
                f"Shader 0x{shader.tuid:016X} alpha map unavailable: {exc}"
            )
            return
        texture_node = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture_node.name = f"Alpha_{texture_id:08X}"
        texture_node.label = f"{_reader_game_label(self.reader)} alpha map"
        texture_node.image = image
        separate = material.node_tree.nodes.new("ShaderNodeSeparateColor")
        separate.name = f"Alpha_Channel_{texture_id:08X}"
        separate.label = "R=coverage"
        separate.mode = "RGB"
        material.node_tree.links.new(
            texture_node.outputs["Color"],
            separate.inputs["Color"],
        )
        coverage = separate.outputs["Red"]
        if shader.alpha_mode == "MASK":
            clip = material.node_tree.nodes.new("ShaderNodeMath")
            clip.name = f"Alpha_Map_Clip_{shader.tuid:016X}"
            clip.label = f"{_reader_game_label(self.reader)} alpha mask"
            clip.operation = "GREATER_THAN"
            clip.inputs[1].default_value = 0.5
            material.node_tree.links.new(coverage, clip.inputs[0])
            coverage = clip.outputs["Value"]
            material.alpha_threshold = 0.5
        material.node_tree.links.new(
            coverage,
            principled.inputs["Alpha"],
        )
        material.surface_render_method = "DITHERED"
        material["insomniac_alpha_source"] = "DEDICATED_RED"

    @staticmethod
    def _apply_specular_power(
        material: bpy.types.Material,
        principled: bpy.types.Node,
        shader: ShaderInfo,
    ) -> None:
        power = shader.specular_power
        roughness_input = principled.inputs["Roughness"]
        if (
            power is None
            or not math.isfinite(power)
            or power < 0.0
            or roughness_input.is_linked
        ):
            return
        roughness = min(1.0, math.sqrt(2.0 / (power + 2.0)))
        roughness_input.default_value = roughness
        material["insomniac_roughness_from_specular_power"] = roughness

    def _add_albedo_colored_emission(
        self,
        material: bpy.types.Material,
        shader: ShaderInfo,
        diffuse_color: bpy.types.NodeSocket | None,
        emissive_mask: bpy.types.NodeSocket,
    ) -> bpy.types.NodeSocket:
        factor = self._base_color_factor(shader)
        color = diffuse_color
        if color is not None and factor[:3] != (1.0, 1.0, 1.0):
            tint = material.node_tree.nodes.new("ShaderNodeVectorMath")
            tint.name = f"Emission_Tint_{shader.tuid:016X}"
            tint.label = (
                f"{_reader_game_label(self.reader)} emission color "
                "multiplier"
            )
            tint.operation = "MULTIPLY"
            tint.inputs[1].default_value = factor[:3]
            material.node_tree.links.new(color, tint.inputs[0])
            color = tint.outputs["Vector"]

        combine = material.node_tree.nodes.new("ShaderNodeVectorMath")
        combine.name = f"Emission_Color_{shader.tuid:016X}"
        combine.label = (
            f"{_reader_game_label(self.reader)} albedo-colored emission"
        )
        combine.operation = "MULTIPLY"
        if color is None:
            combine.inputs[0].default_value = factor[:3]
        else:
            material.node_tree.links.new(color, combine.inputs[0])
        material.node_tree.links.new(emissive_mask, combine.inputs[1])
        return combine.outputs["Vector"]

    @staticmethod
    def _texture_node(
        material: bpy.types.Material,
        image: bpy.types.Image,
        *,
        name: str,
        label: str,
    ) -> bpy.types.Node:
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.name = name
        node.label = label
        node.image = image
        return node

    @staticmethod
    def _texture_key(
        texture_id: int,
        role: str,
        lookup_path: str | None,
    ) -> tuple[int, str, str]:
        return (
            texture_id,
            role,
            _normalized_texture_lookup_path(lookup_path),
        )

    def _warn(self, message: str) -> None:
        LOGGER.warning(message)
        self.warnings.append(message)

    def release_decoded_textures(self) -> None:
        clear_texture_cache = getattr(
            self.reader,
            "clear_texture_cache",
            None,
        )
        if callable(clear_texture_cache):
            clear_texture_cache()

    def image(
        self,
        texture_id: int,
        *,
        role: str = "color",
        lookup_path: str | None = None,
    ) -> bpy.types.Image:
        key = self._texture_key(texture_id, role, lookup_path)
        cached = self.images.get(key)
        if cached is not None:
            return cached
        suffixes = {
            "color": "",
            "normal": "_Normal",
            "packed": "_Packed",
        }
        suffix = suffixes.get(role, f"_{role.title()}")
        scope = _reader_asset_scope(self.reader)
        base_name = "_".join(
            part
            for part in (
                _reader_asset_prefix(self.reader),
                scope,
                "Texture",
                f"{texture_id:08X}",
            )
            if part
        )
        name = f"{base_name}{suffix}"
        image = self._compatible_image(name, key, scope)
        if image is None and bpy.data.images.get(name) is not None:
            discriminator = f"{scope}|{key[2]}|{texture_id:016X}|{role}"
            digest = zlib.crc32(discriminator.encode("utf-8"))
            name = f"{name}_{digest:08X}"
            image = self._compatible_image(name, key, scope)
        if image is not None:
            self._apply_colorspace(image, role)
            self.images[key] = image
            return image

        texture = self.reader.read_texture(
            texture_id,
            lookup_path=lookup_path,
        )
        if image is None:
            image = bpy.data.images.new(
                name,
                width=texture.info.width,
                height=texture.info.height,
                alpha=True,
                float_buffer=False,
                is_data=role != "color",
            )
            self._apply_colorspace(image, role)
            rgba = (
                reconstruct_normal_map(texture)
                if role == "normal"
                else texture.rgba
            )
            pixels = np.multiply(
                np.flipud(rgba),
                np.float32(1.0 / 255.0),
                dtype=np.float32,
            ).ravel()
            image.pixels.foreach_set(pixels)
            image.pack()
            image.update()
        image["insomniac_texture_tuid"] = f"0x{texture.info.tuid:016X}"
        image["insomniac_requested_texture_id"] = f"0x{texture_id:08X}"
        image["insomniac_resolved_texture_id"] = (
            f"0x{texture.info.id:08X}"
        )
        image["insomniac_lookup_path_fallback"] = (
            texture.info.id != texture_id
        )
        image["insomniac_texture_format"] = (
            f"0x{texture.info.format_code:02X}"
        )
        image["insomniac_texture_role"] = role
        image["insomniac_texture_lookup_path"] = lookup_path or ""
        image["insomniac_texture_lookup_key"] = key[2]
        image["insomniac_asset_scope"] = scope
        image[_IMAGE_HAS_ALPHA_KEY] = bool(
            np.min(texture.rgba[..., 3]) < 255
        )
        self.images[key] = image
        return image

    def terrain_lightmap_image(
        self,
        zone_tuid: int,
        lightmap_index: int,
        texture: DecodedTexture,
    ) -> bpy.types.Image:
        return self._terrain_lighting_image(
            zone_tuid,
            lightmap_index,
            texture,
            role="lightmap",
            name_component="Lightmap",
            is_data=False,
        )

    def terrain_directional_lightmap_image(
        self,
        zone_tuid: int,
        lightmap_index: int,
        texture: DecodedTexture,
    ) -> bpy.types.Image:
        return self._terrain_lighting_image(
            zone_tuid,
            lightmap_index,
            texture,
            role="directional_lightmap",
            name_component="Directional_Lightmap",
            is_data=True,
        )

    def _terrain_lighting_image(
        self,
        zone_tuid: int,
        lightmap_index: int,
        texture: DecodedTexture,
        *,
        role: str,
        name_component: str,
        is_data: bool,
    ) -> bpy.types.Image:
        lookup_key = f"zone:{zone_tuid:016X}"
        key = (lightmap_index, role, lookup_key)
        cached = self.images.get(key)
        if cached is not None:
            return cached
        scope = _reader_asset_scope(self.reader)
        name = "_".join(
            part
            for part in (
                _reader_asset_prefix(self.reader),
                scope,
                "Zone",
                f"{zone_tuid:016X}",
                name_component,
                f"{lightmap_index:04X}",
            )
            if part
        )
        image = self._compatible_image(name, key, scope)
        if image is None and bpy.data.images.get(name) is not None:
            digest = zlib.crc32(lookup_key.encode("utf-8"))
            name = f"{name}_{digest:08X}"
            image = self._compatible_image(name, key, scope)
        if image is None:
            image = bpy.data.images.new(
                name,
                width=texture.info.width,
                height=texture.info.height,
                alpha=True,
                float_buffer=False,
                is_data=is_data,
            )
            pixels = np.multiply(
                np.flipud(texture.rgba),
                np.float32(1.0 / 255.0),
                dtype=np.float32,
            ).ravel()
            image.pixels.foreach_set(pixels)
            image.pack()
            image.update()
        self._apply_colorspace(image, role)
        image["insomniac_texture_tuid"] = f"0x{texture.info.tuid:016X}"
        image["insomniac_requested_texture_id"] = (
            f"0x{lightmap_index:08X}"
        )
        image["insomniac_resolved_texture_id"] = (
            f"0x{texture.info.id:08X}"
        )
        image["insomniac_lookup_path_fallback"] = False
        image["insomniac_texture_format"] = (
            f"0x{texture.info.format_code:02X}"
        )
        image["insomniac_texture_role"] = role
        image["insomniac_texture_lookup_path"] = ""
        image["insomniac_texture_lookup_key"] = lookup_key
        image["insomniac_asset_scope"] = scope
        image["insomniac_zone_tuid"] = f"0x{zone_tuid:016X}"
        image["insomniac_lightmap_index"] = lightmap_index
        image[_IMAGE_HAS_ALPHA_KEY] = bool(
            np.min(texture.rgba[..., 3]) < 255
        )
        self.images[key] = image
        return image

    @staticmethod
    def _compatible_image(
        name: str,
        key: tuple[int, str, str],
        scope: str,
    ) -> bpy.types.Image | None:
        requested_id = f"0x{key[0]:08X}"
        direct = bpy.data.images.get(name)
        if direct is None:
            return None
        candidates = (direct,) + tuple(
            image
            for image in bpy.data.images
            if image.name.startswith(f"{name}.")
        )
        for image in candidates:
            if (
                _IMAGE_HAS_ALPHA_KEY in image
                and image.get("insomniac_requested_texture_id")
                == requested_id
                and image.get("insomniac_texture_role") == key[1]
                and image.get("insomniac_texture_lookup_key", "") == key[2]
                and image.get("insomniac_asset_scope", "") == scope
            ):
                return image
        return None

    @staticmethod
    def _apply_colorspace(image: bpy.types.Image, role: str) -> None:
        if image.alpha_mode != "CHANNEL_PACKED":
            image.alpha_mode = "CHANNEL_PACKED"
        colorspace = (
            "sRGB" if role in {"color", "lightmap"} else "Non-Color"
        )
        if image.colorspace_settings.name != colorspace:
            image.colorspace_settings.name = colorspace


def import_materials(
    reader: Resistance2Reader,
    shader_tuids: Iterable[int],
    *,
    cycles_materials: bool = False,
) -> MaterialImportResult:
    warnings: list[str] = []
    factory = _MaterialFactory(
        reader,
        True,
        warnings,
        cycles_materials=cycles_materials,
    )
    materials = tuple(
        factory.material(shader_tuid)
        for shader_tuid in dict.fromkeys(shader_tuids)
    )
    for material in materials:
        material.use_fake_user = True
    factory.release_decoded_textures()
    consume_warnings = getattr(reader, "consume_import_warnings", None)
    if callable(consume_warnings):
        for warning in consume_warnings():
            if warning not in warnings:
                warnings.append(warning)
    return MaterialImportResult(
        materials=materials,
        images=tuple(dict.fromkeys(factory.images.values())),
        warnings=warnings,
    )


def _source_default_moby_meshes(
    asset: MobyAsset,
    *,
    include_customizations: bool,
) -> list[MeshGeometry]:
    customization_segments = frozenset(asset.customization_segments)
    default_draw_mask = asset.default_draw_mask
    return [
        mesh
        for mesh in asset.meshes
        if (
            not mesh.is_damage_permutation
            and mesh.lod_level == 0
            and (
                mesh.segment_index not in customization_segments
                or (
                    include_customizations
                    and default_draw_mask is not None
                    and mesh.segment_index < 64
                    and bool(default_draw_mask & (1 << mesh.segment_index))
                )
            )
        )
    ]


def import_moby(
    context: bpy.types.Context,
    reader: Resistance2Reader,
    tuid: int,
    options: AssetImportOptions,
    *,
    source_path: str,
) -> ImportResult:
    asset = reader.read_moby(tuid)
    collection = _new_collection(context, _safe_name(asset.name))
    root = _create_root(
        collection,
        _safe_name(asset.name),
        options.convert_axes,
        source_path,
        reader,
    )
    warnings: list[str] = []
    materials = _MaterialFactory(
        reader,
        options.materials,
        warnings,
        cycles_materials=options.cycles_materials,
    )
    blender_materials = [
        materials.material(shader_tuid) for shader_tuid in asset.shader_tuids
    ]
    # All materials are built; see _MaterialFactory.release_decoded_textures.
    materials.release_decoded_textures()
    customization_segments = frozenset(asset.customization_segments)
    base_meshes = _source_default_moby_meshes(
        asset,
        include_customizations=False,
    )
    combined = _build_mesh(
        f"{_safe_name(asset.name)}_Mesh",
        base_meshes,
        blender_materials,
    )
    mesh_object = bpy.data.objects.new(_safe_name(asset.name), combined.mesh)
    collection.objects.link(mesh_object)
    mesh_object["insomniac_asset_tuid"] = f"0x{asset.tuid:016X}"
    mesh_object["insomniac_asset_kind"] = "MOBY"

    armature = None
    if asset.skeleton is not None:
        armature = _create_armature(
            context,
            f"{_safe_name(asset.name)}_Rig",
            asset.skeleton,
            collection,
        )
        armature.parent = root
    _bind_moby_mesh(
        mesh_object,
        combined,
        root,
        armature,
        asset.skeleton,
        _reader_game_label(reader),
    )

    optional_object_count = 0
    customization_meshes = [
        mesh
        for mesh in asset.meshes
        if (
            mesh.segment_index in customization_segments
            and mesh.lod_level == 0
        )
    ]
    if customization_meshes:
        customization_collection = _child_collection(
            collection,
            "Customizations",
        )
        customization_collection["insomniac_geometry_kind"] = (
            "CUSTOMIZATIONS"
        )
        if asset.default_draw_mask is not None:
            customization_collection["insomniac_default_draw_mask"] = (
                f"0x{asset.default_draw_mask:016X}"
            )
        for bangle_index, segment_index in enumerate(
            sorted({mesh.segment_index for mesh in customization_meshes})
        ):
            segment_meshes = [
                mesh
                for mesh in customization_meshes
                if mesh.segment_index == segment_index
            ]
            segment_collection = _child_collection(
                customization_collection,
                f"Bangle {segment_index:02d}",
            )
            segment_collection["insomniac_geometry_kind"] = "CUSTOMIZATION"
            segment_collection["insomniac_bangle_index"] = bangle_index
            segment_collection["insomniac_segment_index"] = segment_index
            source_default_visible = (
                asset.default_draw_mask is not None
                and segment_index < 64
                and bool(asset.default_draw_mask & (1 << segment_index))
            )
            segment_collection["insomniac_source_default_visible"] = (
                source_default_visible
            )
            source_default_enabled = (
                options.source_default_parts and source_default_visible
            )
            segment_collection.hide_viewport = not source_default_enabled
            segment_collection.hide_render = not source_default_enabled

            object_name = (
                f"{_safe_name(asset.name)}_Bangle_{segment_index:02d}"
            )
            customization_combined = _build_mesh(
                object_name,
                segment_meshes,
                blender_materials,
            )
            customization_object = bpy.data.objects.new(
                object_name,
                customization_combined.mesh,
            )
            segment_collection.objects.link(customization_object)
            customization_object["insomniac_asset_tuid"] = (
                f"0x{asset.tuid:016X}"
            )
            customization_object["insomniac_asset_kind"] = "MOBY"
            customization_object["insomniac_customization"] = True
            customization_object["insomniac_bangle_index"] = bangle_index
            customization_object["insomniac_segment_index"] = segment_index
            customization_object["insomniac_source_default_visible"] = (
                source_default_visible
            )
            _bind_moby_mesh(
                customization_object,
                customization_combined,
                root,
                armature,
                asset.skeleton,
                _reader_game_label(reader),
            )
            optional_object_count += 1

    damage_meshes = [
        mesh
        for mesh in asset.meshes
        if (
            mesh.is_damage_permutation
            and mesh.segment_index not in customization_segments
            and mesh.lod_level == 0
        )
    ]
    if options.damage_permutations and damage_meshes:
        damage_collection = _child_collection(
            collection,
            "Damage Permutations",
        )
        damage_collection["insomniac_geometry_kind"] = (
            "DAMAGE_PERMUTATIONS"
        )
        for segment_index in sorted(
            {mesh.segment_index for mesh in damage_meshes}
        ):
            segment_meshes = [
                mesh
                for mesh in damage_meshes
                if mesh.segment_index == segment_index
            ]
            object_name = (
                f"{_safe_name(asset.name)}_Damage_{segment_index:02d}"
            )
            damage_combined = _build_mesh(
                object_name,
                segment_meshes,
                blender_materials,
            )
            damage_object = bpy.data.objects.new(
                object_name,
                damage_combined.mesh,
            )
            damage_collection.objects.link(damage_object)
            damage_object["insomniac_asset_tuid"] = (
                f"0x{asset.tuid:016X}"
            )
            damage_object["insomniac_asset_kind"] = "MOBY"
            damage_object["insomniac_damage_permutation"] = True
            damage_object["insomniac_segment_index"] = segment_index
            _bind_moby_mesh(
                damage_object,
                damage_combined,
                root,
                armature,
                asset.skeleton,
                _reader_game_label(reader),
            )
            optional_object_count += 1

    lower_lod_meshes = [
        mesh
        for mesh in asset.meshes
        if mesh.lod_level > 0
        and mesh.segment_index not in customization_segments
        and (
            not mesh.is_damage_permutation
            or options.damage_permutations
        )
    ]
    if options.lower_lods and lower_lod_meshes:
        lod_collection = _child_collection(
            collection,
            "Lower LODs",
        )
        lod_collection["insomniac_geometry_kind"] = "LOWER_LODS"
        for lod_level in sorted(
            {mesh.lod_level for mesh in lower_lod_meshes}
        ):
            lod_meshes = [
                mesh
                for mesh in lower_lod_meshes
                if mesh.lod_level == lod_level
            ]
            object_name = (
                f"{_safe_name(asset.name)}_LOD_{lod_level:02d}"
            )
            lod_combined = _build_mesh(
                object_name,
                lod_meshes,
                blender_materials,
            )
            lod_object = bpy.data.objects.new(
                object_name,
                lod_combined.mesh,
            )
            lod_collection.objects.link(lod_object)
            lod_object["insomniac_asset_tuid"] = (
                f"0x{asset.tuid:016X}"
            )
            lod_object["insomniac_asset_kind"] = "MOBY"
            lod_object["insomniac_lod_level"] = lod_level
            _bind_moby_mesh(
                lod_object,
                lod_combined,
                root,
                armature,
                asset.skeleton,
                _reader_game_label(reader),
            )
            optional_object_count += 1

    if options.collision_geometry and (
        asset.collision_primitives or asset.collision_meshes
    ):
        collision_collection = _child_collection(
            collection,
            "Collision Geometry",
        )
        collision_collection["insomniac_geometry_kind"] = "COLLISION"
        optional_object_count += _import_collision_geometry(
            asset,
            collision_collection,
            root,
            armature,
            asset.skeleton,
            warnings,
            _reader_game_label(reader),
        )

    created_actions: list[bpy.types.Action] = []
    if (
        options.animations
        and armature is not None
        and asset.animset_hash is not None
    ):
        clips = reader.read_animations(asset)
        if options.animation_clip_limit:
            clips = clips[: options.animation_clip_limit]
        _create_actions(
            context,
            armature,
            asset.skeleton,
            clips,
            created_actions=created_actions,
        )
        for clip_index, (action, clip) in enumerate(
            zip(created_actions, clips, strict=True)
        ):
            _tag_action(
                action,
                reader,
                asset,
                clip,
                clip_index,
            )
    consume_warnings = getattr(reader, "consume_import_warnings", None)
    if callable(consume_warnings):
        for warning in consume_warnings():
            if warning not in warnings:
                warnings.append(warning)
    return ImportResult(
        root=root,
        objects=(3 if armature else 2) + optional_object_count,
        meshes=1 + optional_object_count,
        materials=materials.material_count,
        images=len(materials.images),
        actions=len(created_actions),
        warnings=warnings,
    )


def import_moby_animation(
    context: bpy.types.Context,
    reader: Resistance2Reader,
    tuid: int,
    clip_index: int,
    options: AssetImportOptions,
    *,
    source_path: str,
) -> ImportResult:
    asset = reader.read_moby(tuid)
    if asset.skeleton is None or asset.animset_hash is None:
        raise InsomniacError(f"{asset.name} has no importable animations")
    clips = reader.read_animations(asset)
    if not 0 <= clip_index < len(clips):
        raise InsomniacError(
            f"{asset.name} animation index {clip_index} is unavailable"
        )
    clip = clips[clip_index]
    name = _safe_name(f"{asset.name}_{clip.name}")
    collection = _new_collection(context, name)
    root = _create_root(
        collection,
        name,
        options.convert_axes,
        source_path,
        reader,
    )
    root["insomniac_asset_kind"] = "ANIMATION"
    root["insomniac_source_moby_tuid"] = f"0x{asset.tuid:016X}"
    root["insomniac_animation_index"] = clip_index
    root["insomniac_animation_name"] = clip.name
    armature = _create_armature(
        context,
        f"{_safe_name(asset.name)}_Rig",
        asset.skeleton,
        collection,
    )
    armature.parent = root
    created_actions: list[bpy.types.Action] = []
    _create_actions(
        context,
        armature,
        asset.skeleton,
        [clip],
        created_actions=created_actions,
    )
    _tag_action(
        created_actions[0],
        reader,
        asset,
        clip,
        clip_index,
    )
    warnings: list[str] = []
    consume_warnings = getattr(reader, "consume_import_warnings", None)
    if callable(consume_warnings):
        warnings.extend(consume_warnings())
    return ImportResult(
        root=root,
        objects=2,
        actions=1,
        warnings=warnings,
    )


def _bind_moby_mesh(
    mesh_object: bpy.types.Object,
    combined: _CombinedMesh,
    root: bpy.types.Object,
    armature: bpy.types.Object | None,
    skeleton: Skeleton | None,
    game_label: str,
) -> None:
    if armature is None or skeleton is None:
        mesh_object.parent = root
        return
    mesh_object.parent = armature
    modifier = mesh_object.modifiers.new(
        name=f"{game_label} Armature",
        type="ARMATURE",
    )
    modifier.object = armature
    _assign_skin(
        mesh_object,
        skeleton,
        combined.bone_indices,
        combined.bone_weights,
    )


def _import_collision_geometry(
    asset: MobyAsset,
    collection: bpy.types.Collection,
    root: bpy.types.Object,
    armature: bpy.types.Object | None,
    skeleton: Skeleton | None,
    warnings: list[str],
    game_label: str,
) -> int:
    object_count = 0
    kind_names = {4: "Box", 5: "Sphere", 6: "Capsule"}
    for index, primitive in enumerate(asset.collision_primitives):
        kind_name = kind_names.get(primitive.kind)
        if kind_name is None:
            warning = (
                f"Skipped unsupported collision primitive kind "
                f"{primitive.kind} at index {index}"
            )
            LOGGER.warning(warning)
            warnings.append(warning)
            continue
        name = f"Collision_{kind_name}_{index:03d}"
        mesh = _collision_primitive_mesh(name, primitive)
        collision_object = _new_collision_object(
            name,
            mesh,
            collection,
        )
        collision_object["insomniac_collision_kind"] = (
            kind_name.upper()
        )
        if primitive.bone_index is not None:
            collision_object["insomniac_bone_index"] = (
                primitive.bone_index
            )
        _bind_collision_object(
            collision_object,
            root,
            armature,
            skeleton,
            primitive.bone_index,
            game_label,
        )
        object_count += 1

    for index, collision in enumerate(asset.collision_meshes):
        name = f"Collision_Mesh_{index:03d}"
        mesh = _collision_triangle_mesh(name, collision)
        collision_object = _new_collision_object(
            name,
            mesh,
            collection,
        )
        collision_object["insomniac_collision_kind"] = "TRIANGLE_MESH"
        collision_object.parent = root
        object_count += 1
    return object_count


def _new_collision_object(
    name: str,
    mesh: bpy.types.Mesh,
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    collision_object = bpy.data.objects.new(name, mesh)
    collection.objects.link(collision_object)
    collision_object["insomniac_collision_geometry"] = True
    collision_object.display_type = "WIRE"
    collision_object.show_in_front = True
    collision_object.hide_render = True
    collision_object.color = (1.0, 0.18, 0.02, 1.0)
    return collision_object


def _bind_collision_object(
    collision_object: bpy.types.Object,
    root: bpy.types.Object,
    armature: bpy.types.Object | None,
    skeleton: Skeleton | None,
    bone_index: int | None,
    game_label: str,
) -> None:
    if (
        bone_index is None
        or skeleton is None
        or not 0 <= bone_index < skeleton.bone_count
    ):
        collision_object.parent = root
        return
    vertex_count = len(collision_object.data.vertices)
    bone_indices = np.zeros((vertex_count, 4), dtype=np.uint16)
    bone_weights = np.zeros((vertex_count, 4), dtype=np.uint8)
    bone_indices[:, 0] = bone_index
    bone_weights[:, 0] = 255
    _bind_moby_mesh(
        collision_object,
        _CombinedMesh(
            collision_object.data,
            bone_indices,
            bone_weights,
        ),
        root,
        armature,
        skeleton,
        game_label,
    )


def _collision_primitive_mesh(
    name: str,
    primitive: CollisionPrimitive,
) -> bpy.types.Mesh:
    data = np.asarray(primitive.data, dtype=np.float32)
    if data.shape != (4, 4):
        raise InsomniacError(
            f"{name}: collision primitive payload must be a 4x4 matrix"
        )
    if primitive.kind == 4:
        signs = np.asarray(
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
        positions = data[3, :3] + (
            signs * data[:3, 3]
        ) @ data[:3, :3]
        faces = (
            (0, 3, 2, 1),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
        )
    elif primitive.kind == 5:
        positions, faces = _sphere_collision_geometry(
            data[0, :3],
            abs(float(data[0, 3])),
        )
    elif primitive.kind == 6:
        positions, faces = _capsule_collision_geometry(
            data[0, :3],
            data[1, :3],
            abs(float(data[0, 3])),
        )
    else:
        raise InsomniacError(
            f"{name}: unsupported collision primitive kind "
            f"{primitive.kind}"
        )
    return _mesh_from_geometry(name, positions, faces)


def _sphere_collision_geometry(
    center: np.ndarray,
    radius: float,
    *,
    segments: int = 12,
    rings: int = 8,
) -> tuple[np.ndarray, list[tuple[int, ...]]]:
    vertices = [np.asarray(center) + (0.0, 0.0, -radius)]
    for ring in range(1, rings):
        latitude = -math.pi / 2.0 + math.pi * ring / rings
        ring_radius = radius * math.cos(latitude)
        z = radius * math.sin(latitude)
        for segment in range(segments):
            longitude = math.tau * segment / segments
            vertices.append(
                np.asarray(center)
                + (
                    ring_radius * math.cos(longitude),
                    ring_radius * math.sin(longitude),
                    z,
                )
            )
    vertices.append(np.asarray(center) + (0.0, 0.0, radius))
    faces = _ring_faces(segments, rings - 1, len(vertices) - 1)
    return np.asarray(vertices, dtype=np.float32), faces


def _capsule_collision_geometry(
    endpoint_a: np.ndarray,
    endpoint_b: np.ndarray,
    radius: float,
    *,
    segments: int = 12,
) -> tuple[np.ndarray, list[tuple[int, ...]]]:
    endpoint_a = np.asarray(endpoint_a, dtype=np.float32)
    endpoint_b = np.asarray(endpoint_b, dtype=np.float32)
    axis = endpoint_b - endpoint_a
    length = float(np.linalg.norm(axis))
    if length <= 1e-8:
        return _sphere_collision_geometry(
            (endpoint_a + endpoint_b) * 0.5,
            radius,
            segments=segments,
        )
    direction = axis / length
    reference = (
        np.asarray((1.0, 0.0, 0.0), dtype=np.float32)
        if abs(float(direction[0])) < 0.9
        else np.asarray((0.0, 1.0, 0.0), dtype=np.float32)
    )
    tangent = np.cross(direction, reference)
    tangent /= np.linalg.norm(tangent)
    bitangent = np.cross(direction, tangent)

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
    faces = _ring_faces(
        segments,
        len(ring_specs),
        len(vertices) - 1,
    )
    return np.asarray(vertices, dtype=np.float32), faces


def _ring_faces(
    segments: int,
    ring_count: int,
    top_index: int,
) -> list[tuple[int, ...]]:
    faces: list[tuple[int, ...]] = []
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        faces.append((0, 1 + next_segment, 1 + segment))
    for ring in range(ring_count - 1):
        first = 1 + ring * segments
        next_first = first + segments
        for segment in range(segments):
            next_segment = (segment + 1) % segments
            faces.append(
                (
                    first + segment,
                    first + next_segment,
                    next_first + next_segment,
                    next_first + segment,
                )
            )
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
    return faces


def _collision_triangle_mesh(
    name: str,
    collision: CollisionMesh,
) -> bpy.types.Mesh:
    faces = np.asarray(collision.indices, dtype=np.int32).reshape((-1, 3))
    mesh = _mesh_from_geometry(name, collision.positions, faces)
    if len(collision.face_types):
        attribute = mesh.attributes.new(
            name="collision_type",
            type="INT",
            domain="FACE",
        )
        attribute.data.foreach_set(
            "value",
            np.asarray(collision.face_types, dtype=np.int32),
        )
    return mesh


def _mesh_from_geometry(
    name: str,
    positions: np.ndarray,
    faces: Iterable[Iterable[int]],
) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(
        np.asarray(positions, dtype=np.float32).tolist(),
        [],
        [tuple(int(index) for index in face) for face in faces],
    )
    mesh.update(calc_edges=True)
    mesh.validate(clean_customdata=False)
    return mesh


def _static_asset_geometry_and_materials(
    materials: _MaterialFactory,
    asset: TieAsset,
    *,
    variant_kind: str,
) -> tuple[list[MeshGeometry], list[bpy.types.Material]]:
    vertex_lit = any(mesh.colors is not None for mesh in asset.meshes)
    if not vertex_lit or not asset.shader_tuids:
        return (
            asset.meshes,
            [
                materials.material(shader_tuid)
                for shader_tuid in asset.shader_tuids
            ],
        )

    blender_materials: list[bpy.types.Material] = []
    material_slots: dict[tuple[int, bool], int] = {}
    geometries: list[MeshGeometry] = []
    for mesh in asset.meshes:
        shader_index = (
            mesh.shader_index
            if 0 <= mesh.shader_index < len(asset.shader_tuids)
            else 0
        )
        lightmapped = (
            variant_kind == "TIE"
            and materials.tie_lightmap_atlas is not None
            and mesh.secondary_uvs is not None
        )
        key = (shader_index, lightmapped)
        material_slot = material_slots.get(key)
        if material_slot is None:
            material_slot = len(blender_materials)
            material_slots[key] = material_slot
            blender_materials.append(
                materials.vertex_lit_material(
                    asset.shader_tuids[shader_index],
                    variant_kind=variant_kind,
                    lightmapped=lightmapped,
                )
            )
        geometries.append(replace(mesh, shader_index=material_slot))
    return geometries, blender_materials


def import_static_asset(
    context: bpy.types.Context,
    reader: Resistance2Reader,
    tuid: int,
    options: AssetImportOptions,
    *,
    kind: AssetKind,
    source_path: str,
) -> ImportResult:
    kind_config = {
        AssetKind.TIE: ("Tie", "read_tie"),
        AssetKind.SHRUB: ("Shrub", "read_shrub"),
        AssetKind.FOLIAGE: ("Foliage", "read_foliage"),
    }
    config = kind_config.get(kind)
    if config is None:
        raise ValueError(f"{kind.name} is not a static geometry asset")
    label, reader_method = config
    read_asset = getattr(reader, reader_method, None)
    if not callable(read_asset):
        raise InsomniacError(
            f"{_reader_game_label(reader)} does not support standalone "
            f"{label.lower()} geometry"
        )
    asset = read_asset(tuid)
    name = f"{_reader_asset_prefix(reader)}_{label}_{tuid:016X}"
    collection = _new_collection(context, name)
    root = _create_root(
        collection,
        name,
        options.convert_axes,
        source_path,
        reader,
    )
    warnings: list[str] = []
    materials = _MaterialFactory(
        reader,
        options.materials,
        warnings,
        cycles_materials=options.cycles_materials,
    )
    geometries, blender_materials = _static_asset_geometry_and_materials(
        materials,
        asset,
        variant_kind=kind.name,
    )
    # All materials are built; see _MaterialFactory.release_decoded_textures.
    materials.release_decoded_textures()
    combined = _build_mesh(f"{name}_Mesh", geometries, blender_materials)
    obj = bpy.data.objects.new(name, combined.mesh)
    collection.objects.link(obj)
    obj.parent = root
    obj["insomniac_asset_tuid"] = f"0x{asset.tuid:016X}"
    obj["insomniac_asset_kind"] = kind.name
    return ImportResult(
        root=root,
        objects=2,
        meshes=1,
        materials=materials.material_count,
        images=len(materials.images),
        warnings=warnings,
    )


def import_tie(
    context: bpy.types.Context,
    reader: Resistance2Reader,
    tuid: int,
    options: AssetImportOptions,
    *,
    source_path: str,
) -> ImportResult:
    return import_static_asset(
        context,
        reader,
        tuid,
        options,
        kind=AssetKind.TIE,
        source_path=source_path,
    )


def _import_level_static_instances(
    collection: bpy.types.Collection,
    root: bpy.types.Object,
    materials: _MaterialFactory,
    result: ImportResult,
    warnings: list[str],
    instances: Iterable[PlacedInstance],
    *,
    collection_name: str,
    asset_label: str,
    asset_kind: str,
    read_asset: Callable[[int], TieAsset],
) -> None:
    instance_collection = _child_collection(collection, collection_name)
    meshes: dict[int, bpy.types.Mesh] = {}
    for instance in instances:
        mesh = meshes.get(instance.asset_tuid)
        if mesh is None:
            try:
                asset = read_asset(instance.asset_tuid)
                (
                    geometries,
                    asset_materials,
                ) = _static_asset_geometry_and_materials(
                    materials,
                    asset,
                    variant_kind=asset_kind,
                )
                mesh = _build_mesh(
                    f"{asset_label}_{instance.asset_tuid:016X}",
                    geometries,
                    asset_materials,
                ).mesh
                meshes[instance.asset_tuid] = mesh
                result.meshes += 1
            except InsomniacError as exc:
                message = (
                    f"{asset_label} 0x{instance.asset_tuid:016X} "
                    f"skipped: {exc}"
                )
                LOGGER.warning(message)
                warnings.append(message)
                continue
        obj = _create_instance_object(
            instance_collection,
            instance,
            mesh,
            root,
            kind=asset_kind,
        )
        result.objects += 1
        obj["insomniac_zone_instance"] = True
        if asset_kind == "TIE":
            materials.apply_tie_lightmap_transform(obj, instance)


def _import_level_collision(
    collection: bpy.types.Collection,
    root: bpy.types.Object,
    scene: CollisionScene,
    result: ImportResult,
) -> None:
    validate_collision_scene(scene)
    if not scene.instances:
        return

    collision_collection = _child_collection(
        collection,
        "Collision Geometry",
    )
    collision_collection["insomniac_geometry_kind"] = "LEVEL_COLLISION"
    collision_collection.hide_render = True

    meshes_by_id = {mesh.mesh_id: mesh for mesh in scene.meshes}
    blender_meshes: dict[str, bpy.types.Mesh] = {}
    group_collections: dict[str, bpy.types.Collection] = {}
    instances = sorted(
        scene.instances,
        key=lambda instance: (
            instance.source_group.casefold(),
            instance.name.casefold(),
            instance.source_id or "",
            instance.mesh_id,
        ),
    )
    for instance in instances:
        group_collection = group_collections.get(instance.source_group)
        if group_collection is None:
            group_collection = _child_collection(
                collision_collection,
                _safe_name(instance.source_group),
            )
            group_collection["insomniac_collision_source_group"] = (
                instance.source_group
            )
            group_collection.hide_render = True
            group_collections[instance.source_group] = group_collection

        mesh = blender_meshes.get(instance.mesh_id)
        if mesh is None:
            source_mesh = meshes_by_id[instance.mesh_id]
            mesh = _level_collision_mesh(source_mesh)
            blender_meshes[instance.mesh_id] = mesh
            result.meshes += 1

        obj = _new_collision_object(
            _safe_name(instance.name),
            mesh,
            group_collection,
        )
        obj.parent = root
        obj.matrix_local = Matrix(
            np.asarray(instance.matrix, dtype=np.float64).tolist()
        )
        obj["insomniac_collision_kind"] = "LEVEL_MESH"
        obj["insomniac_collision_mesh_id"] = instance.mesh_id
        obj["insomniac_collision_source_group"] = instance.source_group
        if instance.source_id is not None:
            obj["insomniac_collision_source_id"] = instance.source_id
        _set_collision_metadata(obj, instance.metadata)
        result.objects += 1


def _level_collision_mesh(
    collision: LevelCollisionMesh,
) -> bpy.types.Mesh:
    digest = zlib.crc32(collision.mesh_id.encode("utf-8"))
    name = (
        f"Collision_{_safe_name(collision.name)[:42]}_{digest:08X}"
    )
    mesh = _mesh_from_geometry(
        name,
        collision.positions,
        collision.indices,
    )
    mesh["insomniac_collision_geometry"] = True
    mesh["insomniac_collision_mesh_id"] = collision.mesh_id
    if collision.source_kind:
        mesh["insomniac_collision_source_kind"] = collision.source_kind
    if collision.source_id is not None:
        mesh["insomniac_collision_source_id"] = collision.source_id
    _set_collision_metadata(mesh, collision.metadata)
    _set_collision_face_attribute(
        mesh,
        "insomniac_surface_id",
        collision.surface_ids,
    )
    _set_collision_face_attribute(
        mesh,
        "insomniac_material_id",
        collision.material_ids,
    )
    return mesh


def _set_collision_face_attribute(
    mesh: bpy.types.Mesh,
    name: str,
    values: np.ndarray | None,
) -> None:
    if values is None:
        return
    attribute = mesh.attributes.new(
        name=name,
        type="INT",
        domain="FACE",
    )
    attribute.data.foreach_set(
        "value",
        np.asarray(values, dtype=np.int32),
    )


def _set_collision_metadata(
    owner: bpy.types.ID,
    metadata: dict[str, bool | int | float | str],
) -> None:
    if metadata:
        owner["insomniac_collision_metadata"] = json.dumps(
            metadata,
            sort_keys=True,
            separators=(",", ":"),
        )


def import_level(
    context: bpy.types.Context,
    reader: Resistance2Reader,
    options: LevelImportOptions,
    *,
    source_path: str,
) -> ImportResult:
    level_name = PureName(reader.resource_prefix.split("/")[-1] or "Level")
    collection = _new_collection(
        context,
        f"{_reader_asset_prefix(reader)}_{level_name}",
    )
    root = _create_root(
        collection,
        f"{_reader_asset_prefix(reader)}_{level_name}",
        options.convert_axes,
        source_path,
        reader,
    )
    root["insomniac_level_prefix"] = reader.resource_prefix
    warnings: list[str] = []
    materials = _MaterialFactory(
        reader,
        options.materials,
        warnings,
        cycles_materials=options.cycles_materials,
    )
    result = ImportResult(root=root, objects=1, warnings=warnings)

    zones = reader.read_zones()
    if options.ties:
        materials.prepare_tie_lightmap_atlas(zones)
    if options.terrain:
        terrain_collection = _child_collection(collection, "Terrain")
        for zone in zones:
            if not zone.terrain_meshes:
                continue
            geometries: list[MeshGeometry] = []
            zone_materials: list[bpy.types.Material] = []
            material_variants: dict[
                tuple[int, int | None, bool],
                int,
            ] = {}
            use_material_variants = bool(zone.shader_tuids) and any(
                terrain.colors is not None
                or terrain.lightmap_index in zone.lightmaps
                for terrain in zone.terrain_meshes
            )
            for terrain in zone.terrain_meshes:
                shader_index = terrain.shader_index
                if not 0 <= shader_index < len(zone.shader_tuids):
                    shader_index = 0
                has_vertex_colors = terrain.colors is not None
                lightmap = (
                    zone.lightmaps.get(terrain.lightmap_index)
                    if terrain.lightmap_index is not None
                    else None
                )
                directional_lightmap = (
                    zone.directional_lightmaps.get(terrain.lightmap_index)
                    if terrain.lightmap_index is not None
                    else None
                )
                if use_material_variants:
                    variant_key = (
                        shader_index,
                        terrain.lightmap_index,
                        has_vertex_colors,
                    )
                    material_index = material_variants.get(variant_key)
                    if material_index is None:
                        material_index = len(zone_materials)
                        material_variants[variant_key] = material_index
                        zone_materials.append(
                            materials.terrain_material(
                                zone.shader_tuids[shader_index],
                                zone.tuid,
                                terrain.lightmap_index,
                                lightmap,
                                directional_lightmap,
                                vertex_colors=has_vertex_colors,
                            )
                        )
                else:
                    material_index = shader_index
                geometries.append(
                    MeshGeometry(
                        shader_index=material_index,
                        positions=terrain.positions,
                        uvs=terrain.uvs,
                        indices=terrain.indices,
                        secondary_uvs=terrain.secondary_uvs,
                        colors=terrain.colors,
                    )
                )
            if not zone_materials:
                zone_materials = [
                    materials.material(shader_tuid)
                    for shader_tuid in zone.shader_tuids
                ]
            combined = _build_mesh(
                f"Zone_{zone.tuid:016X}_Terrain",
                geometries,
                zone_materials,
            )
            obj = bpy.data.objects.new(
                f"Zone_{zone.tuid:016X}_Terrain",
                combined.mesh,
            )
            terrain_collection.objects.link(obj)
            obj.parent = root
            obj["insomniac_zone_tuid"] = f"0x{zone.tuid:016X}"
            result.objects += 1
            result.meshes += 1

    if options.ties:
        instances = [
            instance
            for zone in zones
            for instance in zone.tie_instances
        ]
        _import_level_static_instances(
            collection,
            root,
            materials,
            result,
            warnings,
            instances,
            collection_name="Static Props",
            asset_label="Tie",
            asset_kind="TIE",
            read_asset=reader.read_tie,
        )

    if options.vegetation:
        vegetation = (
            (
                "Shrubs",
                "Shrub",
                "SHRUB",
                "read_shrub",
                [
                    instance
                    for zone in zones
                    for instance in zone.shrub_instances
                ],
            ),
            (
                "Foliage",
                "Foliage",
                "FOLIAGE",
                "read_foliage",
                [
                    instance
                    for zone in zones
                    for instance in zone.foliage_instances
                ],
            ),
        )
        for (
            collection_name,
            asset_label,
            asset_kind,
            reader_method,
            instances,
        ) in vegetation:
            if not instances:
                continue
            read_asset = getattr(reader, reader_method, None)
            if not callable(read_asset):
                warnings.append(
                    f"Skipped {len(instances)} {asset_label.lower()} "
                    f"placements; {_reader_game_label(reader)} does not "
                    f"implement their geometry reader"
                )
                continue
            _import_level_static_instances(
                collection,
                root,
                materials,
                result,
                warnings,
                instances,
                collection_name=collection_name,
                asset_label=asset_label,
                asset_kind=asset_kind,
                read_asset=read_asset,
            )

        unsupported_shrubs = sum(
            zone.unsupported_shrub_instances for zone in zones
        )
        unsupported_foliage = sum(
            zone.unsupported_foliage_instances for zone in zones
        )
        if unsupported_shrubs or unsupported_foliage:
            skipped = []
            if unsupported_shrubs:
                skipped.append(f"{unsupported_shrubs} shrub")
            if unsupported_foliage:
                skipped.append(f"{unsupported_foliage} foliage")
            warnings.append(
                f"Skipped {' and '.join(skipped)} placements; their "
                f"{_reader_game_label(reader)} geometry chains are "
                "documented but not implemented yet"
            )

    if options.gameplay:
        gameplay_collection = _child_collection(
            collection, "Gameplay Instances"
        )
        moby_meshes: dict[int, bpy.types.Mesh] = {}
        for region in reader.read_gameplay_regions():
            region_collection = _child_collection(
                gameplay_collection, _safe_name(region.name)
            )
            for instance in region.moby_instances:
                mesh = moby_meshes.get(instance.asset_tuid)
                if mesh is None:
                    try:
                        moby = reader.read_moby(instance.asset_tuid)
                        moby_materials = [
                            materials.material(shader_tuid)
                            for shader_tuid in moby.shader_tuids
                        ]
                        mesh = _build_mesh(
                            f"Moby_{instance.asset_tuid:016X}",
                            _source_default_moby_meshes(
                                moby,
                                include_customizations=True,
                            ),
                            moby_materials,
                        ).mesh
                        if moby.default_draw_mask is not None:
                            mesh["insomniac_default_draw_mask"] = (
                                f"0x{moby.default_draw_mask:016X}"
                            )
                        mesh["insomniac_source_default_parts"] = True
                        moby_meshes[instance.asset_tuid] = mesh
                        result.meshes += 1
                    except InsomniacError as exc:
                        message = (
                            f"Gameplay Moby 0x{instance.asset_tuid:016X} "
                            f"skipped: {exc}"
                        )
                        LOGGER.warning(message)
                        warnings.append(message)
                        continue
                obj = _create_instance_object(
                    region_collection,
                    instance,
                    mesh,
                    root,
                    kind="MOBY",
                )
                obj["insomniac_source_default_parts"] = True
                default_draw_mask = mesh.get(
                    "insomniac_default_draw_mask"
                )
                if default_draw_mask is not None:
                    obj["insomniac_default_draw_mask"] = default_draw_mask
                result.objects += 1

    if options.collision_geometry:
        read_level_collision = getattr(
            reader,
            "read_level_collision",
            None,
        )
        if not callable(read_level_collision):
            warnings.append(
                f"{_reader_game_label(reader)} does not implement level "
                "collision geometry"
            )
        else:
            _import_level_collision(
                collection,
                root,
                read_level_collision(),
                result,
            )

    # Level materials are built lazily while terrain, ties and mobys are
    # placed, so release only after the last one.
    # See _MaterialFactory.release_decoded_textures.
    materials.release_decoded_textures()
    warnings.extend(reader.warnings)
    result.materials = materials.material_count
    result.images = len(materials.images)
    return result


def _new_collection(
    context: bpy.types.Context,
    name: str,
) -> bpy.types.Collection:
    collection = bpy.data.collections.new(name)
    context.scene.collection.children.link(collection)
    return collection


def _child_collection(
    parent: bpy.types.Collection,
    name: str,
) -> bpy.types.Collection:
    collection = bpy.data.collections.new(name)
    parent.children.link(collection)
    return collection


def _create_root(
    collection: bpy.types.Collection,
    name: str,
    convert_axes: bool,
    source_path: str,
    reader: Resistance2Reader,
) -> bpy.types.Object:
    root = bpy.data.objects.new(f"{name}_Root", None)
    collection.objects.link(root)
    if convert_axes:
        root.rotation_euler[0] = math.radians(
            _reader_axis_conversion_x_degrees(reader)
        )
    root["insomniac_game"] = _reader_game_id(reader)
    root["insomniac_engine"] = _reader_engine_id(reader)
    root["insomniac_source"] = source_path
    return root


def _build_mesh(
    name: str,
    geometries: Iterable[MeshGeometry],
    materials: list[bpy.types.Material],
) -> _CombinedMesh:
    geometry_list = list(geometries)
    positions_parts: list[np.ndarray] = []
    uv_parts: list[np.ndarray] = []
    secondary_uv_parts: list[np.ndarray] = []
    color_parts: list[np.ndarray] = []
    face_parts: list[np.ndarray] = []
    material_parts: list[np.ndarray] = []
    bone_index_parts: list[np.ndarray] = []
    bone_weight_parts: list[np.ndarray] = []
    any_skin = any(item.bone_indices is not None for item in geometry_list)
    any_secondary_uvs = any(
        item.secondary_uvs is not None for item in geometry_list
    )
    any_colors = any(item.colors is not None for item in geometry_list)
    vertex_offset = 0

    for geometry in geometry_list:
        positions = np.asarray(geometry.positions, dtype=np.float32)
        if not len(positions):
            continue
        raw_indices = np.asarray(geometry.indices, dtype=np.int64)
        triangle_count = len(raw_indices) // 3
        if not triangle_count:
            continue
        triangles = raw_indices[: triangle_count * 3].reshape(-1, 3)
        valid = np.all(
            (triangles >= 0) & (triangles < len(positions)),
            axis=1,
        )
        triangles = triangles[valid]
        if not len(triangles):
            continue
        positions_parts.append(positions)
        geometry_uvs = np.asarray(geometry.uvs, dtype=np.float32)
        uv_parts.append(geometry_uvs)
        if any_secondary_uvs:
            secondary_uv_parts.append(
                np.asarray(
                    (
                        geometry.secondary_uvs
                        if geometry.secondary_uvs is not None
                        else geometry_uvs
                    ),
                    dtype=np.float32,
                )
            )
        if any_colors:
            color_parts.append(
                np.asarray(
                    (
                        geometry.colors
                        if geometry.colors is not None
                        else np.ones((len(positions), 4), dtype=np.float32)
                    ),
                    dtype=np.float32,
                )
            )
        face_parts.append(triangles.astype(np.int32) + vertex_offset)
        material_index = (
            geometry.shader_index
            if 0 <= geometry.shader_index < len(materials)
            else 0
        )
        material_parts.append(
            np.full(len(triangles), material_index, dtype=np.int32)
        )
        if any_skin:
            if geometry.bone_indices is None:
                bone_index_parts.append(
                    np.zeros((len(positions), 4), dtype=np.uint16)
                )
                bone_weight_parts.append(
                    np.zeros((len(positions), 4), dtype=np.uint8)
                )
            else:
                bone_index_parts.append(
                    np.asarray(geometry.bone_indices, dtype=np.uint16)
                )
                bone_weight_parts.append(
                    np.asarray(geometry.bone_weights, dtype=np.uint8)
                )
        vertex_offset += len(positions)

    mesh = bpy.data.meshes.new(name)
    if not positions_parts:
        return _CombinedMesh(mesh, None, None)
    positions = np.concatenate(positions_parts)
    uvs = np.concatenate(uv_parts)
    secondary_uvs = (
        np.concatenate(secondary_uv_parts)
        if secondary_uv_parts
        else None
    )
    colors = np.concatenate(color_parts) if color_parts else None
    faces = np.concatenate(face_parts)
    material_indices = np.concatenate(material_parts)

    mesh.vertices.add(len(positions))
    mesh.vertices.foreach_set("co", positions.reshape(-1))
    mesh.loops.add(faces.size)
    mesh.loops.foreach_set("vertex_index", faces.reshape(-1))
    mesh.polygons.add(len(faces))
    mesh.polygons.foreach_set(
        "loop_start", np.arange(len(faces), dtype=np.int32) * 3
    )
    mesh.polygons.foreach_set(
        "loop_total", np.full(len(faces), 3, dtype=np.int32)
    )
    mesh.polygons.foreach_set("material_index", material_indices)
    mesh.polygons.foreach_set(
        "use_smooth", np.ones(len(faces), dtype=np.bool_)
    )
    uv_layer = mesh.uv_layers.new(name="UVMap")
    loop_uvs = uvs[faces.reshape(-1)].copy()
    loop_uvs[:, 1] = 1.0 - loop_uvs[:, 1]
    if hasattr(uv_layer, "uv"):
        uv_layer.uv.foreach_set("vector", loop_uvs.reshape(-1))
    else:
        uv_layer.data.foreach_set("uv", loop_uvs.reshape(-1))
    if secondary_uvs is not None:
        secondary_uv_layer = mesh.uv_layers.new(name="LightmapUV")
        loop_secondary_uvs = secondary_uvs[faces.reshape(-1)].copy()
        loop_secondary_uvs[:, 1] = 1.0 - loop_secondary_uvs[:, 1]
        if hasattr(secondary_uv_layer, "uv"):
            secondary_uv_layer.uv.foreach_set(
                "vector",
                loop_secondary_uvs.reshape(-1),
            )
        else:
            secondary_uv_layer.data.foreach_set(
                "uv",
                loop_secondary_uvs.reshape(-1),
            )
    if colors is not None:
        color_attribute = mesh.color_attributes.new(
            name="InsomniacVertexColor",
            type="BYTE_COLOR",
            domain="POINT",
        )
        color_attribute.data.foreach_set("color", colors.reshape(-1))
    for material in materials:
        mesh.materials.append(material)
    mesh.update(calc_edges=True)
    mesh.validate(clean_customdata=False)

    bone_indices = (
        np.concatenate(bone_index_parts) if bone_index_parts else None
    )
    bone_weights = (
        np.concatenate(bone_weight_parts) if bone_weight_parts else None
    )
    return _CombinedMesh(mesh, bone_indices, bone_weights)


def _create_armature(
    context: bpy.types.Context,
    name: str,
    skeleton: Skeleton,
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    armature_data = bpy.data.armatures.new(name)
    armature = bpy.data.objects.new(name, armature_data)
    collection.objects.link(armature)
    context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    edit_bones: list[bpy.types.EditBone] = []
    for index, world in enumerate(skeleton.bind_world):
        source_bone = skeleton.bones[index]
        bone = armature_data.edit_bones.new(_bone_name(index))
        matrix = Matrix(np.asarray(world, dtype=np.float64).tolist())
        head = matrix.translation
        direction = matrix.to_3x3().col[1].normalized()
        child_index = source_bone.child_index
        if source_bone.has_child(index, skeleton.bone_count):
            child_head = Vector(skeleton.bind_world[child_index, :3, 3])
            length = max((child_head - head).length, 0.01)
        else:
            length = 0.05
        bone.head = head
        bone.tail = head + direction * length
        bone.matrix = matrix
        bone.length = length
        bone.inherit_scale = (
            "FULL" if source_bone.inherits_scale else "NONE"
        )
        edit_bones.append(bone)
    for index, source_bone in enumerate(skeleton.bones):
        if source_bone.has_parent(index, skeleton.bone_count):
            edit_bones[index].parent = edit_bones[source_bone.parent_index]
            edit_bones[index].use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    for index, source_bone in enumerate(skeleton.bones):
        data_bone = armature_data.bones[_bone_name(index)]
        data_bone["insomniac_bone_index"] = index
        data_bone["insomniac_flags"] = source_bone.flags
    armature["insomniac_scale_shift"] = skeleton.scale_shift
    armature["insomniac_translation_shift"] = skeleton.translation_shift
    return armature


def _assign_skin(
    mesh_object: bpy.types.Object,
    skeleton: Skeleton,
    bone_indices: np.ndarray | None,
    bone_weights: np.ndarray | None,
) -> None:
    if bone_indices is None or bone_weights is None:
        return
    groups = [
        mesh_object.vertex_groups.new(name=_bone_name(index))
        for index in range(skeleton.bone_count)
    ]
    for bone_index, group in enumerate(groups):
        vertex_slots = np.argwhere(
            (bone_indices == bone_index) & (bone_weights > 0)
        )
        if not len(vertex_slots):
            continue
        vertices = vertex_slots[:, 0]
        weights = bone_weights[vertices, vertex_slots[:, 1]]
        for raw_weight in np.unique(weights):
            selected = np.unique(vertices[weights == raw_weight])
            group.add(
                selected.astype(int).tolist(),
                float(raw_weight) / 255.0,
                "REPLACE",
            )


def _create_actions(
    context: bpy.types.Context,
    armature: bpy.types.Object,
    skeleton: Skeleton,
    clips: list[DecodedClip],
    *,
    created_actions: list[bpy.types.Action] | None = None,
) -> int:
    if not clips:
        return 0
    armature.animation_data_create()
    order = _bone_order(skeleton)
    source_bind_inverses = [
        Matrix(np.asarray(world, dtype=np.float64).tolist())
        for world in skeleton.bind_world_inverse
    ]
    blender_rest = [
        armature.data.bones[_bone_name(index)].matrix_local.copy()
        for index in range(skeleton.bone_count)
    ]
    data_bones = [
        armature.data.bones[_bone_name(index)]
        for index in range(skeleton.bone_count)
    ]
    pose_bones = [
        armature.pose.bones[_bone_name(index)]
        for index in range(skeleton.bone_count)
    ]
    scene_fps = max(context.scene.render.fps, 1)
    original_frame = context.scene.frame_current
    armature.animation_data.action = None
    for clip in clips:
        action = bpy.data.actions.new(
            name=f"{armature.name}_{_safe_name(clip.name)}"
        )
        if created_actions is not None:
            created_actions.append(action)
        action.use_fake_user = True
        action["insomniac_frame_rate"] = clip.frame_rate
        action["insomniac_looping"] = clip.looping
        action["insomniac_additive_source"] = clip.additive
        if clip.overlay_base_name is not None:
            action["insomniac_overlay_baked"] = True
            action["insomniac_overlay_base"] = clip.overlay_base_name
        slot = action.slots.new("OBJECT", armature.name)
        action.slots.active = slot
        layer = action.layers.new("Animation")
        strip = layer.strips.new(type="KEYFRAME")
        channelbag = strip.channelbags.new(slot)
        for pose_bone in armature.pose.bones:
            pose_bone.rotation_mode = "QUATERNION"
            pose_bone.matrix_basis.identity()

        frame_count = clip.frame_count
        frames = (
            1.0
            + np.arange(frame_count, dtype=np.float64)
            * scene_fps
            / clip.frame_rate
        )
        locations = np.empty(
            (frame_count, skeleton.bone_count, 3),
            dtype=np.float32,
        )
        rotations = np.empty(
            (frame_count, skeleton.bone_count, 4),
            dtype=np.float32,
        )
        scales = np.empty(
            (frame_count, skeleton.bone_count, 3),
            dtype=np.float32,
        )
        animated_order = _required_bone_order(
            skeleton,
            order,
            clip.animated_bones,
        )
        absolute: list[Matrix] = [Matrix.Identity(4)] * skeleton.bone_count
        blender_pose: list[Matrix] = [Matrix.Identity(4)] * skeleton.bone_count
        for frame_index in range(frame_count):
            unscaled_absolute: list[Matrix | None] = (
                [None] * skeleton.bone_count
            )
            frame_order = order if frame_index == 0 else animated_order
            for bone_index in frame_order:
                translation = Vector(
                    clip.translations[frame_index, bone_index]
                )
                xyzw = clip.rotations_xyzw[frame_index, bone_index]
                rotation = Quaternion(
                    (
                        float(xyzw[3]),
                        float(xyzw[0]),
                        float(xyzw[1]),
                        float(xyzw[2]),
                    )
                )
                scale = Vector(clip.scales[frame_index, bone_index])
                local = Matrix.LocRotScale(translation, rotation, scale)
                source_bone = skeleton.bones[bone_index]
                if source_bone.has_parent(bone_index, skeleton.bone_count):
                    parent_index = source_bone.parent_index
                    parent_matrix = absolute[parent_index]
                    if not source_bone.inherits_scale:
                        parent_matrix = unscaled_absolute[parent_index]
                        if parent_matrix is None:
                            source_parent = absolute[parent_index]
                            parent_matrix = Matrix.LocRotScale(
                                source_parent.translation,
                                source_parent.to_quaternion(),
                                Vector((1.0, 1.0, 1.0)),
                            )
                            unscaled_absolute[parent_index] = parent_matrix
                    absolute[bone_index] = parent_matrix @ local
                else:
                    absolute[bone_index] = local
                blender_pose[bone_index] = (
                    absolute[bone_index]
                    @ source_bind_inverses[bone_index]
                    @ blender_rest[bone_index]
                )
                if (
                    frame_index > 0
                    and not clip.animated_bones[bone_index]
                ):
                    continue
                data_bone = data_bones[bone_index]
                pose_bone = pose_bones[bone_index]
                if source_bone.has_parent(
                    bone_index,
                    skeleton.bone_count,
                ):
                    parent_index = source_bone.parent_index
                    pose_bone.matrix_basis = (
                        data_bone.convert_local_to_pose(
                            matrix=blender_pose[bone_index],
                            matrix_local=blender_rest[bone_index],
                            parent_matrix=blender_pose[parent_index],
                            parent_matrix_local=blender_rest[parent_index],
                            invert=True,
                        )
                    )
                else:
                    pose_bone.matrix_basis = (
                        data_bone.convert_local_to_pose(
                            matrix=blender_pose[bone_index],
                            matrix_local=blender_rest[bone_index],
                            invert=True,
                        )
                    )
                locations[frame_index, bone_index] = pose_bone.location
                rotations[frame_index, bone_index] = (
                    pose_bone.rotation_quaternion
                )
                scales[frame_index, bone_index] = pose_bone.scale

        location_defaults, location_constants = _action_curve_masks(
            locations,
            (0.0, 0.0, 0.0),
            clip.animated_bones,
        )
        rotation_defaults, rotation_constants = _action_curve_masks(
            rotations,
            (1.0, 0.0, 0.0, 0.0),
            clip.animated_bones,
        )
        scale_defaults, scale_constants = _action_curve_masks(
            scales,
            (1.0, 1.0, 1.0),
            clip.animated_bones,
        )
        for bone_index, pose_bone in enumerate(pose_bones):
            sample_count = (
                frame_count if clip.animated_bones[bone_index] else 1
            )
            if not sample_count:
                continue
            group = _write_action_curves(
                channelbag,
                None,
                pose_bone.name,
                pose_bone.path_from_id("location"),
                frames[:sample_count],
                locations[:sample_count, bone_index],
                (0.0, 0.0, 0.0),
                location_defaults[bone_index],
                location_constants[bone_index],
            )
            group = _write_action_curves(
                channelbag,
                group,
                pose_bone.name,
                pose_bone.path_from_id("rotation_quaternion"),
                frames[:sample_count],
                rotations[:sample_count, bone_index],
                (1.0, 0.0, 0.0, 0.0),
                rotation_defaults[bone_index],
                rotation_constants[bone_index],
            )
            _write_action_curves(
                channelbag,
                group,
                pose_bone.name,
                pose_bone.path_from_id("scale"),
                frames[:sample_count],
                scales[:sample_count, bone_index],
                (1.0, 1.0, 1.0),
                scale_defaults[bone_index],
                scale_constants[bone_index],
            )
        action.frame_range = (float(frames[0]), float(frames[-1]))
    armature.animation_data.action = None
    for pose_bone in armature.pose.bones:
        pose_bone.matrix_basis.identity()
        pose_bone.select = True
    context.scene.frame_set(original_frame)
    context.view_layer.update()
    return len(clips)


def _action_curve_masks(
    values: np.ndarray,
    defaults: tuple[float, ...],
    animated_bones: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    defaults_array = np.asarray(defaults, dtype=values.dtype)
    default_mask = (
        np.abs(values[0] - defaults_array) <= _ACTION_VALUE_EPSILON
    )
    constant_mask = np.ones_like(default_mask)
    if np.any(animated_bones):
        animated_values = values[:, animated_bones]
        default_mask[animated_bones] = np.all(
            np.abs(animated_values - defaults_array)
            <= _ACTION_VALUE_EPSILON,
            axis=0,
        )
        constant_mask[animated_bones] = np.all(
            np.abs(animated_values - animated_values[:1])
            <= _ACTION_VALUE_EPSILON,
            axis=0,
        )
    return default_mask, constant_mask


def _tag_action(
    action: bpy.types.Action,
    reader: Resistance2Reader,
    asset: MobyAsset,
    clip: DecodedClip,
    clip_index: int,
) -> None:
    action["insomniac_game"] = _reader_game_id(reader)
    action["insomniac_asset_kind"] = "ANIMATION"
    action["insomniac_source_moby_tuid"] = f"0x{asset.tuid:016X}"
    action["insomniac_source_moby_name"] = asset.name
    action["insomniac_animation_index"] = clip_index
    action["insomniac_animation_name"] = clip.name


def _write_action_curves(
    channelbag: bpy.types.ActionChannelbag,
    group: bpy.types.ActionGroup | None,
    group_name: str,
    data_path: str,
    frames: np.ndarray,
    values: np.ndarray,
    defaults: tuple[float, ...],
    default_mask: np.ndarray,
    constant_mask: np.ndarray,
) -> bpy.types.ActionGroup | None:
    for component, _default in enumerate(defaults):
        component_values = values[:, component]
        if default_mask[component]:
            continue
        if constant_mask[component]:
            component_frames = frames[:1]
            component_values = component_values[:1]
        else:
            component_frames = frames
        if group is None:
            group = channelbag.groups.new(group_name)
        coordinates = np.column_stack((component_frames, component_values))
        interpolation = np.full(
            len(component_frames),
            _LINEAR_INTERPOLATION,
            dtype=np.int32,
        )
        fcurve = channelbag.fcurves.new(data_path, index=component)
        fcurve.group = group
        fcurve.keyframe_points.add(len(component_frames))
        fcurve.keyframe_points.foreach_set("co", coordinates.reshape(-1))
        fcurve.keyframe_points.foreach_set(
            "interpolation",
            interpolation,
        )
        fcurve.update()
    return group


def _bone_order(skeleton: Skeleton) -> list[int]:
    order: list[int] = []
    visited: set[int] = set()

    def visit(index: int) -> None:
        if index in visited:
            return
        bone = skeleton.bones[index]
        if bone.has_parent(index, skeleton.bone_count):
            visit(bone.parent_index)
        visited.add(index)
        order.append(index)

    for index in range(skeleton.bone_count):
        visit(index)
    return order


def _required_bone_order(
    skeleton: Skeleton,
    order: list[int],
    animated_bones: np.ndarray,
) -> list[int]:
    required = set(np.flatnonzero(animated_bones).tolist())
    pending = list(required)
    while pending:
        index = pending.pop()
        bone = skeleton.bones[index]
        if (
            bone.has_parent(index, skeleton.bone_count)
            and bone.parent_index not in required
        ):
            required.add(bone.parent_index)
            pending.append(bone.parent_index)
    return [index for index in order if index in required]


def _create_instance_object(
    collection: bpy.types.Collection,
    instance: PlacedInstance,
    mesh: bpy.types.Mesh,
    root: bpy.types.Object,
    *,
    kind: str,
) -> bpy.types.Object:
    name = _safe_name(instance.name) or f"{kind}_{instance.instance_tuid:016X}"
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.parent = root
    obj.matrix_local = Matrix(np.asarray(instance.matrix, dtype=np.float64).tolist())
    obj["insomniac_asset_tuid"] = f"0x{instance.asset_tuid:016X}"
    obj["insomniac_instance_tuid"] = f"0x{instance.instance_tuid:016X}"
    obj["insomniac_asset_kind"] = kind
    if instance.group:
        obj["insomniac_group"] = instance.group
    if instance.lightmap_index is not None:
        obj["insomniac_lightmap_index"] = instance.lightmap_index
    return obj


def _bone_name(index: int) -> str:
    return f"bone_{index:03d}"


def _stable_color(value: int) -> tuple[float, float, float]:
    return (
        0.2 + ((value >> 0) & 0xFF) / 510.0,
        0.2 + ((value >> 8) & 0xFF) / 510.0,
        0.2 + ((value >> 16) & 0xFF) / 510.0,
    )


def _safe_name(value: str) -> str:
    leaf = value.replace("\\", "/").rstrip("/").split("/")[-1]
    cleaned = re.sub(r"[^A-Za-z0-9_. -]+", "_", leaf).strip(" .")
    return cleaned[:63] or "InsomniacAsset"


class PureName(str):
    def __new__(cls, value: str):
        return super().__new__(cls, _safe_name(value))
