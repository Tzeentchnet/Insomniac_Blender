from __future__ import annotations

from dataclasses import dataclass
import json
from functools import lru_cache
from pathlib import Path
import tempfile
from typing import Callable, Iterable

from ..core.errors import InsomniacError, MissingResourceError
from ..core.iso import extract_game_iso_member, list_game_levels
from ..core.source import GameSource
from .registry import get_game_adapter
from .resistance_2.formats import AssetKind


CATALOG_SCHEMA_VERSION = 1
CATALOG_FILENAME = "disc_catalog.json"
_DISC_LEVEL_ROOT = "PS3_GAME/USRDIR/packed/levels"


@dataclass(frozen=True, slots=True)
class CatalogAsset:
    identifier: str
    kind: AssetKind
    tuid: int
    levels: tuple[str, ...]
    source_name: str | None = None
    game_label: str = "Resistance 2"
    kind_label: str | None = None

    @property
    def display_name(self) -> str:
        if not self.source_name:
            return f"{self.kind.name.title()} 0x{self.tuid:016X}"
        filename = self.source_name.replace("\\", "/").rsplit("/", 1)[-1]
        lower_filename = filename.lower()
        for suffix in (".entity.irb", ".irb", ".entity", ".smf"):
            if lower_filename.endswith(suffix):
                filename = filename[: -len(suffix)]
                break
        words = filename.replace("_", " ").replace("-", " ").split()
        return " ".join(word.capitalize() for word in words) or self.source_name

    @property
    def identity_label(self) -> str:
        kind = self.kind_label or (
            "Moby" if self.kind is AssetKind.MOBY else "Tie"
        )
        return f"{kind} 0x{self.tuid:016X}"

    @property
    def owner_label(self) -> str:
        owner = self.levels[0]
        if len(self.levels) > 1:
            owner = f"{owner} +{len(self.levels) - 1}"
        return owner

    @property
    def duplicate_key(self) -> str:
        return self.display_name.casefold()

    @property
    def search_text(self) -> str:
        source_name = (self.source_name or "").replace("\\", "/")
        expanded_path = (
            source_name.replace("/", " ")
            .replace("_", " ")
            .replace("-", " ")
        )
        return " ".join(
            (
                source_name,
                expanded_path,
                self.display_name,
                self.identity_label,
                f"0x{self.tuid:016X}",
                *self.levels,
            )
        ).casefold()

    def selector_label(
        self,
        *,
        show_tuid: bool,
        show_owner: bool,
    ) -> str:
        parts = [self.display_name]
        if show_tuid and self.identity_label != self.display_name:
            parts.append(self.identity_label)
        if show_owner:
            parts.append(self.owner_label)
        return " - ".join(parts)

    @property
    def label(self) -> str:
        return self.selector_label(show_tuid=True, show_owner=True)

    @property
    def description(self) -> str:
        owners = ", ".join(self.levels[:6])
        if len(self.levels) > 6:
            owners += f", and {len(self.levels) - 6} more"
        kind = self.kind_label or self.kind.name.title()
        identity = f"{self.game_label} {kind} 0x{self.tuid:016X}"
        if not self.source_name:
            return f"{identity}; available in {owners}"
        return f"{self.source_name}; {identity}; available in {owners}"


def filter_catalog_assets(
    assets: Iterable[CatalogAsset],
    query: str,
    *,
    hide_duplicates: bool,
    kind: AssetKind | None = None,
    level: str | None = None,
) -> tuple[CatalogAsset, ...]:
    """Return the assets that satisfy every active filter.

    ``kind`` and ``level`` narrow the catalog structurally; ``query`` then
    requires every whitespace-separated fragment to appear in the asset's
    searchable text. Duplicate names are collapsed last, so the surviving
    entry always satisfies the other filters.
    """
    terms = tuple(
        term
        for term in query.casefold().replace("\\", "/").split()
        if term
    )
    matches: list[CatalogAsset] = []
    seen_names: set[str] = set()
    for asset in assets:
        if kind is not None and asset.kind is not kind:
            continue
        if level is not None and level not in asset.levels:
            continue
        search_text = asset.search_text
        if terms and not all(
            term in search_text
            or (
                len(term) > 3
                and term.endswith("s")
                and term[:-1] in search_text
            )
            for term in terms
        ):
            continue
        if hide_duplicates:
            if asset.duplicate_key in seen_names:
                continue
            seen_names.add(asset.duplicate_key)
        matches.append(asset)
    return tuple(matches)


class GameCatalog:
    def __init__(
        self,
        *,
        game_id: str,
        game_label: str,
        path: Path,
        region: str,
        levels: tuple[str, ...],
        assets: tuple[CatalogAsset, ...],
    ) -> None:
        self.game_id = game_id
        self.game_label = game_label
        self.path = path
        self.region = region
        self.levels = levels
        self.assets = assets
        self._assets_by_identifier = {
            asset.identifier: asset for asset in assets
        }
        self._asset_numbers_by_identifier = {
            asset.identifier: index
            for index, asset in enumerate(assets, start=1)
        }

    def asset(self, identifier: str) -> CatalogAsset:
        try:
            return self._assets_by_identifier[identifier]
        except KeyError as exc:
            raise MissingResourceError(
                f"Asset catalog entry {identifier!r} is unavailable"
            ) from exc

    def asset_number(self, identifier: str) -> int:
        try:
            return self._asset_numbers_by_identifier[identifier]
        except KeyError as exc:
            raise MissingResourceError(
                f"Asset catalog entry {identifier!r} is unavailable"
            ) from exc


def bundled_catalog_path(game_id: str) -> Path:
    adapter = get_game_adapter(game_id)
    if adapter.bundled_catalog_path is None:
        raise MissingResourceError(
            f"{adapter.label} uses a source-driven catalog"
        )
    return adapter.bundled_catalog_path


def local_catalog_path(
    cache_directory: str | Path,
    game_id: str,
) -> Path:
    adapter = get_game_adapter(game_id)
    return (
        Path(cache_directory).expanduser()
        / adapter.cache_key
        / CATALOG_FILENAME
    )


def load_catalog(
    cache_directory: str | Path,
    game_id: str = "RESISTANCE_2",
    *,
    source_path: str | Path | None = None,
) -> GameCatalog:
    adapter = get_game_adapter(game_id)
    if adapter.create_source_catalog is not None:
        return adapter.create_source_catalog(source_path)
    local = local_catalog_path(cache_directory, game_id)
    path = local if local.is_file() else bundled_catalog_path(game_id)
    if not path.is_file():
        raise MissingResourceError(
            f"{adapter.label} import catalog was not found: {path}"
        )
    stat = path.stat()
    return _load_catalog_cached(
        str(path.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
        adapter.id,
        adapter.label,
    )


@lru_cache(maxsize=8)
def _load_catalog_cached(
    path_text: str,
    _modified_ns: int,
    _size: int,
    game_id: str,
    game_label: str,
) -> GameCatalog:
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InsomniacError(f"Could not read import catalog {path}: {exc}") from exc
    if payload.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise InsomniacError(
            f"{path}: unsupported catalog schema "
            f"{payload.get('schema_version')!r}"
        )
    if payload.get("game") != game_id:
        raise InsomniacError(
            f"{path}: catalog is not for {game_label}"
        )

    raw_levels = payload.get("levels")
    if not isinstance(raw_levels, list) or not raw_levels:
        raise InsomniacError(f"{path}: catalog contains no levels")
    raw_names = payload.get("names", {})
    if not isinstance(raw_names, dict):
        raise InsomniacError(f"{path}: catalog names must be an object")
    names: dict[str, str] = {}
    for identifier, source_name in raw_names.items():
        if (
            not isinstance(identifier, str)
            or not isinstance(source_name, str)
            or not source_name
        ):
            raise InsomniacError(f"{path}: malformed catalog name entry")
        names[identifier] = source_name
    level_ids: list[str] = []
    owners: dict[tuple[AssetKind, int], list[str]] = {}
    for raw_level in raw_levels:
        if not isinstance(raw_level, dict):
            raise InsomniacError(f"{path}: malformed level catalog entry")
        level_id = raw_level.get("id")
        if not isinstance(level_id, str) or not level_id:
            raise InsomniacError(f"{path}: level catalog entry has no ID")
        if level_id in level_ids:
            raise InsomniacError(f"{path}: duplicate level {level_id!r}")
        level_ids.append(level_id)
        for field, kind in (("mobys", AssetKind.MOBY), ("ties", AssetKind.TIE)):
            values = raw_level.get(field)
            if not isinstance(values, list):
                raise InsomniacError(
                    f"{path}: level {level_id!r} has no {field} list"
                )
            for value in values:
                try:
                    tuid = int(value, 16)
                except (TypeError, ValueError) as exc:
                    raise InsomniacError(
                        f"{path}: invalid {field} TUID {value!r}"
                    ) from exc
                owners.setdefault((kind, tuid), []).append(level_id)

    levels = tuple(
        sorted(
            level_ids,
            key=lambda value: (value.casefold() != "lobby", value.casefold()),
        )
    )
    assets = tuple(
        CatalogAsset(
            identifier=f"{kind.name}:{tuid:016X}",
            kind=kind,
            tuid=tuid,
            levels=tuple(
                sorted(
                    asset_levels,
                    key=lambda value: (
                        value.casefold() != "lobby",
                        value.casefold(),
                    ),
                )
            ),
            source_name=names.get(f"{kind.name}:{tuid:016X}"),
            game_label=game_label,
        )
        for (kind, tuid), asset_levels in sorted(
            owners.items(),
            key=lambda item: (
                "lobby" not in {
                    level.casefold() for level in item[1]
                },
                item[0][0] is not AssetKind.MOBY,
                item[0][1],
            ),
        )
    )
    return GameCatalog(
        game_id=game_id,
        game_label=game_label,
        path=path,
        region=str(payload.get("region") or "Custom"),
        levels=levels,
        assets=assets,
    )


def build_catalog_from_iso(
    iso_path: str | Path,
    output_path: str | Path,
    *,
    game_id: str = "RESISTANCE_2",
    region: str = "Custom",
    work_directory: str | Path | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, object]:
    adapter = get_game_adapter(game_id)
    iso = Path(iso_path).expanduser().resolve()
    if not iso.is_file():
        raise MissingResourceError(
            f"{adapter.label} ISO was not found: {iso}"
        )
    levels = list_game_levels(iso, game_label=adapter.label)
    work_parent = (
        Path(work_directory).expanduser().resolve()
        if work_directory is not None
        else None
    )
    if work_parent is not None:
        work_parent.mkdir(parents=True, exist_ok=True)

    catalog_levels: list[dict[str, object]] = []
    asset_names: dict[str, str] = {}
    with tempfile.TemporaryDirectory(
        prefix=f"{adapter.cache_key}_catalog_",
        dir=work_parent,
    ) as temporary:
        archive_path = Path(temporary) / "level_cached.psarc"
        for index, level in enumerate(levels, start=1):
            if progress is not None:
                progress(index, len(levels), level)
            member = (
                f"{_DISC_LEVEL_ROOT}/{level}/level_cached.psarc"
            )
            extract_game_iso_member(
                iso,
                member,
                archive_path,
                game_label=adapter.label,
            )
            try:
                with GameSource.open(archive_path) as source:
                    reader = adapter.create_reader(source)
                    moby_pointers = reader.asset_pointers(AssetKind.MOBY)
                    tie_pointers = reader.asset_pointers(AssetKind.TIE)
                    for kind, pointers in (
                        (AssetKind.MOBY, moby_pointers),
                        (AssetKind.TIE, tie_pointers),
                    ):
                        for pointer in pointers:
                            identifier = (
                                f"{kind.name}:{pointer.tuid:016X}"
                            )
                            if identifier in asset_names:
                                continue
                            source_name = reader.read_asset_name(
                                kind,
                                pointer.tuid,
                            )
                            if source_name:
                                asset_names[identifier] = source_name
                    mobys = [
                        f"0x{pointer.tuid:016X}"
                        for pointer in moby_pointers
                    ]
                    ties = [
                        f"0x{pointer.tuid:016X}"
                        for pointer in tie_pointers
                    ]
            finally:
                archive_path.unlink(missing_ok=True)
            catalog_levels.append(
                {
                    "id": level,
                    "mobys": mobys,
                    "ties": ties,
                }
            )

    payload = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "game": adapter.id,
        "region": region,
        "disc": {
            "size": iso.stat().st_size,
            "modified_ns": iso.stat().st_mtime_ns,
        },
        "levels": catalog_levels,
        "names": asset_names,
    }
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    temporary_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_output.replace(output)

    unique_assets = {
        (kind, value)
        for level in catalog_levels
        for kind, field in (("MOBY", "mobys"), ("TIE", "ties"))
        for value in level[field]
    }
    return {
        "catalog": str(output),
        "levels": len(catalog_levels),
        "asset_references": sum(
            len(level["mobys"]) + len(level["ties"])
            for level in catalog_levels
        ),
        "unique_assets": len(unique_assets),
        "mobys": sum(kind == "MOBY" for kind, _value in unique_assets),
        "ties": sum(kind == "TIE" for kind, _value in unique_assets),
        "named_assets": len(asset_names),
        "named_mobys": sum(
            identifier.startswith("MOBY:") for identifier in asset_names
        ),
        "named_ties": sum(
            identifier.startswith("TIE:") for identifier in asset_names
        ),
    }
