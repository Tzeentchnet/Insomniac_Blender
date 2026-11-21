from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from insomniac_asset_io import register
from insomniac_asset_io.blender.importer import (
    LevelImportOptions,
    import_level,
)
from insomniac_asset_io.core.binary import IghwFile
from insomniac_asset_io.core.collision import validate_collision_scene
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_2.level_collision import (
    decode_level_collision_resource,
)
from insomniac_asset_io.games.resistance_2.reader import Resistance2Reader
from insomniac_asset_io.games.resistance_3.reader import Resistance3Reader


def _arguments() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(
        description="Import R2/R3 level collision in Blender"
    )
    parser.add_argument("game", choices=("r2", "r3"))
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--resource-suffix",
        help="Import one collision.dat path ending in this suffix",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args(arguments)


class _CollisionReader:
    def __init__(self, source_reader, scene, resource_prefix: str) -> None:
        self.game_id = source_reader.game_id
        self.game_label = source_reader.game_label
        self.asset_prefix = source_reader.asset_prefix
        self.resource_prefix = resource_prefix
        self.source = source_reader.source
        self.warnings = list(source_reader.warnings)
        self._scene = scene

    @staticmethod
    def read_zones() -> list:
        return []

    def read_level_collision(self):
        return self._scene


def _validate_blend(
    *,
    expected_instances: int,
    expected_meshes: int,
) -> None:
    objects = [
        obj
        for obj in bpy.data.objects
        if obj.get("insomniac_collision_geometry")
        and obj.get("insomniac_collision_kind") == "LEVEL_MESH"
    ]
    if len(objects) != expected_instances:
        raise RuntimeError(
            f"Expected {expected_instances} collision objects, found "
            f"{len(objects)}"
        )
    meshes = {obj.data for obj in objects}
    if len(meshes) != expected_meshes:
        raise RuntimeError(
            f"Expected {expected_meshes} shared collision meshes, found "
            f"{len(meshes)}"
        )
    for obj in objects:
        if obj.display_type != "WIRE" or not obj.hide_render:
            raise RuntimeError(f"{obj.name} is not a non-rendering wire helper")
        if "insomniac_collision_metadata" not in obj:
            raise RuntimeError(f"{obj.name} has no collision metadata")
    for mesh in meshes:
        attribute = mesh.attributes.get("insomniac_surface_id")
        if attribute is None or attribute.domain != "FACE":
            raise RuntimeError(
                f"{mesh.name} has no face-domain collision surface IDs"
            )


def main() -> int:
    args = _arguments()
    reader_type = Resistance2Reader if args.game == "r2" else Resistance3Reader
    register()

    with GameSource.open(args.source) as source:
        source_reader = reader_type(source)
        if args.resource_suffix:
            suffix = args.resource_suffix.replace("\\", "/").casefold()
            candidates = [
                path
                for path in source.paths
                if path.casefold().endswith(suffix)
            ]
            if len(candidates) != 1:
                raise RuntimeError(
                    f"Expected one collision resource ending in "
                    f"{args.resource_suffix!r}, found {len(candidates)}"
                )
            resource_path = candidates[0]
            scene = decode_level_collision_resource(
                IghwFile(source.read(resource_path), label=resource_path),
                resource_path=resource_path,
                quantization_markers=(
                    source_reader.collision_quantization_markers
                ),
                warnings=source_reader.warnings,
            )
            resource_prefix = str(Path(resource_path).parent).replace("\\", "/")
        else:
            scene = source_reader.read_level_collision()
            resource_prefix = source_reader.resource_prefix
        validate_collision_scene(scene, label=f"{args.game.upper()} collision")
        reader = _CollisionReader(
            source_reader,
            scene,
            resource_prefix,
        )
        result = import_level(
            bpy.context,
            reader,
            LevelImportOptions(
                materials=False,
                terrain=False,
                ties=False,
                vegetation=False,
                gameplay=False,
                collision_geometry=True,
            ),
            source_path=str(args.source.resolve()),
        )

    if result.objects != len(scene.instances) + 1:
        raise RuntimeError(
            f"Importer reported {result.objects} objects for "
            f"{len(scene.instances)} collision instances"
        )
    if result.meshes != len(scene.meshes):
        raise RuntimeError(
            f"Importer reported {result.meshes} meshes for "
            f"{len(scene.meshes)} collision prototypes"
        )
    _validate_blend(
        expected_instances=len(scene.instances),
        expected_meshes=len(scene.meshes),
    )

    if args.output is not None:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(output))
        bpy.ops.wm.open_mainfile(filepath=str(output))
        _validate_blend(
            expected_instances=len(scene.instances),
            expected_meshes=len(scene.meshes),
        )

    print(
        f"Imported {len(scene.instances)} collision instances using "
        f"{len(scene.meshes)} shared meshes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
