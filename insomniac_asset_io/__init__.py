"""Insomniac Asset IO Blender extension."""


def register() -> None:
    from .blender import register as register_blender

    register_blender()


def unregister() -> None:
    from .blender import unregister as unregister_blender

    unregister_blender()
