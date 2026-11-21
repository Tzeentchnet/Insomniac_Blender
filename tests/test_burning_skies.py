from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from insomniac_asset_io.core.errors import FormatError, MissingResourceError
from insomniac_asset_io.core.source import GameSource
from insomniac_asset_io.games.resistance_burning_skies.animations import (
    ANIMATION_FRAME_RATE,
    ANIMATION_STREAM_CLASS,
    PackedTrackKind,
    animation_set_clip_names,
    inspect_packed_animation,
    packed_animation_descriptor,
)
from insomniac_asset_io.games.resistance_burning_skies.catalog import (
    build_source_catalog,
)
from insomniac_asset_io.games.resistance_burning_skies.containers import (
    RESOURCE_INFO_PACK_MAGIC,
    STREAM_PACK_MAGIC,
    ResourceInfoPack,
    StreamPack,
)
from insomniac_asset_io.games.resistance_burning_skies.formats import (
    NSIRSRC_MAGIC,
    NSIRSRC_VERSION,
    NsirsrcFile,
)
from insomniac_asset_io.games.resistance_burning_skies.materials import (
    decode_material_texture_paths,
)
from insomniac_asset_io.games.resistance_burning_skies.models import (
    decode_smf,
)
from insomniac_asset_io.games.resistance_burning_skies.reader import (
    BurningSkiesReader,
)
from insomniac_asset_io.games.resistance_burning_skies.textures import (
    FORMAT_PVRTCII4,
    FORMAT_RGBA8_ABGR,
    FORMAT_UBC1,
    FORMAT_UBC2,
    FORMAT_UBC3,
    GXT_MAGIC,
    GXT_VERSION,
    TEXTURE_TYPE_LINEAR,
    TEXTURE_TYPE_SWIZZLED,
    decode_p2t,
)

DATA_BASE = 0x1C0


def _resource(root_type: int, size: int, name: str) -> bytearray:
    data = bytearray(size)
    data[:8] = NSIRSRC_MAGIC
    struct.pack_into("<II", data, 0x08, NSIRSRC_VERSION, DATA_BASE)
    struct.pack_into("<I", data, 0x28, 0x2C)
    encoded_name = name.encode("ascii") + b"\0"
    data[0x2C : 0x2C + len(encoded_name)] = encoded_name
    struct.pack_into("<I", data, 0x5C, root_type)
    return data


def _stream_descriptor(
    data: bytearray,
    *,
    offset: int,
    stream_offset: int,
    stream_size: int,
    scale: float = 1.0,
) -> None:
    struct.pack_into(
        "<f7I",
        data,
        offset,
        scale,
        stream_size,
        stream_offset,
        0,
        0,
        0,
        0,
        0,
    )


def _resource_info_pack(resources: tuple[bytes, ...]) -> bytes:
    inner_base = 0x10
    root_pointer = 0x1C
    table_pointer = 0x28
    next_pointer = table_pointer + len(resources) * 4
    offsets: list[int] = []
    for resource in resources:
        offsets.append(next_pointer)
        next_pointer += len(resource)
    data = bytearray(inner_base + next_pointer)
    struct.pack_into("<I", data, 0, inner_base)
    data[inner_base : inner_base + len(RESOURCE_INFO_PACK_MAGIC)] = (
        RESOURCE_INFO_PACK_MAGIC
    )
    struct.pack_into(
        "<III",
        data,
        inner_base + 0x10,
        1,
        next_pointer,
        root_pointer,
    )
    struct.pack_into(
        "<III",
        data,
        inner_base + root_pointer,
        len(resources),
        table_pointer,
        next_pointer,
    )
    for index, (offset, resource) in enumerate(
        zip(offsets, resources, strict=True)
    ):
        struct.pack_into(
            "<I",
            data,
            inner_base + table_pointer + index * 4,
            offset,
        )
        start = inner_base + offset
        data[start : start + len(resource)] = resource
    return bytes(data)


def _stream_pack(
    entries: tuple[tuple[str, int, bytes, bytes], ...],
) -> bytes:
    inner_base = 0x10
    root_pointer = 0x10
    name_table_pointer = 0x30
    name_records_pointer = 0x38
    name_records_end = name_records_pointer + 4 + len(entries) * 0x0C
    name_pointer = (name_records_end + 3) & ~3
    encoded_names = [name.encode("ascii") + b"\0" for name, *_ in entries]
    name_pointers: list[int] = []
    for encoded_name in encoded_names:
        name_pointers.append(name_pointer)
        name_pointer += len(encoded_name)
    resource_table_pointer = (name_pointer + 0x0F) & ~0x0F
    payload_pointer = (
        resource_table_pointer + 8 + len(entries) * 0x54 + 0x0F
    ) & ~0x0F
    payload_pointers: list[tuple[int, int]] = []
    for _name, _stream_class, primary, secondary in entries:
        primary_pointer = payload_pointer
        payload_pointer += len(primary)
        secondary_pointer = payload_pointer
        payload_pointer += len(secondary)
        payload_pointers.append((primary_pointer, secondary_pointer))

    data = bytearray(inner_base + payload_pointer)
    struct.pack_into("<I", data, 0, inner_base)
    data[inner_base : inner_base + len(STREAM_PACK_MAGIC)] = STREAM_PACK_MAGIC
    struct.pack_into("<I", data, inner_base + 0x04, 2)
    struct.pack_into("<I", data, inner_base + 0x0C, root_pointer)
    root = inner_base + root_pointer
    struct.pack_into("<I", data, root, len(entries))
    struct.pack_into("<I", data, root + 0x0C, resource_table_pointer)
    struct.pack_into("<I", data, root + 0x1C, name_table_pointer)
    struct.pack_into(
        "<II",
        data,
        inner_base + name_table_pointer,
        len(entries),
        name_records_pointer,
    )
    for index, (encoded_name, pointer) in enumerate(
        zip(encoded_names, name_pointers, strict=True)
    ):
        struct.pack_into(
            "<3I",
            data,
            inner_base + name_records_pointer + 4 + index * 0x0C,
            len(encoded_name),
            pointer,
            0x1000 + index,
        )
        start = inner_base + pointer
        data[start : start + len(encoded_name)] = encoded_name

    for index, (
        (name, stream_class, primary, secondary),
        (primary_pointer, secondary_pointer),
    ) in enumerate(zip(entries, payload_pointers, strict=True)):
        del name
        record = (
            inner_base
            + resource_table_pointer
            + 8
            + index * 0x54
        )
        struct.pack_into(
            "<7I",
            data,
            record,
            index,
            stream_class,
            1,
            len(primary),
            primary_pointer,
            len(secondary),
            secondary_pointer,
        )
        primary_start = inner_base + primary_pointer
        secondary_start = inner_base + secondary_pointer
        data[primary_start : primary_start + len(primary)] = primary
        data[secondary_start : secondary_start + len(secondary)] = secondary
    return bytes(data)


def _animation_resource(
    *,
    name: str = "TestClip",
    primary_size: int = 0x80,
    secondary_size: int = 0x40,
) -> bytes:
    data = bytearray(0xB4)
    data[:8] = NSIRSRC_MAGIC
    struct.pack_into("<II", data, 0x08, NSIRSRC_VERSION, len(data))
    struct.pack_into("<I", data, 0x28, 0x2C)
    encoded_name = name.encode("ascii") + b"\0"
    data[0x2C : 0x2C + len(encoded_name)] = encoded_name
    struct.pack_into("<I", data, 0x5C, 0x08)
    struct.pack_into(
        "<f7I",
        data,
        0x8C,
        1.0,
        primary_size,
        0,
        0,
        secondary_size,
        primary_size,
        0,
        0,
    )
    return bytes(data)


def _animation_primary() -> bytes:
    duration = 10.0
    rotation_offset = 0x50
    translation_offset = rotation_offset + 2 * 0x0C
    data = bytearray(translation_offset + 2 * 0x0C)
    struct.pack_into("<II", data, 0, 2, ANIMATION_FRAME_RATE)
    struct.pack_into("<f", data, 0x24, duration)
    struct.pack_into("<2I", data, 0x28, 0xDDDDDDDD, 0xDDDDDDDD)
    struct.pack_into(
        "<4I",
        data,
        0x30,
        2,
        PackedTrackKind.RAW_0,
        2,
        rotation_offset,
    )
    struct.pack_into(
        "<4I",
        data,
        0x40,
        2,
        PackedTrackKind.RAW_1,
        2,
        translation_offset,
    )
    struct.pack_into("<f4h", data, rotation_offset, 0.0, 0, 0, 0, 32767)
    struct.pack_into(
        "<f4h",
        data,
        rotation_offset + 0x0C,
        duration,
        0,
        0,
        16384,
        28377,
    )
    struct.pack_into(
        "<f4h",
        data,
        translation_offset,
        0.0,
        0,
        0,
        0,
        -1,
    )
    struct.pack_into(
        "<f4h",
        data,
        translation_offset + 0x0C,
        duration,
        1024,
        -2048,
        4096,
        -1,
    )
    return bytes(data)


def _model_resource(
    *,
    renderable: bool = True,
    material_names: tuple[str, ...] = ("00000000_11111111_22222222_33333333",),
    material_indices: tuple[int, ...] = (0,),
    lod_material_indices: tuple[tuple[int, ...], ...] | None = None,
    bone_parents: tuple[int, ...] | None = None,
    primary_bone_count: int | None = None,
    skin_palette: tuple[int, ...] | None = None,
    skin_weights: tuple[tuple[float, float, float, float], ...] | None = None,
    skin_local_indices: tuple[tuple[int, int, int, int], ...] | None = None,
    skin_range_lower: int = 0,
    skin_flags: int = 0,
    gib_mesh_indices: tuple[int, ...] = (),
    gib_bone_index: int = 0,
) -> bytes:
    if bone_parents is None:
        default_bone_count = (
            max(1, gib_bone_index + 1 if gib_mesh_indices else 1)
            if renderable
            else 0
        )
        bone_parents = (
            (-1, *(0 for _ in range(default_bone_count - 1)))
            if default_bone_count
            else ()
        )
    if not renderable:
        lod_material_indices = ()
    elif lod_material_indices is None:
        lod_material_indices = (material_indices,)
    lod_count = len(lod_material_indices)
    mesh_count = sum(len(indices) for indices in lod_material_indices)
    lod_records_pointer = 0x140
    entries_pointer = lod_records_pointer + lod_count * 0x10
    mesh_entry_offsets: list[int] = []
    lod_entry_offsets: list[int] = []
    next_entry_offset = entries_pointer
    for lod_indices in lod_material_indices:
        lod_entry_offsets.append(next_entry_offset)
        mesh_entry_offsets.extend(
            next_entry_offset + index * 0x90
            for index in range(len(lod_indices))
        )
        next_entry_offset += len(lod_indices) * 0x90
    flattened_material_indices = tuple(
        index
        for lod_indices in lod_material_indices
        for index in lod_indices
    )
    stride = 44 if skin_palette is not None else 36
    vertex_count = 4
    index_count = 6
    index_offset = (vertex_count * stride + 0x0F) & ~0x0F
    allocation_size = (index_offset + index_count * 2 + 0x0F) & ~0x0F
    entries_end = next_entry_offset
    summary_pointer = (entries_end + 0x0F) & ~0x0F
    descriptor_pointer = summary_pointer + 0x20
    reference_count_pointer = descriptor_pointer + 0x20
    reference_table_end = reference_count_pointer + 12 + mesh_count * 8
    physics_pointer = (
        (reference_table_end + 3) & ~3 if gib_mesh_indices else 0
    )
    gib_property_pointer = (
        physics_pointer + 0x18 if gib_mesh_indices else 0
    )
    gib_property_name = b"prp_traumaBase_small_gib\0"
    physics_end = (
        gib_property_pointer + 4 + len(gib_property_name)
        if gib_mesh_indices
        else 0
    )
    bone_records_pointer = (
        max(reference_table_end, physics_end) + 0x0F
    ) & ~0x0F
    bone_records_end = bone_records_pointer + len(bone_parents) * 0x70
    serialized_end = max(reference_table_end, bone_records_end, physics_end)
    stream_offset = (serialized_end + 0xFF) & ~0xFF
    stream_offsets = tuple(
        stream_offset + lod_level * 0x100
        for lod_level in range(max(lod_count, 1))
    )
    buffer_origins = tuple(
        DATA_BASE + offset + 0x10
        for offset in stream_offsets
    )
    data = _resource(
        0x1C,
        buffer_origins[-1] + allocation_size,
        "TestCube",
    )
    for lod_level, lod_stream_offset in enumerate(stream_offsets):
        _stream_descriptor(
            data,
            offset=0x100 + lod_level * 0x20,
            stream_offset=lod_stream_offset,
            stream_size=allocation_size + 0x10,
            scale=(lod_level + 1) / len(stream_offsets),
        )
    if len(material_names) > 2:
        raise ValueError("Synthetic fixture supports at most two materials")
    record_offsets = (0xD0, 0x180)
    struct.pack_into("<II", data, 0x7C, len(material_names), 0xC0)
    for index, material_name in enumerate(material_names):
        record_offset = record_offsets[index]
        struct.pack_into("<I", data, 0xC0 + index * 4, record_offset)
        struct.pack_into("<I", data, record_offset, 4)
        encoded_name = material_name.encode("ascii") + b"\0"
        data[record_offset + 4 : record_offset + 4 + len(encoded_name)] = encoded_name

    struct.pack_into("<I", data, DATA_BASE, len(material_names))
    declared_bone_count = max(
        len(bone_parents),
        gib_bone_index + 1 if gib_mesh_indices else 0,
    )
    if primary_bone_count is None:
        primary_bone_count = declared_bone_count
    struct.pack_into(
        "<II",
        data,
        DATA_BASE + 0x0C,
        primary_bone_count,
        declared_bone_count,
    )
    struct.pack_into(
        "<I",
        data,
        DATA_BASE + 0x14,
        bone_records_pointer if bone_parents else 0,
    )
    struct.pack_into(
        "<II",
        data,
        DATA_BASE + 0x18,
        lod_count,
        lod_records_pointer if lod_count else 0,
    )
    if gib_mesh_indices:
        struct.pack_into("<I", data, DATA_BASE + 0x04, physics_end)
        struct.pack_into("<I", data, DATA_BASE + 0x28, physics_pointer)
    for bone_index, parent_index in enumerate(bone_parents):
        record_offset = DATA_BASE + bone_records_pointer + bone_index * 0x70
        inverse_world = np.eye(4, dtype=np.float32)
        inverse_world[2, 3] = -float(bone_index)
        struct.pack_into(
            "<16f",
            data,
            record_offset,
            *inverse_world.T.reshape(-1),
        )
        struct.pack_into(
            "<4f",
            data,
            record_offset + 0x40,
            0.0,
            0.0,
            float(bone_index if parent_index < 0 else 1),
            1.0,
        )
        struct.pack_into(
            "<4f",
            data,
            record_offset + 0x50,
            1.0,
            0.0,
            0.0,
            0.0,
        )
        struct.pack_into(
            "<i",
            data,
            record_offset + 0x60,
            parent_index,
        )
    if not renderable:
        return bytes(data)

    for lod_level, lod_indices in enumerate(lod_material_indices):
        struct.pack_into(
            "<2I",
            data,
            DATA_BASE + lod_records_pointer + lod_level * 0x10,
            len(lod_indices),
            lod_entry_offsets[lod_level],
        )
    gib_mesh_index_set = set(gib_mesh_indices)
    for mesh_index, entry_offset in enumerate(mesh_entry_offsets):
        record_offset = DATA_BASE + entry_offset + 0x08
        struct.pack_into(
            "<I",
            data,
            record_offset + 0x0C,
            summary_pointer,
        )
        if skin_palette is not None:
            struct.pack_into(
                "<I",
                data,
                DATA_BASE + entry_offset + 0x44,
                skin_range_lower
                | (len(skin_palette) << 16)
                | (skin_flags << 24),
            )
            data[
                DATA_BASE + entry_offset + 0x70 :
                DATA_BASE + entry_offset + 0x70 + len(skin_palette)
            ] = bytes(skin_palette)
        else:
            rigid_bone_index = (
                gib_bone_index if mesh_index in gib_mesh_index_set else 0
            )
            struct.pack_into(
                "<I",
                data,
                DATA_BASE + entry_offset + 0x44,
                (1 << 16)
                | (
                    gib_bone_index
                    if mesh_index in gib_mesh_index_set
                    else 0
                ),
            )
            data[DATA_BASE + entry_offset + 0x70] = rigid_bone_index
    summary_offset = DATA_BASE + summary_pointer
    descriptor_offset = DATA_BASE + descriptor_pointer
    struct.pack_into(
        "<8I",
        data,
        summary_offset,
        1,
        allocation_size,
        index_count,
        vertex_count,
        0x17 if skin_palette is not None else 0x15,
        0,
        descriptor_pointer,
        0,
    )
    struct.pack_into(
        "<8I",
        data,
        descriptor_offset,
        allocation_size,
        allocation_size,
        vertex_count,
        0,
        2,
        index_count,
        index_offset,
        0,
    )
    reference_count_offset = DATA_BASE + reference_count_pointer
    struct.pack_into(
        "<3I",
        data,
        reference_count_offset,
        mesh_count,
        0x00020500,
        4,
    )
    for reference_index, mesh_index in enumerate(reversed(range(mesh_count))):
        struct.pack_into(
            "<2I",
            data,
            reference_count_offset + 12 + reference_index * 8,
            mesh_entry_offsets[mesh_index],
            flattened_material_indices[mesh_index],
        )
    if gib_mesh_indices:
        struct.pack_into(
            "<6I",
            data,
            DATA_BASE + physics_pointer,
            gib_bone_index,
            gib_property_pointer,
            1,
            0xFFFFFFFF,
            0xFFFFFFFF,
            0,
        )
        struct.pack_into(
            "<I",
            data,
            DATA_BASE + gib_property_pointer,
            0x1388,
        )
        property_name_offset = DATA_BASE + gib_property_pointer + 4
        data[
            property_name_offset : property_name_offset
            + len(gib_property_name)
        ] = gib_property_name

    vertices = (
        ((-1.0, -1.0, 0.0), (0.0, 0.0)),
        ((1.0, -1.0, 0.0), (1.0, 0.0)),
        ((-1.0, 1.0, 0.0), (0.0, 1.0)),
        ((1.0, 1.0, 0.0), (1.0, 1.0)),
    )
    if skin_weights is None:
        skin_weights = ((1.0, 0.0, 0.0, 0.0),) * vertex_count
    if skin_local_indices is None:
        skin_local_indices = ((0, 0, 0, 0),) * vertex_count
    if (
        len(skin_weights) != vertex_count
        or len(skin_local_indices) != vertex_count
    ):
        raise ValueError("Synthetic skin data must contain four vertices")
    for buffer_origin in buffer_origins:
        for index, (position, uv) in enumerate(vertices):
            offset = buffer_origin + index * stride
            struct.pack_into("<3f", data, offset, *position)
            struct.pack_into("<f", data, offset + 12, uv[0])
            struct.pack_into(
                "<4e",
                data,
                offset + 16,
                0.0,
                0.0,
                1.0,
                uv[1] - 1.0,
            )
            struct.pack_into(
                "<4e",
                data,
                offset + 24,
                1.0,
                0.0,
                0.0,
                1.0,
            )
            if skin_palette is None:
                struct.pack_into("<I", data, offset + 32, 0x80000000)
            else:
                struct.pack_into("<4e", data, offset + 32, *skin_weights[index])
                struct.pack_into(
                    "<4B",
                    data,
                    offset + 40,
                    *skin_local_indices[index],
                )
        struct.pack_into(
            "<6H",
            data,
            buffer_origin + index_offset,
            0,
            1,
            2,
            2,
            1,
            3,
        )
    return bytes(data)


def _texture_resource(
    format_code: int = FORMAT_UBC1,
    *,
    payload: bytes | None = None,
    width: int = 4,
    height: int = 4,
    texture_type: int = TEXTURE_TYPE_LINEAR,
) -> bytes:
    stream_offset = 0x200
    gxt_offset = DATA_BASE + stream_offset
    if payload is None:
        if format_code == FORMAT_UBC1:
            payload = struct.pack("<HHI", 0xF800, 0x07E0, 0)
        else:
            payload = bytes(16)
    data = _resource(0x0E, gxt_offset + 0x40 + len(payload), "TestTexture")
    _stream_descriptor(
        data,
        offset=0x100,
        stream_offset=stream_offset,
        stream_size=0x40 + len(payload),
    )
    struct.pack_into(
        "<4s7I",
        data,
        gxt_offset,
        GXT_MAGIC,
        GXT_VERSION,
        1,
        0x40,
        len(payload),
        0,
        0,
        0,
    )
    struct.pack_into(
        "<8I",
        data,
        gxt_offset + 0x20,
        0x40,
        len(payload),
        0xFFFFFFFF,
        0,
        texture_type,
        format_code,
        width | (height << 16),
        1,
    )
    data[gxt_offset + 0x40 :] = payload
    return bytes(data)


class BurningSkiesFormatTests(unittest.TestCase):
    def test_parses_resource_info_and_stream_pack_entries(self):
        resources = (
            bytes(_resource(0x05, DATA_BASE, "TestSet")),
            _animation_resource(),
        )
        resource_pack = ResourceInfoPack(
            _resource_info_pack(resources),
            label="levels/Test.rip",
        )

        self.assertEqual(resource_pack.resource_count, 2)
        self.assertEqual(
            [resource.resource_name for resource in resource_pack.iter_resources()],
            ["TestSet", "TestClip"],
        )
        self.assertEqual(resource_pack.resource(1).root_type, 0x08)

        primary = _animation_primary()
        secondary = bytes(range(64))
        stream_pack = StreamPack(
            _stream_pack(
                (
                    (
                        "TestClip",
                        ANIMATION_STREAM_CLASS,
                        primary,
                        secondary,
                    ),
                )
            ),
            label="levels/Test_global.spk",
        )
        entries = stream_pack.entries_named("TestClip")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].stream_class, ANIMATION_STREAM_CLASS)
        self.assertEqual(stream_pack.primary_data(entries[0]), primary)
        self.assertEqual(stream_pack.secondary_data(entries[0]), secondary)
        self.assertEqual(stream_pack.entries_named("Missing"), ())

    def test_rejects_malformed_resource_and_stream_packs(self):
        invalid_magic = bytearray(
            _resource_info_pack(
                (bytes(_resource(0x05, DATA_BASE, "TestSet")),)
            )
        )
        invalid_magic[0x10 + 0x0F] = ord("!")
        with self.assertRaisesRegex(FormatError, "magic"):
            ResourceInfoPack(bytes(invalid_magic), label="Bad.rip")

        packed_resource = bytearray(
            _resource_info_pack(
                (bytes(_resource(0x05, DATA_BASE, "TestSet")),)
            )
        )
        struct.pack_into("<I", packed_resource, 0x10 + 0x28, 0x20)
        with self.assertRaisesRegex(FormatError, "overlaps"):
            ResourceInfoPack(bytes(packed_resource), label="Bad.rip")

        packed_stream = bytearray(
            _stream_pack(
                (
                    (
                        "TestClip",
                        ANIMATION_STREAM_CLASS,
                        _animation_primary(),
                        bytes(16),
                    ),
                )
            )
        )
        inner_base = struct.unpack_from("<I", packed_stream)[0]
        root_pointer = struct.unpack_from(
            "<I",
            packed_stream,
            inner_base + 0x0C,
        )[0]
        table_pointer = struct.unpack_from(
            "<I",
            packed_stream,
            inner_base + root_pointer + 0x0C,
        )[0]
        record_offset = inner_base + table_pointer + 8
        struct.pack_into("<I", packed_stream, record_offset, 99)
        with self.assertRaisesRegex(FormatError, "invalid name index"):
            StreamPack(bytes(packed_stream), label="Bad.spk")

        struct.pack_into("<I", packed_stream, record_offset, 0)
        struct.pack_into(
            "<I",
            packed_stream,
            record_offset + 0x10,
            len(packed_stream),
        )
        with self.assertRaisesRegex(FormatError, "outside the stream pack"):
            StreamPack(bytes(packed_stream), label="Bad.spk")

    def test_inspects_animation_set_descriptor_and_raw_tracks(self):
        set_data = _resource(0x05, DATA_BASE, "TestSet")
        struct.pack_into("<II", set_data, 0x7C, 2, 0x100)
        struct.pack_into("<2I", set_data, 0x100, 0x110, 0x130)
        struct.pack_into("<I", set_data, 0x110, 0x02)
        set_data[0x114 : 0x11A] = b"Walk\0\0"
        struct.pack_into("<I", set_data, 0x130, 0x02)
        set_data[0x134 : 0x139] = b"Idle\0"
        animation_set = NsirsrcFile(bytes(set_data), label="TestSet")

        self.assertEqual(
            animation_set_clip_names(animation_set),
            ("Walk", "Idle"),
        )

        resource = NsirsrcFile(
            _animation_resource(),
            label="TestClip",
        )
        descriptor = packed_animation_descriptor(resource)
        self.assertEqual(descriptor.primary_size, 0x80)
        self.assertEqual(descriptor.primary_offset, 0)
        self.assertEqual(descriptor.secondary_size, 0x40)
        self.assertEqual(descriptor.secondary_offset, 0x80)

        clip = inspect_packed_animation(
            _animation_primary(),
            label="TestClip",
        )
        self.assertEqual(clip.frame_rate, ANIMATION_FRAME_RATE)
        self.assertEqual(clip.duration, 10.0)
        self.assertEqual(len(clip.tracks), 2)
        self.assertEqual(clip.tracks[0].bone_index, 2)
        self.assertIs(clip.tracks[0].kind, PackedTrackKind.RAW_0)
        self.assertIs(clip.tracks[1].kind, PackedTrackKind.RAW_1)
        np.testing.assert_array_equal(
            clip.tracks[0].times,
            np.array((0.0, 10.0), dtype=np.float32),
        )
        np.testing.assert_array_equal(
            clip.tracks[1].values[1],
            np.array((1024, -2048, 4096, -1), dtype=np.int16),
        )

        placeholder = inspect_packed_animation(
            bytes(0x28),
            label="Empty",
        )
        self.assertTrue(placeholder.is_placeholder)
        self.assertEqual(placeholder.tracks, ())

    def test_rejects_malformed_raw_animation_tracks(self):
        cases: list[tuple[str, int, int | float, str]] = [
            ("frame rate", 0x04, 30, "frame rate"),
            ("track kind", 0x34, 3, "unknown kind"),
            ("key range", 0x3C, 0xFFFF, "key range"),
            ("duration", 0x24, 9.0, "ends at"),
        ]
        for name, offset, value, message in cases:
            with self.subTest(name=name):
                data = bytearray(_animation_primary())
                if isinstance(value, float):
                    struct.pack_into("<f", data, offset, value)
                else:
                    struct.pack_into("<I", data, offset, value)
                with self.assertRaisesRegex(FormatError, message):
                    inspect_packed_animation(bytes(data), label=name)

    def test_decodes_rigid_gbuffer_geometry_and_uvs(self):
        model = decode_smf(
            _model_resource(),
            label="models/TestCube.smf",
        )

        self.assertEqual(model.name, "TestCube")
        self.assertEqual(
            model.material_names,
            ("00000000_11111111_22222222_33333333",),
        )
        self.assertTrue(model.material_binding_supported)
        self.assertEqual(len(model.meshes), 1)
        mesh = model.meshes[0]
        self.assertEqual(mesh.lod_level, 0)
        np.testing.assert_array_equal(
            mesh.indices,
            np.array([0, 1, 2, 2, 1, 3], dtype=np.int32),
        )
        np.testing.assert_allclose(
            mesh.uvs,
            np.array(
                ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)),
                dtype=np.float32,
            ),
        )
        np.testing.assert_array_equal(
            mesh.bone_indices,
            np.zeros((4, 4), dtype=np.uint16),
        )
        np.testing.assert_array_equal(
            mesh.bone_weights,
            np.array(
                (
                    (1.0, 0.0, 0.0, 0.0),
                    (1.0, 0.0, 0.0, 0.0),
                    (1.0, 0.0, 0.0, 0.0),
                    (1.0, 0.0, 0.0, 0.0),
                ),
                dtype=np.float32,
            ),
        )
        self.assertIsNotNone(model.skeleton)

    def test_rejects_model_without_render_geometry(self):
        with self.assertRaisesRegex(
            MissingResourceError,
            "neither renderable GBuffer geometry nor a recoverable skeleton",
        ):
            decode_smf(
                _model_resource(renderable=False),
                label="models/TestPhysics.smf",
            )

    def test_decodes_skeleton_only_model(self):
        model = decode_smf(
            _model_resource(
                renderable=False,
                bone_parents=(-1, 0),
            ),
            label="models/TestSkeleton.smf",
        )

        self.assertEqual(model.meshes, [])
        self.assertEqual(model.material_names, ())
        self.assertIsNotNone(model.skeleton)
        assert model.skeleton is not None
        self.assertEqual(model.skeleton.bone_count, 2)
        self.assertEqual(model.skeleton.root_bone, 0)
        self.assertEqual(model.skeleton.bones[1].parent_index, 0)
        np.testing.assert_allclose(
            model.skeleton.bind_world[:, :3, 3],
            np.array(((0.0, 0.0, 0.0), (0.0, 0.0, 1.0))),
            atol=1e-6,
        )

    def test_uses_total_bone_record_count_for_skeleton(self):
        model = decode_smf(
            _model_resource(
                renderable=False,
                bone_parents=(-1, 0, 1, 2),
                primary_bone_count=2,
            ),
            label="models/TestExtendedSkeleton.smf",
        )

        assert model.skeleton is not None
        self.assertEqual(model.skeleton.bone_count, 4)
        self.assertEqual(model.skeleton.bones[3].parent_index, 2)

    def test_decodes_skinned_half_weights_and_local_bone_palette(self):
        source_weights = (
            (0.5, 0.25, 0.25, 0.0),
            (0.33325, 0.33325, 0.33325, 0.0),
            (1.0, 0.0, 0.0, 0.0),
            (0.1, 0.2, 0.3, 0.4),
        )
        model = decode_smf(
            _model_resource(
                bone_parents=(-1, 0, 0, 0),
                skin_palette=(3, 1, 1, 2),
                skin_weights=source_weights,
                skin_local_indices=(
                    (0, 1, 2, 7),
                    (0, 1, 3, 7),
                    (2, 255, 255, 255),
                    (0, 1, 2, 3),
                ),
                skin_range_lower=10,
                skin_flags=9,
            ),
            label="models/TestSkinned.smf",
        )

        self.assertIsNotNone(model.skeleton)
        assert model.skeleton is not None
        self.assertEqual(model.skeleton.bone_count, 4)
        mesh = model.meshes[0]
        np.testing.assert_array_equal(
            mesh.bone_indices,
            np.array(
                (
                    (3, 1, 0, 0),
                    (3, 1, 2, 0),
                    (1, 0, 0, 0),
                    (3, 1, 0, 2),
                ),
                dtype=np.uint16,
            ),
        )
        expected_weights = np.asarray(
            source_weights,
            dtype=np.float16,
        ).astype(np.float32)
        expected_sums = expected_weights.sum(axis=1)
        expected_weights[0, 1] += expected_weights[0, 2]
        expected_weights[0, 2] = 0.0
        expected_weights[3, 1] += expected_weights[3, 2]
        expected_weights[3, 2] = 0.0
        expected_weights /= expected_sums[:, np.newaxis]
        np.testing.assert_allclose(
            mesh.bone_weights,
            expected_weights,
            atol=1e-7,
        )
        np.testing.assert_allclose(
            mesh.bone_weights.sum(axis=1),
            np.ones(4, dtype=np.float32),
            atol=1e-7,
        )

    def test_rejects_invalid_skinned_influences(self):
        valid_weights = ((1.0, 0.0, 0.0, 0.0),) * 4
        valid_indices = ((0, 0, 0, 0),) * 4
        cases = (
            (
                "bone palette count",
                {
                    "skin_palette": (),
                },
            ),
            (
                "skin index exceeds",
                {
                    "skin_palette": (1, 2),
                    "skin_local_indices": (
                        (2, 0, 0, 0),
                        *valid_indices[1:],
                    ),
                },
            ),
            (
                "bone palette exceeds",
                {
                    "skin_palette": (4,),
                },
            ),
            (
                "normalization tolerance",
                {
                    "skin_palette": (1,),
                    "skin_weights": (
                        (0.5, 0.0, 0.0, 0.0),
                        *valid_weights[1:],
                    ),
                },
            ),
            (
                "negative skin weights",
                {
                    "skin_palette": (1,),
                    "skin_weights": (
                        (0.5, -0.1, 0.6, 0.0),
                        *valid_weights[1:],
                    ),
                },
            ),
            (
                "zero-sum skin vertex",
                {
                    "skin_palette": (1,),
                    "skin_weights": (
                        (0.0, 0.0, 0.0, 0.0),
                        *valid_weights[1:],
                    ),
                },
            ),
            (
                "non-finite skin weights",
                {
                    "skin_palette": (1,),
                    "skin_weights": (
                        (float("nan"), 0.0, 0.0, 0.0),
                        *valid_weights[1:],
                    ),
                },
            ),
        )

        for message, overrides in cases:
            with self.subTest(message=message):
                parameters = {
                    "bone_parents": (-1, 0, 0, 0),
                    "skin_weights": valid_weights,
                    "skin_local_indices": valid_indices,
                    **overrides,
                }
                with self.assertRaisesRegex(FormatError, message):
                    decode_smf(
                        _model_resource(**parameters),
                        label="models/TestInvalidSkin.smf",
                    )

    def test_uses_serialized_packet_material_map(self):
        model = decode_smf(
            _model_resource(
                material_names=(
                    "00000000_11111111_22222222_33333333",
                    "AAAAAAAA_BBBBBBBB_CCCCCCCC_DDDDDDDD",
                ),
                material_indices=(1, 0, 1),
            ),
            label="models/TestMappedMaterials.smf",
        )

        self.assertTrue(model.material_binding_supported)
        self.assertEqual(model.material_dependency_count, 2)
        self.assertEqual(
            model.material_names,
            (
                "00000000_11111111_22222222_33333333",
                "AAAAAAAA_BBBBBBBB_CCCCCCCC_DDDDDDDD",
            ),
        )
        self.assertEqual(
            [mesh.shader_index for mesh in model.meshes],
            [1, 0, 1],
        )

    def test_classifies_authored_gib_packets_as_damage(self):
        model = decode_smf(
            _model_resource(
                material_indices=(0, 0),
                gib_mesh_indices=(1,),
                gib_bone_index=5,
            ),
            label="models/TestDamage.smf",
        )

        self.assertFalse(model.meshes[0].is_damage_permutation)
        self.assertTrue(model.meshes[1].is_damage_permutation)
        self.assertEqual(model.meshes[1].segment_index, 5)

    def test_decodes_all_lod_groups_and_exact_material_offsets(self):
        model = decode_smf(
            _model_resource(
                material_names=(
                    "00000000_11111111_22222222_33333333",
                    "AAAAAAAA_BBBBBBBB_CCCCCCCC_DDDDDDDD",
                ),
                lod_material_indices=((1, 0), (0, 1)),
            ),
            label="models/TestLods.smf",
        )

        self.assertEqual(
            [mesh.lod_level for mesh in model.meshes],
            [0, 0, 1, 1],
        )
        self.assertEqual(
            [mesh.shader_index for mesh in model.meshes],
            [0, 1, 1, 0],
        )

    def test_rejects_out_of_range_model_index(self):
        data = bytearray(_model_resource())
        index_offset = DATA_BASE + 0x300 + 0x10 + 0x90
        struct.pack_into("<H", data, index_offset, 10)

        with self.assertRaisesRegex(FormatError, "index exceeds"):
            decode_smf(bytes(data), label="models/Invalid.smf")

    def test_rejects_invalid_rigid_bone_palette(self):
        entry_offset = DATA_BASE + 0x150
        cases = (
            ("rigid mesh 0 has 2 palette bones", 2, 0),
            ("bone palette exceeds", 1, 1),
        )
        for message, palette_count, bone_index in cases:
            with self.subTest(message=message):
                data = bytearray(_model_resource())
                data[entry_offset + 0x46] = palette_count
                data[entry_offset + 0x70] = bone_index
                with self.assertRaisesRegex(FormatError, message):
                    decode_smf(bytes(data), label="models/InvalidRigid.smf")

    def test_decodes_linear_ubc1_gxt(self):
        texture = decode_p2t(
            _texture_resource(),
            label="textures/Test.p2t",
            texture_id=123,
        )

        self.assertEqual(texture.rgba.shape, (4, 4, 4))
        np.testing.assert_array_equal(
            texture.rgba,
            np.full((4, 4, 4), (255, 0, 0, 255), dtype=np.uint8),
        )

    def test_decodes_pvrtc2_hard_transition_and_punchthrough(self):
        blocks = (
            (0xE4E4E4E4, 0xFC1FFC01 | 0x8000),
            (0x1B1B1B1B, 0x83E0FFFF),
            (0xAAAAAAAA, 0xFFFF801F),
            (0x55555555, 0xFC007C01),
        )
        payload = b"".join(
            struct.pack("<2I", *block)
            for block in blocks
        )
        texture = decode_p2t(
            _texture_resource(
                FORMAT_PVRTCII4,
                payload=payload,
                width=8,
                height=8,
                texture_type=TEXTURE_TYPE_SWIZZLED,
            ),
            label="textures/TestNormal.p2t",
            texture_id=456,
        )

        coordinates = (
            (0, 0),
            (0, 3),
            (1, 6),
            (2, 2),
            (3, 5),
            (4, 1),
            (5, 4),
            (7, 7),
        )
        actual = [
            tuple(int(channel) for channel in texture.rgba[y, x])
            for y, x in coordinates
        ]
        self.assertEqual(
            actual,
            [
                (191, 63, 127, 255),
                (255, 0, 0, 255),
                (159, 95, 191, 0),
                (255, 0, 159, 255),
                (255, 255, 255, 255),
                (255, 0, 0, 255),
                (255, 0, 0, 255),
                (207, 71, 79, 255),
            ],
        )

    def test_decodes_ubc2_explicit_alpha(self):
        payload = bytes([0xFF] * 8) + struct.pack(
            "<HHI",
            0xF800,
            0x07E0,
            0,
        )
        texture = decode_p2t(
            _texture_resource(FORMAT_UBC2, payload=payload),
            label="textures/TestUbc2.p2t",
            texture_id=124,
        )

        np.testing.assert_array_equal(
            texture.rgba,
            np.full((4, 4, 4), (255, 0, 0, 255), dtype=np.uint8),
        )

    def test_decodes_ubc3_interpolated_alpha(self):
        payload = (
            bytes((255, 0))
            + bytes(6)
            + struct.pack(
                "<HHI",
                0xF800,
                0x07E0,
                0,
            )
        )
        texture = decode_p2t(
            _texture_resource(FORMAT_UBC3, payload=payload),
            label="textures/TestUbc3.p2t",
            texture_id=125,
        )

        np.testing.assert_array_equal(
            texture.rgba,
            np.full((4, 4, 4), (255, 0, 0, 255), dtype=np.uint8),
        )

    def test_decodes_ubc1_four_color_interpolation_without_byte_wrap(self):
        selectors = sum(3 << (2 * pixel) for pixel in range(16))
        payload = struct.pack("<HHI", 0xF800, 0x07E0, selectors)

        texture = decode_p2t(
            _texture_resource(FORMAT_UBC1, payload=payload),
            label="textures/TestUbc1Interpolation.p2t",
            texture_id=126,
        )

        np.testing.assert_array_equal(
            texture.rgba,
            np.full((4, 4, 4), (85, 170, 0, 255), dtype=np.uint8),
        )

    def test_decodes_32_bit_bgra_channel_order(self):
        texture = decode_p2t(
            _texture_resource(
                FORMAT_RGBA8_ABGR,
                payload=bytes((255, 0, 0, 128)) * 16,
            ),
            label="textures/TestColor.p2t",
            texture_id=126,
        )

        np.testing.assert_array_equal(
            texture.rgba,
            np.full((4, 4, 4), (0, 0, 255, 128), dtype=np.uint8),
        )

    def test_unswizzles_vita_rectangular_morton_block_order(self):
        colors = (
            0xF800,
            0x07E0,
            0x001F,
            0xFFFF,
            0xFFE0,
            0x07FF,
            0xF81F,
            0x0000,
        )
        payload = b"".join(struct.pack("<HHI", color, 0, 0) for color in colors)
        texture = decode_p2t(
            _texture_resource(
                FORMAT_UBC1,
                payload=payload,
                width=16,
                height=8,
                texture_type=TEXTURE_TYPE_SWIZZLED,
            ),
            label="textures/TestSwizzled.p2t",
            texture_id=127,
        )

        expected = (
            (255, 0, 0),
            (0, 0, 255),
            (255, 255, 0),
            (255, 0, 255),
            (0, 255, 0),
            (255, 255, 255),
            (0, 255, 255),
            (0, 0, 0),
        )
        actual = tuple(
            tuple(int(channel) for channel in texture.rgba[y, x, :3])
            for y in (1, 5)
            for x in (1, 5, 9, 13)
        )
        self.assertEqual(actual, expected)

    def test_resolves_bmx_texture_parameter_names(self):
        data = _resource(0x09, DATA_BASE, "TestMaterial")
        strings = (
            b"g_DiffuseMapTexture\0Test_CLR\0"
            b"g_NormalMapTexture\0Test_NRM\0"
            b"g_GlossMapTexture\0Test_GLS\0"
            b"g_DetailMapTexture\0Test_DTL\0"
            b"g_EmissiveMapTexture\0Test_GLO\0"
            b"g_AlphaMapTexture\0Test_APH\0"
        )
        data[0x100 : 0x100 + len(strings)] = strings

        textures = decode_material_texture_paths(
            bytes(data),
            label="materials/Test.bmx",
            texture_paths_by_stem={
                "test_clr": "textures/Test_CLR.p2t",
                "test_nrm": "textures/Test_NRM.p2t",
                "test_gls": "textures/Test_GLS.p2t",
                "test_dtl": "textures/Test_DTL.p2t",
                "test_glo": "textures/Test_GLO.p2t",
                "test_aph": "textures/Test_APH.p2t",
            },
        )

        self.assertEqual(textures.albedo, "textures/Test_CLR.p2t")
        self.assertEqual(textures.normal, "textures/Test_NRM.p2t")
        self.assertEqual(textures.specular, "textures/Test_GLS.p2t")
        self.assertEqual(textures.specular_semantics, "GLOSS")
        self.assertEqual(textures.detail, "textures/Test_DTL.p2t")
        self.assertEqual(textures.emissive, "textures/Test_GLO.p2t")
        self.assertEqual(textures.alpha, "textures/Test_APH.p2t")

    def test_distinguishes_bmx_specular_map_from_gloss(self):
        data = _resource(0x09, DATA_BASE, "TestMaterial")
        strings = b"g_SpecularMapTexture\0Test_SPC\0"
        data[0x100 : 0x100 + len(strings)] = strings

        textures = decode_material_texture_paths(
            bytes(data),
            label="materials/Test.bmx",
            texture_paths_by_stem={
                "test_spc": "textures/Test_SPC.p2t",
            },
        )

        self.assertEqual(textures.specular, "textures/Test_SPC.p2t")
        self.assertEqual(textures.specular_semantics, "SPECULAR")

    def test_decodes_color_only_opaque_emissive_material(self):
        data = _resource(0x09, DATA_BASE + 0x300, "TestEmissive")
        data[0x100 : 0x100 + len(b"OpaqueEmissive\0")] = (
            b"OpaqueEmissive\0"
        )
        root = DATA_BASE
        struct.pack_into(
            "<II",
            data,
            root + 0x24,
            0xCCCCCCCC,
            0xCCCCCCCC,
        )
        parameters = (
            ("diffuse_color", 0x0B, (0.25, 0.5, 0.75)),
            ("emissive_color", 0x0B, (1.0, 0.5, 0.25)),
            ("emissive_factor", 0x09, (2.0,)),
            ("Use_Emissive_Texture", 0x09, (0.0,)),
        )
        records_pointer = 0x100
        struct.pack_into(
            "<II",
            data,
            root + 0x70,
            len(parameters),
            records_pointer,
        )
        for index, (name, type_id, value) in enumerate(parameters):
            name_pointer = 0x180 + index * 0x20
            value_pointer = 0x220 + index * 0x10
            struct.pack_into(
                "<6I",
                data,
                root + records_pointer + index * 0x18,
                name_pointer,
                type_id,
                value_pointer,
                value_pointer,
                0,
                0,
            )
            encoded_name = name.encode("ascii") + b"\0"
            data[
                root + name_pointer : root
                + name_pointer
                + len(encoded_name)
            ] = encoded_name
            struct.pack_into(
                f"<{len(value)}f",
                data,
                root + value_pointer,
                *value,
            )

        material = decode_material_texture_paths(
            bytes(data),
            label="materials/TestEmissive.bmx",
            texture_paths_by_stem={},
        )

        self.assertEqual(
            material.diffuse_tint,
            (0.25, 0.5, 0.75, 1.0),
        )
        self.assertEqual(material.emissive_color, (1.0, 0.5, 0.25))
        self.assertEqual(material.emissive_factor, 2.0)
        self.assertFalse(material.use_emissive_texture)
        self.assertTrue(material.emissive_enabled)

        with tempfile.TemporaryDirectory() as temporary:
            source_root = Path(temporary)
            (source_root / "models").mkdir()
            (source_root / "models" / "Test.smf").write_bytes(b"")
            (source_root / "materials").mkdir()
            (source_root / "materials" / "ColorOnly.bmx").write_bytes(data)
            textured_data = bytearray(data)
            struct.pack_into("<f", textured_data, root + 0x250, 1.0)
            (source_root / "materials" / "MissingMap.bmx").write_bytes(
                textured_data
            )
            with GameSource.open(source_root) as source:
                reader = BurningSkiesReader(source)
                for name, uses_texture in (
                    ("ColorOnly.bmx", False),
                    ("MissingMap.bmx", True),
                ):
                    with self.subTest(name=name):
                        shader = reader.read_shader(
                            reader.material_id(f"materials/{name}")
                        )
                        self.assertTrue(shader.emissive_enabled)
                        self.assertIs(
                            shader.use_emissive_texture,
                            uses_texture,
                        )
                        self.assertIsNone(shader.emissive_texture_id)

    def test_classifies_auxiliary_bmx_materials(self):
        data = _resource(0x09, DATA_BASE, "TestMaterial")
        strings = b"WorldCollision\0OnlyCastShadows\0"
        data[0x100 : 0x100 + len(strings)] = strings

        material = decode_material_texture_paths(
            bytes(data),
            label="materials/Test.bmx",
            texture_paths_by_stem={},
        )

        self.assertTrue(material.world_collision)
        self.assertTrue(material.only_cast_shadows)
        self.assertTrue(material.is_auxiliary)

    def test_builds_source_driven_catalog_from_smf_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_path = root / "models" / "Test_Cube.smf"
            model_path.parent.mkdir()
            model_path.write_bytes(_model_resource())

            catalog = build_source_catalog(root)

        self.assertEqual(catalog.game_id, "RESISTANCE_BURNING_SKIES")
        self.assertEqual(catalog.region, "Experimental Vita archive")
        self.assertEqual(len(catalog.assets), 1)
        asset = catalog.assets[0]
        self.assertEqual(asset.display_name, "Test Cube")
        self.assertEqual(asset.identity_label.split()[0], "Model")
        self.assertEqual(asset.source_name, "models/Test_Cube.smf")

    def test_existing_invalid_catalog_source_reports_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "build.psa"
            path.write_bytes(b"not a PSARC")

            with self.assertRaisesRegex(
                MissingResourceError,
                "contains no named models",
            ):
                build_source_catalog(path)

    def test_reports_material_fallback_for_missing_bmx(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_path = root / "models" / "Test_Cube.smf"
            model_path.parent.mkdir()
            model_path.write_bytes(_model_resource())
            with GameSource.open(root) as source:
                reader = BurningSkiesReader(source)
                model = reader.read_moby(reader.model_id("models/Test_Cube.smf"))
                warnings = reader.consume_import_warnings()

        self.assertEqual(model.shader_tuids, ())
        self.assertTrue(
            any("BMX dependencies are unavailable" in item for item in warnings)
        )
        self.assertEqual(reader.consume_import_warnings(), ())


if __name__ == "__main__":
    unittest.main()
