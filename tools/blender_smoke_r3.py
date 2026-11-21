from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from insomniac_asset_io.blender import register
from insomniac_asset_io.blender.importer import (
    AssetImportOptions,
    LevelImportOptions,
    import_level,
    import_moby,
)
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.formats import AssetKind
from insomniac_asset_io.games.resistance_3.reader import Resistance3Reader


def _arguments() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(
        description="Import one Resistance 3 Moby in Blender"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--tuid", type=lambda value: int(value, 0))
    parser.add_argument("--animations", action="store_true")
    parser.add_argument("--level", action="store_true")
    parser.add_argument("--level-materials", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expect-normal-materials", type=int, default=0)
    parser.add_argument("--expect-gloss-materials", type=int, default=0)
    parser.add_argument(
        "--expect-colored-emission",
        action="append",
        default=[],
        metavar="SHADER:PACKED:ALBEDO",
    )
    parser.add_argument(
        "--expect-disabled-emission",
        action="append",
        default=[],
        metavar="SHADER:PACKED",
    )
    parser.add_argument(
        "--expect-luminance-alpha",
        action="append",
        default=[],
        metavar="SHADER:ALBEDO:FACTOR",
    )
    parser.add_argument("--expect-terrain-lightmaps", type=int, default=0)
    parser.add_argument("--expect-tie-lightmaps", type=int, default=0)
    return parser.parse_args(arguments)


def _validate_surface_materials() -> tuple[int, int, int]:
    normal_count = 0
    gloss_count = 0
    unsupported_normal_count = 0
    for material in bpy.data.materials:
        if not material.get("insomniac_surface_material_channels"):
            continue
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is None:
            raise RuntimeError(f"{material.name} has no Principled BSDF")
        if principled.inputs["Metallic"].is_linked:
            raise RuntimeError(
                f"{material.name} speculatively connects a metallic channel"
            )

        flags = int(material.get("insomniac_material_flags", 0))
        normal_bound = bool(
            material.get("insomniac_normal_texture_bound", False)
        )
        if flags & 0x08 and normal_bound:
            if material.get("insomniac_normal_map_supported") is False:
                unsupported_normal_count += 1
            else:
                texture_id = str(
                    material["insomniac_normal_texture_id"]
                ).removeprefix("0x")
                normal = material.node_tree.nodes.get(
                    f"Normal_Map_{texture_id}"
                )
                if normal is None or not any(
                    link.from_node == normal
                    and link.to_node == principled
                    and link.to_socket == principled.inputs["Normal"]
                    for link in material.node_tree.links
                ):
                    raise RuntimeError(
                        f"{material.name} does not connect its R3 normal map"
                    )
                normal_count += 1

        packed_bound = bool(
            material.get("insomniac_packed_texture_bound", False)
        )
        if flags & 0x04 and packed_bound:
            texture_id = str(
                material["insomniac_packed_texture_id"]
            ).removeprefix("0x")
            roughness = material.node_tree.nodes.get(
                f"Roughness_{texture_id}"
            )
            if roughness is None or not any(
                link.from_node == roughness
                and link.to_node == principled
                and link.to_socket == principled.inputs["Roughness"]
                for link in material.node_tree.links
            ):
                raise RuntimeError(
                    f"{material.name} does not connect packed red gloss"
                )
            gloss_count += 1
    return normal_count, gloss_count, unsupported_normal_count


def _validate_directional_lightmaps(
    expected_terrain: int,
    expected_ties: int,
) -> tuple[int, int]:
    terrain_count = 0
    tie_count = 0
    if expected_terrain:
        terrain_images = [
            image
            for image in bpy.data.images
            if image.get("insomniac_texture_role")
            == "directional_lightmap"
        ]
        terrain_count = len(terrain_images)
        if terrain_count < expected_terrain:
            raise RuntimeError(
                f"Expected at least {expected_terrain} terrain directional "
                f"lightmaps, found {terrain_count}"
            )
        if any(
            image.colorspace_settings.name != "Non-Color"
            or image.packed_file is None
            for image in terrain_images
        ):
            raise RuntimeError(
                "R3 terrain directional lightmaps are not packed Non-Color "
                "images"
            )
        terrain_materials = [
            material
            for material in bpy.data.materials
            if material.get("insomniac_terrain_material")
            and material.get("insomniac_lightmap_index") is not None
        ]
        if not terrain_materials or any(
            material.node_tree.nodes.get(
                "R3_Terrain_Directional_Multiply"
            )
            is None
            or material.node_tree.nodes.get("R3_Terrain_RNM_Factor") is None
            for material in terrain_materials
        ):
            raise RuntimeError(
                "R3 terrain materials do not compose directional lightmaps"
            )
    if expected_ties:
        tie_atlases = [
            image
            for image in bpy.data.images
            if image.get("insomniac_texture_role")
            == "tie_directional_lightmap_atlas"
        ]
        if len(tie_atlases) != 1:
            raise RuntimeError(
                "Expected one directional Tie lightmap atlas, found "
                f"{len(tie_atlases)}"
            )
        tie_count = int(
            tie_atlases[0].get("insomniac_lightmap_count", 0)
        )
        if tie_count < expected_ties:
            raise RuntimeError(
                f"Expected at least {expected_ties} directional Tie "
                f"lightmaps, found {tie_count}"
            )
        if (
            tie_atlases[0].colorspace_settings.name != "Non-Color"
            or tie_atlases[0].packed_file is None
        ):
            raise RuntimeError(
                "R3 directional Tie atlas is not a packed Non-Color image"
            )
        tie_materials = [
            material
            for material in bpy.data.materials
            if material.get("insomniac_vertex_lit_kind") == "TIE"
            and material.get("insomniac_tie_lightmap_material")
        ]
        if not tie_materials or any(
            material.node_tree.nodes.get(
                "R3_TIE_Directional_Lightmap_Atlas"
            )
            is None
            or material.node_tree.nodes.get("R3_TIE_RNM_Factor") is None
            for material in tie_materials
        ):
            raise RuntimeError(
                "R3 Tie materials do not compose the directional atlas"
            )
    return terrain_count, tie_count


def _hex_id(value: str) -> str:
    return value.removeprefix("0x").upper()


def _validate_emission_materials(
    colored_expectations: list[str],
    disabled_expectations: list[str],
) -> tuple[int, int]:
    for expectation in colored_expectations:
        parts = expectation.split(":")
        if len(parts) != 3:
            raise RuntimeError(
                f"Invalid colored emission expectation {expectation!r}"
            )
        shader_id, packed_id, albedo_id = map(_hex_id, parts)
        material_name = f"R3_Shader_{shader_id}"
        material = bpy.data.materials.get(material_name)
        if material is None:
            raise RuntimeError(f"Missing material {material_name}")
        if material.get("insomniac_emissive") is not True:
            raise RuntimeError(f"{material_name} is not emissive")
        if (
            material.get("insomniac_emissive_color_source")
            != "ALBEDO_X_BASE_COLOR_FACTOR_X_PACKED_BLUE"
        ):
            raise RuntimeError(
                f"{material_name} does not use authored emission color"
            )

        principled = material.node_tree.nodes.get("Principled BSDF")
        diffuse = material.node_tree.nodes.get(f"Diffuse_{albedo_id}")
        separate = material.node_tree.nodes.get(
            f"Packed_Channels_{packed_id}"
        )
        emission = material.node_tree.nodes.get(
            f"Emission_Color_{shader_id}"
        )
        if any(
            node is None
            for node in (principled, diffuse, separate, emission)
        ):
            raise RuntimeError(
                f"{material_name} is missing its colored emission graph"
            )
        links = material.node_tree.links
        if not any(
            link.from_node == separate
            and link.from_socket == separate.outputs["Blue"]
            and link.to_node == emission
            and link.to_socket == emission.inputs[1]
            for link in links
        ):
            raise RuntimeError(
                f"{material_name} does not multiply packed blue into emission"
            )
        color_link = next(
            (
                link
                for link in links
                if link.to_node == emission
                and link.to_socket == emission.inputs[0]
            ),
            None,
        )
        if color_link is None:
            raise RuntimeError(
                f"{material_name} does not supply an authored emission color"
            )
        if color_link.from_node != diffuse:
            tint = color_link.from_node
            if not any(
                link.from_node == diffuse
                and link.from_socket == diffuse.outputs["Color"]
                and link.to_node == tint
                and link.to_socket == tint.inputs[0]
                for link in links
            ):
                raise RuntimeError(
                    f"{material_name} emission tint does not use albedo"
                )
        if not any(
            link.from_node == emission
            and link.to_node == principled
            and link.to_socket == principled.inputs["Emission Color"]
            for link in links
        ):
            raise RuntimeError(
                f"{material_name} does not connect colored emission"
            )
        if not math.isclose(
            principled.inputs["Emission Strength"].default_value,
            1.0,
            abs_tol=1e-6,
        ):
            raise RuntimeError(
                f"{material_name} has a speculative emission strength"
            )

    for expectation in disabled_expectations:
        parts = expectation.split(":")
        if len(parts) != 2:
            raise RuntimeError(
                f"Invalid disabled emission expectation {expectation!r}"
            )
        shader_id, packed_id = map(_hex_id, parts)
        material_name = f"R3_Shader_{shader_id}"
        material = bpy.data.materials.get(material_name)
        if material is None:
            raise RuntimeError(f"Missing material {material_name}")
        if material.get("insomniac_emissive_mask") is not True:
            raise RuntimeError(
                f"{material_name} does not retain its packed blue mask"
            )
        if material.get("insomniac_emissive_control_enabled") is not False:
            raise RuntimeError(
                f"{material_name} ignores its emission-disable control"
            )
        if material.get("insomniac_emissive") is not False:
            raise RuntimeError(
                f"{material_name} incorrectly enables emission"
            )
        principled = material.node_tree.nodes.get("Principled BSDF")
        if (
            material.node_tree.nodes.get(f"Packed_{packed_id}") is None
            or principled is None
            or principled.inputs["Emission Color"].is_linked
        ):
            raise RuntimeError(
                f"{material_name} has an invalid disabled-emission graph"
            )
    return len(colored_expectations), len(disabled_expectations)


def _validate_luminance_alpha_materials(expectations: list[str]) -> int:
    for expectation in expectations:
        parts = expectation.split(":")
        if len(parts) != 3:
            raise RuntimeError(
                f"Invalid luminance-alpha expectation {expectation!r}"
            )
        shader_id, albedo_id = map(_hex_id, parts[:2])
        factor = float(parts[2])
        material_name = f"R3_Shader_{shader_id}"
        material = bpy.data.materials.get(material_name)
        if material is None:
            raise RuntimeError(f"Missing material {material_name}")
        if (
            material.get("insomniac_alpha_source")
            != "ALBEDO_LUMINANCE_X_MATERIAL_ALPHA"
            or material.get("insomniac_alpha_approximation")
            != "R3_MODE_1_BC1_LUMINANCE"
        ):
            raise RuntimeError(
                f"{material_name} does not use luminance-derived coverage"
            )

        principled = material.node_tree.nodes.get("Principled BSDF")
        diffuse = material.node_tree.nodes.get(f"Diffuse_{albedo_id}")
        coverage = material.node_tree.nodes.get(
            f"Alpha_Coverage_{shader_id}"
        )
        strength = material.node_tree.nodes.get(
            f"Alpha_Strength_{shader_id}"
        )
        if any(
            node is None
            for node in (principled, diffuse, coverage, strength)
        ):
            raise RuntimeError(
                f"{material_name} is missing its luminance-alpha graph"
            )
        if (
            strength.operation != "MULTIPLY"
            or not strength.use_clamp
            or not math.isclose(
                strength.inputs[1].default_value,
                factor,
                abs_tol=1e-6,
            )
        ):
            raise RuntimeError(
                f"{material_name} has an invalid overlay strength"
            )
        links = material.node_tree.links
        required_links = (
            (
                diffuse,
                diffuse.outputs["Color"],
                coverage,
                coverage.inputs["Color"],
            ),
            (
                coverage,
                coverage.outputs["Val"],
                strength,
                strength.inputs[0],
            ),
            (
                strength,
                strength.outputs["Value"],
                principled,
                principled.inputs["Alpha"],
            ),
        )
        for from_node, from_socket, to_node, to_socket in required_links:
            if not any(
                link.from_node == from_node
                and link.from_socket == from_socket
                and link.to_node == to_node
                and link.to_socket == to_socket
                for link in links
            ):
                raise RuntimeError(
                    f"{material_name} has an invalid luminance-alpha link"
                )
    return len(expectations)


def main() -> int:
    args = _arguments()
    register()
    settings = bpy.context.scene.insomniac_asset_io
    settings.game = "RESISTANCE_3"
    settings.source_path = str(args.source.resolve())

    with GameSource.open(args.source) as source:
        reader = Resistance3Reader(source)
        if args.level:
            result = import_level(
                bpy.context,
                reader,
                LevelImportOptions(materials=args.level_materials),
                source_path=str(args.source.resolve()),
            )
        else:
            tuid = args.tuid
            if tuid is None:
                tuid = reader.asset_pointers(AssetKind.MOBY)[0].tuid
            result = import_moby(
                bpy.context,
                reader,
                tuid,
                AssetImportOptions(
                    materials=True,
                    animations=args.animations,
                ),
                source_path=str(args.source.resolve()),
            )

    if result.root.get("insomniac_game") != "RESISTANCE_3":
        raise RuntimeError("Imported root is not tagged as Resistance 3")
    if result.meshes < 1 or result.materials < 1:
        raise RuntimeError(
            "Resistance 3 import did not create mesh and material data"
        )
    if not args.level and result.images < 1:
        raise RuntimeError("Resistance 3 Moby import created no images")
    normal_count = 0
    gloss_count = 0
    unsupported_normal_count = 0
    colored_emission_count = 0
    disabled_emission_count = 0
    luminance_alpha_count = 0
    terrain_lightmap_count = 0
    terrain_directional_lightmap_count = 0
    tie_lightmap_count = 0
    tie_directional_lightmap_count = 0
    if args.expect_normal_materials or args.expect_gloss_materials:
        (
            normal_count,
            gloss_count,
            unsupported_normal_count,
        ) = _validate_surface_materials()
        if normal_count < args.expect_normal_materials:
            raise RuntimeError(
                f"Expected at least {args.expect_normal_materials} R3 normal "
                f"materials, found {normal_count}"
            )
        if gloss_count < args.expect_gloss_materials:
            raise RuntimeError(
                f"Expected at least {args.expect_gloss_materials} R3 gloss "
                f"materials, found {gloss_count}"
            )
    if args.expect_colored_emission or args.expect_disabled_emission:
        (
            colored_emission_count,
            disabled_emission_count,
        ) = _validate_emission_materials(
            args.expect_colored_emission,
            args.expect_disabled_emission,
        )
    if args.expect_luminance_alpha:
        luminance_alpha_count = _validate_luminance_alpha_materials(
            args.expect_luminance_alpha
        )
    if args.expect_terrain_lightmaps:
        terrain_lightmap_count = sum(
            image.get("insomniac_texture_role") == "lightmap"
            for image in bpy.data.images
        )
        if terrain_lightmap_count < args.expect_terrain_lightmaps:
            raise RuntimeError(
                f"Expected at least {args.expect_terrain_lightmaps} terrain "
                f"lightmaps, found {terrain_lightmap_count}"
            )
        terrain_meshes = [
            obj.data
            for obj in bpy.data.objects
            if obj.type == "MESH" and obj.get("insomniac_zone_tuid")
        ]
        if not terrain_meshes or any(
            "InsomniacVertexColor" not in mesh.color_attributes
            or "LightmapUV" not in mesh.uv_layers
            for mesh in terrain_meshes
        ):
            raise RuntimeError(
                "R3 terrain is missing vertex lighting or lightmap UV data"
            )
    if args.expect_tie_lightmaps:
        atlases = [
            image
            for image in bpy.data.images
            if image.get("insomniac_texture_role")
            == "tie_lightmap_atlas"
        ]
        if len(atlases) != 1:
            raise RuntimeError(
                f"Expected one Tie lightmap atlas, found {len(atlases)}"
            )
        tie_lightmap_count = int(
            atlases[0].get("insomniac_lightmap_count", 0)
        )
        if tie_lightmap_count < args.expect_tie_lightmaps:
            raise RuntimeError(
                f"Expected at least {args.expect_tie_lightmaps} Tie "
                f"lightmaps, found {tie_lightmap_count}"
            )
        lit_ties = [
            obj
            for obj in bpy.data.objects
            if obj.get("insomniac_asset_kind") == "TIE"
            and obj.get("insomniac_lightmap_index") is not None
        ]
        if len(lit_ties) < args.expect_tie_lightmaps or any(
            "insomniac_lightmap_uv_scale" not in obj
            or "insomniac_lightmap_uv_offset" not in obj
            or "LightmapUV" not in obj.data.uv_layers
            or "InsomniacVertexColor" not in obj.data.color_attributes
            for obj in lit_ties
        ):
            raise RuntimeError(
                "R3 Ties are missing baked-light transforms, UV2, or "
                "vertex lighting"
            )
        tie_materials = [
            material
            for material in bpy.data.materials
            if material.get("insomniac_vertex_lit_kind") == "TIE"
            and material.get("insomniac_tie_lightmap_material")
        ]
        if not tie_materials or any(
            material.node_tree.nodes.get("R3_TIE_Lightmap_Atlas") is None
            for material in tie_materials
        ):
            raise RuntimeError(
                "R3 Tie materials do not sample the compact lightmap atlas"
            )
    (
        terrain_directional_lightmap_count,
        tie_directional_lightmap_count,
    ) = _validate_directional_lightmaps(
        args.expect_terrain_lightmaps,
        args.expect_tie_lightmaps,
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        output = str(args.output.resolve())
        if bpy.ops.wm.save_as_mainfile(filepath=output) != {"FINISHED"}:
            raise RuntimeError(f"Could not save {output}")
        if bpy.ops.wm.open_mainfile(filepath=output) != {"FINISHED"}:
            raise RuntimeError(f"Could not reopen {output}")
        _validate_directional_lightmaps(
            args.expect_terrain_lightmaps,
            args.expect_tie_lightmaps,
        )
    print(
        "INSOMNIAC_R3_BLENDER_SMOKE="
        f"objects={result.objects},meshes={result.meshes},"
        f"materials={result.materials},images={result.images},"
        f"actions={result.actions},normal_materials={normal_count},"
        f"gloss_materials={gloss_count},"
        f"unsupported_normals={unsupported_normal_count},"
        f"colored_emission={colored_emission_count},"
        f"disabled_emission={disabled_emission_count},"
        f"luminance_alpha={luminance_alpha_count},"
        f"terrain_lightmaps={terrain_lightmap_count},"
        f"terrain_directional={terrain_directional_lightmap_count},"
        f"tie_lightmaps={tie_lightmap_count},"
        f"tie_directional={tie_directional_lightmap_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
