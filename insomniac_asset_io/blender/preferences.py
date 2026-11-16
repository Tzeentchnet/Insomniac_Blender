from __future__ import annotations

import bpy

from ..core.iso import DEFAULT_CACHE_DIRECTORY
from ..games.registry import GAME_ADAPTERS

_INTERNAL_PACKAGE_SUFFIX = ".insomniac_asset_io.blender"


def _addon_module() -> str:
    package = __package__ or ""
    if package.endswith(_INTERNAL_PACKAGE_SUFFIX):
        return package[: -len(_INTERNAL_PACKAGE_SUFFIX)]
    if package.endswith(".blender"):
        return package[: -len(".blender")]
    return package


ADDON_MODULE = _addon_module()


class InsomniacAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_MODULE

    cycles_materials: bpy.props.BoolProperty(
        name="Enhanced source materials",
        description=(
            "Apply source color multipliers; verified R2 and R3 normal and "
            "gloss nodes are built by default, and this option does not "
            "change the active render engine"
        ),
        default=False,
    )
    resistance_2_iso_path: bpy.props.StringProperty(
        name="Resistance 2 ISO",
        description=(
            "A legally obtained decrypted Resistance 2 ISO used for imports "
            "and local cache generation"
        ),
        subtype="FILE_PATH",
    )
    resistance_3_iso_path: bpy.props.StringProperty(
        name="Resistance 3 ISO",
        description=(
            "A legally obtained decrypted Resistance 3 ISO used for imports "
            "and local cache generation"
        ),
        subtype="FILE_PATH",
    )
    ratchet_tod_iso_path: bpy.props.StringProperty(
        name="Tools of Destruction ISO",
        description=(
            "A legally obtained decrypted BCUS98127 v02.00 ISO used for "
            "Ratchet & Clank Future: Tools of Destruction imports"
        ),
        subtype="FILE_PATH",
    )
    last_game: bpy.props.EnumProperty(
        name="Last selected game",
        items=[
            (adapter.id, adapter.label, adapter.engine)
            for adapter in GAME_ADAPTERS.values()
        ],
        default="RESISTANCE_2",
        options={"HIDDEN"},
    )
    source_paths_initialized: bpy.props.BoolProperty(
        default=False,
        options={"HIDDEN"},
    )
    resistance_2_last_source_path: bpy.props.StringProperty(
        subtype="FILE_PATH",
        options={"HIDDEN"},
    )
    resistance_3_last_source_path: bpy.props.StringProperty(
        subtype="FILE_PATH",
        options={"HIDDEN"},
    )
    resistance_burning_skies_last_source_path: bpy.props.StringProperty(
        subtype="FILE_PATH",
        options={"HIDDEN"},
    )
    ratchet_tod_last_source_path: bpy.props.StringProperty(
        subtype="FILE_PATH",
        options={"HIDDEN"},
    )
    asset_cache_directory: bpy.props.StringProperty(
        name="Local cache directory",
        description=(
            "Stores extracted PSARCs and the generated Blender Asset Browser library"
        ),
        subtype="DIR_PATH",
        default=str(DEFAULT_CACHE_DIRECTORY),
    )
    asset_cache_game: bpy.props.EnumProperty(
        name="Catalog / Asset Browser game",
        description=("Game used by catalog regeneration and Asset Browser cache tools"),
        items=[
            (adapter.id, adapter.label, adapter.engine)
            for adapter in GAME_ADAPTERS.values()
            if adapter.supports_iso
        ],
        default="RESISTANCE_2",
    )
    asset_cache_level: bpy.props.StringProperty(
        name="Cached level",
        description=(
            "Disc level whose Moby and Tie assets will be cached, for example "
            "lobby or chicxulub/chicxulub"
        ),
        default="lobby",
    )
    asset_cache_materials: bpy.props.BoolProperty(
        name="Cache materials and textures",
        description="Decode and pack materials into generated asset files",
        default=True,
    )
    asset_cache_animations: bpy.props.BoolProperty(
        name="Cache Moby animations",
        description=(
            "Include decoded Actions in generated Moby files; disabled by "
            "default to keep the Asset Browser cache compact"
        ),
        default=False,
    )
    asset_cache_previews: bpy.props.BoolProperty(
        name="Generate previews",
        description="Render collection previews for the Blender Asset Browser",
        default=True,
    )
    asset_import_materials: bpy.props.BoolProperty(
        name="Materials",
        description="Build materials and decode textures for model imports",
        default=True,
    )
    asset_import_animations: bpy.props.BoolProperty(
        name="Moby animations",
        description="Decode a Moby's animset into Blender Actions",
        default=True,
    )
    asset_import_damage_permutations: bpy.props.BoolProperty(
        name="Damage permutations",
        description=(
            "Import optional damaged model segments into a separate Collection"
        ),
        default=False,
    )
    asset_import_lower_lods: bpy.props.BoolProperty(
        name="Lower LODs",
        description=(
            "Import optional lower-detail model levels into a separate "
            "Collection"
        ),
        default=False,
    )
    asset_import_collision_geometry: bpy.props.BoolProperty(
        name="Collision geometry",
        description=("Import model collision shapes into a separate Collection"),
        default=False,
    )
    asset_animation_clip_limit: bpy.props.IntProperty(
        name="Animation limit",
        description="Maximum clips to create; zero imports every clip",
        min=0,
        default=0,
    )
    asset_convert_axes: bpy.props.BoolProperty(
        name="Y-up to Z-up",
        description="Rotate imported models into Blender Z-up space",
        default=True,
    )
    asset_selector_hide_duplicates: bpy.props.BoolProperty(
        name="Hide duplicate asset names",
        description=(
            "Show one result for assets with the same readable basename; "
            "exact path filters can still surface a specific variant"
        ),
        default=True,
    )
    asset_selector_show_tuids: bpy.props.BoolProperty(
        name="Show TUIDs in asset labels",
        description="Append asset kind and TUID to selector labels",
        default=False,
    )
    asset_selector_show_owner_levels: bpy.props.BoolProperty(
        name="Show owner levels in asset labels",
        description="Append the first owning level and additional-level count",
        default=False,
    )
    level_import_materials: bpy.props.BoolProperty(
        name="Materials",
        description="Build materials and decode textures for level imports",
        default=True,
    )
    level_import_terrain: bpy.props.BoolProperty(
        name="Terrain",
        default=True,
    )
    level_import_ties: bpy.props.BoolProperty(
        name="Static props",
        default=True,
    )
    level_import_gameplay: bpy.props.BoolProperty(
        name="Gameplay instances",
        description="Place Moby meshes from gameplay region tables",
        default=True,
    )
    level_convert_axes: bpy.props.BoolProperty(
        name="Y-up to Z-up",
        description="Rotate imported levels into Blender Z-up space",
        default=True,
    )

    def draw(self, _context):
        layout = self.layout
        source = layout.box()
        source.label(text="Game sources and extraction", icon="DISC")
        source.prop(self, "ratchet_tod_iso_path")
        source.prop(self, "resistance_2_iso_path")
        source.prop(self, "resistance_3_iso_path")
        source.prop(self, "asset_cache_directory")
        source.prop(self, "asset_cache_game")
        source.operator(
            "insomniac.rebuild_import_catalog",
            text="Regenerate Import Catalog from ISO",
            icon="FILE_REFRESH",
        )
        source.label(
            text="Bundled USA catalogs are used until regenerated locally",
            icon="INFO",
        )

        model = layout.box()
        model.label(text="Model / Static Mesh imports", icon="MESH_DATA")
        model.prop(self, "asset_import_materials")
        model.prop(self, "asset_import_animations")
        model.prop(self, "asset_import_damage_permutations")
        model.prop(self, "asset_import_lower_lods")
        model.prop(self, "asset_import_collision_geometry")
        if self.asset_import_animations:
            model.prop(self, "asset_animation_clip_limit")
        model.prop(self, "asset_convert_axes")
        model.separator()
        model.label(text="Asset selector", icon="FILTER")
        model.prop(self, "asset_selector_hide_duplicates")
        model.prop(self, "asset_selector_show_tuids")
        model.prop(self, "asset_selector_show_owner_levels")

        level = layout.box()
        level.label(text="Level imports", icon="WORLD_DATA")
        level.prop(self, "level_import_materials")
        level.prop(self, "level_import_terrain")
        level.prop(self, "level_import_ties")
        level.prop(self, "level_import_gameplay")
        level.prop(self, "level_convert_axes")

        materials = layout.box()
        materials.label(text="Material processing", icon="MATERIAL")
        materials.prop(self, "cycles_materials")
        materials.label(
            text="Applies to future imports and generated caches only",
            icon="INFO",
        )

        cache = layout.box()
        cache.label(text="Asset Browser", icon="ASSET_MANAGER")
        cache.prop(self, "asset_cache_level")
        cache.prop(self, "asset_cache_materials")
        cache.prop(self, "asset_cache_animations")
        cache.prop(self, "asset_cache_previews")
        cache.operator(
            "insomniac.rebuild_asset_library",
            text="Regenerate Selected Level Cache",
            icon="FILE_REFRESH",
        )
        cache.operator(
            "insomniac.register_asset_library",
            text="Register Asset Library",
            icon="ASSET_MANAGER",
        )
        cache.label(
            text="Generated locally from your ISO; game assets are not bundled",
            icon="INFO",
        )


def addon_preferences(
    context: bpy.types.Context,
) -> InsomniacAddonPreferences | None:
    addon = context.preferences.addons.get(ADDON_MODULE)
    if addon is None:
        return None
    return addon.preferences


def cycles_materials_enabled(context: bpy.types.Context) -> bool:
    preferences = addon_preferences(context)
    if preferences is None:
        return False
    return bool(preferences.cycles_materials)


CLASSES = (InsomniacAddonPreferences,)
