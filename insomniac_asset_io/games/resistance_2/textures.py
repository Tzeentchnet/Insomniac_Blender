from __future__ import annotations

from functools import lru_cache

import numpy as np

from ...core.errors import FormatError, UnsupportedFormatError
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

_BC1_DTYPE = np.dtype(
    [
        ("c0", "<u2"),
        ("c1", "<u2"),
        ("selectors", "<u4"),
    ]
)
_BC2_DTYPE = np.dtype(
    [
        ("alpha", "<u8"),
        ("c0", "<u2"),
        ("c1", "<u2"),
        ("selectors", "<u4"),
    ]
)
_BC3_DTYPE = np.dtype(
    [
        ("a0", "u1"),
        ("a1", "u1"),
        ("alpha_bytes", "u1", (6,)),
        ("c0", "<u2"),
        ("c1", "<u2"),
        ("selectors", "<u4"),
    ]
)
_BC_COLOR_SELECTOR_SHIFTS = np.arange(0, 32, 2, dtype=np.uint32)
_BC2_ALPHA_SELECTOR_SHIFTS = np.arange(0, 64, 4, dtype=np.uint64)
_BC3_ALPHA_BYTE_SHIFTS = np.arange(0, 48, 8, dtype=np.uint64)
_BC3_ALPHA_SELECTOR_SHIFTS = np.arange(0, 48, 3, dtype=np.uint64)


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


def _colors_565(values: np.ndarray) -> np.ndarray:
    values = values.astype(np.uint16, copy=False)
    colors = np.empty((len(values), 4), dtype=np.uint8)
    r = (values >> 11) & 0x1F
    g = (values >> 5) & 0x3F
    b = values & 0x1F
    colors[:, 0] = (r << 3) | (r >> 2)
    colors[:, 1] = (g << 2) | (g >> 4)
    colors[:, 2] = (b << 3) | (b >> 2)
    colors[:, 3] = 255
    return colors


def _bc1_palettes(
    c0: np.ndarray,
    c1: np.ndarray,
    *,
    force_four: bool,
) -> np.ndarray:
    p0 = _colors_565(c0)
    p1 = _colors_565(c1)
    p0_rgb = p0[:, :3].astype(np.uint16)
    p1_rgb = p1[:, :3].astype(np.uint16)
    four_color = (
        np.ones(len(c0), dtype=np.bool_) if force_four else c0 > c1
    )

    palettes = np.empty((len(c0), 4, 4), dtype=np.uint8)
    palettes[:, 0] = p0
    palettes[:, 1] = p1
    palettes[:, 2, :3] = np.where(
        four_color[:, None],
        (2 * p0_rgb + p1_rgb) // 3,
        (p0_rgb + p1_rgb) // 2,
    )
    palettes[:, 2, 3] = 255
    # Match the previous decoder's uint8 wrap for byte-identical output.
    wrapped_p1_twice = (2 * p1_rgb) & 0xFF
    palettes[:, 3, :3] = np.where(
        four_color[:, None],
        (p0_rgb + wrapped_p1_twice) // 3,
        0,
    )
    palettes[:, 3, 3] = np.where(four_color, 255, 0)
    return palettes


def _decode_bc_color_blocks(
    c0: np.ndarray,
    c1: np.ndarray,
    selectors: np.ndarray,
    *,
    force_four: bool,
) -> np.ndarray:
    palettes = _bc1_palettes(c0, c1, force_four=force_four)
    indices = (
        (selectors[:, None] >> _BC_COLOR_SELECTOR_SHIFTS) & 3
    ).astype(np.intp)
    return palettes[np.arange(len(palettes))[:, None], indices]


def _assemble_bc_image(
    blocks: np.ndarray,
    blocks_x: int,
    blocks_y: int,
    width: int,
    height: int,
) -> np.ndarray:
    image = (
        blocks.reshape(blocks_y, blocks_x, 4, 4, 4)
        .transpose(0, 2, 1, 3, 4)
        .reshape(blocks_y * 4, blocks_x * 4, 4)
    )
    return image[:height, :width].copy()


def _bc3_alpha_palettes(a0: np.ndarray, a1: np.ndarray) -> np.ndarray:
    a0_u16 = a0.astype(np.uint16)
    a1_u16 = a1.astype(np.uint16)
    seven_indices = np.arange(1, 7, dtype=np.uint16)
    seven_values = (
        (7 - seven_indices) * a0_u16[:, None]
        + seven_indices * a1_u16[:, None]
    ) // 7
    five_indices = np.arange(1, 5, dtype=np.uint16)
    five_values = np.empty((len(a0), 6), dtype=np.uint16)
    five_values[:, :4] = (
        (5 - five_indices) * a0_u16[:, None]
        + five_indices * a1_u16[:, None]
    ) // 5
    five_values[:, 4] = 0
    five_values[:, 5] = 255

    palettes = np.empty((len(a0), 8), dtype=np.uint8)
    palettes[:, 0] = a0
    palettes[:, 1] = a1
    palettes[:, 2:] = np.where(
        (a0 > a1)[:, None],
        seven_values,
        five_values,
    )
    return palettes


def _decode_bc1(raw: bytes, width: int, height: int) -> np.ndarray:
    blocks_x, blocks_y = _block_layout(width, height)
    block_count = blocks_x * blocks_y
    expected = block_count * _BC1_DTYPE.itemsize
    if len(raw) < expected:
        raise FormatError(f"DXT1 payload is {len(raw)} bytes; expected {expected}")
    encoded = np.frombuffer(raw, dtype=_BC1_DTYPE, count=block_count)
    blocks = _decode_bc_color_blocks(
        encoded["c0"],
        encoded["c1"],
        encoded["selectors"],
        force_four=False,
    )
    return _assemble_bc_image(blocks, blocks_x, blocks_y, width, height)


def _decode_bc2(raw: bytes, width: int, height: int) -> np.ndarray:
    blocks_x, blocks_y = _block_layout(width, height)
    block_count = blocks_x * blocks_y
    expected = block_count * _BC2_DTYPE.itemsize
    if len(raw) < expected:
        raise FormatError(f"DXT3 payload is {len(raw)} bytes; expected {expected}")
    encoded = np.frombuffer(raw, dtype=_BC2_DTYPE, count=block_count)
    blocks = _decode_bc_color_blocks(
        encoded["c0"],
        encoded["c1"],
        encoded["selectors"],
        force_four=True,
    )
    blocks[:, :, 3] = (
        (
            encoded["alpha"][:, None] >> _BC2_ALPHA_SELECTOR_SHIFTS
        )
        & 0xF
    ).astype(np.uint8) * 17
    return _assemble_bc_image(blocks, blocks_x, blocks_y, width, height)


def _decode_bc3(raw: bytes, width: int, height: int) -> np.ndarray:
    blocks_x, blocks_y = _block_layout(width, height)
    block_count = blocks_x * blocks_y
    expected = block_count * _BC3_DTYPE.itemsize
    if len(raw) < expected:
        raise FormatError(f"DXT5 payload is {len(raw)} bytes; expected {expected}")
    encoded = np.frombuffer(raw, dtype=_BC3_DTYPE, count=block_count)
    blocks = _decode_bc_color_blocks(
        encoded["c0"],
        encoded["c1"],
        encoded["selectors"],
        force_four=True,
    )
    alpha_bits = np.sum(
        encoded["alpha_bytes"].astype(np.uint64)
        << _BC3_ALPHA_BYTE_SHIFTS,
        axis=1,
        dtype=np.uint64,
    )
    alpha_indices = (
        (alpha_bits[:, None] >> _BC3_ALPHA_SELECTOR_SHIFTS) & 7
    ).astype(np.intp)
    alpha_palettes = _bc3_alpha_palettes(encoded["a0"], encoded["a1"])
    blocks[:, :, 3] = alpha_palettes[
        np.arange(block_count)[:, None],
        alpha_indices,
    ]
    return _assemble_bc_image(blocks, blocks_x, blocks_y, width, height)


def _block_layout(width: int, height: int) -> tuple[int, int]:
    return max(1, (width + 3) // 4), max(1, (height + 3) // 4)


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
