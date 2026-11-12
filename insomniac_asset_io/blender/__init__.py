from __future__ import annotations

import bpy

from .asset_cache import ensure_asset_library_registered
from .operators import CLASSES as OPERATOR_CLASSES
from .panels import CLASSES as PANEL_CLASSES
from .preferences import (
    CLASSES as PREFERENCE_CLASSES,
    addon_preferences,
)
from .properties import CLASSES as PROPERTY_CLASSES, InsomniacSceneSettings


CLASSES = (
    *PREFERENCE_CLASSES,
    *PROPERTY_CLASSES,
    *OPERATOR_CLASSES,
    *PANEL_CLASSES,
)


def _import_menu(self, _context) -> None:
    self.layout.operator(
        "insomniac.select_source",
        text="Insomniac Game Assets",
    )


def register() -> None:
    registered: list[type] = []
    scene_property_added = False
    menu_added = False
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
        bpy.types.Scene.insomniac_asset_io = bpy.props.PointerProperty(
            type=InsomniacSceneSettings
        )
        scene_property_added = True
        bpy.types.TOPBAR_MT_file_import.append(_import_menu)
        menu_added = True
        preferences = addon_preferences(bpy.context)
        if preferences is not None:
            ensure_asset_library_registered(
                bpy.context,
                bpy.path.abspath(preferences.asset_cache_directory),
            )
    except Exception:
        if menu_added:
            bpy.types.TOPBAR_MT_file_import.remove(_import_menu)
        if scene_property_added:
            del bpy.types.Scene.insomniac_asset_io
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        raise


def unregister() -> None:
    bpy.types.TOPBAR_MT_file_import.remove(_import_menu)
    del bpy.types.Scene.insomniac_asset_io
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
