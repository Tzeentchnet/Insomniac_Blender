from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.errors import AmbiguousResourceError, InsomniacError
from ...core.iso import prepare_game_level
from ...core.source import GameSource
from ..catalog import (
    CatalogDiscIdentity,
    CatalogTextureOwner,
    GameCatalog,
    load_catalog,
)
from .reader import Resistance2Reader

if TYPE_CHECKING:
    from ..registry import GameAdapter


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TextureDependency:
    texture_id: int
    lookup_path: str
    candidates: tuple[CatalogTextureOwner, ...]

    @property
    def levels(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {candidate.level for candidate in self.candidates},
                key=str.casefold,
            )
        )


def build_texture_catalog_metadata(reader: object) -> dict[str, object]:
    if not isinstance(reader, Resistance2Reader):
        raise TypeError("R2 texture catalog metadata requires an R2 reader")
    paths_by_id = reader.texture_lookup_paths_by_id()
    textures = [
        {
            "id": f"0x{info.id:08X}",
            "tuid": f"0x{info.tuid:016X}",
            "format_code": info.format_code,
            "mip_count": info.mip_count,
            "width": info.width,
            "height": info.height,
            "length": info.length,
            "sha256": reader.texture_payload_digest(info.id),
            "paths": list(paths_by_id.get(info.id, ())),
        }
        for info in sorted(
            reader.texture_infos.values(),
            key=lambda value: value.id,
        )
    ]
    return {"textures": textures}


def plan_texture_dependencies(
    reader: Resistance2Reader,
    catalog: GameCatalog,
    level_name: str,
) -> tuple[TextureDependency, ...]:
    planned: list[
        tuple[int, str, tuple[CatalogTextureOwner, ...]]
    ] = []
    missing_owners: list[tuple[int, str]] = []
    for (
        texture_id,
        lookup_path,
    ) in reader.missing_exact_texture_references():
        exact_candidates = catalog.texture_owners_for_id(texture_id)
        if not exact_candidates and reader.has_available_texture_for_path(
            texture_id,
            lookup_path,
        ):
            continue
        indexed_candidates = (
            exact_candidates
            if exact_candidates
            else catalog.texture_owners_for_path(lookup_path)
        )
        candidates = tuple(
            candidate
            for candidate in indexed_candidates
            if candidate.level.casefold() != level_name.casefold()
        )
        if not candidates:
            missing_owners.append((texture_id, lookup_path))
            continue
        exact = bool(exact_candidates)
        if len(candidates) > 1 and any(
            candidate.payload_sha256 is None
            for candidate in candidates
        ):
            raise AmbiguousResourceError(
                f"Texture 0x{texture_id:08X} has multiple catalog owners "
                "without payload digests; regenerate the import catalog"
            )
        signatures = {
            (
                candidate.format_code,
                candidate.width,
                candidate.height,
                candidate.payload_sha256,
            )
            for candidate in candidates
        }
        if len(signatures) > 1:
            choices = ", ".join(
                (
                    f"{candidate.level}:"
                    f"0x{candidate.texture_id:08X}/"
                    f"0x{candidate.tuid:016X} "
                    f"{candidate.width}x{candidate.height} "
                    f"format 0x{candidate.format_code:02X} "
                    f"sha256 {candidate.payload_sha256 or 'missing'}"
                )
                for candidate in candidates
            )
            source = (
                f"ID 0x{texture_id:08X}"
                if exact
                else f"path {lookup_path!r}"
            )
            raise AmbiguousResourceError(
                f"Texture {source} has conflicting catalog payloads: "
                f"{choices}"
            )
        signature = next(iter(signatures))
        if exact and signature[-1] is not None:
            aliases = reader.available_texture_ids_for_path(
                texture_id,
                lookup_path,
            )
            has_identical_local_alias = any(
                (
                    owner.format_code,
                    owner.width,
                    owner.height,
                    owner.payload_sha256,
                )
                == signature
                for alias_id in aliases
                for owner in catalog.texture_owners_for_id(alias_id)
                if owner.level.casefold() == level_name.casefold()
            )
            if has_identical_local_alias:
                continue
        planned.append((texture_id, lookup_path, candidates))
    if missing_owners:
        LOGGER.warning(
            "R2 texture catalog has no owner for %s",
            ", ".join(
                (
                    f"0x{texture_id:08X}"
                    + (f" ({lookup_path})" if lookup_path else "")
                )
                for texture_id, lookup_path in missing_owners
            ),
        )
    uncovered = set(range(len(planned)))
    selected_levels: list[str] = []
    while uncovered:
        coverage: dict[str, set[int]] = {}
        for dependency_index in uncovered:
            for candidate in planned[dependency_index][2]:
                coverage.setdefault(candidate.level, set()).add(
                    dependency_index
                )
        selected_level = min(
            coverage,
            key=lambda level: (
                -len(coverage[level]),
                level.casefold(),
                level,
            ),
        )
        selected_levels.append(selected_level)
        uncovered.difference_update(coverage[selected_level])

    level_priority = {
        level: index for index, level in enumerate(selected_levels)
    }
    return tuple(
        TextureDependency(
            texture_id=texture_id,
            lookup_path=lookup_path,
            candidates=(
                min(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.level in level_priority
                    ),
                    key=lambda candidate: (
                        level_priority[candidate.level],
                        candidate.level.casefold(),
                        candidate.texture_id,
                        candidate.tuid,
                    ),
                ),
            ),
        )
        for texture_id, lookup_path, candidates in planned
    )


def _validate_catalog_disc(
    catalog: GameCatalog,
    iso_path: str | Path,
    adapter: GameAdapter,
) -> CatalogDiscIdentity:
    disc = catalog.disc
    if disc is None:
        raise InsomniacError(
            f"{catalog.path}: texture ownership metadata has no disc identity; "
            "regenerate the import catalog"
        )
    iso = Path(iso_path).expanduser().resolve()
    actual_size = iso.stat().st_size
    if disc.size != actual_size:
        raise InsomniacError(
            f"{catalog.path}: texture ownership metadata is for a "
            f"{disc.size}-byte disc, but the selected ISO is "
            f"{actual_size} bytes; regenerate the import catalog"
        )
    if (
        disc.title_id is not None
        and adapter.iso_title_id is not None
        and disc.title_id != adapter.iso_title_id
    ):
        raise InsomniacError(
            f"{catalog.path}: texture ownership metadata is for "
            f"{disc.title_id}, not {adapter.iso_title_id}"
        )
    if (
        disc.version is not None
        and adapter.iso_version is not None
        and disc.version != adapter.iso_version
    ):
        raise InsomniacError(
            f"{catalog.path}: texture ownership metadata is for version "
            f"{disc.version}, not {adapter.iso_version}"
        )
    return disc


def prepare_texture_dependencies(
    adapter: GameAdapter,
    iso_path: str | Path,
    level_name: str,
    cache_directory: str | Path,
    cached_level: Path,
) -> None:
    catalog = load_catalog(cache_directory, adapter.id)
    with GameSource.open(cached_level) as source:
        reader = Resistance2Reader(source)
        missing_exact = reader.missing_exact_texture_references()
        if not missing_exact:
            return
        if not catalog.texture_owners:
            raise InsomniacError(
                f"{catalog.path}: catalog has no R2 texture ownership "
                "metadata; regenerate the import catalog"
            )
        catalog_disc = _validate_catalog_disc(catalog, iso_path, adapter)
        dependencies = plan_texture_dependencies(
            reader,
            catalog,
            level_name,
        )
    if not dependencies:
        return

    sibling_levels = tuple(
        sorted(
            {
                level
                for dependency in dependencies
                for level in dependency.levels
            },
            key=str.casefold,
        )
    )
    dependency_metadata = {
        "schema_version": 1,
        "kind": "r2_texture_owners",
        "catalog_disc": catalog_disc.as_dict(),
        "levels": list(sibling_levels),
        "textures": [
            {
                "id": f"0x{dependency.texture_id:08X}",
                "path": dependency.lookup_path or None,
                "owners": list(dependency.levels),
            }
            for dependency in dependencies
        ],
    }
    prepare_game_level(
        iso_path,
        level_name,
        cache_directory,
        cache_key=adapter.cache_key,
        game_label=adapter.label,
        level_archives=adapter.level_archives,
        global_archives=adapter.global_archives,
        primary_resource=adapter.level_primary_resource,
        container_member=adapter.iso_container_member,
        expected_title_id=adapter.iso_title_id,
        expected_version=adapter.iso_version,
        sibling_levels=sibling_levels,
        dependency_metadata=dependency_metadata,
    )

    with GameSource.open(cached_level) as source:
        reader = Resistance2Reader(source)
        for dependency in dependencies:
            reader.read_texture(
                dependency.texture_id,
                lookup_path=dependency.lookup_path or None,
            )


__all__ = (
    "TextureDependency",
    "build_texture_catalog_metadata",
    "plan_texture_dependencies",
    "prepare_texture_dependencies",
)
