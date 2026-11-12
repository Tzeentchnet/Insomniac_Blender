from __future__ import annotations

import logging
import math

import bpy
from bpy.app.handlers import persistent


LOGGER = logging.getLogger(__name__)
_ACTION_MARKER = "insomniac_frame_rate"
_LAST_SYNC_KEY: tuple[int, int, int, float, float] | None = None
_SYNCING = False


def _set_frame_bounds(
    scene: bpy.types.Scene,
    start: int,
    end: int,
    *,
    preview: bool,
) -> None:
    start_name = "frame_preview_start" if preview else "frame_start"
    end_name = "frame_preview_end" if preview else "frame_end"
    if start > getattr(scene, end_name):
        setattr(scene, end_name, end)
    setattr(scene, start_name, start)
    setattr(scene, end_name, end)


def sync_active_action_range(
    scene: bpy.types.Scene | None = None,
) -> bool:
    global _LAST_SYNC_KEY, _SYNCING

    if _SYNCING:
        return False
    active_scene = scene or getattr(bpy.context, "scene", None)
    view_layer = getattr(bpy.context, "view_layer", None)
    if (
        active_scene is None
        or view_layer is None
        or getattr(bpy.context, "scene", None) != active_scene
    ):
        return False

    active_object = view_layer.objects.active
    animation_data = (
        active_object.animation_data if active_object is not None else None
    )
    action = animation_data.action if animation_data is not None else None
    if action is None:
        key = (
            active_scene.as_pointer(),
            active_object.as_pointer() if active_object is not None else 0,
            0,
            0.0,
            0.0,
        )
        _LAST_SYNC_KEY = key
        return False

    raw_start, raw_end = map(float, action.frame_range)
    key = (
        active_scene.as_pointer(),
        active_object.as_pointer(),
        action.as_pointer(),
        raw_start,
        raw_end,
    )
    if key == _LAST_SYNC_KEY:
        return False
    _LAST_SYNC_KEY = key
    if _ACTION_MARKER not in action:
        return False
    if not math.isfinite(raw_start) or not math.isfinite(raw_end):
        LOGGER.warning(
            "Cannot synchronize non-finite frame range for Action %s",
            action.name,
        )
        return False

    start = math.floor(raw_start)
    end = math.ceil(raw_end)
    if end < start:
        LOGGER.warning(
            "Cannot synchronize reversed frame range %s-%s for Action %s",
            raw_start,
            raw_end,
            action.name,
        )
        return False

    _SYNCING = True
    try:
        _set_frame_bounds(active_scene, start, end, preview=False)
        if active_scene.use_preview_range:
            _set_frame_bounds(active_scene, start, end, preview=True)
        if not start <= active_scene.frame_current <= end:
            active_scene.frame_set(
                min(max(active_scene.frame_current, start), end)
            )
    finally:
        _SYNCING = False
    return True


@persistent
def _sync_action_range_after_update(
    scene: bpy.types.Scene,
    _depsgraph: bpy.types.Depsgraph,
) -> None:
    sync_active_action_range(scene)


@persistent
def _sync_action_range_after_load(_unused: object = None) -> None:
    global _LAST_SYNC_KEY
    _LAST_SYNC_KEY = None
    sync_active_action_range()


def register_action_range_sync() -> None:
    global _LAST_SYNC_KEY
    _LAST_SYNC_KEY = None
    handlers = bpy.app.handlers
    if _sync_action_range_after_update not in handlers.depsgraph_update_post:
        handlers.depsgraph_update_post.append(_sync_action_range_after_update)
    if _sync_action_range_after_load not in handlers.load_post:
        handlers.load_post.append(_sync_action_range_after_load)
    sync_active_action_range()


def unregister_action_range_sync() -> None:
    global _LAST_SYNC_KEY
    handlers = bpy.app.handlers
    if _sync_action_range_after_update in handlers.depsgraph_update_post:
        handlers.depsgraph_update_post.remove(_sync_action_range_after_update)
    if _sync_action_range_after_load in handlers.load_post:
        handlers.load_post.remove(_sync_action_range_after_load)
    _LAST_SYNC_KEY = None
