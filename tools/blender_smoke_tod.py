from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _arguments() -> argparse.Namespace:
    args = sys.argv
    args = args[args.index("--") + 1 :] if "--" in args else []
    parser = argparse.ArgumentParser(
        description="Smoke-test a Tools of Destruction Blender import"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--kind",
        choices=("moby", "tie", "shrub", "foliage", "level"),
        required=True,
    )
    parser.add_argument("--tuid", type=lambda value: int(value, 0))
    parser.add_argument("--materials", action="store_true")
    parser.add_argument("--animations", action="store_true")
    return parser.parse_args(args)


def _clear_scene(bpy) -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in tuple(bpy.data.collections):
        if collection.name != "Collection":
            bpy.data.collections.remove(collection)


def main() -> None:
    args = _arguments()
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    import bpy

    from insomniac_asset_io.blender.importer import (
        AssetImportOptions,
        LevelImportOptions,
        import_level,
        import_moby,
        import_static_asset,
    )
    from insomniac_asset_io.core.source import GameSource
    from insomniac_asset_io.games.ratchet_tod.reader import (
        RatchetTodReader,
    )
    from insomniac_asset_io.games.resistance_2.formats import AssetKind

    args.output.parent.mkdir(parents=True, exist_ok=True)
    _clear_scene(bpy)
    with GameSource.open(args.source) as source:
        reader = RatchetTodReader(source)
        if args.kind == "moby":
            if args.tuid is None:
                raise RuntimeError("--tuid is required for a Moby import")
            result = import_moby(
                bpy.context,
                reader,
                args.tuid,
                AssetImportOptions(
                    materials=args.materials,
                    animations=args.animations,
                ),
                source_path=str(args.source),
            )
        elif args.kind in {"tie", "shrub", "foliage"}:
            if args.tuid is None:
                raise RuntimeError(
                    f"--tuid is required for a {args.kind} import"
                )
            kind = {
                "tie": AssetKind.TIE,
                "shrub": AssetKind.SHRUB,
                "foliage": AssetKind.FOLIAGE,
            }[args.kind]
            result = import_static_asset(
                bpy.context,
                reader,
                args.tuid,
                AssetImportOptions(
                    materials=args.materials,
                    animations=False,
                ),
                kind=kind,
                source_path=str(args.source),
            )
        else:
            result = import_level(
                bpy.context,
                reader,
                LevelImportOptions(
                    materials=args.materials,
                    terrain=True,
                    ties=True,
                    vegetation=True,
                    gameplay=True,
                ),
                source_path=str(args.source),
            )
            zones = reader.read_zones()
            expected_foliage = sum(
                len(zone.foliage_instances)
                for zone in zones
            )
            imported_foliage = sum(
                obj.get("insomniac_asset_kind") == "FOLIAGE"
                for obj in bpy.data.objects
            )
            if imported_foliage != expected_foliage:
                raise RuntimeError(
                    "Foliage placement mismatch: expected "
                    f"{expected_foliage}, imported {imported_foliage}"
                )
            expected_shrubs = sum(
                len(zone.shrub_instances)
                for zone in zones
            )
            imported_shrubs = sum(
                obj.get("insomniac_asset_kind") == "SHRUB"
                for obj in bpy.data.objects
            )
            if imported_shrubs != expected_shrubs:
                raise RuntimeError(
                    "Shrub placement mismatch: expected "
                    f"{expected_shrubs}, imported {imported_shrubs}"
                )

    if result.root.get("insomniac_game") != "RATCHET_TOD":
        raise RuntimeError("Imported root does not identify the TOD adapter")
    if result.meshes < 1:
        raise RuntimeError("Import produced no meshes")
    if args.kind == "moby" and args.animations and result.actions < 1:
        raise RuntimeError("Animated Moby import produced no Actions")
    if args.materials and result.materials < 1:
        raise RuntimeError("Material import produced no materials")

    if args.materials:
        bpy.ops.file.pack_all()
    save_result = bpy.ops.wm.save_as_mainfile(
        filepath=str(args.output.resolve())
    )
    if "FINISHED" not in save_result:
        raise RuntimeError(f"Could not save {args.output}")
    print(
        json.dumps(
            {
                "kind": args.kind,
                "output": str(args.output.resolve()),
                "objects": result.objects,
                "meshes": result.meshes,
                "materials": result.materials,
                "images": result.images,
                "actions": result.actions,
                "warnings": result.warnings or [],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
