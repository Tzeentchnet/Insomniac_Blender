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
    CollisionMesh,
    CollisionPrimitive,
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

_IMAGE_HAS_ALPHA_KEY = "insomniac_has_alpha"
_LINEAR_INTERPOLATION = (
    bpy.types.Keyframe.bl_rna.properties["interpolation"]
    .enum_items["LINEAR"]
    .value
)


@dataclass(frozen=True, slots=True)
class AssetImportOptions:
    materials: bool = True
    animations: bool = True
    animation_clip_limit: int = 0
    convert_axes: bool = True
    cycles_materials: bool = False
    damage_permutations: bool = False
    collision_geometry: bool = False


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
            image = self.image(
                texture_id,
                lookup_path=shader.texture_lookup_paths[0],
            )
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

        self._add_alpha(material, principled, node, shader)

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
            image = self.image(
                texture_id,
                role="normal",
                lookup_path=shader.texture_lookup_paths[1],
            )
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
        lookup_path = shader.texture_lookup_paths[2]
        try:
            texture = self.reader.read_texture(
                texture_id,
                lookup_path=lookup_path,
            )
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

        image = self.image(
            texture_id,
            role="packed",
            lookup_path=lookup_path,
        )
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

    def release_decoded_textures(self) -> None:
        # Decoded RGBA arrays are only needed while images are built and
        # while the alpha and emissive checks run. After that, the Blender
        # images hold the pixels, so free the reader's texture cache. It
        # otherwise keeps every texture in memory for the reader's lifetime,
        # which can reach gigabytes on a level import.
        # Trade-off: a reader shared across imports, such as the asset cache
        # build, will decode a texture again if a later import needs it and
        # its Blender image no longer exists.
        self.reader.clear_texture_cache()

    def image(
        self,
        texture_id: int,
        *,
        role: str = "color",
        lookup_path: str | None = None,
    ) -> bpy.types.Image:
        key = (texture_id, role)
        cached = self.images.get(key)
        if cached is not None:
            return cached
        suffixes = {
            "color": "",
            "normal": "_Normal",
            "packed": "_Packed",
        }
        suffix = suffixes.get(role, f"_{role.title()}")
        name = f"R2_Texture_{texture_id:08X}{suffix}"
        image = bpy.data.images.get(name)
        if image is not None and _IMAGE_HAS_ALPHA_KEY in image:
            # Built by an earlier import: reuse it without decoding again.
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
            )
            # Set the colorspace before writing and packing the pixels.
            # Changing it on a packed image discards the buffer and makes
            # Blender reload it from the packed PNG.
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
        else:
            self._apply_colorspace(image, role)
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
        # Whether the source texture's alpha is not fully opaque.
        image[_IMAGE_HAS_ALPHA_KEY] = bool(
            np.any(texture.rgba[..., 3] < 255)
        )
        self.images[key] = image
        return image

    @staticmethod
    def _apply_colorspace(image: bpy.types.Image, role: str) -> None:
        if image.alpha_mode != "CHANNEL_PACKED":
            image.alpha_mode = "CHANNEL_PACKED"
        if role == "color":
            return
        # Only assign when it changes; every assignment frees the buffer.
        if image.colorspace_settings.name != "Non-Color":
            image.colorspace_settings.name = "Non-Color"


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
    # All materials are built; see _MaterialFactory.release_decoded_textures.
    materials.release_decoded_textures()
    base_meshes = [
        mesh for mesh in asset.meshes if not mesh.is_damage_permutation
    ]
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
    )

    optional_object_count = 0
    damage_meshes = [
        mesh for mesh in asset.meshes if mesh.is_damage_permutation
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
        )

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
        objects=(3 if armature else 2) + optional_object_count,
        meshes=1 + optional_object_count,
        materials=len(materials.materials),
        images=len(materials.images),
        actions=action_count,
        warnings=warnings,
    )


def _bind_moby_mesh(
    mesh_object: bpy.types.Object,
    combined: _CombinedMesh,
    root: bpy.types.Object,
    armature: bpy.types.Object | None,
    skeleton: Skeleton | None,
) -> None:
    if armature is None or skeleton is None:
        mesh_object.parent = root
        return
    mesh_object.parent = armature
    modifier = mesh_object.modifiers.new(
        name="Resistance 2 Armature",
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
    # All materials are built; see _MaterialFactory.release_decoded_textures.
    materials.release_decoded_textures()
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
                            (
                                geometry
                                for geometry in moby.meshes
                                if not geometry.is_damage_permutation
                            ),
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

    # Level materials are built lazily while terrain, ties and mobys are
    # placed, so release only after the last one.
    # See _MaterialFactory.release_decoded_textures.
    materials.release_decoded_textures()
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
        action.use_fake_user = True
        action["insomniac_frame_rate"] = clip.frame_rate
        action["insomniac_looping"] = clip.looping
        action["insomniac_additive_source"] = clip.additive
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
        absolute: list[Matrix] = [Matrix.Identity(4)] * skeleton.bone_count
        blender_pose: list[Matrix] = [Matrix.Identity(4)] * skeleton.bone_count
        for frame_index in range(frame_count):
            unscaled_absolute: list[Matrix | None] = (
                [None] * skeleton.bone_count
            )
            for bone_index in order:
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

        for bone_index, pose_bone in enumerate(pose_bones):
            sample_count = (
                frame_count if clip.animated_bones[bone_index] else 1
            )
            if not sample_count:
                continue
            group = channelbag.groups.new(pose_bone.name)
            _write_action_curves(
                channelbag,
                group,
                pose_bone.path_from_id("location"),
                frames[:sample_count],
                locations[:sample_count, bone_index],
            )
            _write_action_curves(
                channelbag,
                group,
                pose_bone.path_from_id("rotation_quaternion"),
                frames[:sample_count],
                rotations[:sample_count, bone_index],
            )
            _write_action_curves(
                channelbag,
                group,
                pose_bone.path_from_id("scale"),
                frames[:sample_count],
                scales[:sample_count, bone_index],
            )
        action.frame_range = (float(frames[0]), float(frames[-1]))
    armature.animation_data.action = None
    for pose_bone in armature.pose.bones:
        pose_bone.matrix_basis.identity()
        pose_bone.select = True
    context.scene.frame_set(original_frame)
    context.view_layer.update()
    return len(clips)


def _write_action_curves(
    channelbag: bpy.types.ActionChannelbag,
    group: bpy.types.ActionGroup,
    data_path: str,
    frames: np.ndarray,
    values: np.ndarray,
) -> None:
    coordinates = np.empty((len(frames), 2), dtype=np.float64)
    coordinates[:, 0] = frames
    interpolation = np.full(
        len(frames),
        _LINEAR_INTERPOLATION,
        dtype=np.int32,
    )
    for component in range(values.shape[1]):
        coordinates[:, 1] = values[:, component]
        fcurve = channelbag.fcurves.new(data_path, index=component)
        fcurve.group = group
        fcurve.keyframe_points.add(len(frames))
        fcurve.keyframe_points.foreach_set("co", coordinates.reshape(-1))
        fcurve.keyframe_points.foreach_set(
            "interpolation",
            interpolation,
        )
        fcurve.update()


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
