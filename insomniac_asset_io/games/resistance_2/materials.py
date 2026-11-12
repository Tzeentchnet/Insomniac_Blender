from __future__ import annotations

import numpy as np

from .formats import DecodedTexture, UInt8Array


EMISSIVE_CHANNEL_INDEX = 2
EMISSIVE_CHANNEL_NAME = "Blue"
_SUPPORTED_NORMAL_FORMATS = frozenset({0x08, 0x0B, 0x88, 0x8B, 0xA8})


def emissive_mask(texture: DecodedTexture) -> UInt8Array:
    return texture.rgba[..., EMISSIVE_CHANNEL_INDEX]


def has_emissive_mask(texture: DecodedTexture) -> bool:
    return bool(np.any(emissive_mask(texture)))


def can_reconstruct_normal_map(texture: DecodedTexture) -> bool:
    return texture.info.format_code in _SUPPORTED_NORMAL_FORMATS


def reconstruct_normal_map(texture: DecodedTexture) -> UInt8Array:
    source = texture.rgba
    x_source = (
        source[..., 0]
        if texture.info.format_code in {0x0B, 0x8B}
        else source[..., 3]
    )
    x = x_source.astype(np.float32) / 127.5 - 1.0
    y = 1.0 - source[..., 1].astype(np.float32) / 127.5
    z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))

    normal = np.empty_like(source)
    normal[..., 0] = x_source
    normal[..., 1] = 255 - source[..., 1]
    normal[..., 2] = np.rint((z * 0.5 + 0.5) * 255.0).astype(np.uint8)
    normal[..., 3] = 255
    return normal
