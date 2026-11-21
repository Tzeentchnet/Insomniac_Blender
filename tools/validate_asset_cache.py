from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import bpy

_CATALOG_NAMESPACE = uuid.UUID("23cceef5-2adc-420f-a221-5242aa1fc438")
_LEVEL_LOCAL_GAMES = {"RESISTANCE_FOM", "RATCHET_TOD"}
_GAME_LABELS = (
    "Resistance: Fall of Man",
    "Resistance - Fall of Man",
    "Ratchet & Clank Future: Tools of Destruction",
    "Ratchet & Clank Future - Tools of Destruction",
    "Resistance 2",
    "Resistance 3",
)
_COLLECTION_KINDS = {"MOBY", "TIE", "SHRUB", "FOLIAGE"}
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
    parser.add_argument("--expected-shrubs", type=int)
    parser.add_argument("--expected-foliage", type=int)
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
            if kind in _COLLECTION_KINDS:
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

        if kind in _COLLECTION_KINDS:
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
        catalog_path = asset.get("catalog_path")
        if isinstance(catalog_path, str):
            expected_catalog_id = str(
                uuid.uuid5(_CATALOG_NAMESPACE, catalog_path)
            )
            _require(
                str(datablock.asset_data.catalog_id)
                == expected_catalog_id,
                f"{path} catalog ID does not match {catalog_path!r}",
            )
        preview = datablock.preview
        if require_previews and kind != "ANIMATION":
            _require(preview is not None, f"{path} has no Asset Browser preview")
            _require(
                tuple(preview.image_size) == (128, 128),
                f"{path} has an unexpected preview size "
                f"{tuple(preview.image_size)}",
            )
        if kind in _COLLECTION_KINDS:
            _require(
                len(datablock.all_objects) >= 2,
                f"{path} has no imported geometry",
            )
            if kind == "MOBY" and asset.get(
                "source_default_parts"
            ):
                pending = list(datablock.children)
                while pending:
                    child = pending.pop()
                    pending.extend(child.children)
                    if not child.get(
                        "insomniac_source_default_visible",
                        False,
                    ):
                        continue
                    _require(
                        not child.hide_viewport
                        and not child.hide_render,
                        f"{path} hides source-default collection "
                        f"{child.name!r}",
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


def _logical_id(
    manifest: dict[str, Any],
    asset: dict[str, Any],
) -> str:
    logical_id = asset.get("logical_id")
    if isinstance(logical_id, str):
        return logical_id
    game = str(manifest["game"])
    level = str(manifest["level"])
    kind = str(asset["kind"])
    tuid = str(asset["tuid"])
    scope = f":{level}" if game in _LEVEL_LOCAL_GAMES else ""
    if kind == "ANIMATION":
        return (
            f"MOBY{scope}:{tuid}:ACTION:{asset['name']}"
        )
    return f"{kind}{scope}:{tuid}"


def _unique_assets(
    manifests: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    unique: dict[str, dict[str, Any]] = {}
    references = 0
    for manifest in manifests:
        for asset in manifest["assets"]:
            references += 1
            logical_id = _logical_id(manifest, asset)
            existing = unique.get(logical_id)
            if existing is None:
                unique[logical_id] = asset
                continue
            _require(
                existing["file"] == asset["file"],
                f"Logical asset {logical_id} is duplicated in "
                f"{existing['file']} and {asset['file']}",
            )
            _require(
                existing["kind"] == asset["kind"]
                and existing["name"] == asset["name"],
                f"Logical asset {logical_id} has inconsistent manifests",
            )
    return list(unique.values()), references


def _catalog_entries(path: Path) -> dict[str, str]:
    by_id: dict[str, str] = {}
    by_path: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if (
            not raw_line
            or raw_line.startswith("#")
            or raw_line == "VERSION 1"
        ):
            continue
        try:
            catalog_id, remainder = raw_line.split(":", 1)
            catalog_path, _simple_name = remainder.rsplit(":", 1)
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid Asset Browser catalog line: {raw_line!r}"
            ) from exc
        _require(
            catalog_id not in by_id,
            f"Duplicate Asset Browser catalog UUID {catalog_id}",
        )
        _require(
            catalog_path not in by_path,
            f"Duplicate Asset Browser catalog path {catalog_path}",
        )
        by_id[catalog_id] = catalog_path
        by_path[catalog_path] = catalog_id
    return by_id


def main() -> int:
    args = _arguments()
    library = args.library.resolve()
    catalog = library / "blender_assets.cats.txt"
    _require(catalog.is_file(), f"Asset catalog is missing: {catalog}")
    catalog_entries = _catalog_entries(catalog)
    manifests = sorted(library.glob("*/**/manifest.json"))
    _require(
        manifests,
        f"No Insomniac Asset IO cache manifests found under {library}",
    )

    manifest_payloads: list[dict[str, Any]] = []
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_payloads.append(manifest)
    assets, asset_references = _unique_assets(manifest_payloads)
    if all(
        manifest.get("schema_version") == 2
        for manifest in manifest_payloads
    ):
        desired_catalogs = {
            str(asset["catalog_path"]) for asset in assets
        }
        generated_catalogs = {
            path
            for path in catalog_entries.values()
            if any(
                path == label or path.startswith(f"{label}/")
                for label in _GAME_LABELS
            )
        }
        _require(
            generated_catalogs == desired_catalogs,
            "Asset catalog contains stale or missing generated paths: "
            f"expected {sorted(desired_catalogs)}, "
            f"found {sorted(generated_catalogs)}",
        )

    mobys = sum(asset["kind"] == "MOBY" for asset in assets)
    ties = sum(asset["kind"] == "TIE" for asset in assets)
    shrubs = sum(asset["kind"] == "SHRUB" for asset in assets)
    foliage = sum(asset["kind"] == "FOLIAGE" for asset in assets)
    materials = sum(asset["kind"] == "SHADER" for asset in assets)
    animations = sum(asset["kind"] == "ANIMATION" for asset in assets)
    if args.expected_assets is not None:
        _require(len(assets) == args.expected_assets, "Unexpected cached asset count")
    if args.expected_mobys is not None:
        _require(mobys == args.expected_mobys, "Unexpected cached Moby count")
    if args.expected_ties is not None:
        _require(ties == args.expected_ties, "Unexpected cached Tie count")
    if args.expected_shrubs is not None:
        _require(
            shrubs == args.expected_shrubs,
            "Unexpected cached Shrub count",
        )
    if args.expected_foliage is not None:
        _require(
            foliage == args.expected_foliage,
            "Unexpected cached Foliage count",
        )
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
        catalog_path = asset.get("catalog_path")
        if isinstance(catalog_path, str):
            expected_id = str(
                uuid.uuid5(_CATALOG_NAMESPACE, catalog_path)
            )
            _require(
                catalog_entries.get(expected_id) == catalog_path,
                f"Catalog file does not contain {catalog_path!r}",
            )
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
        "asset_references": asset_references,
        "deduplicated_references": asset_references - len(assets),
        "mobys": mobys,
        "ties": ties,
        "shrubs": shrubs,
        "foliage": foliage,
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
