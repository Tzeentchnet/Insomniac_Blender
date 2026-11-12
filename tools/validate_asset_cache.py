from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import bpy


_DATABLOCK_COLLECTIONS = (
    "actions",
    "armatures",
    "collections",
    "images",
    "materials",
    "meshes",
    "objects",
)


def _arguments() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(
        description="Validate a generated Insomniac Blender asset library"
    )
    parser.add_argument("library", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--expected-assets", type=int)
    parser.add_argument("--expected-mobys", type=int)
    parser.add_argument("--expected-ties", type=int)
    return parser.parse_args(arguments)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _snapshot() -> dict[str, set[bpy.types.ID]]:
    return {
        name: set(getattr(bpy.data, name))
        for name in _DATABLOCK_COLLECTIONS
    }


def _cleanup(snapshot: dict[str, set[bpy.types.ID]]) -> None:
    created: set[bpy.types.ID] = set()
    for name in _DATABLOCK_COLLECTIONS:
        created.update(set(getattr(bpy.data, name)) - snapshot[name])
    if created:
        bpy.data.batch_remove(created)


def _validate_asset(
    library: Path,
    asset: dict[str, Any],
) -> tuple[int, int]:
    path = library / asset["file"]
    _require(path.is_file(), f"Cached asset is missing: {path}")
    snapshot = _snapshot()
    try:
        with bpy.data.libraries.load(
            str(path),
            link=False,
            assets_only=True,
        ) as (available, loaded):
            _require(
                len(available.collections) == 1,
                f"{path} exposes {len(available.collections)} Collection assets",
            )
            loaded.collections = available.collections
        collection = loaded.collections[0]
        _require(collection is not None, f"{path} did not load its Collection")
        _require(collection.asset_data is not None, f"{path} is not marked as an asset")
        _require(
            collection.get("insomniac_asset_tuid") == asset["tuid"],
            f"{path} TUID metadata does not match its manifest",
        )
        _require(
            collection.get("insomniac_asset_kind") == asset["kind"],
            f"{path} kind metadata does not match its manifest",
        )
        _require(
            str(collection.asset_data.catalog_id)
            != "00000000-0000-0000-0000-000000000000",
            f"{path} has no Asset Browser catalog",
        )
        preview = collection.preview
        _require(preview is not None, f"{path} has no Asset Browser preview")
        _require(
            tuple(preview.image_size) == (128, 128),
            f"{path} has an unexpected preview size {tuple(preview.image_size)}",
        )
        _require(len(collection.all_objects) >= 2, f"{path} has no imported geometry")
        game_images = [
            image
            for image in bpy.data.images
            if image.get("insomniac_texture_tuid")
        ]
        _require(
            all(image.packed_file is not None for image in game_images),
            f"{path} has unpacked game textures",
        )
        return len(collection.all_objects), len(game_images)
    finally:
        _cleanup(snapshot)


def main() -> int:
    args = _arguments()
    library = args.library.resolve()
    catalog = library / "blender_assets.cats.txt"
    _require(catalog.is_file(), f"Asset catalog is missing: {catalog}")
    manifests = sorted(library.glob("resistance_2/**/manifest.json"))
    _require(manifests, f"No Resistance 2 cache manifests found under {library}")

    assets: list[dict[str, Any]] = []
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assets.extend(manifest["assets"])

    mobys = sum(asset["kind"] == "MOBY" for asset in assets)
    ties = sum(asset["kind"] == "TIE" for asset in assets)
    if args.expected_assets is not None:
        _require(len(assets) == args.expected_assets, "Unexpected cached asset count")
    if args.expected_mobys is not None:
        _require(mobys == args.expected_mobys, "Unexpected cached Moby count")
    if args.expected_ties is not None:
        _require(ties == args.expected_ties, "Unexpected cached Tie count")

    object_dependencies = 0
    packed_images = 0
    for asset in assets:
        objects, images = _validate_asset(library, asset)
        object_dependencies += objects
        packed_images += images

    report = {
        "library": str(library),
        "manifests": len(manifests),
        "assets": len(assets),
        "mobys": mobys,
        "ties": ties,
        "object_dependencies": object_dependencies,
        "packed_image_dependencies": packed_images,
        "previews": len(assets),
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    print("INSOMNIAC_ASSET_CACHE_VALIDATION=" + text)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
