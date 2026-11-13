from __future__ import annotations

import struct

import numpy as np

from ...core.errors import FormatError
from ..resistance_2.formats import DecodedTexture, TextureInfo
from .formats import NsirsrcFile, StreamDescriptor

GXT_MAGIC = b"GXT\0"
GXT_VERSION = 0x10000003
GXT_TEXTURE_RECORD_SIZE = 0x20
GXT_HEADER_SIZE = 0x20

TEXTURE_TYPE_SWIZZLED = 0x00000000
TEXTURE_TYPE_SWIZZLED_ARBITRARY = 0x40000000
TEXTURE_TYPE_LINEAR = 0x60000000

FORMAT_RGBA8_ABGR = 0x0C001000
FORMAT_PVRTCII4 = 0x83000000
FORMAT_UBC1 = 0x85000000
FORMAT_UBC2 = 0x86000000
FORMAT_UBC3 = 0x87000000


def decode_p2t(
    data: bytes,
    *,
    label: str,
    texture_id: int,
) -> DecodedTexture:
    resource = NsirsrcFile(data, label=label)
    if resource.root_type not in {0x0A, 0x0E}:
        raise FormatError(
            f"{label}: root type 0x{resource.root_type:X} is not a texture"
        )
    candidates: list[tuple[int, int, StreamDescriptor]] = []
    for stream in resource.stream_descriptors():
        candidate_offset = resource.physical_offset(stream.primary_offset)
        if (
            candidate_offset + GXT_HEADER_SIZE + GXT_TEXTURE_RECORD_SIZE
            > len(data)
            or data[candidate_offset : candidate_offset + 4] != GXT_MAGIC
        ):
            continue
        candidate_dimensions = struct.unpack_from(
            "<I",
            data,
            candidate_offset + GXT_HEADER_SIZE + 0x18,
        )[0]
        candidate_width = candidate_dimensions & 0xFFFF
        candidate_height = candidate_dimensions >> 16
        candidates.append(
            (
                candidate_width * candidate_height,
                candidate_offset,
                stream,
            )
        )
    if not candidates:
        raise FormatError(f"{label}: no embedded GXT texture stream found")
    _area, gxt_offset, stream = max(candidates, key=lambda item: item[0])
    resource.require(gxt_offset, GXT_HEADER_SIZE, purpose="GXT header")
    if data[gxt_offset : gxt_offset + 4] != GXT_MAGIC:
        raise FormatError(f"{label}: embedded stream is not a GXT texture")
    (
        _magic,
        version,
        texture_count,
        _data_offset,
        _data_size,
        palette4_count,
        palette8_count,
        reserved,
    ) = struct.unpack_from("<4s7I", data, gxt_offset)
    if version != GXT_VERSION:
        raise FormatError(
            f"{label}: unsupported GXT version 0x{version:08X}"
        )
    if texture_count != 1:
        raise FormatError(
            f"{label}: expected one GXT texture, found {texture_count}"
        )
    if palette4_count or palette8_count or reserved:
        raise FormatError(f"{label}: paletted GXT textures are unsupported")

    record_offset = gxt_offset + GXT_HEADER_SIZE
    resource.require(
        record_offset,
        GXT_TEXTURE_RECORD_SIZE,
        purpose="GXT texture record",
    )
    (
        texel_offset,
        texel_size,
        palette_index,
        flags,
        texture_type,
        format_code,
        dimensions,
        mip_word,
    ) = struct.unpack_from("<8I", data, record_offset)
    if palette_index != 0xFFFFFFFF or flags:
        raise FormatError(
            f"{label}: unsupported GXT palette or texture flags"
        )
    width = dimensions & 0xFFFF
    height = dimensions >> 16
    mip_count = mip_word & 0xFF
    if not width or not height or not mip_count:
        raise FormatError(
            f"{label}: invalid GXT dimensions or mip count"
        )
    texel_data_offset = gxt_offset + texel_offset
    serialized_stream_end = min(
        len(data),
        gxt_offset + stream.primary_size,
    )
    if not gxt_offset <= texel_data_offset <= serialized_stream_end:
        raise FormatError(f"{label}: invalid GXT texel offset")
    texels = data[texel_data_offset:serialized_stream_end]

    if format_code == FORMAT_PVRTCII4:
        block_width = max(2, (width + 3) // 4)
        block_height = max(2, (height + 3) // 4)
        top_size = block_width * block_height * 8
        if len(texels) < top_size:
            raise FormatError(f"{label}: truncated PVRTCII 4bpp top mip")
        if texture_type == TEXTURE_TYPE_LINEAR:
            twiddled = False
        elif texture_type in {
            TEXTURE_TYPE_SWIZZLED,
            TEXTURE_TYPE_SWIZZLED_ARBITRARY,
        }:
            if (
                not _is_power_of_two(block_width)
                or not _is_power_of_two(block_height)
            ):
                raise FormatError(
                    f"{label}: unsupported non-power-of-two swizzled "
                    f"PVRTCII top mip {width}x{height}"
                )
            twiddled = True
        else:
            raise FormatError(
                f"{label}: unsupported GXT texture type "
                f"0x{texture_type:08X}"
            )
        rgba = _decode_pvrtc2_4bpp(
            texels[:top_size],
            width,
            height,
            twiddled=twiddled,
        )
    elif format_code == FORMAT_UBC1:
        unit_size = 8
        unit_width = max(1, (width + 3) // 4)
        unit_height = max(1, (height + 3) // 4)
        top_size = unit_width * unit_height * unit_size
        top = _layout_units(
            texels,
            unit_width,
            unit_height,
            unit_size,
            texture_type,
            label=label,
        )
        rgba = _decode_ubc1(top[:top_size], width, height)
    elif format_code in {FORMAT_UBC2, FORMAT_UBC3}:
        unit_size = 16
        unit_width = max(1, (width + 3) // 4)
        unit_height = max(1, (height + 3) // 4)
        top_size = unit_width * unit_height * unit_size
        top = _layout_units(
            texels,
            unit_width,
            unit_height,
            unit_size,
            texture_type,
            label=label,
        )
        decoder = (
            _decode_ubc2 if format_code == FORMAT_UBC2 else _decode_ubc3
        )
        rgba = decoder(top[:top_size], width, height)
    elif format_code == FORMAT_RGBA8_ABGR:
        top_size = width * height * 4
        top = _layout_units(
            texels,
            width,
            height,
            4,
            texture_type,
            label=label,
        )
        if len(top) < top_size:
            raise FormatError(f"{label}: truncated RGBA8 top mip")
        bgra = np.frombuffer(
            top,
            dtype=np.uint8,
            count=top_size,
        ).reshape((height, width, 4))
        rgba = bgra[..., [2, 1, 0, 3]].copy()
    else:
        raise FormatError(
            f"{label}: unsupported GXT format 0x{format_code:08X}"
        )

    return DecodedTexture(
        info=TextureInfo(
            id=texture_id,
            tuid=texture_id,
            format_code=format_code,
            mip_count=mip_count,
            width=width,
            height=height,
            offset=texel_data_offset,
            length=texel_size,
        ),
        rgba=rgba,
    )


def _layout_units(
    data: bytes,
    width: int,
    height: int,
    unit_size: int,
    texture_type: int,
    *,
    label: str,
) -> bytes:
    required = width * height * unit_size
    if len(data) < required:
        raise FormatError(
            f"{label}: top mip requires 0x{required:X} bytes, "
            f"only 0x{len(data):X} are available"
        )
    if texture_type == TEXTURE_TYPE_LINEAR:
        return data[:required]
    if texture_type not in {
        TEXTURE_TYPE_SWIZZLED,
        TEXTURE_TYPE_SWIZZLED_ARBITRARY,
    }:
        raise FormatError(
            f"{label}: unsupported GXT texture type "
            f"0x{texture_type:08X}"
        )
    if not _is_power_of_two(width) or not _is_power_of_two(height):
        raise FormatError(
            f"{label}: unsupported non-power-of-two swizzled top mip "
            f"{width}x{height}"
        )
    output = bytearray(required)
    for y in range(height):
        for x in range(width):
            source_index = _twiddled_index(x, y, width, height)
            source = source_index * unit_size
            target = (y * width + x) * unit_size
            output[target : target + unit_size] = data[
                source : source + unit_size
            ]
    return bytes(output)


def _twiddled_index(x: int, y: int, width: int, height: int) -> int:
    minimum = min(width, height)
    mask = minimum - 1
    value = 0
    bit = 1
    shift = 0
    while bit < minimum:
        value |= (y & bit) << shift
        shift += 1
        value |= (x & bit) << shift
        bit <<= 1
    if width > height:
        value |= (x & ~mask) * minimum
    elif height > width:
        value |= (y & ~mask) * minimum
    return value


def _is_power_of_two(value: int) -> bool:
    return value > 0 and not (value & (value - 1))


def _pvrtc2_color_a(word: int) -> np.ndarray:
    if word & 0x80000000:
        return np.array(
            (
                (word & 0x7C00) >> 10,
                (word & 0x03E0) >> 5,
                (word & 0x001E) | ((word & 0x001E) >> 4),
                0xF,
            ),
            dtype=np.int32,
        )
    return np.array(
        (
            ((word & 0x0F00) >> 7) | ((word & 0x0F00) >> 11),
            ((word & 0x00F0) >> 3) | ((word & 0x00F0) >> 7),
            ((word & 0x000E) << 1) | ((word & 0x000E) >> 2),
            (word & 0x7000) >> 11,
        ),
        dtype=np.int32,
    )


def _pvrtc2_color_b(word: int) -> np.ndarray:
    if word & 0x80000000:
        return np.array(
            (
                (word & 0x7C000000) >> 26,
                (word & 0x03E00000) >> 21,
                (word & 0x001F0000) >> 16,
                0xF,
            ),
            dtype=np.int32,
        )
    return np.array(
        (
            ((word & 0x0F000000) >> 23)
            | ((word & 0x0F000000) >> 27),
            ((word & 0x00F00000) >> 19)
            | ((word & 0x00F00000) >> 23),
            ((word & 0x000F0000) >> 15)
            | ((word & 0x000F0000) >> 19),
            ((word & 0x70000000) >> 27) | 1,
        ),
        dtype=np.int32,
    )


def _pvrtc2_expanded_colors(
    word: int,
) -> tuple[np.ndarray, np.ndarray]:
    color_a = _pvrtc2_color_a(word) * 16
    color_b = _pvrtc2_color_b(word) * 16
    color_a[:3] = (color_a[:3] >> 6) + (color_a[:3] >> 1)
    color_b[:3] = (color_b[:3] >> 6) + (color_b[:3] >> 1)
    color_a[3] = (color_a[3] >> 4) + color_a[3]
    color_b[3] = (color_b[3] >> 4) + color_b[3]
    return color_a, color_b


def _pvrtc2_interpolate(
    top_left: np.ndarray,
    top_right: np.ndarray,
    bottom_left: np.ndarray,
    bottom_right: np.ndarray,
) -> np.ndarray:
    output = np.empty((4, 4, 4), dtype=np.int32)
    horizontal_top = top_right - top_left
    horizontal_bottom = bottom_right - bottom_left
    row_top = top_left * 4
    row_bottom = bottom_left * 4
    for y in range(4):
        value = row_top * 4
        vertical = row_bottom - row_top
        for x in range(4):
            output[y, x, :3] = (value[:3] >> 6) + (value[:3] >> 1)
            output[y, x, 3] = (value[3] >> 4) + value[3]
            value += vertical
        row_top += horizontal_top
        row_bottom += horizontal_bottom
    return output


def _pvrtc2_unpack_modulations(
    values: np.ndarray,
    word: tuple[int, int],
    northwest: tuple[int, int],
    offset_x: int,
    offset_y: int,
) -> None:
    bits, color_word = word
    mode = color_word & 1
    hard_transition = bool(northwest[1] & (1 << 15))
    for y in range(4):
        for x in range(4):
            value = bits & 3
            bits >>= 2
            center = (
                hard_transition
                and 2 <= y + offset_y <= 5
                and 2 <= x + offset_x <= 5
            )
            if mode:
                values[y + offset_y, x + offset_x] = (
                    value + 30
                    if center
                    else (0, 4, 14, 8)[value]
                )
            else:
                modulation = (0, 3, 5, 8)[value]
                values[y + offset_y, x + offset_x] = (
                    modulation + 20 if center else modulation
                )


def _pvrtc2_palette(
    endpoint_a: tuple[np.ndarray, ...],
    endpoint_b: tuple[np.ndarray, ...],
) -> np.ndarray:
    ap, aq, ar, a_s = endpoint_a
    bp, bq, br, bs = endpoint_b
    result = np.empty((4, 16, 4), dtype=np.int32)
    result[:, 0] = (
        ap,
        (ap * 5 + bp * 3) // 8,
        (ap * 3 + bp * 5) // 8,
        bp,
    )
    columns = (
        (ap, bp, aq, bq),
        (ap, bp, aq, bq),
        (ap, bp, aq, bq),
        (ap, bp, ar, br),
        (ap, bp, aq, br),
        (ap, bp, aq, bq),
        (a_s, bp, aq, bq),
        (ap, bp, ar, br),
        (ap, bp, ar, br),
        (ap, bs, ar, bq),
        (a_s, bs, aq, bq),
        (ap, bp, ar, br),
        (ap, bs, ar, br),
        (a_s, bs, ar, br),
        (a_s, bs, ar, bq),
    )
    for index, colors in enumerate(columns, start=1):
        result[:, index] = colors
    return result


def _pvrtc2_decompress_words(
    p: tuple[int, int],
    q: tuple[int, int],
    r: tuple[int, int],
    s: tuple[int, int],
) -> np.ndarray:
    modulations = np.zeros((8, 8), dtype=np.int32)
    _pvrtc2_unpack_modulations(modulations, p, p, 0, 0)
    _pvrtc2_unpack_modulations(modulations, q, p, 4, 0)
    _pvrtc2_unpack_modulations(modulations, r, p, 0, 4)
    _pvrtc2_unpack_modulations(modulations, s, p, 4, 4)

    words = (p, q, r, s)
    colors_a = tuple(_pvrtc2_color_a(word[1]) for word in words)
    colors_b = tuple(_pvrtc2_color_b(word[1]) for word in words)
    interpolated_a = _pvrtc2_interpolate(*colors_a)
    interpolated_b = _pvrtc2_interpolate(*colors_b)
    expanded = tuple(
        _pvrtc2_expanded_colors(word[1])
        for word in words
    )
    expanded_a = tuple(item[0] for item in expanded)
    expanded_b = tuple(item[1] for item in expanded)
    palette = None

    output = np.empty((4, 4, 4), dtype=np.uint8)
    for y in range(4):
        for x in range(4):
            modulation = int(modulations[x + 2, y + 2])
            color_a = interpolated_a[y, x]
            color_b = interpolated_b[y, x]
            if modulation >= 30:
                if palette is None:
                    palette = _pvrtc2_palette(expanded_a, expanded_b)
                result = palette[modulation - 30, y * 4 + x]
            else:
                if modulation >= 20:
                    block = (0 if x < 2 else 2) + (0 if y < 2 else 1)
                    color_a = expanded_a[block]
                    color_b = expanded_b[block]
                    modulation -= 20
                punchthrough = modulation > 10
                if punchthrough:
                    modulation -= 10
                result = (
                    color_a * (8 - modulation)
                    + color_b * modulation
                ) // 8
                if punchthrough:
                    result[3] = 0
            output[x, y] = result
    return output


def _decode_pvrtc2_4bpp(
    data: bytes,
    width: int,
    height: int,
    *,
    twiddled: bool,
) -> np.ndarray:
    block_width = max(2, (width + 3) // 4)
    block_height = max(2, (height + 3) // 4)
    words: list[list[tuple[int, int]]] = [
        [(0, 0)] * block_width
        for _ in range(block_height)
    ]
    for y in range(block_height):
        for x in range(block_width):
            block_index = (
                _twiddled_index(x, y, block_width, block_height)
                if twiddled
                else y * block_width + x
            )
            words[y][x] = struct.unpack_from(
                "<2I",
                data,
                block_index * 8,
            )

    output = np.empty(
        (block_height * 4, block_width * 4, 4),
        dtype=np.uint8,
    )
    for word_y in range(-1, block_height - 1):
        for word_x in range(-1, block_width - 1):
            px, py = word_x % block_width, word_y % block_height
            qx, qy = (word_x + 1) % block_width, py
            rx, ry = px, (word_y + 1) % block_height
            sx, sy = qx, ry
            pixels = _pvrtc2_decompress_words(
                words[py][px],
                words[qy][qx],
                words[ry][rx],
                words[sy][sx],
            )
            output[
                py * 4 + 2 : py * 4 + 4,
                px * 4 + 2 : px * 4 + 4,
            ] = pixels[:2, :2]
            output[
                qy * 4 + 2 : qy * 4 + 4,
                qx * 4 : qx * 4 + 2,
            ] = pixels[:2, 2:]
            output[
                ry * 4 : ry * 4 + 2,
                rx * 4 + 2 : rx * 4 + 4,
            ] = pixels[2:, :2]
            output[
                sy * 4 : sy * 4 + 2,
                sx * 4 : sx * 4 + 2,
            ] = pixels[2:, 2:]
    return output[:height, :width]


def _decode_565(value: int) -> np.ndarray:
    return np.array(
        (
            ((value >> 11) & 0x1F) * 255 // 31,
            ((value >> 5) & 0x3F) * 255 // 63,
            (value & 0x1F) * 255 // 31,
            255,
        ),
        dtype=np.uint8,
    )


def _color_palette(block: bytes, *, force_four_color: bool) -> np.ndarray:
    color0, color1 = struct.unpack_from("<HH", block)
    first = _decode_565(color0)
    second = _decode_565(color1)
    palette = np.empty((4, 4), dtype=np.uint8)
    palette[0] = first
    palette[1] = second
    if color0 > color1 or force_four_color:
        palette[2] = (
            (2 * first.astype(np.uint16) + second.astype(np.uint16)) // 3
        ).astype(np.uint8)
        palette[3] = (
            (first.astype(np.uint16) + 2 * second.astype(np.uint16)) // 3
        ).astype(np.uint8)
    else:
        palette[2] = (
            (first.astype(np.uint16) + second.astype(np.uint16)) // 2
        ).astype(np.uint8)
        palette[3] = (0, 0, 0, 0)
    return palette


def _decode_color_block(
    block: bytes,
    *,
    force_four_color: bool,
) -> np.ndarray:
    palette = _color_palette(block, force_four_color=force_four_color)
    indices = struct.unpack_from("<I", block, 4)[0]
    output = np.empty((4, 4, 4), dtype=np.uint8)
    for pixel in range(16):
        output[pixel // 4, pixel % 4] = palette[
            (indices >> (pixel * 2)) & 0x03
        ]
    return output


def _decode_ubc1(data: bytes, width: int, height: int) -> np.ndarray:
    return _decode_blocks(
        data,
        width,
        height,
        block_size=8,
        decoder=lambda block: _decode_color_block(
            block,
            force_four_color=False,
        ),
    )


def _decode_ubc2(data: bytes, width: int, height: int) -> np.ndarray:
    def decode(block: bytes) -> np.ndarray:
        output = _decode_color_block(
            block[8:],
            force_four_color=True,
        )
        alpha_bits = int.from_bytes(block[:8], "little")
        for pixel in range(16):
            output[pixel // 4, pixel % 4, 3] = (
                (alpha_bits >> (pixel * 4)) & 0x0F
            ) * 17
        return output

    return _decode_blocks(
        data,
        width,
        height,
        block_size=16,
        decoder=decode,
    )


def _decode_ubc3(data: bytes, width: int, height: int) -> np.ndarray:
    def decode(block: bytes) -> np.ndarray:
        alpha0 = block[0]
        alpha1 = block[1]
        alphas = [alpha0, alpha1]
        if alpha0 > alpha1:
            alphas.extend(
                ((7 - index) * alpha0 + index * alpha1) // 7
                for index in range(1, 7)
            )
        else:
            alphas.extend(
                ((5 - index) * alpha0 + index * alpha1) // 5
                for index in range(1, 5)
            )
            alphas.extend((0, 255))
        alpha_bits = int.from_bytes(block[2:8], "little")
        output = _decode_color_block(
            block[8:],
            force_four_color=True,
        )
        for pixel in range(16):
            output[pixel // 4, pixel % 4, 3] = alphas[
                (alpha_bits >> (pixel * 3)) & 0x07
            ]
        return output

    return _decode_blocks(
        data,
        width,
        height,
        block_size=16,
        decoder=decode,
    )


def _decode_blocks(
    data: bytes,
    width: int,
    height: int,
    *,
    block_size: int,
    decoder,
) -> np.ndarray:
    block_width = max(1, (width + 3) // 4)
    block_height = max(1, (height + 3) // 4)
    required = block_width * block_height * block_size
    if len(data) < required:
        raise FormatError("GXT top mip block data is truncated")
    output = np.empty((height, width, 4), dtype=np.uint8)
    for block_y in range(block_height):
        for block_x in range(block_width):
            offset = (
                (block_y * block_width + block_x) * block_size
            )
            pixels = decoder(data[offset : offset + block_size])
            copy_height = min(4, height - block_y * 4)
            copy_width = min(4, width - block_x * 4)
            output[
                block_y * 4 : block_y * 4 + copy_height,
                block_x * 4 : block_x * 4 + copy_width,
            ] = pixels[:copy_height, :copy_width]
    return output
