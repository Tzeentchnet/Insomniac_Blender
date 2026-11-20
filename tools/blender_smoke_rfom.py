from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _arguments() -> argparse.Namespace:
    args = sys.argv
    args = args[args.index("--") + 1 :] if "--" in args else []
    parser = argparse.ArgumentParser(
        description="Smoke-test a Resistance: Fall of Man Blender import"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--kind",
        choices=("moby", "tie", "level"),
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
        import_tie,
    )
    from insomniac_asset_io.core.source import GameSource
    from insomniac_asset_io.games.resistance_fom.reader import (
        ResistanceFomReader,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    _clear_scene(bpy)
    with GameSource.open(args.source) as source:
        reader = ResistanceFomReader(source)
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
        elif args.kind == "tie":
            if args.tuid is None:
                raise RuntimeError("--tuid is required for a Tie import")
            result = import_tie(
                bpy.context,
                reader,
                args.tuid,
                AssetImportOptions(
                    materials=args.materials,
                    animations=False,
                ),
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
                    gameplay=True,
                ),
                source_path=str(args.source),
            )

    if result.root.get("insomniac_game") != "RESISTANCE_FOM":
        raise RuntimeError("Imported root does not identify the RFoM adapter")
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
