from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import re
from typing import Iterable

import bpy
from mathutils import Matrix, Quaternion, Vector
import numpy as np

from ..core.errors import InsomniacError
from ..games.resistance_2.formats import (
    DecodedClip,
    MeshGeometry,
    MobyAsset,
    PlacedInstance,
    ShaderInfo,
    Skeleton,
)
from ..games.resistance_2.materials import (
    EMISSIVE_CHANNEL_NAME,
    has_emissive_mask,
    reconstruct_normal_map,
)
from ..games.resistance_2.reader import Resistance2Reader


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AssetImportOptions:
    materials: bool = True
    animations: bool = True
    animation_clip_limit: int = 0
    convert_axes: bool = True
    cycles_materials: bool = False


@dataclass(frozen=True, slots=True)
class LevelImportOptions:
    materials: bool = True
    terrain: bool = True
    ties: bool = True
    gameplay: bool = True
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
        self.materials: dict[int, bpy.types.Material] = {}
        self.images: dict[tuple[int, str], bpy.types.Image] = {}

    def material(self, shader_tuid: int) -> bpy.types.Material:
        cached = self.materials.get(shader_tuid)
        if cached is not None:
            return cached
        name = f"R2_Shader_{shader_tuid:016X}"
        material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        material.use_nodes = True
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
                    self._add_diffuse(material, principled, shader)
                    if self.cycles_materials:
                        self._add_normal(material, principled, shader)
                    self._add_packed(material, principled, shader)
        material["insomniac_cycles_material"] = self.cycles_materials
        self.materials[shader_tuid] = material
        return material

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
        texture_names = ("diffuse", "normal", "packed", "detail")
        texture_ids = (
            shader.albedo_texture_id,
            shader.normal_texture_id,
            shader.expensive_texture_id,
            shader.detail_texture_id,
        )
        for texture_name, texture_id, texture_path in zip(
            texture_names,
            texture_ids,
            shader.texture_lookup_paths,
        ):
            if texture_id is not None:
                material[f"insomniac_{texture_name}_texture_id"] = (
                    f"0x{texture_id:08X}"
                )
            if texture_path is not None:
                material[f"insomniac_{texture_name}_texture_path"] = (
                    texture_path
                )

    def _apply_base_color_factor(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        shader: ShaderInfo,
    ) -> None:
        factor = self._base_color_factor(shader)
        material["insomniac_base_color_factor"] = list(factor)
        valid = self._has_base_color_factor(shader)
        material["insomniac_base_color_factor_valid"] = valid
        if self.cycles_materials and not valid:
            self._warn(
                f"Shader 0x{shader.tuid:016X} has an unsupported, "
                "non-finite, or out-of-range base color factor; the Cycles "
                "material uses neutral white"
            )
        if not self.cycles_materials:
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
    ) -> None:
        texture_id = shader.albedo_texture_id
        if texture_id is None:
            return
        try:
            texture = self.reader.read_texture(texture_id)
            image = self.image(texture_id)
        except InsomniacError as exc:
            self._warn(
                f"Shader 0x{shader.tuid:016X} diffuse fallback: {exc}"
            )
            return
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.name = f"Diffuse_{texture_id:08X}"
        node.label = (
            "Resistance 2 source placeholder: default pink"
            if shader.source_placeholder_kind == "DEFAULT_PINK"
            else "Resistance 2 diffuse"
        )
        node.image = image
        color_output = node.outputs["Color"]
        factor = self._base_color_factor(shader)
        if self.cycles_materials and factor[:3] != (1.0, 1.0, 1.0):
            tint = material.node_tree.nodes.new("ShaderNodeVectorMath")
            tint.name = f"Base_Color_Tint_{shader.tuid:016X}"
            tint.label = "Resistance 2 base color multiplier"
            tint.operation = "MULTIPLY"
            tint.inputs[1].default_value = factor[:3]
            material.node_tree.links.new(color_output, tint.inputs[0])
            color_output = tint.outputs["Vector"]
        material.node_tree.links.new(
            color_output,
            principled.inputs["Base Color"],
        )

        if self.cycles_materials:
            self._add_alpha(material, principled, node, shader)
        elif np.any(texture.rgba[..., 3] < 255):
            material.node_tree.links.new(
                node.outputs["Alpha"],
                principled.inputs["Alpha"],
            )

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
        if alpha_mode == "MASK":
            clip = material.node_tree.nodes.new("ShaderNodeMath")
            clip.name = f"Alpha_Clip_{shader.tuid:016X}"
            clip.label = "Resistance 2 alpha mask"
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

    def _add_normal(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        shader: ShaderInfo,
    ) -> None:
        texture_id = shader.normal_texture_id
        if texture_id is None:
            return
        try:
            image = self.image(texture_id, role="normal")
        except InsomniacError as exc:
            self._warn(
                f"Shader 0x{shader.tuid:016X} normal map unavailable: {exc}"
            )
            return
        texture_node = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture_node.name = f"Normal_{texture_id:08X}"
        texture_node.label = "Resistance 2 reconstructed normal"
        texture_node.image = image
        normal = material.node_tree.nodes.new("ShaderNodeNormalMap")
        normal.name = f"Normal_Map_{texture_id:08X}"
        normal.label = "Resistance 2 tangent normal"
        normal.space = "TANGENT"
        material.node_tree.links.new(
            texture_node.outputs["Color"],
            normal.inputs["Color"],
        )
        material.node_tree.links.new(
            normal.outputs["Normal"],
            principled.inputs["Normal"],
        )

    def _add_packed(
        self,
        material: bpy.types.Material,
        principled: bpy.types.Node,
        shader: ShaderInfo,
    ) -> None:
        texture_id = shader.expensive_texture_id
        if texture_id is None:
            material["insomniac_emissive"] = False
            return
        material["insomniac_packed_texture_id"] = f"0x{texture_id:08X}"
        material["insomniac_emissive_channel"] = "B"
        try:
            texture = self.reader.read_texture(texture_id)
        except InsomniacError as exc:
            self._warn(
                f"Shader 0x{shader.tuid:016X} emission unavailable: {exc}"
            )
            return
        emissive = has_emissive_mask(texture)
        material["insomniac_emissive"] = emissive
        use_surface_channels = self.cycles_materials and (
            shader.use_glossiness or shader.use_specular
        )
        if not emissive and not use_surface_channels:
            return

        image = self.image(texture_id, role="packed")
        texture_node = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture_node.name = f"Packed_{texture_id:08X}"
        texture_node.label = "Resistance 2 packed material map"
        texture_node.image = image
        separate = material.node_tree.nodes.new("ShaderNodeSeparateColor")
        separate.name = f"Packed_Channels_{texture_id:08X}"
        separate.label = "R=gloss G=specular B=emission"
        separate.mode = "RGB"
        material.node_tree.links.new(
            texture_node.outputs["Color"],
            separate.inputs["Color"],
        )
        if self.cycles_materials and shader.use_glossiness:
            roughness = material.node_tree.nodes.new("ShaderNodeMath")
            roughness.name = f"Roughness_{texture_id:08X}"
            roughness.label = "Resistance 2 inverted red gloss"
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
        if self.cycles_materials and shader.use_specular:
            material.node_tree.links.new(
                separate.outputs["Green"],
                principled.inputs["Specular IOR Level"],
            )
        if emissive:
            material.node_tree.links.new(
                separate.outputs[EMISSIVE_CHANNEL_NAME],
                principled.inputs["Emission Color"],
            )
            principled.inputs["Emission Strength"].default_value = 1.0
            material["insomniac_emissive_strength"] = 1.0

    def _warn(self, message: str) -> None:
        LOGGER.warning(message)
        self.warnings.append(message)

    def image(
        self,
        texture_id: int,
        *,
        role: str = "color",
    ) -> bpy.types.Image:
        key = (texture_id, role)
        cached = self.images.get(key)
        if cached is not None:
            return cached
        texture = self.reader.read_texture(texture_id)
        suffixes = {
            "color": "",
            "normal": "_Normal",
            "packed": "_Packed",
        }
        suffix = suffixes.get(role, f"_{role.title()}")
        name = f"R2_Texture_{texture_id:08X}{suffix}"
        image = bpy.data.images.get(name)
        if image is None:
            image = bpy.data.images.new(
                name,
                width=texture.info.width,
                height=texture.info.height,
                alpha=True,
                float_buffer=False,
            )
            rgba = (
                reconstruct_normal_map(texture)
                if role == "normal"
                else texture.rgba
            )
            pixels = (
                np.flipud(rgba).astype(np.float32).reshape(-1) / 255.0
            )
            image.pixels.foreach_set(pixels)
            image.pack()
            image.update()
        if role != "color":
            image.colorspace_settings.name = "Non-Color"
        image["insomniac_texture_tuid"] = f"0x{texture.info.tuid:016X}"
        image["insomniac_texture_format"] = (
            f"0x{texture.info.format_code:02X}"
        )
        image["insomniac_texture_role"] = role
        self.images[key] = image
        return image


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
    combined = _build_mesh(
        f"{_safe_name(asset.name)}_Mesh",
        asset.meshes,
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
        mesh_object.parent = armature
        modifier = mesh_object.modifiers.new(
            name="Resistance 2 Armature",
            type="ARMATURE",
        )
        modifier.object = armature
        _assign_skin(
            mesh_object,
            asset.skeleton,
            combined.bone_indices,
            combined.bone_weights,
        )
    else:
        mesh_object.parent = root

    action_count = 0
    if (
        options.animations
        and armature is not None
        and asset.animset_hash is not None
    ):
        clips = reader.read_animations(asset)
        if options.animation_clip_limit:
            clips = clips[: options.animation_clip_limit]
        action_count = _create_actions(
            context,
            armature,
            asset.skeleton,
            clips,
        )
    return ImportResult(
        root=root,
        objects=3 if armature else 2,
        meshes=1,
        materials=len(materials.materials),
        images=len(materials.images),
        actions=action_count,
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
    asset = reader.read_tie(tuid)
    name = f"R2_Tie_{tuid:016X}"
    collection = _new_collection(context, name)
    root = _create_root(collection, name, options.convert_axes, source_path)
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
    combined = _build_mesh(f"{name}_Mesh", asset.meshes, blender_materials)
    obj = bpy.data.objects.new(name, combined.mesh)
    collection.objects.link(obj)
    obj.parent = root
    obj["insomniac_asset_tuid"] = f"0x{asset.tuid:016X}"
    obj["insomniac_asset_kind"] = "TIE"
    return ImportResult(
        root=root,
        objects=2,
        meshes=1,
        materials=len(materials.materials),
        images=len(materials.images),
        warnings=warnings,
    )


def import_level(
    context: bpy.types.Context,
    reader: Resistance2Reader,
    options: LevelImportOptions,
    *,
    source_path: str,
) -> ImportResult:
    level_name = PureName(reader.resource_prefix.split("/")[-1] or "Level")
    collection = _new_collection(context, f"R2_{level_name}")
    root = _create_root(
        collection,
        f"R2_{level_name}",
        options.convert_axes,
        source_path,
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
    if options.terrain:
        terrain_collection = _child_collection(collection, "Terrain")
        for zone in zones:
            if not zone.terrain_meshes:
                continue
            geometries = [
                MeshGeometry(
                    shader_index=terrain.shader_index,
                    positions=terrain.positions,
                    uvs=terrain.uvs,
                    indices=terrain.indices,
                )
                for terrain in zone.terrain_meshes
            ]
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
        tie_collection = _child_collection(collection, "Static Props")
        tie_meshes: dict[int, bpy.types.Mesh] = {}
        instances = [
            instance
            for zone in zones
            for instance in zone.tie_instances
        ]
        for instance in instances:
            mesh = tie_meshes.get(instance.asset_tuid)
            if mesh is None:
                try:
                    tie = reader.read_tie(instance.asset_tuid)
                    tie_materials = [
                        materials.material(shader_tuid)
                        for shader_tuid in tie.shader_tuids
                    ]
                    mesh = _build_mesh(
                        f"Tie_{instance.asset_tuid:016X}",
                        tie.meshes,
                        tie_materials,
                    ).mesh
                    tie_meshes[instance.asset_tuid] = mesh
                    result.meshes += 1
                except InsomniacError as exc:
                    message = (
                        f"Tie 0x{instance.asset_tuid:016X} skipped: {exc}"
                    )
                    LOGGER.warning(message)
                    warnings.append(message)
                    continue
            obj = _create_instance_object(
                tie_collection,
                instance,
                mesh,
                root,
                kind="TIE",
            )
            result.objects += 1
            obj["insomniac_zone_instance"] = True

        unsupported_shrubs = sum(
            zone.unsupported_shrub_instances for zone in zones
        )
        unsupported_foliage = sum(
            zone.unsupported_foliage_instances for zone in zones
        )
        if unsupported_shrubs or unsupported_foliage:
            warnings.append(
                f"Skipped {unsupported_shrubs} shrub and "
                f"{unsupported_foliage} foliage placements; their R2 "
                "geometry chains are documented but not implemented yet"
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
                            moby.meshes,
                            moby_materials,
                        ).mesh
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
                _create_instance_object(
                    region_collection,
                    instance,
                    mesh,
                    root,
                    kind="MOBY",
                )
                result.objects += 1

    warnings.extend(reader.warnings)
    result.materials = len(materials.materials)
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
) -> bpy.types.Object:
    root = bpy.data.objects.new(f"{name}_Root", None)
    collection.objects.link(root)
    if convert_axes:
        root.rotation_euler[0] = math.radians(90.0)
    root["insomniac_game"] = "RESISTANCE_2"
    root["insomniac_engine"] = "V2"
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
    face_parts: list[np.ndarray] = []
    material_parts: list[np.ndarray] = []
    bone_index_parts: list[np.ndarray] = []
    bone_weight_parts: list[np.ndarray] = []
    any_skin = any(item.bone_indices is not None for item in geometry_list)
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
        uv_parts.append(np.asarray(geometry.uvs, dtype=np.float32))
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
        bone = armature_data.edit_bones.new(_bone_name(index))
        matrix = Matrix(np.asarray(world, dtype=np.float64).tolist())
        head = matrix.translation
        direction = matrix.to_3x3().col[1].normalized()
        child_index = skeleton.bones[index].child_index
        if 0 <= child_index < skeleton.bone_count and child_index != index:
            child_head = Vector(skeleton.bind_world[child_index, :3, 3])
            length = max((child_head - head).length, 0.01)
        else:
            length = 0.05
        bone.head = head
        bone.tail = head + direction * length
        bone.matrix = matrix
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
) -> int:
    if not clips:
        return 0
    armature.animation_data_create()
    order = _bone_order(skeleton)
    first_action = None
    scene_fps = max(context.scene.render.fps, 1)
    original_frame = context.scene.frame_current
    for clip in clips:
        action = bpy.data.actions.new(
            name=f"{armature.name}_{_safe_name(clip.name)}"
        )
        action.use_fake_user = True
        action["insomniac_frame_rate"] = clip.frame_rate
        action["insomniac_looping"] = clip.looping
        action["insomniac_additive_source"] = clip.additive
        armature.animation_data.action = action
        if first_action is None:
            first_action = action
        for pose_bone in armature.pose.bones:
            pose_bone.rotation_mode = "QUATERNION"
            pose_bone.matrix_basis.identity()

        absolute: list[Matrix] = [Matrix.Identity(4)] * skeleton.bone_count
        for frame_index in range(clip.frame_count):
            frame = 1.0 + frame_index * scene_fps / clip.frame_rate
            context.scene.frame_set(int(frame))
            for bone_index in order:
                translation = Vector(
                    clip.translations[frame_index, bone_index]
                )
                xyzw = clip.rotations_xyzw[frame_index, bone_index]
                rotation = Quaternion(
                    (float(xyzw[3]), float(xyzw[0]), float(xyzw[1]), float(xyzw[2]))
                )
                scale = Vector(clip.scales[frame_index, bone_index])
                local = Matrix.LocRotScale(translation, rotation, scale)
                source_bone = skeleton.bones[bone_index]
                if source_bone.has_parent(bone_index, skeleton.bone_count):
                    absolute[bone_index] = (
                        absolute[source_bone.parent_index] @ local
                    )
                else:
                    absolute[bone_index] = local
                pose_bone = armature.pose.bones[_bone_name(bone_index)]
                pose_bone.matrix = absolute[bone_index]
                if frame_index == 0 or clip.animated_bones[bone_index]:
                    pose_bone.keyframe_insert(
                        data_path="location",
                        frame=frame,
                        group=pose_bone.name,
                    )
                    pose_bone.keyframe_insert(
                        data_path="rotation_quaternion",
                        frame=frame,
                        group=pose_bone.name,
                    )
                    pose_bone.keyframe_insert(
                        data_path="scale",
                        frame=frame,
                        group=pose_bone.name,
                    )
    armature.animation_data.action = first_action
    context.scene.frame_set(original_frame)
    return len(clips)


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
