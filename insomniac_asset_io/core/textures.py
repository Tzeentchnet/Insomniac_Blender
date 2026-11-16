from __future__ import annotations

import numpy as np

from .errors import FormatError


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


def block_layout(width: int, height: int) -> tuple[int, int]:
    return max(1, (width + 3) // 4), max(1, (height + 3) // 4)


def decode_bc1(raw: bytes, width: int, height: int) -> np.ndarray:
    blocks_x, blocks_y = block_layout(width, height)
    block_count = blocks_x * blocks_y
    encoded = _encoded_blocks(raw, block_count, _BC1_DTYPE, "BC1")
    blocks = _decode_bc_color_blocks(
        encoded["c0"],
        encoded["c1"],
        encoded["selectors"],
        force_four=False,
    )
    return _assemble_bc_image(blocks, blocks_x, blocks_y, width, height)


def decode_bc2(raw: bytes, width: int, height: int) -> np.ndarray:
    blocks_x, blocks_y = block_layout(width, height)
    block_count = blocks_x * blocks_y
    encoded = _encoded_blocks(raw, block_count, _BC2_DTYPE, "BC2")
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


def decode_bc3(raw: bytes, width: int, height: int) -> np.ndarray:
    blocks_x, blocks_y = block_layout(width, height)
    block_count = blocks_x * blocks_y
    encoded = _encoded_blocks(raw, block_count, _BC3_DTYPE, "BC3")
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
    alpha_palettes = _bc3_alpha_palettes(
        encoded["a0"],
        encoded["a1"],
    )
    blocks[:, :, 3] = alpha_palettes[
        np.arange(block_count)[:, None],
        alpha_indices,
    ]
    return _assemble_bc_image(blocks, blocks_x, blocks_y, width, height)


def _encoded_blocks(
    raw: bytes,
    block_count: int,
    dtype: np.dtype,
    format_name: str,
) -> np.ndarray:
    expected = block_count * dtype.itemsize
    if len(raw) < expected:
        raise FormatError(
            f"{format_name} payload is {len(raw)} bytes; expected {expected}"
        )
    return np.frombuffer(raw, dtype=dtype, count=block_count)


def _colors_565(values: np.ndarray) -> np.ndarray:
    values = values.astype(np.uint16, copy=False)
    colors = np.empty((len(values), 4), dtype=np.uint8)
    red = (values >> 11) & 0x1F
    green = (values >> 5) & 0x3F
    blue = values & 0x1F
    colors[:, 0] = (red << 3) | (red >> 2)
    colors[:, 1] = (green << 2) | (green >> 4)
    colors[:, 2] = (blue << 3) | (blue >> 2)
    colors[:, 3] = 255
    return colors


def _bc1_palettes(
    color0: np.ndarray,
    color1: np.ndarray,
    *,
    force_four: bool,
) -> np.ndarray:
    palette0 = _colors_565(color0)
    palette1 = _colors_565(color1)
    palette0_rgb = palette0[:, :3].astype(np.uint16)
    palette1_rgb = palette1[:, :3].astype(np.uint16)
    four_color = (
        np.ones(len(color0), dtype=np.bool_)
        if force_four
        else color0 > color1
    )

    palettes = np.empty((len(color0), 4, 4), dtype=np.uint8)
    palettes[:, 0] = palette0
    palettes[:, 1] = palette1
    palettes[:, 2, :3] = np.where(
        four_color[:, None],
        (2 * palette0_rgb + palette1_rgb) // 3,
        (palette0_rgb + palette1_rgb) // 2,
    )
    palettes[:, 2, 3] = 255
    palettes[:, 3, :3] = np.where(
        four_color[:, None],
        (palette0_rgb + 2 * palette1_rgb) // 3,
        0,
    )
    palettes[:, 3, 3] = np.where(four_color, 255, 0)
    return palettes


def _decode_bc_color_blocks(
    color0: np.ndarray,
    color1: np.ndarray,
    selectors: np.ndarray,
    *,
    force_four: bool,
) -> np.ndarray:
    palettes = _bc1_palettes(
        color0,
        color1,
        force_four=force_four,
    )
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


def _bc3_alpha_palettes(
    alpha0: np.ndarray,
    alpha1: np.ndarray,
) -> np.ndarray:
    alpha0_u16 = alpha0.astype(np.uint16)
    alpha1_u16 = alpha1.astype(np.uint16)
    seven_indices = np.arange(1, 7, dtype=np.uint16)
    seven_values = (
        (7 - seven_indices) * alpha0_u16[:, None]
        + seven_indices * alpha1_u16[:, None]
    ) // 7
    five_indices = np.arange(1, 5, dtype=np.uint16)
    five_values = np.empty((len(alpha0), 6), dtype=np.uint16)
    five_values[:, :4] = (
        (5 - five_indices) * alpha0_u16[:, None]
        + five_indices * alpha1_u16[:, None]
    ) // 5
    five_values[:, 4] = 0
    five_values[:, 5] = 255

    palettes = np.empty((len(alpha0), 8), dtype=np.uint8)
    palettes[:, 0] = alpha0
    palettes[:, 1] = alpha1
    palettes[:, 2:] = np.where(
        (alpha0 > alpha1)[:, None],
        seven_values,
        five_values,
    )
    return palettes
