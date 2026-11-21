from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import bpy

from ..core.errors import InsomniacError
from ..core.source import GameSource
from ..games.registry import (
    GAME_ADAPTERS,
    GameAdapter,
    get_game_adapter,
)
from ..games.resistance_2.formats import AssetKind, AssetPointer
from .asset_cache import (
    _CATALOG_NAMESPACE,
    ASSET_CACHE_SCHEMA_VERSION,
    AssetCacheOptions,
    _asset_collection,
    _mark_action_asset,
    _mark_asset,
    _mark_material_asset,
    _remove_new_datablocks,
    _safe_component,
    _snapshot_datablocks,
    asset_library_path,
)
from .importer import (
    AssetImportOptions,
    import_materials,
    import_moby,
    import_static_asset,
)

_STATIC_ASSET_KINDS = (
    AssetKind.TIE,
    AssetKind.SHRUB,
    AssetKind.FOLIAGE,
)
_ASSET_KIND_LABELS = {
    AssetKind.MOBY: ("Moby", "Mobys"),
    AssetKind.TIE: ("Tie", "Ties"),
    AssetKind.SHRUB: ("Shrub", "Shrubs"),
    AssetKind.FOLIAGE: ("Foliage", "Foliage"),
    AssetKind.SHADER: ("Material", "Materials"),
}


def generate_asset_library_v2(
    context: bpy.types.Context,
    *,
    game_id: str,
    iso_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
    options: AssetCacheOptions,
    progress: Callable[[dict[str, object]], None] | None = None,
    job_id: str | None = None,
) -> dict[str, object]:
    adapter = get_game_adapter(game_id)
    iso = Path(iso_path).expanduser().resolve()
    cache_root = Path(cache_directory).expanduser().resolve()
    normalized_level = level_name.replace("\\", "/").strip("/")
    if not normalized_level:
        raise InsomniacError("The Asset Browser cache level is empty")

    _publish_progress(
        progress,
        phase="prepare",
        current=0,
        total=0,
        message=f"Preparing {adapter.label} level {normalized_level}",
    )
    cached_level = adapter.prepare_level(
        iso,
        normalized_level,
        cache_root,
    )
    library_root = asset_library_path(cache_root).resolve()
    level_parts = tuple(
        _safe_component(part)
        for part in normalized_level.split("/")
        if part
    )
    relative_level = Path(adapter.cache_key, *level_parts)
    final_level = library_root / relative_level
    final_manifest = (final_level / "manifest.json").resolve()
    token = _safe_component(job_id or uuid.uuid4().hex)
    staging_root = cache_root / f".asset-cache-staging-{token}"
    staged_library = staging_root / "library"
    staged_level = staged_library / relative_level
    if staging_root.exists():
        raise InsomniacError(
            f"Asset Browser staging directory already exists: "
            f"{staging_root}"
        )
    staged_level.mkdir(parents=True, exist_ok=False)

    source_identity: dict[str, object] = {
        "iso": str(iso),
        "size": iso.stat().st_size,
        "modified_ns": iso.stat().st_mtime_ns,
    }
    existing_manifests = _load_library_manifests(library_root)
    cached_groups = _cached_asset_groups(
        existing_manifests,
        library_root,
    )
    assets: list[dict[str, object]] = []
    warnings: list[str] = []
    generated_groups: dict[str, list[dict[str, object]]] = {}
    staged_asset_replacements: list[tuple[Path, Path]] = []
    generated_count = 0
    reused_count = 0
    duplicate_pointer_count = 0
    current = 0
    total = 0

    try:
        with GameSource.open(cached_level) as source:
            reader = adapter.create_reader(source)
            pointers_by_kind: dict[
                AssetKind,
                tuple[AssetPointer, ...],
            ] = {}
            requested_kinds = [AssetKind.MOBY, AssetKind.TIE]
            if options.vegetation:
                requested_kinds.extend(
                    (AssetKind.SHRUB, AssetKind.FOLIAGE)
                )
            for kind in requested_kinds:
                if not _reader_supports_kind(reader, kind):
                    continue
                pointers, duplicate_count = _unique_asset_pointers(
                    reader.asset_pointers(kind)
                )
                duplicate_pointer_count += duplicate_count
                if options.asset_limit:
                    pointers = pointers[: options.asset_limit]
                pointers_by_kind[kind] = pointers

            material_pointers: tuple[AssetPointer, ...] = ()
            if options.materials:
                (
                    material_pointers,
                    duplicate_count,
                ) = _unique_asset_pointers(
                    reader.asset_pointers(AssetKind.SHADER)
                )
                duplicate_pointer_count += duplicate_count
                if options.asset_limit:
                    material_pointers = material_pointers[
                        : options.asset_limit
                    ]

            total = sum(
                len(pointers)
                for pointers in pointers_by_kind.values()
            ) + len(material_pointers)
            _publish_progress(
                progress,
                phase="generate",
                current=0,
                total=total,
                message=f"Generating {total} unique assets",
            )

            for kind, pointers in pointers_by_kind.items():
                for pointer in pointers:
                    group, generated = _cache_geometry_asset(
                        context=context,
                        reader=reader,
                        adapter=adapter,
                        pointer=pointer,
                        kind=kind,
                        normalized_level=normalized_level,
                        level_parts=level_parts,
                        iso=iso,
                        library_root=library_root,
                        staged_library=staged_library,
                        source_identity=source_identity,
                        options=options,
                        cached_groups=cached_groups,
                    )
                    assets.extend(group)
                    warnings.extend(_group_warnings(group))
                    logical_id = _asset_logical_id(
                        adapter,
                        kind,
                        pointer.tuid,
                        normalized_level,
                    )
                    if generated:
                        generated_groups[logical_id] = group
                        staged_path = (
                            staged_library / str(group[0]["file"])
                        )
                        if not adapter.level_local_asset_ids:
                            staged_asset_replacements.append(
                                (
                                    staged_path,
                                    library_root
                                    / str(group[0]["file"]),
                                )
                            )
                        generated_count += 1
                    else:
                        reused_count += 1
                    current += 1
                    _publish_progress(
                        progress,
                        phase="generate",
                        current=current,
                        total=total,
                        message=(
                            f"{'Generated' if generated else 'Reused'} "
                            f"{kind.name} 0x{pointer.tuid:016X}"
                        ),
                    )

            if material_pointers and adapter.level_local_asset_ids:
                (
                    material_assets,
                    material_warnings,
                ) = _generate_level_material_assets(
                    reader=reader,
                    adapter=adapter,
                    pointers=material_pointers,
                    normalized_level=normalized_level,
                    relative_level=relative_level,
                    staged_level=staged_level,
                    source_identity=source_identity,
                    options=options,
                )
                assets.extend(material_assets)
                warnings.extend(material_warnings)
                generated_count += len(material_pointers)
                for pointer in material_pointers:
                    current += 1
                    _publish_progress(
                        progress,
                        phase="generate",
                        current=current,
                        total=total,
                        message=(
                            "Generated SHADER "
                            f"0x{pointer.tuid:016X}"
                        ),
                    )
            elif material_pointers:
                for pointer in material_pointers:
                    group, generated = _cache_global_material_asset(
                        reader=reader,
                        adapter=adapter,
                        pointer=pointer,
                        normalized_level=normalized_level,
                        level_parts=level_parts,
                        library_root=library_root,
                        staged_library=staged_library,
                        source_identity=source_identity,
                        options=options,
                        cached_groups=cached_groups,
                    )
                    assets.extend(group)
                    warnings.extend(_group_warnings(group))
                    logical_id = _asset_logical_id(
                        adapter,
                        AssetKind.SHADER,
                        pointer.tuid,
                        normalized_level,
                    )
                    if generated:
                        generated_groups[logical_id] = group
                        staged_path = (
                            staged_library / str(group[0]["file"])
                        )
                        staged_asset_replacements.append(
                            (
                                staged_path,
                                library_root / str(group[0]["file"]),
                            )
                        )
                        generated_count += 1
                    else:
                        reused_count += 1
                    current += 1
                    _publish_progress(
                        progress,
                        phase="generate",
                        current=current,
                        total=total,
                        message=(
                            f"{'Generated' if generated else 'Reused'} "
                            f"SHADER 0x{pointer.tuid:016X}"
                        ),
                    )

        manifest = {
            "schema_version": ASSET_CACHE_SCHEMA_VERSION,
            "game": adapter.id,
            "level": normalized_level,
            "generated_at": datetime.now(UTC).isoformat(),
            "source": source_identity,
            "options": _manifest_options(options),
            "deduplication_scope": (
                "LEVEL"
                if adapter.level_local_asset_ids
                else "GAME"
            ),
            "assets": assets,
            "warnings": warnings,
        }
        staged_manifest = staged_level / "manifest.json"
        _write_json(staged_manifest, manifest)

        manifest_states = dict(existing_manifests)
        manifest_states[final_manifest] = manifest
        manifest_updates: list[tuple[Path, Path]] = []
        replaced_record_files: set[Path] = set()
        if not adapter.level_local_asset_ids and generated_groups:
            update_directory = staging_root / "manifest-updates"
            update_index = 0
            for path, existing_manifest in tuple(
                manifest_states.items()
            ):
                if path == final_manifest:
                    continue
                updated, removed_files = _replace_manifest_groups(
                    existing_manifest,
                    generated_groups,
                )
                if updated is None:
                    continue
                staged_update = (
                    update_directory / f"{update_index:04d}.json"
                )
                update_index += 1
                _write_json(staged_update, updated)
                manifest_updates.append((staged_update, path))
                manifest_states[path] = updated
                replaced_record_files.update(
                    library_root / relative
                    for relative in removed_files
                )

        catalogs = _catalogs_from_manifests(
            manifest_states.values()
        )
        staged_catalog = staging_root / "blender_assets.cats.txt"
        _write_catalog_file(
            library_root / "blender_assets.cats.txt",
            staged_catalog,
            catalogs,
        )
        referenced_files = _referenced_asset_files(
            library_root,
            manifest_states.values(),
        )
        deletions = {
            path.resolve()
            for path in replaced_record_files
            if path.resolve() not in referenced_files
            and path.suffix.casefold() == ".blend"
            and not _is_within(path, final_level)
        }
        if not adapter.level_local_asset_ids:
            shared_root = (
                library_root / adapter.cache_key / "_shared"
            )
            if shared_root.is_dir():
                deletions.update(
                    path.resolve()
                    for path in shared_root.rglob("*.blend")
                    if path.resolve() not in referenced_files
                )

        replacements = [
            (staged_level, final_level),
            *staged_asset_replacements,
            *manifest_updates,
            (
                staged_catalog,
                library_root / "blender_assets.cats.txt",
            ),
        ]
        _publish_progress(
            progress,
            phase="commit",
            current=total,
            total=total,
            message="Committing generated assets and catalogs",
            cancellable=False,
        )
        _transactional_replace(
            replacements,
            deletions,
            staging_root=staging_root,
        )
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise

    return {
        "game": adapter.id,
        "library": str(library_root),
        "level": normalized_level,
        "assets": len(assets),
        "mobys": _count_kind(assets, "MOBY"),
        "ties": _count_kind(assets, "TIE"),
        "shrubs": _count_kind(assets, "SHRUB"),
        "foliage": _count_kind(assets, "FOLIAGE"),
        "materials": _count_kind(assets, "SHADER"),
        "animations": _count_kind(assets, "ANIMATION"),
        "generated": generated_count,
        "reused": reused_count,
        "duplicate_pointers_removed": duplicate_pointer_count,
        "warnings": len(warnings),
        "manifest": str(final_manifest),
    }


def _cache_geometry_asset(
    *,
    context: bpy.types.Context,
    reader: object,
    adapter: GameAdapter,
    pointer: AssetPointer,
    kind: AssetKind,
    normalized_level: str,
    level_parts: tuple[str, ...],
    iso: Path,
    library_root: Path,
    staged_library: Path,
    source_identity: dict[str, object],
    options: AssetCacheOptions,
    cached_groups: dict[str, list[list[dict[str, object]]]],
) -> tuple[list[dict[str, object]], bool]:
    logical_id = _asset_logical_id(
        adapter,
        kind,
        pointer.tuid,
        normalized_level,
    )
    signature = _generation_signature(
        adapter,
        kind,
        source_identity,
        options,
    )
    relative_file = _asset_relative_file(
        adapter,
        level_parts,
        kind,
        pointer.tuid,
    )
    if not adapter.level_local_asset_ids:
        reused = _reusable_asset_group(
            cached_groups.get(logical_id, ()),
            signature=signature,
            expected_file=relative_file,
            library_root=library_root,
        )
        if reused is not None:
            return [dict(record) for record in reused], False

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
            damage_permutations=options.damage_permutations,
            lower_lods=options.lower_lods,
            collision_geometry=options.collision_geometry,
            source_default_parts=options.source_default_parts,
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
            result = import_static_asset(
                context,
                reader,
                pointer.tuid,
                import_options,
                kind=kind,
                source_path=str(iso),
            )
        created_actions = tuple(
            sorted(
                set(bpy.data.actions) - snapshot["actions"],
                key=lambda action: action.name.casefold(),
            )
        )
        collection = _asset_collection(result.root)
        catalog_path = _asset_catalog_path(
            adapter,
            normalized_level,
            kind,
        )
        _mark_asset(
            collection,
            kind=kind,
            tuid=pointer.tuid,
            level_name=normalized_level,
            catalog_id=uuid.uuid5(
                _CATALOG_NAMESPACE,
                catalog_path,
            ),
            generate_preview=options.generate_previews,
            adapter=adapter,
        )
        animation_catalog_path = ""
        if created_actions:
            animation_catalog_path = _animation_catalog_path(
                adapter,
                normalized_level,
                collection.name,
            )
            animation_catalog_id = uuid.uuid5(
                _CATALOG_NAMESPACE,
                animation_catalog_path,
            )
            for action in created_actions:
                _mark_action_asset(
                    action,
                    moby_tuid=pointer.tuid,
                    moby_name=collection.name,
                    level_name=normalized_level,
                    catalog_id=animation_catalog_id,
                    adapter=adapter,
                )

        output = staged_library / relative_file
        output.parent.mkdir(parents=True, exist_ok=True)
        bpy.data.libraries.write(
            str(output),
            {collection, *created_actions},
            fake_user=True,
            compress=True,
        )
        main_record = _geometry_asset_record(
            logical_id=logical_id,
            kind=kind,
            tuid=pointer.tuid,
            name=collection.name,
            relative_file=relative_file,
            catalog_path=catalog_path,
            source_level=normalized_level,
            signature=signature,
            result=result,
        )
        if kind is AssetKind.MOBY:
            main_record["source_default_parts"] = (
                options.source_default_parts
            )
        group = [main_record]
        group.extend(
            _action_asset_record(
                parent_logical_id=logical_id,
                moby_tuid=pointer.tuid,
                action=action,
                relative_file=relative_file,
                catalog_path=animation_catalog_path,
                source_level=normalized_level,
                signature=signature,
            )
            for action in created_actions
        )
        if result.warnings:
            group[0]["warnings"] = list(result.warnings)
        return group, True
    except Exception as exc:
        raise InsomniacError(
            f"Failed to cache {kind.name} "
            f"0x{pointer.tuid:016X}: {exc}"
        ) from exc
    finally:
        _remove_new_datablocks(snapshot)


def _cache_global_material_asset(
    *,
    reader: object,
    adapter: GameAdapter,
    pointer: AssetPointer,
    normalized_level: str,
    level_parts: tuple[str, ...],
    library_root: Path,
    staged_library: Path,
    source_identity: dict[str, object],
    options: AssetCacheOptions,
    cached_groups: dict[str, list[list[dict[str, object]]]],
) -> tuple[list[dict[str, object]], bool]:
    logical_id = _asset_logical_id(
        adapter,
        AssetKind.SHADER,
        pointer.tuid,
        normalized_level,
    )
    signature = _generation_signature(
        adapter,
        AssetKind.SHADER,
        source_identity,
        options,
    )
    relative_file = _asset_relative_file(
        adapter,
        level_parts,
        AssetKind.SHADER,
        pointer.tuid,
    )
    reused = _reusable_asset_group(
        cached_groups.get(logical_id, ()),
        signature=signature,
        expected_file=relative_file,
        library_root=library_root,
    )
    if reused is not None:
        return [dict(record) for record in reused], False

    snapshot = _snapshot_datablocks()
    try:
        result = import_materials(
            reader,
            (pointer.tuid,),
            cycles_materials=options.cycles_materials,
        )
        if len(result.materials) != 1:
            raise InsomniacError(
                "Material import did not return exactly one material"
            )
        material = result.materials[0]
        catalog_path = _asset_catalog_path(
            adapter,
            normalized_level,
            AssetKind.SHADER,
        )
        _mark_material_asset(
            material,
            shader_tuid=pointer.tuid,
            level_name=normalized_level,
            catalog_id=uuid.uuid5(
                _CATALOG_NAMESPACE,
                catalog_path,
            ),
            generate_preview=options.generate_previews,
            adapter=adapter,
        )
        output = staged_library / relative_file
        output.parent.mkdir(parents=True, exist_ok=True)
        bpy.data.libraries.write(
            str(output),
            {material},
            fake_user=True,
            compress=True,
        )
        record = _material_asset_record(
            logical_id=logical_id,
            tuid=pointer.tuid,
            material=material,
            relative_file=relative_file,
            catalog_path=catalog_path,
            source_level=normalized_level,
            signature=signature,
            image_count=len(result.images),
        )
        if result.warnings:
            record["warnings"] = list(result.warnings)
        return [record], True
    except Exception as exc:
        raise InsomniacError(
            f"Failed to cache Material "
            f"0x{pointer.tuid:016X}: {exc}"
        ) from exc
    finally:
        _remove_new_datablocks(snapshot)


def _generate_level_material_assets(
    *,
    reader: object,
    adapter: GameAdapter,
    pointers: tuple[AssetPointer, ...],
    normalized_level: str,
    relative_level: Path,
    staged_level: Path,
    source_identity: dict[str, object],
    options: AssetCacheOptions,
) -> tuple[list[dict[str, object]], list[str]]:
    snapshot = _snapshot_datablocks()
    try:
        result = import_materials(
            reader,
            (pointer.tuid for pointer in pointers),
            cycles_materials=options.cycles_materials,
        )
        catalog_path = _asset_catalog_path(
            adapter,
            normalized_level,
            AssetKind.SHADER,
        )
        catalog_id = uuid.uuid5(
            _CATALOG_NAMESPACE,
            catalog_path,
        )
        relative_file = (
            relative_level / "shader" / "materials.blend"
        )
        output = staged_level / "shader" / "materials.blend"
        output.parent.mkdir(parents=True, exist_ok=True)
        signature = _generation_signature(
            adapter,
            AssetKind.SHADER,
            source_identity,
            options,
        )
        records: list[dict[str, object]] = []
        for pointer, material in zip(
            pointers,
            result.materials,
            strict=True,
        ):
            _mark_material_asset(
                material,
                shader_tuid=pointer.tuid,
                level_name=normalized_level,
                catalog_id=catalog_id,
                generate_preview=options.generate_previews,
                adapter=adapter,
            )
            records.append(
                _material_asset_record(
                    logical_id=_asset_logical_id(
                        adapter,
                        AssetKind.SHADER,
                        pointer.tuid,
                        normalized_level,
                    ),
                    tuid=pointer.tuid,
                    material=material,
                    relative_file=relative_file,
                    catalog_path=catalog_path,
                    source_level=normalized_level,
                    signature=signature,
                    image_count=sum(
                        image.users > 0 for image in result.images
                    ),
                )
            )
        bpy.data.libraries.write(
            str(output),
            set(result.materials),
            fake_user=True,
            compress=True,
        )
        return records, result.warnings
    except Exception as exc:
        raise InsomniacError(
            f"Failed to cache level materials: {exc}"
        ) from exc
    finally:
        _remove_new_datablocks(snapshot)


def _publish_progress(
    callback: Callable[[dict[str, object]], None] | None,
    *,
    phase: str,
    current: int,
    total: int,
    message: str,
    cancellable: bool = True,
) -> None:
    if callback is None:
        return
    callback(
        {
            "phase": phase,
            "current": current,
            "total": total,
            "message": message,
            "cancellable": cancellable,
        }
    )


def _reader_supports_kind(reader: object, kind: AssetKind) -> bool:
    if kind is AssetKind.MOBY:
        return callable(getattr(reader, "read_moby", None))
    if kind in _STATIC_ASSET_KINDS:
        return callable(
            getattr(reader, f"read_{kind.name.casefold()}", None)
        )
    return False


def _unique_asset_pointers(
    pointers: Iterable[AssetPointer],
) -> tuple[tuple[AssetPointer, ...], int]:
    unique: list[AssetPointer] = []
    seen: set[int] = set()
    duplicate_count = 0
    for pointer in pointers:
        if pointer.tuid in seen:
            duplicate_count += 1
            continue
        seen.add(pointer.tuid)
        unique.append(pointer)
    return tuple(unique), duplicate_count


def _asset_logical_id(
    adapter: GameAdapter,
    kind: AssetKind,
    tuid: int,
    level_name: str,
) -> str:
    return adapter.catalog_identifier(kind, tuid, level_name)


def _action_logical_id(
    parent_logical_id: str,
    action_name: str,
) -> str:
    return f"{parent_logical_id}:ACTION:{action_name}"


def _asset_catalog_path(
    adapter: GameAdapter,
    level_name: str,
    kind: AssetKind,
) -> str:
    try:
        plural = _ASSET_KIND_LABELS[kind][1]
    except KeyError as exc:
        raise ValueError(
            f"{kind.name} is not an Asset Browser kind"
        ) from exc
    components = [_catalog_game_component(adapter)]
    if adapter.level_local_asset_ids:
        components.append(level_name)
    components.append(plural)
    return "/".join(components)


def _animation_catalog_path(
    adapter: GameAdapter,
    level_name: str,
    moby_name: str,
) -> str:
    components = [_catalog_game_component(adapter)]
    if adapter.level_local_asset_ids:
        components.append(level_name)
    components.extend(("Animations", moby_name))
    return "/".join(components)


def _catalog_game_component(adapter: GameAdapter) -> str:
    return adapter.label.replace(":", " -")


def _asset_relative_file(
    adapter: GameAdapter,
    level_parts: tuple[str, ...],
    kind: AssetKind,
    tuid: int,
) -> Path:
    scope = (
        Path(adapter.cache_key, *level_parts)
        if adapter.level_local_asset_ids
        else Path(adapter.cache_key, "_shared")
    )
    filename = f"{kind.name.casefold()}_{tuid:016x}.blend"
    return scope / kind.name.casefold() / filename


def _manifest_options(
    options: AssetCacheOptions,
) -> dict[str, object]:
    return {
        "materials": options.materials,
        "animations": options.animations,
        "convert_axes": options.convert_axes,
        "cycles_materials": options.cycles_materials,
        "generate_previews": options.generate_previews,
        "damage_permutations": options.damage_permutations,
        "lower_lods": options.lower_lods,
        "collision_geometry": options.collision_geometry,
        "source_default_parts": options.source_default_parts,
        "vegetation": options.vegetation,
        "asset_limit": options.asset_limit,
    }


def _generation_signature(
    adapter: GameAdapter,
    kind: AssetKind,
    source_identity: dict[str, object],
    options: AssetCacheOptions,
) -> str:
    relevant_options: dict[str, object] = {
        "convert_axes": options.convert_axes,
        "generate_previews": options.generate_previews,
    }
    if kind is AssetKind.SHADER:
        relevant_options["cycles_materials"] = (
            options.cycles_materials
        )
    else:
        relevant_options.update(
            {
                "materials": options.materials,
                "cycles_materials": options.cycles_materials,
            }
        )
    if kind is AssetKind.MOBY:
        relevant_options.update(
            {
                "animations": options.animations,
                "damage_permutations": options.damage_permutations,
                "lower_lods": options.lower_lods,
                "collision_geometry": options.collision_geometry,
                "source_default_parts": options.source_default_parts,
            }
        )
    payload = {
        "schema_version": ASSET_CACHE_SCHEMA_VERSION,
        "game": adapter.id,
        "kind": kind.name,
        "source": {
            "size": source_identity["size"],
            "modified_ns": source_identity["modified_ns"],
        },
        "options": relevant_options,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _geometry_asset_record(
    *,
    logical_id: str,
    kind: AssetKind,
    tuid: int,
    name: str,
    relative_file: Path,
    catalog_path: str,
    source_level: str,
    signature: str,
    result: Any,
) -> dict[str, object]:
    return {
        "logical_id": logical_id,
        "kind": kind.name,
        "tuid": f"0x{tuid:016X}",
        "name": name,
        "file": relative_file.as_posix(),
        "catalog_path": catalog_path,
        "source_level": source_level,
        "generation_signature": signature,
        "objects": result.objects,
        "meshes": result.meshes,
        "materials": result.materials,
        "images": result.images,
        "actions": result.actions,
    }


def _action_asset_record(
    *,
    parent_logical_id: str,
    moby_tuid: int,
    action: bpy.types.Action,
    relative_file: Path,
    catalog_path: str,
    source_level: str,
    signature: str,
) -> dict[str, object]:
    return {
        "logical_id": _action_logical_id(
            parent_logical_id,
            action.name,
        ),
        "parent_logical_id": parent_logical_id,
        "kind": "ANIMATION",
        "tuid": f"0x{moby_tuid:016X}",
        "name": action.name,
        "file": relative_file.as_posix(),
        "catalog_path": catalog_path,
        "source_level": source_level,
        "generation_signature": signature,
        "moby_tuid": f"0x{moby_tuid:016X}",
        "frame_rate": action.get("insomniac_frame_rate"),
    }


def _material_asset_record(
    *,
    logical_id: str,
    tuid: int,
    material: bpy.types.Material,
    relative_file: Path,
    catalog_path: str,
    source_level: str,
    signature: str,
    image_count: int,
) -> dict[str, object]:
    return {
        "logical_id": logical_id,
        "kind": "SHADER",
        "tuid": f"0x{tuid:016X}",
        "name": material.name,
        "file": relative_file.as_posix(),
        "catalog_path": catalog_path,
        "source_level": source_level,
        "generation_signature": signature,
        "materials": 1,
        "images": image_count,
    }


def _load_library_manifests(
    library_root: Path,
) -> dict[Path, dict[str, object]]:
    manifests: dict[Path, dict[str, object]] = {}
    if not library_root.is_dir():
        return manifests
    for path in sorted(library_root.glob("*/**/manifest.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InsomniacError(
                f"Could not read Asset Browser manifest {path}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise InsomniacError(
                f"Asset Browser manifest {path} is not an object"
            )
        manifests[path.resolve()] = payload
    return manifests


def _manifest_assets(
    manifest: dict[str, object],
) -> list[dict[str, object]]:
    assets = manifest.get("assets", [])
    if not isinstance(assets, list) or not all(
        isinstance(asset, dict) for asset in assets
    ):
        raise InsomniacError(
            "Asset Browser manifest assets are invalid"
        )
    return assets


def _cached_asset_groups(
    manifests: dict[Path, dict[str, object]],
    library_root: Path,
) -> dict[str, list[list[dict[str, object]]]]:
    groups: dict[str, list[list[dict[str, object]]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for manifest in manifests.values():
        records = _manifest_assets(manifest)
        actions_by_parent: dict[
            str,
            list[dict[str, object]],
        ] = {}
        for record in records:
            parent = record.get("parent_logical_id")
            if isinstance(parent, str):
                actions_by_parent.setdefault(parent, []).append(
                    record
                )
        for record in records:
            logical_id = record.get("logical_id")
            if (
                not isinstance(logical_id, str)
                or record.get("kind") == "ANIMATION"
            ):
                continue
            file_value = record.get("file")
            signature = record.get("generation_signature")
            if not isinstance(file_value, str) or not isinstance(
                signature,
                str,
            ):
                continue
            key = (logical_id, file_value, signature)
            if key in seen:
                continue
            seen.add(key)
            group = [dict(record)]
            group.extend(
                dict(action)
                for action in sorted(
                    actions_by_parent.get(logical_id, ()),
                    key=lambda action: str(
                        action.get("logical_id", "")
                    ),
                )
            )
            if all(
                (library_root / str(item["file"])).is_file()
                for item in group
            ):
                groups.setdefault(logical_id, []).append(group)
    return groups


def _reusable_asset_group(
    groups: Iterable[list[dict[str, object]]],
    *,
    signature: str,
    expected_file: Path,
    library_root: Path,
) -> list[dict[str, object]] | None:
    expected = expected_file.as_posix()
    for group in groups:
        main = group[0]
        if (
            main.get("generation_signature") == signature
            and main.get("file") == expected
            and all(
                (library_root / str(record["file"])).is_file()
                for record in group
            )
        ):
            return group
    return None


def _replace_manifest_groups(
    manifest: dict[str, object],
    replacements: dict[str, list[dict[str, object]]],
) -> tuple[dict[str, object] | None, set[Path]]:
    assets = _manifest_assets(manifest)
    replaced: set[str] = set()
    output: list[dict[str, object]] = []
    removed_files: set[Path] = set()
    for record in assets:
        logical_id = _record_logical_id(manifest, record)
        parent_id = _record_parent_logical_id(manifest, record)
        target = (
            logical_id
            if logical_id in replacements
            else parent_id
            if parent_id in replacements
            else None
        )
        if target is None:
            output.append(record)
            continue
        file_value = record.get("file")
        if isinstance(file_value, str):
            removed_files.add(Path(file_value))
        if target not in replaced:
            output.extend(
                dict(replacement)
                for replacement in replacements[target]
            )
            replaced.add(target)
    if not replaced:
        return None, set()
    updated = dict(manifest)
    updated["assets"] = output
    return updated, removed_files


def _record_logical_id(
    manifest: dict[str, object],
    record: dict[str, object],
) -> str | None:
    logical_id = record.get("logical_id")
    if isinstance(logical_id, str):
        return logical_id
    kind_name = record.get("kind")
    if kind_name == "ANIMATION":
        parent = _record_parent_logical_id(manifest, record)
        name = record.get("name")
        if parent is not None and isinstance(name, str):
            return _action_logical_id(parent, name)
        return None
    if not isinstance(kind_name, str):
        return None
    game_id = manifest.get("game")
    level_name = manifest.get("level")
    if not isinstance(game_id, str) or not isinstance(
        level_name,
        str,
    ):
        return None
    try:
        kind = AssetKind[kind_name]
        tuid = int(str(record["tuid"]), 0)
        adapter = get_game_adapter(game_id)
    except (KeyError, TypeError, ValueError):
        return None
    return _asset_logical_id(adapter, kind, tuid, level_name)


def _record_parent_logical_id(
    manifest: dict[str, object],
    record: dict[str, object],
) -> str | None:
    parent = record.get("parent_logical_id")
    if isinstance(parent, str):
        return parent
    if record.get("kind") != "ANIMATION":
        return None
    game_id = manifest.get("game")
    level_name = manifest.get("level")
    if not isinstance(game_id, str) or not isinstance(
        level_name,
        str,
    ):
        return None
    try:
        tuid = int(
            str(record.get("moby_tuid", record["tuid"])),
            0,
        )
        adapter = get_game_adapter(game_id)
    except (KeyError, TypeError, ValueError):
        return None
    return _asset_logical_id(
        adapter,
        AssetKind.MOBY,
        tuid,
        level_name,
    )


def _catalogs_from_manifests(
    manifests: Iterable[dict[str, object]],
) -> dict[str, tuple[uuid.UUID, str]]:
    catalogs: dict[str, tuple[uuid.UUID, str]] = {}
    for manifest in manifests:
        game_id = manifest.get("game")
        level_name = manifest.get("level")
        if (
            not isinstance(game_id, str)
            or not isinstance(level_name, str)
            or game_id not in GAME_ADAPTERS
        ):
            raise InsomniacError(
                "Asset Browser manifest game or level is invalid"
            )
        adapter = get_game_adapter(game_id)
        records = _manifest_assets(manifest)
        moby_names_by_file = {
            str(record.get("file")): str(record.get("name"))
            for record in records
            if record.get("kind") == "MOBY"
        }
        legacy = (
            manifest.get("schema_version")
            != ASSET_CACHE_SCHEMA_VERSION
        )
        for record in records:
            catalog_path = record.get("catalog_path")
            if not isinstance(catalog_path, str):
                kind_name = record.get("kind")
                if kind_name == "ANIMATION":
                    moby_name = moby_names_by_file.get(
                        str(record.get("file")),
                        str(record.get("name", "Moby")),
                    )
                    components = [
                        (
                            adapter.label
                            if legacy
                            else _catalog_game_component(adapter)
                        )
                    ]
                    if legacy or adapter.level_local_asset_ids:
                        components.append(level_name)
                    components.extend(("Animations", moby_name))
                    catalog_path = "/".join(components)
                else:
                    try:
                        kind = AssetKind[str(kind_name)]
                        plural = _ASSET_KIND_LABELS[kind][1]
                    except (KeyError, TypeError) as exc:
                        raise InsomniacError(
                            "Asset Browser manifest has unsupported "
                            f"kind {kind_name!r}"
                        ) from exc
                    components = [
                        (
                            adapter.label
                            if legacy
                            else _catalog_game_component(adapter)
                        )
                    ]
                    if legacy or adapter.level_local_asset_ids:
                        components.append(level_name)
                    components.append(plural)
                    catalog_path = "/".join(components)
            catalogs[catalog_path] = (
                uuid.uuid5(_CATALOG_NAMESPACE, catalog_path),
                catalog_path.rsplit("/", 1)[-1],
            )
    return catalogs


def _write_catalog_file(
    existing_path: Path,
    output_path: Path,
    catalogs: dict[str, tuple[uuid.UUID, str]],
) -> None:
    entries: dict[str, tuple[str, str]] = {}
    game_prefixes = tuple(
        prefix
        for adapter in GAME_ADAPTERS.values()
        for prefix in (
            f"{adapter.label}/",
            f"{_catalog_game_component(adapter)}/",
        )
    )
    if existing_path.is_file():
        try:
            lines = existing_path.read_text(
                encoding="utf-8"
            ).splitlines()
        except OSError as exc:
            raise InsomniacError(
                f"Could not read Asset Browser catalogs: {exc}"
            ) from exc
        for raw_line in lines:
            if (
                not raw_line
                or raw_line.startswith("#")
                or raw_line == "VERSION 1"
            ):
                continue
            try:
                catalog_id, remainder = raw_line.split(":", 1)
                path, simple_name = remainder.rsplit(":", 1)
            except ValueError as exc:
                raise InsomniacError(
                    "Invalid Asset Browser catalog line "
                    f"{raw_line!r}"
                ) from exc
            if path.startswith(game_prefixes):
                continue
            if catalog_id in entries:
                raise InsomniacError(
                    "Duplicate Asset Browser catalog UUID "
                    f"{catalog_id}"
                )
            if any(
                existing_path == path
                for existing_path, _name in entries.values()
            ):
                raise InsomniacError(
                    "Duplicate Asset Browser catalog path "
                    f"{path}"
                )
            entries[catalog_id] = (path, simple_name)
    for catalog_path, (catalog_id, simple_name) in catalogs.items():
        entries[str(catalog_id)] = (catalog_path, simple_name)

    output_path.parent.mkdir(parents=True, exist_ok=True)
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
    output_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _referenced_asset_files(
    library_root: Path,
    manifests: Iterable[dict[str, object]],
) -> set[Path]:
    root = library_root.resolve()
    referenced: set[Path] = set()
    for manifest in manifests:
        for record in _manifest_assets(manifest):
            file_value = record.get("file")
            if not isinstance(file_value, str):
                raise InsomniacError(
                    "Asset Browser manifest file path is invalid"
                )
            path = (root / file_value).resolve()
            if not _is_within(path, root):
                raise InsomniacError(
                    "Asset Browser manifest path escapes the library: "
                    f"{file_value}"
                )
            referenced.add(path)
    return referenced


def _transactional_replace(
    replacements: Iterable[tuple[Path, Path]],
    deletions: Iterable[Path],
    *,
    staging_root: Path,
) -> None:
    replacement_list = list(replacements)
    targets = [
        target.resolve() for _staged, target in replacement_list
    ]
    if len(set(targets)) != len(targets):
        raise InsomniacError(
            "Asset Browser transaction has duplicate replacement targets"
        )
    missing = [
        staged
        for staged, _target in replacement_list
        if not staged.exists()
    ]
    if missing:
        raise InsomniacError(
            "Asset Browser transaction is missing staged path "
            f"{missing[0]}"
        )

    target_set = set(targets)
    deletion_list = sorted(
        {
            path.resolve()
            for path in deletions
            if path.resolve() not in target_set
        },
        key=lambda path: len(path.parts),
        reverse=True,
    )
    backup_root = staging_root / "_backup"
    backup_root.mkdir(parents=True, exist_ok=False)
    backups: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        for index, target in enumerate(
            [*targets, *deletion_list]
        ):
            if not target.exists():
                continue
            backup = backup_root / f"{index:08d}"
            target.replace(backup)
            backups.append((target, backup))
        for staged, target in replacement_list:
            resolved_target = target.resolve()
            resolved_target.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            staged.replace(resolved_target)
            installed.append(resolved_target)
    except Exception:
        for target in reversed(installed):
            _remove_path(target)
        for target, backup in reversed(backups):
            target.parent.mkdir(parents=True, exist_ok=True)
            backup.replace(target)
        raise
    shutil.rmtree(staging_root)


def _write_json(
    path: Path,
    payload: dict[str, object],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _group_warnings(
    group: list[dict[str, object]],
) -> list[str]:
    warnings = group[0].get("warnings", ())
    if not isinstance(warnings, list):
        return []
    return [str(warning) for warning in warnings]


def _count_kind(
    assets: Iterable[dict[str, object]],
    kind: str,
) -> int:
    return sum(asset.get("kind") == kind for asset in assets)
