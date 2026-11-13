from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from insomniac_asset_io.blender import register
from insomniac_asset_io.blender.importer import (
    AssetImportOptions,
    import_moby,
)
from insomniac_asset_io.blender.operators import (
    _catalog,
    _inspect_source,
)
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_burning_skies.reader import (
    BurningSkiesReader,
)

DEFAULT_MODELS = (
    "AP_BASE_FAxe.smf",
    "AP_BASE_AMShip.smf",
    "AP_BASE_VTOL.smf",
    "GE_OB_AdvHybridBody01.smf",
    "CH_BASE_Hybrid.smf",
    "CH_PHYS_Hybrid.smf",
    "CH_PHYS_ADrone.smf",
    "RS_OB_Car_C.smf",
    "GE_OB_LOD_TEST.smf",
)


def _arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(
        description="Import Burning Skies models inside Blender"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--render", type=Path)
    return parser.parse_args(values)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


class _Reporter:
    def __init__(self) -> None:
        self.messages: list[tuple[set[str], str]] = []

    def report(self, levels: set[str], message: str) -> None:
        self.messages.append((levels, message))


def _render_preview(mesh_object: bpy.types.Object, path: Path) -> None:
    bpy.context.view_layer.update()
    corners = [
        mesh_object.matrix_world @ Vector(corner) for corner in mesh_object.bound_box
    ]
    minimum = Vector(
        tuple(min(corner[axis] for corner in corners) for axis in range(3))
    )
    maximum = Vector(
        tuple(max(corner[axis] for corner in corners) for axis in range(3))
    )
    center = (minimum + maximum) * 0.5
    extent = max(maximum - minimum)
    _require(extent > 0.0, "Cannot frame an empty model")

    camera_data = bpy.data.cameras.new("Preview Camera")
    camera = bpy.data.objects.new("Preview Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    direction = Vector((1.25, -1.75, 0.9)).normalized()
    camera.location = center + direction * extent * 2.2
    camera.rotation_euler = (
        (center - camera.location)
        .to_track_quat(
            "-Z",
            "Y",
        )
        .to_euler()
    )
    camera_data.lens = 55
    bpy.context.scene.camera = camera

    key_data = bpy.data.lights.new("Preview Key", "AREA")
    key_data.energy = max(500.0, extent * extent * 75.0)
    key_data.shape = "DISK"
    key_data.size = extent
    key = bpy.data.objects.new("Preview Key", key_data)
    bpy.context.scene.collection.objects.link(key)
    key.location = center + Vector((-1.0, -1.0, 1.5)) * extent
    key.rotation_euler = (
        (center - key.location)
        .to_track_quat(
            "-Z",
            "Y",
        )
        .to_euler()
    )

    world = bpy.context.scene.world
    world.color = (0.03, 0.03, 0.03)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 640
    scene.render.resolution_y = 640
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    path.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(path.resolve())
    bpy.ops.render.render(write_still=True)


def main() -> None:
    args = _arguments()
    register()
    for item in tuple(bpy.data.objects):
        bpy.data.objects.remove(item, do_unlink=True)
    settings = bpy.context.scene.insomniac_asset_io
    settings.game = "RESISTANCE_BURNING_SKIES"
    settings.source_path = str(args.source.resolve())
    inspection = _Reporter()
    inspection_result = _inspect_source(inspection, bpy.context)
    _require(
        inspection_result == {"FINISHED"},
        f"Source inspection failed: {inspection.messages}",
    )
    catalog = _catalog(bpy.context)
    _require(
        len(catalog.assets) == 821,
        f"Expected 821 catalog models, found {len(catalog.assets)}",
    )
    _require(
        bool(settings.asset_tuid),
        "Source inspection did not select the first model",
    )

    reports = []
    imported_mesh_objects: list[bpy.types.Object] = []
    with GameSource.open(args.source) as source:
        reader = BurningSkiesReader(source)
        paths_by_name = {
            Path(path).name.casefold(): path
            for path in source.paths
            if path.casefold().endswith(".smf")
        }
        for requested_name in args.model or DEFAULT_MODELS:
            path = paths_by_name.get(requested_name.casefold())
            _require(path is not None, f"Model is unavailable: {requested_name}")
            tuid = reader.model_id(path)
            source_asset = reader.read_moby(tuid)
            source_meshes = [
                mesh
                for mesh in source_asset.meshes
                if mesh.lod_level == 0 and not mesh.is_damage_permutation
            ]
            expected_vertices = sum(
                len(mesh.positions)
                for mesh in source_meshes
            )
            source_faces: set[tuple[int, int, int]] = set()
            vertex_offset = 0
            for source_mesh in source_meshes:
                for face in source_mesh.indices.reshape((-1, 3)):
                    source_faces.add(
                        tuple(sorted(int(index) + vertex_offset for index in face))
                    )
                vertex_offset += len(source_mesh.positions)
            expected_polygons = len(source_faces)
            result = import_moby(
                bpy.context,
                reader,
                tuid,
                AssetImportOptions(
                    materials=True,
                    animations=False,
                    convert_axes=True,
                ),
                source_path=str(args.source.resolve()),
            )
            _require(
                math.isclose(
                    result.root.rotation_euler[0],
                    math.radians(reader.axis_conversion_x_degrees),
                    abs_tol=1e-6,
                ),
                f"{requested_name}: incorrect source-axis conversion",
            )
            mesh_objects = [
                item
                for item in result.root.users_collection[0].objects
                if item.type == "MESH"
            ]
            _require(
                len(mesh_objects) == 1,
                f"{requested_name}: expected one combined mesh object",
            )
            mesh = mesh_objects[0].data
            imported_mesh_objects.append(mesh_objects[0])
            expected_bones = (
                source_asset.skeleton.bone_count
                if source_asset.skeleton is not None
                else 0
            )
            armatures = [
                item
                for item in result.root.users_collection[0].objects
                if item.type == "ARMATURE"
            ]
            _require(
                len(mesh.vertices) == expected_vertices,
                f"{requested_name}: expected {expected_vertices} vertices, "
                f"found {len(mesh.vertices)}",
            )
            _require(
                len(mesh.polygons) == expected_polygons,
                f"{requested_name}: expected {expected_polygons} polygons, "
                f"found {len(mesh.polygons)}",
            )
            _require(
                len(mesh.uv_layers) == int(bool(expected_vertices)),
                f"{requested_name}: unexpected UV layer count",
            )
            _require(
                len(armatures) == int(bool(expected_bones)),
                f"{requested_name}: unexpected armature count",
            )
            if expected_bones:
                _require(
                    len(armatures[0].data.bones) == expected_bones,
                    f"{requested_name}: expected {expected_bones} bones, "
                    f"found {len(armatures[0].data.bones)}",
                )
            reports.append(
                {
                    "bones": expected_bones,
                    "model": path,
                    "source_meshes": len(source_meshes),
                    "source_meshes_all_lods": len(source_asset.meshes),
                    "vertices": len(mesh.vertices),
                    "polygons": len(mesh.polygons),
                    "materials": result.materials,
                    "images": result.images,
                    "warnings": result.warnings or [],
                }
            )

    report = {
        "catalog_models": len(catalog.assets),
        "source_summary": settings.source_summary,
        "source": str(args.source.resolve()),
        "models": reports,
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    print(f"BURNING_SKIES_BLENDER_SMOKE={text}")
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    if args.render is not None:
        _require(
            len(imported_mesh_objects) == 1,
            "--render requires exactly one --model",
        )
        _render_preview(imported_mesh_objects[0], args.render)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(args.output.resolve()))


if __name__ == "__main__":
    main()
