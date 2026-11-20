from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

import bpy
from bpy.app.handlers import persistent

from ..core.errors import InsomniacError, MissingResourceError
from ..core.iso import DEFAULT_CACHE_DIRECTORY
from ..games.catalog import (
    CatalogAsset,
    GameCatalog,
    filter_catalog_assets,
    load_catalog,
)
from ..games.registry import GAME_ADAPTERS
from ..games.resistance_2.formats import AssetKind

AssetEnumItem = tuple[str, str, str, int, int]


_ASSET_ITEMS: list[AssetEnumItem] = []
_LEVEL_ITEMS: list[tuple[str, str, str]] = []
_LEVEL_FILTER_ITEMS: list[tuple[str, str, str]] = []
DEFAULT_ASSET_IDENTIFIER = "MOBY:00897CA26A876DEF"
ALL_KINDS = "ALL"
ALL_LEVELS = "__ALL__"
LOGGER = logging.getLogger(__name__)
_PERSISTENT_SETTING_NAMES = (
    "game",
    "source_path",
    "asset_filter",
    "asset_kind_filter",
    "asset_level_filter",
    "asset_tuid",
    "level_id",
)
_SESSION_SETTINGS: dict[str, str] = {}
_RESTORING_PERSISTED_SELECTION = False


@dataclass(frozen=True, slots=True)
class AssetSelectorState:
    """Everything the sidebar needs to draw the asset selector."""

    items: tuple[AssetEnumItem, ...]
    total_count: int
    match_count: int
    filters_active: bool
    selected: CatalogAsset | None
    selected_label: str
    selection_matches_filter: bool


def _current_scene_settings() -> bpy.types.PropertyGroup | None:
    scene = getattr(bpy.context, "scene", None)
    if scene is None or not hasattr(scene, "insomniac_asset_io"):
        return None
    return scene.insomniac_asset_io


def _addon_preferences(
    context: bpy.types.Context | None,
) -> bpy.types.AddonPreferences | None:
    if context is None:
        context = getattr(bpy, "context", None)
    if context is None:
        return None
    from .preferences import addon_preferences

    return addon_preferences(context)


def _initialize_source_preferences(
    preferences: bpy.types.AddonPreferences,
) -> None:
    if preferences.source_paths_initialized:
        return
    for adapter in GAME_ADAPTERS.values():
        source_path = ""
        if adapter.supports_iso:
            source_path = getattr(
                preferences,
                adapter.iso_preference_name,
                "",
            )
        setattr(
            preferences,
            adapter.source_preference_name,
            source_path,
        )
    preferences.source_paths_initialized = True


def _remember_game_source(
    settings: bpy.types.PropertyGroup,
    context: bpy.types.Context | None,
) -> None:
    if _RESTORING_PERSISTED_SELECTION:
        return
    preferences = _addon_preferences(context)
    if preferences is None:
        return
    _initialize_source_preferences(preferences)
    game_id = _game_id(settings)
    preferences.last_game = game_id
    setattr(
        preferences,
        GAME_ADAPTERS[game_id].source_preference_name,
        settings.source_path,
    )


def _restore_game_source(
    settings: bpy.types.PropertyGroup,
    context: bpy.types.Context | None,
) -> bool:
    global _RESTORING_PERSISTED_SELECTION
    preferences = _addon_preferences(context)
    if preferences is None:
        return False
    _initialize_source_preferences(preferences)
    game_id = preferences.last_game
    if game_id not in GAME_ADAPTERS:
        game_id = "RESISTANCE_2"
    source_path = getattr(
        preferences,
        GAME_ADAPTERS[game_id].source_preference_name,
        "",
    )
    _RESTORING_PERSISTED_SELECTION = True
    try:
        settings.game = game_id
        settings.source_path = source_path
    finally:
        _RESTORING_PERSISTED_SELECTION = False
    return True


@persistent
def _remember_scene_settings(_unused: object = None) -> None:
    settings = _current_scene_settings()
    if settings is None:
        return
    _remember_game_source(settings, getattr(bpy, "context", None))
    for name in _PERSISTENT_SETTING_NAMES:
        _SESSION_SETTINGS[name] = getattr(settings, name)


@persistent
def _restore_scene_settings(_unused: object = None) -> None:
    settings = _current_scene_settings()
    if settings is None:
        return
    previous_game = _game_id(settings)
    had_session_settings = bool(_SESSION_SETTINGS)
    restored_preferences = _restore_game_source(
        settings,
        getattr(bpy, "context", None),
    )
    for name, value in _SESSION_SETTINGS.items():
        if restored_preferences and name in {"game", "source_path"}:
            continue
        try:
            setattr(settings, name, value)
        except (TypeError, ValueError) as exc:
            LOGGER.warning(
                "Could not restore Insomniac setting %s=%r: %s",
                name,
                value,
                exc,
            )
    if (
        restored_preferences
        and not had_session_settings
        and previous_game != _game_id(settings)
    ):
        _reset_game_specific_choices(settings, bpy.context)
    if not had_session_settings:
        for name in _PERSISTENT_SETTING_NAMES:
            _SESSION_SETTINGS[name] = getattr(settings, name)


def register_scene_settings_persistence() -> None:
    _restore_scene_settings()
    if _remember_scene_settings not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_remember_scene_settings)
    if _restore_scene_settings not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_restore_scene_settings)


def unregister_scene_settings_persistence() -> None:
    _remember_scene_settings()
    if _remember_scene_settings in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_remember_scene_settings)
    if _restore_scene_settings in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_restore_scene_settings)


def _cache_directory(context: bpy.types.Context | None) -> str:
    if context is None:
        return str(DEFAULT_CACHE_DIRECTORY)
    from .preferences import addon_preferences

    preferences = addon_preferences(context)
    if preferences is None:
        return str(DEFAULT_CACHE_DIRECTORY)
    return bpy.path.abspath(preferences.asset_cache_directory)


def _selector_preferences(
    context: bpy.types.Context | None,
) -> tuple[bool, bool, bool]:
    if context is None:
        return True, False, False
    from .preferences import addon_preferences

    preferences = addon_preferences(context)
    if preferences is None:
        return True, False, False
    return (
        bool(preferences.asset_selector_hide_duplicates),
        bool(preferences.asset_selector_show_tuids),
        bool(preferences.asset_selector_show_owner_levels),
    )


def _game_id(settings: bpy.types.PropertyGroup | None) -> str:
    if settings is None:
        return "RESISTANCE_2"
    value = getattr(settings, "game", "RESISTANCE_2")
    return value if value in GAME_ADAPTERS else "RESISTANCE_2"


def _catalog(
    settings: bpy.types.PropertyGroup | None,
    context: bpy.types.Context | None,
) -> GameCatalog:
    game_id = _game_id(settings)
    source_path = (
        bpy.path.abspath(settings.source_path)
        if settings is not None and getattr(settings, "source_path", "")
        else None
    )
    cache_directory = _cache_directory(context)
    try:
        return load_catalog(
            cache_directory,
            game_id,
            source_path=source_path,
        )
    except InsomniacError as exc:
        adapter = GAME_ADAPTERS[game_id]
        if source_path is None or adapter.create_source_catalog is None:
            raise
        LOGGER.warning(
            "Could not load %s source catalog from %s: %s",
            adapter.label,
            source_path,
            exc,
        )
        return load_catalog(
            cache_directory,
            game_id,
            source_path=None,
        )


def _kind_filter(settings: bpy.types.PropertyGroup) -> AssetKind | None:
    value = settings.asset_kind_filter
    if value == "MOBY":
        return AssetKind.MOBY
    if value == "TIE":
        return AssetKind.TIE
    if value == "SHADER":
        return AssetKind.SHADER
    return None


def _level_filter(
    settings: bpy.types.PropertyGroup,
    catalog: GameCatalog,
) -> str | None:
    # A dynamic enum reads back as "" when its stored index no longer exists
    # (for example after the catalog was regenerated); treat that as no filter.
    value = settings.asset_level_filter
    if value in catalog.levels:
        return value
    return None


@lru_cache(maxsize=64)
def _build_asset_items(
    catalog: GameCatalog,
    query: str,
    kind: AssetKind | None,
    level: str | None,
    hide_duplicates: bool,
    show_tuids: bool,
    show_owner_levels: bool,
) -> tuple[tuple[AssetEnumItem, ...], int]:
    """Return every matching asset, alphabetically, plus the unfiltered total.

    The list is deliberately unbounded: it feeds a search popup, which only
    draws the rows that fit, so the whole catalog stays reachable.
    """
    matches = sorted(
        filter_catalog_assets(
            catalog.assets,
            query,
            hide_duplicates=hide_duplicates,
            kind=kind,
            level=level,
        ),
        key=lambda asset: (asset.duplicate_key, asset.identifier),
    )
    duplicate_counts = Counter(asset.duplicate_key for asset in matches)
    items: list[AssetEnumItem] = []
    for asset in matches:
        label = asset.selector_label(
            show_tuid=show_tuids,
            show_owner=show_owner_levels,
        )
        if not show_tuids and duplicate_counts[asset.duplicate_key] > 1:
            label = f"{label} - {asset.identity_label}"
        items.append(
            (
                asset.identifier,
                label,
                asset.description,
                0,
                catalog.asset_number(asset.identifier),
            )
        )
    if query.strip() or kind is not None or level is not None:
        total = len(
            filter_catalog_assets(
                catalog.assets,
                "",
                hide_duplicates=hide_duplicates,
            )
        )
    else:
        total = len(matches)
    return tuple(items), total


def asset_selector_state(
    settings: bpy.types.PropertyGroup,
    context: bpy.types.Context | None,
) -> AssetSelectorState:
    catalog = _catalog(settings, context)
    hide_duplicates, show_tuids, show_owner_levels = _selector_preferences(context)
    query = settings.asset_filter
    kind = _kind_filter(settings)
    level = _level_filter(settings, catalog)
    items, total = _build_asset_items(
        catalog,
        query,
        kind,
        level,
        hide_duplicates,
        show_tuids,
        show_owner_levels,
    )
    try:
        selected = catalog.asset(settings.asset_tuid)
    except MissingResourceError:
        selected = None
    # Checked against the selected asset itself, so a selection that is merely
    # the hidden twin of a listed duplicate still counts as matching.
    selection_matches_filter = selected is not None and bool(
        filter_catalog_assets(
            (selected,),
            query,
            hide_duplicates=False,
            kind=kind,
            level=level,
        )
    )
    return AssetSelectorState(
        items=items,
        total_count=total,
        match_count=len(items),
        filters_active=bool(query.strip() or kind is not None or level is not None),
        selected=selected,
        selected_label=(
            selected.selector_label(
                show_tuid=show_tuids,
                show_owner=show_owner_levels,
            )
            if selected is not None
            else ""
        ),
        selection_matches_filter=selection_matches_filter,
    )


def asset_search_items(
    _owner: object,
    context: bpy.types.Context | None,
) -> list[AssetEnumItem]:
    """Enum callback listing every asset that passes the sidebar filters."""
    global _ASSET_ITEMS
    if context is None or context.scene is None:
        return _ASSET_ITEMS
    settings = context.scene.insomniac_asset_io
    # Blender keeps pointers into these strings, so the list must stay alive.
    _ASSET_ITEMS = list(asset_selector_state(settings, context).items)
    return _ASSET_ITEMS


def asset_selector_status(state: AssetSelectorState) -> str:
    if not state.filters_active:
        return f"{state.total_count:,} assets; click Asset to search by name"
    if state.match_count == 0:
        return f"No assets match the filters (0 of {state.total_count:,})"
    return f"{state.match_count:,} of {state.total_count:,} assets match"


def set_asset_selection(
    settings: bpy.types.PropertyGroup,
    identifier: str,
) -> None:
    settings.asset_tuid = identifier


def clear_asset_filters(settings: bpy.types.PropertyGroup) -> None:
    settings.asset_filter = ""
    settings.asset_kind_filter = ALL_KINDS
    settings.asset_level_filter = ALL_LEVELS


def ensure_asset_selection(
    settings: bpy.types.PropertyGroup,
    context: bpy.types.Context,
) -> str:
    catalog = _catalog(settings, context)
    if not catalog.assets:
        set_asset_selection(settings, "")
        return ""
    try:
        catalog.asset(settings.asset_tuid)
    except MissingResourceError:
        set_asset_selection(settings, catalog.assets[0].identifier)
    return settings.asset_tuid


def _level_filter_items(
    settings: bpy.types.PropertyGroup,
    context: bpy.types.Context | None,
) -> list[tuple[str, str, str]]:
    global _LEVEL_FILTER_ITEMS
    catalog = _catalog(settings, context)
    _LEVEL_FILTER_ITEMS = [
        (ALL_LEVELS, "All Levels", "Do not filter assets by level"),
        *(
            (level, level, f"Only list assets used by {level}")
            for level in catalog.levels
        ),
    ]
    return _LEVEL_FILTER_ITEMS


def _level_items(
    settings: bpy.types.PropertyGroup,
    context: bpy.types.Context | None,
) -> list[tuple[str, str, str]]:
    global _LEVEL_ITEMS
    catalog = _catalog(settings, context)
    _LEVEL_ITEMS = [
        (
            level,
            level,
            (f"{catalog.game_label} level {level} from the {catalog.region} catalog"),
        )
        for level in catalog.levels
    ]
    return _LEVEL_ITEMS


def _reset_game_specific_choices(
    settings: bpy.types.PropertyGroup,
    context: bpy.types.Context,
) -> None:
    settings.source_summary = ""
    settings.last_message = ""
    clear_asset_filters(settings)
    catalog = _catalog(settings, context)
    set_asset_selection(
        settings,
        catalog.assets[0].identifier if catalog.assets else "",
    )
    if catalog.levels:
        settings.level_id = catalog.levels[0]


def _update_game(
    settings: bpy.types.PropertyGroup,
    context: bpy.types.Context,
) -> None:
    global _RESTORING_PERSISTED_SELECTION
    if _RESTORING_PERSISTED_SELECTION:
        return
    game_id = _game_id(settings)
    preferences = _addon_preferences(context)
    source_path = ""
    if preferences is not None:
        _initialize_source_preferences(preferences)
        previous_game = preferences.last_game
        if previous_game in GAME_ADAPTERS:
            setattr(
                preferences,
                GAME_ADAPTERS[previous_game].source_preference_name,
                settings.source_path,
            )
        preferences.last_game = game_id
        source_path = getattr(
            preferences,
            GAME_ADAPTERS[game_id].source_preference_name,
            "",
        )

    _RESTORING_PERSISTED_SELECTION = True
    try:
        settings.source_path = source_path
    finally:
        _RESTORING_PERSISTED_SELECTION = False
    _reset_game_specific_choices(settings, context)


def _update_source_path(
    settings: bpy.types.PropertyGroup,
    context: bpy.types.Context,
) -> None:
    _remember_game_source(settings, context)


class InsomniacAssetPickerItem(bpy.types.PropertyGroup):
    identifier: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    label: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    description: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    search_text: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    kind: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})


class InsomniacSceneSettings(bpy.types.PropertyGroup):
    game: bpy.props.EnumProperty(
        name="Game",
        items=[
            (
                adapter.id,
                adapter.label,
                adapter.engine,
            )
            for adapter in GAME_ADAPTERS.values()
        ],
        default="RESISTANCE_2",
        update=_update_game,
    )
    source_path: bpy.props.StringProperty(
        name="Source",
        description=(
            "A decrypted game ISO, extracted assetlookup.dat, level PSARC, "
            "Resistance level data folder, Burning Skies build.psa, or "
            "folder containing extracted resources"
        ),
        subtype="FILE_PATH",
        update=_update_source_path,
    )
    source_summary: bpy.props.StringProperty(
        name="Summary",
        options={"SKIP_SAVE"},
    )
    source_inspection_busy: bpy.props.BoolProperty(
        name="Source inspection in progress",
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    last_message: bpy.props.StringProperty(
        name="Last result",
        options={"SKIP_SAVE"},
    )
    asset_filter: bpy.props.StringProperty(
        name="Asset Path Filter",
        description=(
            "Only list assets whose source path, display name, kind, TUID, "
            "or level contains every space-separated fragment. Leave empty "
            "to list everything"
        ),
        options={"TEXTEDIT_UPDATE"},
    )
    asset_kind_filter: bpy.props.EnumProperty(
        name="Asset Kind",
        description="Only list assets of one kind",
        items=[
            (ALL_KINDS, "All", "List Mobys, Ties, and materials"),
            ("MOBY", "Mobys", "Animated or dynamic models"),
            ("TIE", "Ties", "Static level meshes"),
            ("SHADER", "Materials", "Standalone shader materials"),
        ],
        default=ALL_KINDS,
    )
    asset_level_filter: bpy.props.EnumProperty(
        name="Used In",
        description="Only list assets used by one level",
        items=_level_filter_items,
    )
    asset_tuid: bpy.props.StringProperty(
        name="Selected Asset TUID",
        default=DEFAULT_ASSET_IDENTIFIER,
        options={"HIDDEN"},
    )
    level_id: bpy.props.EnumProperty(
        name="Level",
        description=("Importable level from the active extraction catalog"),
        items=_level_items,
    )


CLASSES = (
    InsomniacAssetPickerItem,
    InsomniacSceneSettings,
)
