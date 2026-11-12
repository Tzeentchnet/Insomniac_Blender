from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import traceback

import bpy


def _arguments() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(
        description="Generate an Insomniac Asset IO Blender Asset Browser cache"
    )
    parser.add_argument("--game", required=True)
    parser.add_argument("--iso", type=Path, required=True)
    parser.add_argument("--level", required=True)
    parser.add_argument("--cache-directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--no-materials", action="store_true")
    parser.add_argument("--animations", action="store_true")
    parser.add_argument("--no-convert-axes", action="store_true")
    parser.add_argument("--cycles-materials", action="store_true")
    parser.add_argument("--damage-permutations", action="store_true")
    parser.add_argument("--collision-geometry", action="store_true")
    parser.add_argument("--no-previews", action="store_true")
    parser.add_argument("--asset-limit", type=int, default=0)
    return parser.parse_args(arguments)


def _load_generator():
    extension_root = Path(__file__).resolve().parents[2]
    root_text = str(extension_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from insomniac_asset_io.blender.asset_cache import (
        AssetCacheOptions,
        generate_asset_library,
    )

    return AssetCacheOptions, generate_asset_library


def main() -> int:
    args = _arguments()
    AssetCacheOptions, generate = _load_generator()
    try:
        report = generate(
            bpy.context,
            game_id=args.game,
            iso_path=args.iso,
            level_name=args.level,
            cache_directory=args.cache_directory,
            options=AssetCacheOptions(
                materials=not args.no_materials,
                animations=args.animations,
                convert_axes=not args.no_convert_axes,
                cycles_materials=args.cycles_materials,
                generate_previews=not args.no_previews,
                damage_permutations=args.damage_permutations,
                collision_geometry=args.collision_geometry,
                asset_limit=max(args.asset_limit, 0),
            ),
        )
    except Exception as exc:
        report = {
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(report["traceback"], file=sys.stderr)
        return 1

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("INSOMNIAC_ASSET_CACHE_REPORT=" + json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
