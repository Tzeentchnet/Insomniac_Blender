from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import uuid

import bpy

from ..core.errors import InsomniacError
from ..core.source import GameSource
from ..games.registry import GameAdapter, get_game_adapter
from ..games.resistance_2.formats import AssetKind
from .importer import AssetImportOptions, import_moby, import_tie


ASSET_LIBRARY_NAME = "Insomniac Assets"
ASSET_LIBRARY_DIRECTORY = "asset_library"
ASSET_CACHE_SCHEMA_VERSION = 1
_CATALOG_NAMESPACE = uuid.UUID("23cceef5-2adc-420f-a221-5242aa1fc438")
_DATABLOCK_COLLECTIONS = (
    "actions",
    "armatures",
    "collections",
    "images",
    "materials",
    "meshes",
    "objects",
)


@dataclass(frozen=True, slots=True)
class AssetCacheOptions:
    materials: bool = True
    animations: bool = False
    convert_axes: bool = True
    cycles_materials: bool = False
    generate_previews: bool = True
    damage_permutations: bool = False
    collision_geometry: bool = False
    asset_limit: int = 0


def asset_library_path(cache_directory: str | Path) -> Path:
    return Path(cache_directory).expanduser() / ASSET_LIBRARY_DIRECTORY


def ensure_asset_library_registered(
    context: bpy.types.Context,
    cache_directory: str | Path,
) -> bpy.types.UserAssetLibrary:
    library_path = asset_library_path(cache_directory).resolve()
    library_path.mkdir(parents=True, exist_ok=True)
    libraries = context.preferences.filepaths.asset_libraries
    for library in libraries:
        configured = Path(bpy.path.abspath(library.path)).expanduser()
        try:
            same_path = configured.resolve() == library_path
        except OSError:
            same_path = False
        if same_path:
            library.enabled = True
            library.import_method = "APPEND"
            return library

    names = {library.name for library in libraries}
    name = ASSET_LIBRARY_NAME
    suffix = 2
    while name in names:
        name = f"{ASSET_LIBRARY_NAME} {suffix}"
        suffix += 1
    library = libraries.new(name=name, directory=str(library_path))
    library.enabled = True
    library.import_method = "APPEND"
    return library


def run_asset_cache_worker(
    *,
    game_id: str,
    iso_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
    options: AssetCacheOptions,
) -> dict[str, object]:
    cache_root = Path(cache_directory).expanduser()
    cache_root.mkdir(parents=True, exist_ok=True)
    report_path = cache_root / f".asset-cache-report-{uuid.uuid4().hex}.json"
    worker = Path(__file__).with_name("asset_cache_worker.py")
    command = [
        bpy.app.binary_path,
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(worker),
        "--",
        "--game",
        game_id,
        "--iso",
        str(Path(iso_path).expanduser()),
        "--level",
        level_name,
        "--cache-directory",
        str(cache_root),
        "--report",
        str(report_path),
    ]
    if not options.materials:
        command.append("--no-materials")
    if options.animations:
        command.append("--animations")
    if not options.convert_axes:
        command.append("--no-convert-axes")
    if options.cycles_materials:
        command.append("--cycles-materials")
    if options.damage_permutations:
        command.append("--damage-permutations")
    if options.collision_geometry:
        command.append("--collision-geometry")
    if not options.generate_previews:
        command.append("--no-previews")
    if options.asset_limit:
        command.extend(("--asset-limit", str(options.asset_limit)))

    try:
        adapter = get_game_adapter(game_id)
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=6 * 60 * 60,
        )
        if result.returncode != 0:
            output = "\n".join(
                line
                for line in (result.stdout + "\n" + result.stderr).splitlines()
                if line.strip()
            )
            detail = "\n".join(output.splitlines()[-20:])
            raise InsomniacError(
                f"{adapter.label} Asset Browser cache generation failed"
                + (f":\n{detail}" if detail else "")
            )
        if not report_path.is_file():
            raise InsomniacError(
                "The Asset Browser cache worker finished without a report"
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise InsomniacError("The Asset Browser cache report is invalid")
        return report
    finally:
        report_path.unlink(missing_ok=True)


def generate_asset_library(
    context: bpy.types.Context,
    *,
    game_id: str,
    iso_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
    options: AssetCacheOptions,
) -> dict[str, object]:
    adapter = get_game_adapter(game_id)
    iso = Path(iso_path).expanduser().resolve()
    cache_root = Path(cache_directory).expanduser().resolve()
    cached_level = adapter.prepare_level(iso, level_name, cache_root)
    library_root = asset_library_path(cache_root)
    level_parts = tuple(
        _safe_component(part)
        for part in level_name.replace("\\", "/").split("/")
        if part
    )
    if not level_parts:
        raise InsomniacError("The Asset Browser cache level is empty")

    relative_level = Path(adapter.cache_key, *level_parts)
    final_level = library_root / relative_level
    staging_root = cache_root / f".asset-cache-staging-{uuid.uuid4().hex}"
    staged_level = staging_root / relative_level
    staged_level.mkdir(parents=True, exist_ok=False)

    assets: list[dict[str, object]] = []
    warnings: list[str] = []
    catalogs: dict[str, tuple[uuid.UUID, str]] = {}
    try:
        with GameSource.open(cached_level) as source:
            reader = adapter.create_reader(source)
            for kind in (AssetKind.MOBY, AssetKind.TIE):
                pointers = reader.asset_pointers(kind)
                if options.asset_limit:
                    pointers = pointers[: options.asset_limit]
                catalog_path = (
                    f"{adapter.label}/{level_name.replace('\\', '/')}/"
                    f"{'Mobys' if kind is AssetKind.MOBY else 'Ties'}"
                )
                catalog_id = uuid.uuid5(_CATALOG_NAMESPACE, catalog_path)
                catalogs[catalog_path] = (catalog_id, catalog_path.rsplit("/", 1)[-1])
                kind_directory = staged_level / kind.name.casefold()
                kind_directory.mkdir(parents=True, exist_ok=True)

                for pointer in pointers:
                    snapshot = _snapshot_datablocks()
                    try:
                        import_options = AssetImportOptions(
                            materials=options.materials,
                            animations=(
                                options.animations
                                if kind is AssetKind.MOBY
                                else False
                            ),
                            convert_axes=options.convert_axes,
                            cycles_materials=options.cycles_materials,
                            damage_permutations=(
                                options.damage_permutations
                            ),
                            collision_geometry=options.collision_geometry,
                        )
                        if kind is AssetKind.MOBY:
                            result = import_moby(
                                context,
                                reader,
                                pointer.tuid,
                                import_options,
                                source_path=str(iso),
                            )
                        else:
                            result = import_tie(
                                context,
                                reader,
                                pointer.tuid,
                                import_options,
                                source_path=str(iso),
                            )
                        collection = _asset_collection(result.root)
                        _mark_asset(
                            collection,
                            kind=kind,
                            tuid=pointer.tuid,
                            level_name=level_name,
                            catalog_id=catalog_id,
                            generate_preview=options.generate_previews,
                            adapter=adapter,
                        )
                        filename = (
                            f"{kind.name.casefold()}_{pointer.tuid:016x}.blend"
                        )
                        output = kind_directory / filename
                        bpy.data.libraries.write(
                            str(output),
                            {collection},
                            fake_user=True,
                            compress=True,
                        )
                        relative_file = relative_level / kind.name.casefold() / filename
                        assets.append(
                            {
                                "kind": kind.name,
                                "tuid": f"0x{pointer.tuid:016X}",
                                "name": collection.name,
                                "file": relative_file.as_posix(),
                                "objects": result.objects,
                                "meshes": result.meshes,
                                "materials": result.materials,
                                "images": result.images,
                                "actions": result.actions,
                            }
                        )
                        warnings.extend(result.warnings or ())
                    except Exception as exc:
                        raise InsomniacError(
                            f"Failed to cache {kind.name} "
                            f"0x{pointer.tuid:016X}: {exc}"
                        ) from exc
                    finally:
                        _remove_new_datablocks(snapshot)

        manifest = {
            "schema_version": ASSET_CACHE_SCHEMA_VERSION,
            "game": adapter.id,
            "level": level_name.replace("\\", "/"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "iso": str(iso),
                "size": iso.stat().st_size,
                "modified_ns": iso.stat().st_mtime_ns,
            },
            "options": {
                "materials": options.materials,
                "animations": options.animations,
                "convert_axes": options.convert_axes,
                "cycles_materials": options.cycles_materials,
                "generate_previews": options.generate_previews,
                "damage_permutations": options.damage_permutations,
                "collision_geometry": options.collision_geometry,
                "asset_limit": options.asset_limit,
            },
            "assets": assets,
            "warnings": warnings,
        }
        (staged_level / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _commit_level(staged_level, final_level, staging_root)
        _merge_catalogs(library_root, catalogs)
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise

    return {
        "game": adapter.id,
        "library": str(library_root),
        "level": level_name.replace("\\", "/"),
        "assets": len(assets),
        "mobys": sum(asset["kind"] == "MOBY" for asset in assets),
        "ties": sum(asset["kind"] == "TIE" for asset in assets),
        "warnings": len(warnings),
        "manifest": str(final_level / "manifest.json"),
    }


def _safe_component(value: str) -> str:
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if character in invalid else character for character in value)
    cleaned = cleaned.strip().rstrip(".")
    return cleaned or "unnamed"


def _asset_collection(root: bpy.types.Object) -> bpy.types.Collection:
    collections = tuple(root.users_collection)
    if len(collections) != 1:
        raise InsomniacError(
            f"Imported root {root.name!r} belongs to {len(collections)} collections"
        )
    return collections[0]


def _mark_asset(
    collection: bpy.types.Collection,
    *,
    kind: AssetKind,
    tuid: int,
    level_name: str,
    catalog_id: uuid.UUID,
    generate_preview: bool,
    adapter: GameAdapter,
) -> None:
    kind_label = "Moby" if kind is AssetKind.MOBY else "Tie"
    collection["insomniac_game"] = adapter.id
    collection["insomniac_asset_kind"] = kind.name
    collection["insomniac_asset_tuid"] = f"0x{tuid:016X}"
    collection["insomniac_cache_level"] = level_name.replace("\\", "/")
    collection.asset_mark()
    metadata = collection.asset_data
    metadata.catalog_id = str(catalog_id)
    metadata.description = (
        f"{adapter.label} {kind_label} 0x{tuid:016X} from {level_name}"
    )
    metadata.tags.new(adapter.label, skip_if_exists=True)
    metadata.tags.new(kind_label, skip_if_exists=True)
    metadata.tags.new(f"0x{tuid:016X}", skip_if_exists=True)
    if generate_preview:
        collection.asset_generate_preview()


def _snapshot_datablocks() -> dict[str, set[bpy.types.ID]]:
    return {
        name: set(getattr(bpy.data, name))
        for name in _DATABLOCK_COLLECTIONS
    }


def _remove_new_datablocks(
    snapshot: dict[str, set[bpy.types.ID]],
) -> None:
    created: set[bpy.types.ID] = set()
    for name in _DATABLOCK_COLLECTIONS:
        created.update(set(getattr(bpy.data, name)) - snapshot[name])
    if created:
        bpy.data.batch_remove(created)


def _commit_level(
    staged_level: Path,
    final_level: Path,
    staging_root: Path,
) -> None:
    final_level.parent.mkdir(parents=True, exist_ok=True)
    previous = staging_root / "_previous"
    if final_level.exists():
        final_level.replace(previous)
    try:
        staged_level.replace(final_level)
    except Exception:
        if previous.exists() and not final_level.exists():
            previous.replace(final_level)
        raise
    shutil.rmtree(staging_root, ignore_errors=True)


def _merge_catalogs(
    library_root: Path,
    catalogs: dict[str, tuple[uuid.UUID, str]],
) -> None:
    library_root.mkdir(parents=True, exist_ok=True)
    catalog_file = library_root / "blender_assets.cats.txt"
    entries: dict[str, tuple[str, str]] = {}
    if catalog_file.is_file():
        for raw_line in catalog_file.read_text(encoding="utf-8").splitlines():
            if not raw_line or raw_line.startswith("#") or raw_line == "VERSION 1":
                continue
            parts = raw_line.split(":", 2)
            if len(parts) == 3:
                entries[parts[0]] = (parts[1], parts[2])
    for catalog_path, (catalog_id, simple_name) in catalogs.items():
        entries[str(catalog_id)] = (catalog_path, simple_name)

    lines = [
        "# This is an Asset Catalog Definition file for Blender.",
        "# Generated and updated by Insomniac Asset IO.",
        "VERSION 1",
        "",
    ]
    lines.extend(
        f"{catalog_id}:{path}:{simple_name}"
        for catalog_id, (path, simple_name) in sorted(
            entries.items(),
            key=lambda item: item[1][0].casefold(),
        )
    )
    temporary = catalog_file.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(catalog_file)
