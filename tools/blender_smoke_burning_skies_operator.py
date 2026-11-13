from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import bpy


def _arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(
        description=(
            "Import a Burning Skies model through the installed Blender "
            "extension operator"
        )
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--model", default="AP_BASE_VTOL.smf")
    parser.add_argument("--report", type=Path)
    return parser.parse_args(values)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


class _Reporter:
    def __init__(self) -> None:
        self.messages: list[tuple[set[str], str]] = []

    def report(self, levels: set[str], message: str) -> None:
        self.messages.append((levels, message))


def _addon_preferences() -> Any:
    matches = [
        addon.preferences
        for module, addon in bpy.context.preferences.addons.items()
        if module.endswith(".insomniac_asset_io")
        and hasattr(addon.preferences, "asset_import_materials")
    ]
    _require(
        len(matches) == 1,
        f"Expected one installed Insomniac extension, found {len(matches)}",
    )
    return matches[0]


def _extension_module(name: str) -> ModuleType:
    module = sys.modules.get(name)
    _require(module is not None, f"Installed extension module is missing: {name}")
    return module


def main() -> None:
    args = _arguments()
    _require(args.source.is_file(), f"Source is missing: {args.source}")

    settings = bpy.context.scene.insomniac_asset_io
    settings.game = "RESISTANCE_BURNING_SKIES"
    settings.source_path = str(args.source.resolve())

    package = type(settings).__module__.rsplit(".", 1)[0]
    operators = _extension_module(f"{package}.operators")
    properties = _extension_module(f"{package}.properties")
    reporter = _Reporter()
    inspection = operators._inspect_source(reporter, bpy.context)
    _require(
        inspection == {"FINISHED"},
        f"Source inspection failed: {reporter.messages}",
    )

    catalog = operators._catalog(bpy.context)
    requested = args.model.casefold()
    asset = next(
        (
            item
            for item in catalog.assets
            if item.source_name is not None
            and Path(item.source_name).name.casefold() == requested
        ),
        None,
    )
    _require(asset is not None, f"Model is unavailable: {args.model}")
    properties.set_asset_selection(settings, asset.identifier)

    preferences = _addon_preferences()
    preferences.asset_import_materials = True
    preferences.asset_import_animations = False
    preferences.asset_convert_axes = True

    for item in tuple(bpy.data.objects):
        bpy.data.objects.remove(item, do_unlink=True)

    result = bpy.ops.insomniac.import_asset()
    _require(result == {"FINISHED"}, f"Import operator returned {result}")
    roots = [
        item
        for item in bpy.data.objects
        if item.get("insomniac_game") == "RESISTANCE_BURNING_SKIES"
    ]
    _require(len(roots) == 1, f"Expected one imported root, found {len(roots)}")
    root = roots[0]
    _require(
        math.isclose(root.rotation_euler[0], 0.0, abs_tol=1e-6),
        "Installed operator applied an incorrect Y-up axis conversion",
    )
    mesh_objects = [
        item for item in root.users_collection[0].objects if item.type == "MESH"
    ]
    _require(
        len(mesh_objects) == 1,
        f"Expected one imported mesh object, found {len(mesh_objects)}",
    )
    mesh = mesh_objects[0].data
    armatures = [
        item for item in root.users_collection[0].objects if item.type == "ARMATURE"
    ]
    linked_images = {
        node.image
        for material in mesh.materials
        if material is not None and material.node_tree is not None
        for node in material.node_tree.nodes
        if node.type == "TEX_IMAGE" and node.image is not None
    }
    if len(mesh.vertices):
        _require(len(mesh.polygons) > 0, "Imported mesh has no polygons")
        _require(len(mesh.materials) > 0, "Imported mesh has no materials")
        _require(len(mesh.uv_layers) == 1, "Imported mesh has no UV layer")
        _require(linked_images, "Imported materials have no linked images")
        _require(not armatures, "Static model unexpectedly imported a rig")
    else:
        _require(
            len(armatures) == 1,
            "Skeleton-only model did not import one armature",
        )
        _require(
            len(armatures[0].data.bones) > 0,
            "Imported armature has no bones",
        )

    report = {
        "bones": len(armatures[0].data.bones) if armatures else 0,
        "catalog_models": len(catalog.assets),
        "images": len(linked_images),
        "last_message": settings.last_message,
        "materials": len(mesh.materials),
        "model": asset.source_name,
        "polygons": len(mesh.polygons),
        "source": str(args.source.resolve()),
        "vertices": len(mesh.vertices),
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    print(f"BURNING_SKIES_OPERATOR_SMOKE={text}")
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
