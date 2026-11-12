from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

import bpy


DEFAULT_MOBY_TUID = "0x00897CA26A876DEF"


def _arguments() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(
        description="Exercise Insomniac Asset IO inside Blender"
    )
    parser.add_argument(
        "mode",
        choices=(
            "prepare",
            "catalog",
            "moby",
            "level",
            "verify-moby",
            "verify-level",
        ),
    )
    parser.add_argument("--iso", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--level-name", default="lobby")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--moby-tuid", default=DEFAULT_MOBY_TUID)
    parser.add_argument("--expected-bones", type=int, default=44)
    parser.add_argument("--expected-actions", type=int, default=21)
    parser.add_argument("--expected-catalog-assets", type=int, default=4130)
    parser.add_argument(
        "--expected-catalog-named-assets",
        type=int,
        default=4130,
    )
    parser.add_argument(
        "--expected-catalog-first-id",
        default="MOBY:00897CA26A876DEF",
    )
    parser.add_argument(
        "--expected-catalog-first-name",
        default="Mp Scientist Head",
    )
    parser.add_argument("--expected-prefix", default="built/levels/lobby")
    parser.add_argument("--expected-terrain", type=int, default=0)
    parser.add_argument("--expected-terrain-vertices", type=int, default=0)
    parser.add_argument("--expected-terrain-polygons", type=int, default=0)
    parser.add_argument("--expected-ties", type=int, default=11)
    parser.add_argument("--expected-mobys", type=int, default=7)
    parser.add_argument(
        "--expected-material-texture",
        action="append",
        default=[],
        metavar="SHADER_TUID:TEXTURE_ID",
    )
    parser.add_argument(
        "--expected-emissive",
        action="append",
        default=[],
        metavar="SHADER_TUID:TEXTURE_ID",
    )
    parser.add_argument(
        "--expected-non-emissive",
        action="append",
        default=[],
        metavar="SHADER_TUID:TEXTURE_ID",
    )
    parser.add_argument(
        "--cycles-materials",
        action="store_true",
        help="Enable the default-off Cycles material Addon Preference",
    )
    parser.add_argument(
        "--expected-cycles-material",
        action="append",
        default=[],
        metavar="SHADER_TUID:NORMAL_ID:PACKED_ID",
    )
    parser.add_argument(
        "--expected-cycles-tint",
        action="append",
        default=[],
        metavar="SHADER_TUID",
    )
    parser.add_argument(
        "--expected-blend-mode",
        action="append",
        default=[],
        metavar="SHADER_TUID:MODE",
    )
    parser.add_argument(
        "--expected-standard-material",
        action="append",
        default=[],
        metavar="SHADER_TUID",
    )
    parser.add_argument(
        "--expected-alpha-material",
        action="append",
        default=[],
        metavar="SHADER_TUID",
    )
    parser.add_argument(
        "--expected-source-placeholder",
        action="append",
        default=[],
        metavar="SHADER_TUID",
    )
    parser.add_argument(
        "--cycles-render-material",
        metavar="SHADER_TUID",
        help="Compile and render one imported material with Cycles",
    )
    return parser.parse_args(arguments)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _settings():
    _require(
        hasattr(bpy.context.scene, "insomniac_asset_io"),
        "Insomniac Asset IO is not registered",
    )
    return bpy.context.scene.insomniac_asset_io


def _write_report(path: Path | None, report: dict[str, Any]) -> None:
    text = json.dumps(report, indent=2, sort_keys=True)
    print(f"INSOMNIAC_SMOKE_REPORT={text}")
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")


def _prepare(args: argparse.Namespace) -> dict[str, Any]:
    _require(args.iso is not None, "prepare requires --iso")
    _require(args.cache is not None, "prepare requires --cache")
    settings = _settings()
    preferences = _addon_preferences()
    preferences.resistance_2_iso_path = str(args.iso.resolve())
    preferences.asset_cache_directory = str(args.cache.resolve())
    settings.level_id = args.level_name
    result = bpy.ops.insomniac.prepare_iso()
    _require(result == {"FINISHED"}, f"ISO preparation returned {result}")

    source = Path(bpy.path.abspath(settings.source_path)).resolve()
    _require(source.is_file(), f"Prepared level archive is missing: {source}")
    packed = next(
        (parent for parent in source.parents if parent.name.casefold() == "packed"),
        None,
    )
    _require(packed is not None, f"No packed ancestor for {source}")
    archives = (
        source,
        source.with_name("level_uncached.psarc"),
        packed / "game" / "global_cached.psarc",
        packed / "game" / "global_uncached.psarc",
    )
    for archive in archives:
        _require(archive.is_file(), f"Prepared archive is missing: {archive}")
    return {
        "mode": "prepare",
        "source": str(source),
        "archives": {
            archive.name: {
                "path": str(archive),
                "bytes": archive.stat().st_size,
            }
            for archive in archives
        },
    }


def _addon_preferences() -> Any:
    matches = [
        addon.preferences
        for module, addon in bpy.context.preferences.addons.items()
        if module.endswith(".insomniac_asset_io")
        and hasattr(addon.preferences, "cycles_materials")
    ]
    _require(
        len(matches) == 1,
        f"Expected one Insomniac Addon Preference, found {len(matches)}",
    )
    return matches[0]


def _validate_catalog(args: argparse.Namespace) -> dict[str, Any]:
    preferences = _addon_preferences()
    if args.cache is not None:
        preferences.asset_cache_directory = str(args.cache.resolve())
    if args.iso is not None:
        preferences.resistance_2_iso_path = str(args.iso.resolve())
    settings = _settings()
    module = sys.modules[type(settings).__module__]
    catalog = module.load_catalog(module._cache_directory(bpy.context))
    module.ensure_asset_selection(settings, bpy.context)
    module.clear_asset_filters(settings)
    module._build_asset_items.cache_clear()
    started = time.perf_counter()
    items = module.asset_search_items(None, bpy.context)
    cold_selector_ms = (time.perf_counter() - started) * 1000.0
    started = time.perf_counter()
    for _index in range(100):
        module.asset_search_items(None, bpy.context)
    warm_selector_100_ms = (time.perf_counter() - started) * 1000.0
    _require(
        cold_selector_ms < 1000.0,
        f"Cold selector callback took {cold_selector_ms:.1f} ms",
    )
    _require(
        warm_selector_100_ms < 1000.0,
        f"100 cached selector callbacks took {warm_selector_100_ms:.1f} ms",
    )
    _require(
        len(catalog.assets) == args.expected_catalog_assets,
        f"Expected {args.expected_catalog_assets} catalog assets, "
        f"found {len(catalog.assets)}",
    )
    named_assets = sum(
        asset.source_name is not None for asset in catalog.assets
    )
    _require(
        named_assets == args.expected_catalog_named_assets,
        f"Expected {args.expected_catalog_named_assets} named assets, "
        f"found {named_assets}",
    )
    _require(
        len(items) == 4127,
        f"Expected the unfiltered selector to list all 4,127 deduplicated "
        f"names, found {len(items)}",
    )
    labels = [item[1].casefold() for item in items]
    _require(
        labels == sorted(labels),
        "Unfiltered selector entries are not in alphabetical order",
    )
    first = catalog.assets[0]
    _require(
        first.identifier == args.expected_catalog_first_id,
        f"Unexpected first asset {first.identifier}",
    )
    _require(
        first.display_name == args.expected_catalog_first_name,
        f"Unexpected first asset name {first.display_name!r}",
    )
    _require(
        any(
            item[0] == first.identifier and item[1] == first.display_name
            for item in items
        ),
        "The default selector label is not the compact display name",
    )
    default_state = module.asset_selector_state(settings, bpy.context)
    _require(
        not default_state.filters_active
        and default_state.total_count == default_state.match_count,
        "Cleared filters still report an active filter",
    )
    _require(
        default_state.match_count == 4127,
        f"Expected 4,127 deduplicated names, found "
        f"{default_state.match_count}",
    )

    settings.asset_filter = (
        "entities objects architecture nb_lobby_skyline_1"
    )
    path_state = module.asset_selector_state(settings, bpy.context)
    path_items = module.asset_search_items(None, bpy.context)
    skyline = [
        item
        for item in path_items
        if item[0] == "TIE:EEA01C826A7525C0"
    ]
    _require(path_state.match_count == 1, "Path filter did not find one Tie")
    _require(
        len(skyline) == 1 and skyline[0][1] == "Nb Lobby Skyline 1",
        f"Unexpected path-filtered selector result: {skyline}",
    )
    result = bpy.ops.insomniac.choose_asset(asset="TIE:EEA01C826A7525C0")
    _require(
        result == {"FINISHED"}
        and settings.asset_tuid == "TIE:EEA01C826A7525C0",
        "Choosing a filtered asset did not update the stored TUID",
    )
    settings.asset_filter = ""
    _require(
        settings.asset_tuid == "TIE:EEA01C826A7525C0",
        "Selection shifted after clearing the filter",
    )

    settings.asset_kind_filter = "MOBY"
    moby_state = module.asset_selector_state(settings, bpy.context)
    _require(
        moby_state.match_count > 0
        and all(item[0].startswith("MOBY:") for item in moby_state.items),
        "Kind filter listed assets other than Mobys",
    )
    _require(
        moby_state.selected is not None
        and moby_state.selected.identifier == "TIE:EEA01C826A7525C0"
        and not moby_state.selection_matches_filter,
        "A selection outside the kind filter was not retained and flagged",
    )
    settings.asset_kind_filter = "ALL"
    settings.asset_level_filter = "lobby"
    lobby_state = module.asset_selector_state(settings, bpy.context)
    _require(
        0 < lobby_state.match_count < lobby_state.total_count
        and lobby_state.selection_matches_filter,
        "Level filter did not narrow the selector to lobby assets",
    )
    result = bpy.ops.insomniac.clear_asset_filters()
    _require(
        result == {"FINISHED"}
        and not module.asset_selector_state(
            settings,
            bpy.context,
        ).filters_active,
        "Clear Filters left a filter active",
    )

    settings.asset_filter = "tentacle.entity.irb"
    hidden_duplicate_count = module.asset_selector_state(
        settings,
        bpy.context,
    ).match_count
    preferences.asset_selector_hide_duplicates = False
    shown_duplicate_count = module.asset_selector_state(
        settings,
        bpy.context,
    ).match_count
    _require(
        (hidden_duplicate_count, shown_duplicate_count) == (1, 2),
        "Duplicate-name preference did not change selector results",
    )
    preferences.asset_selector_hide_duplicates = True

    settings.asset_filter = "nb_lobby_skyline_1"
    preferences.asset_selector_show_tuids = True
    preferences.asset_selector_show_owner_levels = True
    detailed_items = module.asset_search_items(None, bpy.context)
    detailed_skyline = next(
        item
        for item in detailed_items
        if item[0] == "TIE:EEA01C826A7525C0"
    )
    _require(
        "Tie 0xEEA01C826A7525C0" in detailed_skyline[1]
        and "lobby" in detailed_skyline[1],
        f"Detailed selector metadata is missing: {detailed_skyline[1]}",
    )
    preferences.asset_selector_show_tuids = False
    preferences.asset_selector_show_owner_levels = False
    module.clear_asset_filters(settings)
    module.set_asset_selection(settings, first.identifier)
    return {
        "kind": "catalog",
        "catalog": str(catalog.path),
        "region": catalog.region,
        "levels": len(catalog.levels),
        "assets": len(catalog.assets),
        "named_assets": named_assets,
        "moby_filter_matches": moby_state.match_count,
        "lobby_filter_matches": lobby_state.match_count,
        "cold_selector_ms": round(cold_selector_ms, 3),
        "warm_selector_100_ms": round(warm_selector_100_ms, 3),
        "default_filtered_assets": default_state.match_count,
        "path_filter_matches": path_state.match_count,
        "hidden_duplicate_matches": hidden_duplicate_count,
        "shown_duplicate_matches": shown_duplicate_count,
        "first_identifier": first.identifier,
        "first_source_name": first.source_name,
        "first_label": first.label,
    }


def _configure_source(source: Path, args: argparse.Namespace) -> Any:
    _require(source.is_file(), f"Level source is missing: {source}")
    preferences = _addon_preferences()
    preferences.cycles_materials = args.cycles_materials
    preferences.asset_import_materials = True
    preferences.asset_import_animations = True
    preferences.asset_animation_clip_limit = 0
    preferences.asset_convert_axes = True
    preferences.level_import_materials = True
    preferences.level_import_terrain = True
    preferences.level_import_ties = True
    preferences.level_import_gameplay = True
    preferences.level_convert_axes = True
    settings = _settings()
    settings.source_path = str(source.resolve())
    settings.level_id = args.level_name
    if source.suffix.casefold() == ".iso":
        _require(args.cache is not None, "ISO sources require --cache")
        preferences.resistance_2_iso_path = str(source.resolve())
        preferences.asset_cache_directory = str(args.cache.resolve())
    return settings


def _clear_startup_scene() -> None:
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in tuple(bpy.data.collections):
        bpy.data.collections.remove(collection)


def _source_roots() -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.data.objects
        if obj.get("insomniac_game") == "RESISTANCE_2"
        and obj.get("insomniac_engine") == "V2"
    ]


def _validate_moby(args: argparse.Namespace) -> dict[str, Any]:
    mesh_objects = [
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH" and obj.get("insomniac_asset_kind") == "MOBY"
    ]
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    roots = _source_roots()
    actions = [
        action
        for action in bpy.data.actions
        if "insomniac_frame_rate" in action
    ]
    images = [
        image
        for image in bpy.data.images
        if "insomniac_texture_tuid" in image
    ]

    _require(len(mesh_objects) == 1, "Expected one imported Moby mesh object")
    _require(len(armatures) == 1, "Expected one imported Moby armature")
    _require(len(roots) == 1, "Expected one imported Moby root")
    _require(
        len(actions) == args.expected_actions,
        f"Expected {args.expected_actions} Actions, found {len(actions)}",
    )

    mesh_object = mesh_objects[0]
    mesh = mesh_object.data
    armature = armatures[0]
    _require(len(mesh.vertices) > 0, "Moby mesh has no vertices")
    _require(len(mesh.polygons) > 0, "Moby mesh has no polygons")
    _require(len(mesh.uv_layers) > 0, "Moby mesh has no UV map")
    _require(len(mesh.materials) > 0, "Moby mesh has no materials")
    _require(len(mesh_object.vertex_groups) > 0, "Moby mesh has no weights")
    _require(
        any(modifier.type == "ARMATURE" for modifier in mesh_object.modifiers),
        "Moby mesh has no armature modifier",
    )
    _require(
        len(armature.data.bones) == args.expected_bones,
        f"Expected {args.expected_bones} bones, "
        f"found {len(armature.data.bones)}",
    )
    _require(images, "Moby import created no decoded images")
    _require(
        all(len(action.slots) == 1 for action in actions),
        "Every imported Action must have exactly one Blender 5.2 slot",
    )
    _require(
        math.isclose(roots[0].rotation_euler.x, math.pi / 2.0, abs_tol=1e-5),
        "Moby root axis conversion is missing",
    )
    emissive_count, non_emissive_count = _validate_emissive_materials(
        args.expected_emissive,
        args.expected_non_emissive,
    )
    placeholder_count = _validate_source_placeholders(
        args.expected_source_placeholder,
    )
    (
        cycles_count,
        tinted_count,
        blend_mode_count,
        standard_count,
        alpha_material_count,
    ) = _validate_cycles_materials(
        args.expected_cycles_material,
        args.expected_cycles_tint,
        args.expected_blend_mode,
        args.expected_standard_material,
        args.expected_alpha_material,
    )
    return {
        "kind": "moby",
        "asset_tuid": mesh_object.get("insomniac_asset_tuid"),
        "objects": len(bpy.data.objects),
        "vertices": len(mesh.vertices),
        "polygons": len(mesh.polygons),
        "uv_layers": len(mesh.uv_layers),
        "materials": len(mesh.materials),
        "images": len(images),
        "bones": len(armature.data.bones),
        "vertex_groups": len(mesh_object.vertex_groups),
        "actions": len(actions),
        "action_slots": sum(len(action.slots) for action in actions),
        "verified_material_textures": _validate_material_textures(
            args.expected_material_texture,
        ),
        "verified_emissive_materials": emissive_count,
        "verified_non_emissive_materials": non_emissive_count,
        "verified_source_placeholders": placeholder_count,
        "verified_cycles_materials": cycles_count,
        "verified_cycles_tints": tinted_count,
        "verified_blend_modes": blend_mode_count,
        "verified_standard_materials": standard_count,
        "verified_alpha_materials": alpha_material_count,
        "cycles_render": _validate_cycles_render(
            args.cycles_render_material,
        ),
        "source": roots[0].get("insomniac_source"),
    }


def _validate_shared_meshes(objects: list[bpy.types.Object]) -> int:
    by_asset: dict[str, list[bpy.types.Object]] = {}
    for obj in objects:
        by_asset.setdefault(str(obj.get("insomniac_asset_tuid")), []).append(obj)
    shared_groups = 0
    for instances in by_asset.values():
        if len(instances) < 2:
            continue
        shared_groups += 1
        first = instances[0].data
        _require(
            all(instance.data == first for instance in instances[1:]),
            "Repeated level placements do not share their mesh datablock",
        )
    return shared_groups


def _validate_material_textures(expectations: list[str]) -> int:
    for expectation in expectations:
        try:
            shader_tuid, texture_id = expectation.split(":", maxsplit=1)
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid material expectation {expectation!r}"
            ) from exc
        shader_tuid = shader_tuid.removeprefix("0x").upper()
        texture_id = texture_id.removeprefix("0x").upper()
        material_name = f"R2_Shader_{shader_tuid}"
        node_name = f"Diffuse_{texture_id}"
        material = bpy.data.materials.get(material_name)
        _require(material is not None, f"Missing material {material_name}")
        node = material.node_tree.nodes.get(node_name)
        _require(node is not None, f"{material_name} has no {node_name} node")
        _require(
            node.bl_idname == "ShaderNodeTexImage",
            f"{node_name} is not an image texture node",
        )
        _require(node.image is not None, f"{node_name} has no image")
        _require(
            node.image.name == f"R2_Texture_{texture_id}",
            f"{node_name} uses unexpected image {node.image.name}",
        )
        principled = material.node_tree.nodes.get("Principled BSDF")
        _require(
            principled is not None,
            f"{material_name} has no Principled BSDF node",
        )
        _require(
            any(
                link.from_node == node
                and link.from_socket == node.outputs["Color"]
                and link.to_node == principled
                and link.to_socket == principled.inputs["Base Color"]
                for link in material.node_tree.links
            ),
            f"{node_name} is not linked to the Principled Base Color",
        )
    return len(expectations)


def _material_expectation(
    expectation: str,
) -> tuple[bpy.types.Material, str, str]:
    try:
        shader_tuid, texture_id = expectation.split(":", maxsplit=1)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid emission expectation {expectation!r}"
        ) from exc
    shader_tuid = shader_tuid.removeprefix("0x").upper()
    texture_id = texture_id.removeprefix("0x").upper()
    material_name = f"R2_Shader_{shader_tuid}"
    material = bpy.data.materials.get(material_name)
    _require(material is not None, f"Missing material {material_name}")
    return material, material_name, texture_id


def _validate_emissive_materials(
    emissive: list[str],
    non_emissive: list[str],
) -> tuple[int, int]:
    for expectation in emissive:
        material, material_name, texture_id = _material_expectation(
            expectation
        )
        _require(
            material.get("insomniac_emissive") is True,
            f"{material_name} was not detected as emissive",
        )
        texture_node = material.node_tree.nodes.get(f"Packed_{texture_id}")
        separate = material.node_tree.nodes.get(
            f"Packed_Channels_{texture_id}"
        )
        principled = material.node_tree.nodes.get("Principled BSDF")
        _require(
            texture_node is not None,
            f"{material_name} has no packed texture node",
        )
        _require(
            separate is not None,
            f"{material_name} has no packed-channel node",
        )
        _require(
            principled is not None,
            f"{material_name} has no Principled BSDF node",
        )
        _require(
            texture_node.image is not None,
            f"{material_name} packed texture node has no image",
        )
        _require(
            texture_node.image.name == f"R2_Texture_{texture_id}_Packed",
            f"{material_name} uses unexpected packed image",
        )
        _require(
            texture_node.image.colorspace_settings.name == "Non-Color",
            f"{material_name} packed image is not Non-Color data",
        )
        _require(
            any(
                link.from_node == separate
                and link.from_socket == separate.outputs["Blue"]
                and link.to_node == principled
                and link.to_socket == principled.inputs["Emission Color"]
                for link in material.node_tree.links
            ),
            f"{material_name} blue channel is not linked to emission",
        )
        _require(
            math.isclose(
                principled.inputs["Emission Strength"].default_value,
                1.0,
                abs_tol=1e-6,
            ),
            f"{material_name} has an unexpected emission strength",
        )

    for expectation in non_emissive:
        material, material_name, texture_id = _material_expectation(
            expectation
        )
        _require(
            material.get("insomniac_emissive") is False,
            f"{material_name} was incorrectly detected as emissive",
        )
        _require(
            material.get("insomniac_cycles_material") is True
            or material.node_tree.nodes.get(f"Packed_{texture_id}") is None,
            f"{material_name} created a packed node in standard mode",
        )
        principled = material.node_tree.nodes.get("Principled BSDF")
        _require(
            principled is not None,
            f"{material_name} has no Principled BSDF node",
        )
        _require(
            not principled.inputs["Emission Color"].is_linked,
            f"{material_name} unexpectedly links Principled emission",
        )
    return len(emissive), len(non_emissive)


def _validate_source_placeholders(expectations: list[str]) -> int:
    for value in expectations:
        shader_tuid = value.removeprefix("0x").upper()
        material_name = f"R2_Shader_{shader_tuid}"
        material = bpy.data.materials.get(material_name)
        _require(material is not None, f"Missing material {material_name}")
        _require(
            material.get("insomniac_source_placeholder") is True,
            f"{material_name} is not marked as a source placeholder",
        )
        _require(
            material.get("insomniac_source_placeholder_kind")
            == "DEFAULT_PINK",
            f"{material_name} has the wrong placeholder kind",
        )
        diffuse = next(
            (
                node
                for node in material.node_tree.nodes
                if node.name.startswith("Diffuse_")
            ),
            None,
        )
        _require(diffuse is not None, f"{material_name} has no diffuse node")
        _require(
            diffuse.label
            == "Resistance 2 source placeholder: default pink",
            f"{material_name} diffuse node is not labeled as a placeholder",
        )
    return len(expectations)


def _validate_cycles_materials(
    expectations: list[str],
    tinted: list[str],
    blend_modes: list[str],
    standard: list[str],
    alpha_materials: list[str],
) -> tuple[int, int, int, int, int]:
    for expectation in expectations:
        try:
            shader_tuid, normal_id, packed_id = expectation.split(
                ":",
                maxsplit=2,
            )
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid Cycles material expectation {expectation!r}"
            ) from exc
        shader_tuid = shader_tuid.removeprefix("0x").upper()
        normal_id = normal_id.removeprefix("0x").upper()
        packed_id = packed_id.removeprefix("0x").upper()
        material_name = f"R2_Shader_{shader_tuid}"
        material = bpy.data.materials.get(material_name)
        _require(material is not None, f"Missing material {material_name}")
        _require(
            material.get("insomniac_cycles_material") is True,
            f"{material_name} is not marked as a Cycles material",
        )
        principled = material.node_tree.nodes.get("Principled BSDF")
        texture_node = material.node_tree.nodes.get(f"Normal_{normal_id}")
        normal_node = material.node_tree.nodes.get(f"Normal_Map_{normal_id}")
        packed = material.node_tree.nodes.get(f"Packed_{packed_id}")
        separate = material.node_tree.nodes.get(
            f"Packed_Channels_{packed_id}"
        )
        roughness = material.node_tree.nodes.get(f"Roughness_{packed_id}")
        for node, description in (
            (principled, "Principled BSDF"),
            (texture_node, "reconstructed normal texture"),
            (normal_node, "Normal Map"),
            (packed, "packed texture"),
            (separate, "packed channel separator"),
            (roughness, "roughness conversion"),
        ):
            _require(
                node is not None,
                f"{material_name} has no {description} node",
            )
        _require(
            texture_node.image is not None
            and texture_node.image.name == f"R2_Texture_{normal_id}_Normal",
            f"{material_name} uses an unexpected reconstructed normal image",
        )
        _require(
            texture_node.image.colorspace_settings.name == "Non-Color",
            f"{material_name} normal image is not Non-Color data",
        )
        _require(
            any(
                link.from_node == normal_node
                and link.to_node == principled
                and link.to_socket == principled.inputs["Normal"]
                for link in material.node_tree.links
            ),
            f"{material_name} does not connect its reconstructed normal",
        )
        _require(
            any(
                link.from_node == separate
                and link.from_socket == separate.outputs["Red"]
                and link.to_node == roughness
                for link in material.node_tree.links
            )
            and any(
                link.from_node == roughness
                and link.to_node == principled
                and link.to_socket == principled.inputs["Roughness"]
                for link in material.node_tree.links
            ),
            f"{material_name} does not convert red gloss to roughness",
        )

    for value in tinted:
        shader_tuid = value.removeprefix("0x").upper()
        material_name = f"R2_Shader_{shader_tuid}"
        material = bpy.data.materials.get(material_name)
        _require(material is not None, f"Missing material {material_name}")
        tint = material.node_tree.nodes.get(
            f"Base_Color_Tint_{shader_tuid}"
        )
        principled = material.node_tree.nodes.get("Principled BSDF")
        _require(tint is not None, f"{material_name} has no tint node")
        _require(
            principled is not None
            and any(
                link.from_node == tint
                and link.to_node == principled
                and link.to_socket == principled.inputs["Base Color"]
                for link in material.node_tree.links
            ),
            f"{material_name} tint is not connected to Base Color",
        )

    for expectation in blend_modes:
        try:
            shader_tuid, mode_text = expectation.split(":", maxsplit=1)
            mode = int(mode_text, 0)
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid blend mode expectation {expectation!r}"
            ) from exc
        shader_tuid = shader_tuid.removeprefix("0x").upper()
        material_name = f"R2_Shader_{shader_tuid}"
        material = bpy.data.materials.get(material_name)
        _require(material is not None, f"Missing material {material_name}")
        _require(
            material.get("insomniac_blend_mode") == mode,
            f"{material_name} has the wrong source blend mode",
        )
        _require(
            material.surface_render_method == "DITHERED",
            f"{material_name} does not use dithered alpha rendering",
        )
        if mode == 4:
            _require(
                material.node_tree.nodes.get(
                    f"Alpha_Clip_{shader_tuid}"
                )
                is not None,
                f"{material_name} has no alpha-clip node",
            )

    for value in standard:
        shader_tuid = value.removeprefix("0x").upper()
        material_name = f"R2_Shader_{shader_tuid}"
        material = bpy.data.materials.get(material_name)
        _require(material is not None, f"Missing material {material_name}")
        _require(
            material.get("insomniac_cycles_material") is False,
            f"{material_name} unexpectedly uses Cycles processing",
        )
        _require(
            not any(
                node.name.startswith(("Normal_", "Roughness_"))
                for node in material.node_tree.nodes
            ),
            f"{material_name} has advanced nodes in standard mode",
        )

    for value in alpha_materials:
        shader_tuid = value.removeprefix("0x").upper()
        material_name = f"R2_Shader_{shader_tuid}"
        material = bpy.data.materials.get(material_name)
        _require(material is not None, f"Missing material {material_name}")
        diffuse = next(
            (
                node
                for node in material.node_tree.nodes
                if node.name.startswith("Diffuse_")
            ),
            None,
        )
        principled = material.node_tree.nodes.get("Principled BSDF")
        _require(diffuse is not None, f"{material_name} has no diffuse node")
        _require(
            principled is not None
            and any(
                link.from_node == diffuse
                and link.from_socket == diffuse.outputs["Alpha"]
                and link.to_node == principled
                and link.to_socket == principled.inputs["Alpha"]
                for link in material.node_tree.links
            ),
            f"{material_name} does not connect albedo alpha",
        )
        _require(
            material.get("insomniac_alpha_mode") == "BLEND",
            f"{material_name} is not classified as blended alpha",
        )

    return (
        len(expectations),
        len(tinted),
        len(blend_modes),
        len(standard),
        len(alpha_materials),
    )


def _validate_cycles_render(shader_tuid: str | None) -> dict[str, Any] | None:
    if shader_tuid is None:
        return None

    shader_tuid = shader_tuid.removeprefix("0x").upper()
    material_name = f"R2_Shader_{shader_tuid}"
    material = bpy.data.materials.get(material_name)
    _require(material is not None, f"Missing material {material_name}")
    _require(
        material.get("insomniac_cycles_material") is True,
        f"{material_name} is not marked as a Cycles material",
    )

    source_scene = bpy.context.scene
    source_engine = source_scene.render.engine
    scene = bpy.data.scenes.new("Insomniac_Cycles_Validation")
    mesh = bpy.data.meshes.new("Insomniac_Cycles_Validation_Mesh")
    camera_data = bpy.data.cameras.new("Insomniac_Cycles_Validation_Camera")
    light_data = bpy.data.lights.new(
        "Insomniac_Cycles_Validation_Light",
        type="AREA",
    )
    plane = bpy.data.objects.new("Insomniac_Cycles_Validation_Plane", mesh)
    camera = bpy.data.objects.new(
        "Insomniac_Cycles_Validation_Camera",
        camera_data,
    )
    light = bpy.data.objects.new(
        "Insomniac_Cycles_Validation_Light",
        light_data,
    )
    try:
        mesh.from_pydata(
            [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0),
             (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)],
            [],
            [(0, 1, 2, 3)],
        )
        uv_layer = mesh.uv_layers.new(name="UVMap")
        uv_by_vertex = (
            (0.0, 0.0),
            (1.0, 0.0),
            (1.0, 1.0),
            (0.0, 1.0),
        )
        for loop in mesh.loops:
            uv_layer.data[loop.index].uv = uv_by_vertex[loop.vertex_index]
        mesh.materials.append(material)

        scene.collection.objects.link(plane)
        scene.collection.objects.link(camera)
        scene.collection.objects.link(light)
        camera.location = (0.0, 0.0, 3.0)
        camera_data.type = "ORTHO"
        camera_data.ortho_scale = 2.5
        scene.camera = camera
        light.location = (0.0, 0.0, 2.0)
        light_data.energy = 500.0
        light_data.shape = "DISK"
        light_data.size = 3.0
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 1
        scene.cycles.use_denoising = False
        scene.render.resolution_x = 16
        scene.render.resolution_y = 16
        scene.render.resolution_percentage = 100

        with tempfile.TemporaryDirectory(
            prefix="insomniac_cycles_validation_",
        ) as temporary:
            render_path = Path(temporary) / "material.png"
            scene.render.filepath = str(render_path)
            scene.render.image_settings.file_format = "PNG"
            result = bpy.ops.render.render(
                scene=scene.name,
                write_still=True,
            )
            _require(result == {"FINISHED"}, f"Cycles render returned {result}")
            _require(
                render_path.is_file() and render_path.stat().st_size > 0,
                "Cycles render did not write a validation image",
            )
            render_bytes = render_path.stat().st_size
        _require(
            source_scene.render.engine == source_engine,
            "Cycles validation changed the imported scene render engine",
        )
        return {
            "material": material_name,
            "engine": scene.render.engine,
            "samples": scene.cycles.samples,
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "render_bytes": render_bytes,
            "source_engine_unchanged": True,
        }
    finally:
        bpy.data.objects.remove(plane, do_unlink=True)
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.objects.remove(light, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        bpy.data.cameras.remove(camera_data)
        bpy.data.lights.remove(light_data)
        bpy.data.scenes.remove(scene)


def _validate_level(args: argparse.Namespace) -> dict[str, Any]:
    roots = [
        obj
        for obj in _source_roots()
        if obj.get("insomniac_level_prefix") == args.expected_prefix
    ]
    terrain = [
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH" and "insomniac_zone_tuid" in obj
    ]
    ties = [
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH"
        and obj.get("insomniac_asset_kind") == "TIE"
        and obj.get("insomniac_zone_instance")
    ]
    mobys = [
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH"
        and obj.get("insomniac_asset_kind") == "MOBY"
    ]
    imported_meshes = terrain + ties + mobys
    images = [
        image
        for image in bpy.data.images
        if "insomniac_texture_tuid" in image
    ]

    _require(
        len(roots) == 1,
        f"Expected one level root for {args.expected_prefix}",
    )
    _require(
        len(terrain) == args.expected_terrain,
        f"Expected {args.expected_terrain} terrain objects, "
        f"found {len(terrain)}",
    )
    terrain_vertices = sum(len(obj.data.vertices) for obj in terrain)
    terrain_polygons = sum(len(obj.data.polygons) for obj in terrain)
    _require(
        terrain_vertices == args.expected_terrain_vertices,
        f"Expected {args.expected_terrain_vertices} terrain vertices, "
        f"found {terrain_vertices}",
    )
    _require(
        terrain_polygons == args.expected_terrain_polygons,
        f"Expected {args.expected_terrain_polygons} terrain polygons, "
        f"found {terrain_polygons}",
    )
    _require(
        len(ties) == args.expected_ties,
        f"Expected {args.expected_ties} Tie placements, found {len(ties)}",
    )
    _require(
        len(mobys) == args.expected_mobys,
        f"Expected {args.expected_mobys} gameplay Moby placements, "
        f"found {len(mobys)}",
    )
    _require(imported_meshes, "Level import created no meshes")
    _require(
        all(len(obj.data.uv_layers) > 0 for obj in imported_meshes),
        "At least one imported level mesh has no UV map",
    )
    _require(
        all(len(obj.data.materials) > 0 for obj in imported_meshes),
        "At least one imported level mesh has no materials",
    )
    _require(images, "Level import created no decoded images")
    _require(
        math.isclose(roots[0].rotation_euler.x, math.pi / 2.0, abs_tol=1e-5),
        "Level root axis conversion is missing",
    )
    mesh_datablocks = {obj.data.as_pointer() for obj in imported_meshes}
    materials = {
        material.as_pointer()
        for obj in imported_meshes
        for material in obj.data.materials
        if material is not None
    }
    emissive_count, non_emissive_count = _validate_emissive_materials(
        args.expected_emissive,
        args.expected_non_emissive,
    )
    placeholder_count = _validate_source_placeholders(
        args.expected_source_placeholder,
    )
    (
        cycles_count,
        tinted_count,
        blend_mode_count,
        standard_count,
        alpha_material_count,
    ) = _validate_cycles_materials(
        args.expected_cycles_material,
        args.expected_cycles_tint,
        args.expected_blend_mode,
        args.expected_standard_material,
        args.expected_alpha_material,
    )
    return {
        "kind": "level",
        "prefix": roots[0].get("insomniac_level_prefix"),
        "objects": len(bpy.data.objects),
        "mesh_datablocks": len(mesh_datablocks),
        "terrain_objects": len(terrain),
        "terrain_vertices": terrain_vertices,
        "terrain_polygons": terrain_polygons,
        "tie_instances": len(ties),
        "gameplay_moby_instances": len(mobys),
        "shared_tie_groups": _validate_shared_meshes(ties),
        "shared_moby_groups": _validate_shared_meshes(mobys),
        "materials": len(materials),
        "images": len(images),
        "verified_material_textures": _validate_material_textures(
            args.expected_material_texture
        ),
        "verified_emissive_materials": emissive_count,
        "verified_non_emissive_materials": non_emissive_count,
        "verified_source_placeholders": placeholder_count,
        "verified_cycles_materials": cycles_count,
        "verified_cycles_tints": tinted_count,
        "verified_blend_modes": blend_mode_count,
        "verified_standard_materials": standard_count,
        "verified_alpha_materials": alpha_material_count,
        "cycles_render": _validate_cycles_render(
            args.cycles_render_material,
        ),
        "source": roots[0].get("insomniac_source"),
    }


def _save(output: Path, report: dict[str, Any]) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    result = bpy.ops.wm.save_as_mainfile(filepath=str(output.resolve()))
    _require(result == {"FINISHED"}, f"Saving returned {result}")
    _require(output.is_file(), f"Blender did not create {output}")
    report["blend_file"] = str(output.resolve())
    report["blend_bytes"] = output.stat().st_size
    return report


def _import_moby(args: argparse.Namespace) -> dict[str, Any]:
    _require(args.source is not None, "moby requires --source")
    _require(args.output is not None, "moby requires --output")
    _clear_startup_scene()
    settings = _configure_source(args.source, args)
    tuid = int(args.moby_tuid.replace("_", ""), 0)
    module = sys.modules[type(settings).__module__]
    module.set_asset_selection(settings, f"MOBY:{tuid:016X}")
    result = bpy.ops.insomniac.import_asset()
    _require(result == {"FINISHED"}, f"Moby import returned {result}")
    return _save(args.output, _validate_moby(args))


def _import_level(args: argparse.Namespace) -> dict[str, Any]:
    _require(args.source is not None, "level requires --source")
    _require(args.output is not None, "level requires --output")
    _clear_startup_scene()
    _configure_source(args.source, args)
    result = bpy.ops.insomniac.import_level()
    _require(result == {"FINISHED"}, f"Level import returned {result}")
    return _save(args.output, _validate_level(args))


def main() -> int:
    args = _arguments()
    if args.mode == "prepare":
        report = _prepare(args)
    elif args.mode == "catalog":
        report = _validate_catalog(args)
    elif args.mode == "moby":
        report = _import_moby(args)
    elif args.mode == "level":
        report = _import_level(args)
    elif args.mode == "verify-moby":
        report = _validate_moby(args)
        report["blend_file"] = bpy.data.filepath
        report["reopened"] = True
    else:
        report = _validate_level(args)
        report["blend_file"] = bpy.data.filepath
        report["reopened"] = True
    _write_report(args.report, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
