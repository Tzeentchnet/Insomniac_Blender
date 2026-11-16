from __future__ import annotations

from functools import lru_cache

import numpy as np

from ...core.errors import FormatError, UnsupportedFormatError
from ...core.textures import (
    block_layout as _block_layout,
    decode_bc1 as _decode_bc1,
    decode_bc2 as _decode_bc2,
    decode_bc3 as _decode_bc3,
)
from .formats import DecodedTexture, TextureInfo


FORMAT_NAMES = {
    0x01: "R8",
    0x02: "RGB5A1",
    0x03: "RGBA4",
    0x04: "R5G6B5",
    0x05: "A8R8G8B8",
    0x06: "DXT1",
    0x07: "DXT3",
    0x08: "DXT5",
    0x0B: "RG8",
    0x81: "R8",
    0x82: "RGB5A1",
    0x83: "RGBA4",
    0x84: "R5G6B5",
    0x85: "A8R8G8B8",
    0x86: "DXT1",
    0x87: "DXT3",
    0x88: "DXT5",
    0x8B: "RG8",
    0xA5: "A8R8G8B8_LINEAR",
    0xA6: "BC1_LINEAR",
    0xA7: "DXT3",
    0xA8: "DXT5",
}

def texture_mip_size(width: int, height: int, format_code: int) -> int:
    if width <= 0 or height <= 0:
        raise FormatError(f"Invalid texture dimensions {width}x{height}")
    name = FORMAT_NAMES.get(format_code)
    if name is None:
        raise UnsupportedFormatError(
            f"Unknown texture format 0x{format_code:02X}"
        )
    if name in {"DXT1", "BC1_LINEAR"}:
        blocks_x, blocks_y = _block_layout(width, height)
        return blocks_x * blocks_y * 8
    if name in {"DXT3", "DXT5"}:
        blocks_x, blocks_y = _block_layout(width, height)
        return blocks_x * blocks_y * 16
    if name in {"A8R8G8B8", "A8R8G8B8_LINEAR"}:
        return width * height * 4
    if name in {"R5G6B5", "RGB5A1", "RGBA4", "RG8"}:
        return width * height * 2
    if name == "R8":
        return width * height
    raise UnsupportedFormatError(f"Decoder not implemented for {name}")


def decode_texture(info: TextureInfo, raw: bytes) -> DecodedTexture:
    name = FORMAT_NAMES.get(info.format_code)
    if name is None:
        raise UnsupportedFormatError(
            f"Texture 0x{info.tuid:016X} uses unknown format "
            f"0x{info.format_code:02X}"
        )
    if name in {"DXT1", "BC1_LINEAR"}:
        rgba = _decode_bc1(raw, info.width, info.height)
    elif name == "DXT3":
        rgba = _decode_bc2(raw, info.width, info.height)
    elif name == "DXT5":
        rgba = _decode_bc3(raw, info.width, info.height)
    elif name in {"A8R8G8B8", "A8R8G8B8_LINEAR"}:
        rgba = _decode_a8r8g8b8(
            raw,
            info.width,
            info.height,
            swizzled=name == "A8R8G8B8",
        )
    elif name == "R5G6B5":
        rgba = _decode_r5g6b5(raw, info.width, info.height)
    elif name == "RGB5A1":
        rgba = _decode_rgb5a1(raw, info.width, info.height)
    elif name == "RGBA4":
        rgba = _decode_rgba4(raw, info.width, info.height)
    elif name == "R8":
        rgba = _decode_r8(raw, info.width, info.height)
    elif name == "RG8":
        rgba = _decode_rg8(raw, info.width, info.height)
    else:
        raise UnsupportedFormatError(f"Decoder not implemented for {name}")
    return DecodedTexture(info=info, rgba=rgba)


@lru_cache(maxsize=32)
def _morton_indices(width: int, height: int) -> np.ndarray:
    pixels = width * height

    def morton_index(value: int) -> int:
        x_stride = y_stride = 1
        source = value
        x_extent = width
        y_extent = height
        x_value = y_value = 0
        while x_extent > 1 or y_extent > 1:
            if x_extent > 1:
                x_value += x_stride * (source & 1)
                source >>= 1
                x_stride *= 2
                x_extent >>= 1
            if y_extent > 1:
                y_value += y_stride * (source & 1)
                source >>= 1
                y_stride *= 2
                y_extent >>= 1
        return y_value * width + x_value

    return np.fromiter(
        (morton_index(index) for index in range(pixels)),
        dtype=np.int64,
        count=pixels,
    )


def _unswizzle(values: np.ndarray, width: int, height: int) -> np.ndarray:
    pixels = width * height
    output = np.empty_like(values)
    output[_morton_indices(width, height)] = values[:pixels]
    return output.reshape(height, width, *values.shape[1:])


def _require_pixels(raw: bytes, width: int, height: int, bpp: int) -> int:
    pixels = width * height
    expected = pixels * bpp
    if len(raw) < expected:
        raise FormatError(
            f"Texture payload is {len(raw)} bytes; expected at least {expected}"
        )
    return pixels


def _decode_a8r8g8b8(
    raw: bytes,
    width: int,
    height: int,
    *,
    swizzled: bool,
) -> np.ndarray:
    pixels = _require_pixels(raw, width, height, 4)
    source = np.frombuffer(raw, dtype=np.uint8, count=pixels * 4).reshape(-1, 4)
    rgba = source[:, [3, 2, 1, 0]].copy()
    if swizzled:
        return _unswizzle(rgba, width, height)
    return rgba.reshape(height, width, 4)


def _decode_r5g6b5(raw: bytes, width: int, height: int) -> np.ndarray:
    pixels = _require_pixels(raw, width, height, 2)
    value = np.frombuffer(raw, dtype=">u2", count=pixels).astype(np.uint16)
    rgba = np.empty((pixels, 4), dtype=np.uint8)
    r = (value >> 11) & 0x1F
    g = (value >> 5) & 0x3F
    b = value & 0x1F
    rgba[:, 0] = (r << 3) | (r >> 2)
    rgba[:, 1] = (g << 2) | (g >> 4)
    rgba[:, 2] = (b << 3) | (b >> 2)
    rgba[:, 3] = 255
    return _unswizzle(rgba, width, height)


def _decode_rgb5a1(raw: bytes, width: int, height: int) -> np.ndarray:
    pixels = _require_pixels(raw, width, height, 2)
    value = np.frombuffer(raw, dtype=">u2", count=pixels).astype(np.uint16)
    rgba = np.empty((pixels, 4), dtype=np.uint8)
    r = (value >> 11) & 0x1F
    g = (value >> 6) & 0x1F
    b = (value >> 1) & 0x1F
    rgba[:, 0] = (r << 3) | (r >> 2)
    rgba[:, 1] = (g << 3) | (g >> 2)
    rgba[:, 2] = (b << 3) | (b >> 2)
    rgba[:, 3] = np.where(value & 1, 255, 0)
    return _unswizzle(rgba, width, height)


def _decode_rgba4(raw: bytes, width: int, height: int) -> np.ndarray:
    pixels = _require_pixels(raw, width, height, 2)
    value = np.frombuffer(raw, dtype=">u2", count=pixels).astype(np.uint16)
    rgba = np.empty((pixels, 4), dtype=np.uint8)
    for channel, shift in enumerate((12, 8, 4, 0)):
        nibble = (value >> shift) & 0xF
        rgba[:, channel] = (nibble << 4) | nibble
    return _unswizzle(rgba, width, height)


def _decode_r8(raw: bytes, width: int, height: int) -> np.ndarray:
    pixels = _require_pixels(raw, width, height, 1)
    value = np.frombuffer(raw, dtype=np.uint8, count=pixels)
    rgba = np.empty((pixels, 4), dtype=np.uint8)
    rgba[:, :3] = value[:, None]
    rgba[:, 3] = 255
    return _unswizzle(rgba, width, height)


def _decode_rg8(raw: bytes, width: int, height: int) -> np.ndarray:
    pixels = _require_pixels(raw, width, height, 2)
    value = np.frombuffer(raw, dtype=np.uint8, count=pixels * 2).reshape(-1, 2)
    rgba = np.zeros((pixels, 4), dtype=np.uint8)
    rgba[:, :2] = value
    rgba[:, 3] = 255
    return _unswizzle(rgba, width, height)
