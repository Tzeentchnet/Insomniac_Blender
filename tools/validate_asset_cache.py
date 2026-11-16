from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
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
    parser.add_argument("--expected-materials", type=int)
    parser.add_argument("--expected-animations", type=int)
    parser.add_argument(
        "--skip-previews",
        action="store_true",
        help="Do not require generated Collection and Material previews",
    )
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
    *,
    require_previews: bool,
) -> tuple[int, int, int]:
    path = library / asset["file"]
    _require(path.is_file(), f"Cached asset is missing: {path}")
    kind = asset["kind"]
    name = asset["name"]
    snapshot = _snapshot()
    try:
        with bpy.data.libraries.load(
            str(path),
            link=False,
            assets_only=True,
        ) as (available, loaded):
            if kind in {"MOBY", "TIE"}:
                _require(
                    name in available.collections,
                    f"{path} does not expose Collection asset {name!r}",
                )
                loaded.collections = [name]
            elif kind == "SHADER":
                _require(
                    name in available.materials,
                    f"{path} does not expose Material asset {name!r}",
                )
                loaded.materials = [name]
            elif kind == "ANIMATION":
                _require(
                    name in available.actions,
                    f"{path} does not expose Action asset {name!r}",
                )
                loaded.actions = [name]
            else:
                raise RuntimeError(f"{path} has unsupported asset kind {kind!r}")

        if kind in {"MOBY", "TIE"}:
            datablock = loaded.collections[0]
            tuid_property = "insomniac_asset_tuid"
        elif kind == "SHADER":
            datablock = loaded.materials[0]
            tuid_property = "insomniac_shader_tuid"
        else:
            datablock = loaded.actions[0]
            tuid_property = "insomniac_source_moby_tuid"
        _require(datablock is not None, f"{path} did not load {name!r}")
        _require(
            datablock.asset_data is not None,
            f"{path} {name!r} is not marked as an asset",
        )
        _require(
            datablock.get(tuid_property) == asset["tuid"],
            f"{path} TUID metadata does not match its manifest",
        )
        _require(
            datablock.get("insomniac_asset_kind") == kind,
            f"{path} kind metadata does not match its manifest",
        )
        _require(
            str(datablock.asset_data.catalog_id)
            != "00000000-0000-0000-0000-000000000000",
            f"{path} has no Asset Browser catalog",
        )
        preview = datablock.preview
        if require_previews and kind != "ANIMATION":
            _require(preview is not None, f"{path} has no Asset Browser preview")
            _require(
                tuple(preview.image_size) == (128, 128),
                f"{path} has an unexpected preview size "
                f"{tuple(preview.image_size)}",
            )
        if kind in {"MOBY", "TIE"}:
            _require(
                len(datablock.all_objects) >= 2,
                f"{path} has no imported geometry",
            )
            object_count = len(datablock.all_objects)
        else:
            object_count = 0
        if kind == "ANIMATION":
            _require(
                datablock.get("insomniac_frame_rate") is not None,
                f"{path} Action has no source frame rate",
            )
        game_images = [
            image
            for image in bpy.data.images
            if image.get("insomniac_texture_tuid")
        ]
        _require(
            all(image.packed_file is not None for image in game_images),
            f"{path} has unpacked game textures",
        )
        return object_count, len(game_images), int(preview is not None)
    finally:
        _cleanup(snapshot)


def main() -> int:
    args = _arguments()
    library = args.library.resolve()
    catalog = library / "blender_assets.cats.txt"
    _require(catalog.is_file(), f"Asset catalog is missing: {catalog}")
    manifests = sorted(library.glob("*/**/manifest.json"))
    _require(
        manifests,
        f"No Insomniac Asset IO cache manifests found under {library}",
    )

    assets: list[dict[str, Any]] = []
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assets.extend(manifest["assets"])

    mobys = sum(asset["kind"] == "MOBY" for asset in assets)
    ties = sum(asset["kind"] == "TIE" for asset in assets)
    materials = sum(asset["kind"] == "SHADER" for asset in assets)
    animations = sum(asset["kind"] == "ANIMATION" for asset in assets)
    if args.expected_assets is not None:
        _require(len(assets) == args.expected_assets, "Unexpected cached asset count")
    if args.expected_mobys is not None:
        _require(mobys == args.expected_mobys, "Unexpected cached Moby count")
    if args.expected_ties is not None:
        _require(ties == args.expected_ties, "Unexpected cached Tie count")
    if args.expected_materials is not None:
        _require(
            materials == args.expected_materials,
            "Unexpected cached Material count",
        )
    if args.expected_animations is not None:
        _require(
            animations == args.expected_animations,
            "Unexpected cached Animation count",
        )

    object_dependencies = 0
    packed_images = 0
    previews = 0
    for asset in assets:
        objects, images, asset_previews = _validate_asset(
            library,
            asset,
            require_previews=not args.skip_previews,
        )
        object_dependencies += objects
        packed_images += images
        previews += asset_previews

    report = {
        "library": str(library),
        "manifests": len(manifests),
        "assets": len(assets),
        "mobys": mobys,
        "ties": ties,
        "materials": materials,
        "animations": animations,
        "object_dependencies": object_dependencies,
        "packed_image_dependencies": packed_images,
        "previews": previews,
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    print("INSOMNIAC_ASSET_CACHE_VALIDATION=" + text)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
