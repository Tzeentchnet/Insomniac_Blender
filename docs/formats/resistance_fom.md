# Resistance: Fall of Man formats

This adapter targets the USA `BCUS98107` v01.00 retail disc. It validates
`TITLE_ID` and `VERSION` from `PARAM.SFO` before extracting data.

No game payloads are distributed. The bundled metadata catalog contains
36,034 level-scoped references across 41 levels: 5,556 Mobys, 6,714 Ties,
and 23,764 Materials.

## Archive topology

Most retail assets are entries in `PS3_GAME/USRDIR/GAME.PSARC`, a 6.5 GB
zlib PSARC on the tested disc. The archive contains level resources under:

```text
packed/levels/<level>/
```

The add-on opens `GAME.PSARC` as a bounded seekable view over its ISO9660
extents. It reads the PSARC table of contents and decompresses only these
entries for the selected level:

- `ps3levelmain.dat`
- `ps3levelverts.dat`
- `ps3leveltexs.dat`
- `ps3gameplay.dat`

This avoids copying the complete archive. Catalog generation extracts only
`ps3levelmain.dat`.

`ps3levelmain.dat` and `ps3levelverts.dat` are big-endian IGHW 1.1 files.
`ps3leveltexs.dat` is a raw payload file. `ps3gameplay.dat` is a big-endian
IGHW 0.2 file with buffer-mode sections.

## Level-local identities

| Kind | Reader identity |
|---|---|
| Moby | `u16` class ID at Moby header offset `0x1A` |
| Tie | byte offset of its `0x3400` header |
| Material | index in `ps3levelmain.dat:0x5001` |
| Texture | byte offset of its `0x5300` metadata record |

The values repeat between levels. Catalog identifiers therefore use
`KIND:level:VALUE`.

Retail names are not present in a proven in-disc table for this chain. The
reader uses deterministic `Moby_XXXX`, `Tie_XXXXXXXX`, and `Material_XXXX`
names.

## Mobys

Section `0xD100` contains `0xC0`-byte Moby V1 headers.

| Header offset | Type | Meaning |
|---:|---|---|
| `0x00` | `vec3 + f32` | bounding sphere |
| `0x14` | `u16` | bone count |
| `0x16` | `u16` | animation count |
| `0x18` | `u16` | bangle count |
| `0x1A` | `u16` | Moby class ID |
| `0x20` | `u32` | skeleton pointer |
| `0x24` | `u32` | inline animation pointer table |
| `0x28` | `u32` | bangle table |
| `0x34` | `u32` | raw index-payload base |
| `0x38` | `u32` | raw vertex-payload base |
| `0x3C` | `f32` | mesh position scale |

Bangles contain a primitive-table pointer and `u32` primitive count. RFoM
primitive records have stride `0x20`:

| Primitive offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u16` | material index |
| `0x02` | `u16` | vertex count |
| `0x04` | `u16` | index count |
| `0x06` | `u8` | bone-palette count |
| `0x07` | `u8` | vertex format |
| `0x08` | `u32` | index offset in `u16` units |
| `0x0C` | `u32` | vertex byte offset |
| `0x10` | `u32` | bone-palette pointer |

Moby geometry addresses raw `ps3leveltexs.dat`. Static vertices have stride
`0x14`; skinned vertices have stride `0x1C`. The shared semantic mesh decoder
handles signed scaled positions, half-float UVs, palettes, and weights.

## Inline animations

Each Moby header points to an array of absolute
`ps3levelmain.dat` animation pointers. The control headers, skeleton records,
quantized tracks, and additive subset bone-count behavior use the shared old
PS3 animation structures.

RFoM has a dedicated playback profile. It does not apply the Tools of
Destruction 8-bit `<< 2` transform or pair-frame reduction, and it does not
treat flag `0x0200` as the later Resistance delta-position/scale convention.

## Ties

Section `0x3400` contains `0x40`-byte Tie V1 headers:

| Header offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u32` | primitive table |
| `0x08` | `u16` | primitive count |
| `0x10` | `u32` | vertex-buffer byte offset |
| `0x14` | `u32` | optional secondary UV stream offset |
| `0x18` | `u16` | authored Tie ID |
| `0x20` | `vec3` | per-axis position scale |

Tie primitives have stride `0x20`. The material index is at `0x00`, index
offset/count at `0x04`/`0x08`, vertex count at `0x0A`, and the local
`0x14`-byte vertex index at `0x0C`. Geometry comes from
`ps3levelverts.dat:0x9000` and `0x9100`.

## Materials and textures

Section `0x5001` contains `0x80`-byte Material V1 records:

| Offset | Type | Meaning |
|---:|---|---|
| `0x1C` | `u8` | specular, gloss, normal, and detail flags |
| `0x1D` | `u8` | blend mode |
| `0x20` | `u32[4]` | albedo, normal, packed, and detail texture pointers |
| `0x30` | `vec4[5]` | raw material parameters |

Texture references are byte offsets of `0x5300` records. The reader retains
the complete four-byte material control word and all five parameter vectors.
Blend mode `4` is masked, nonzero modes are blended, and zero is opaque.

Section `0x5300` contains `0x20`-byte Texture V1 records. Pixel offset, mip
count, full format byte, width, and height are at `0x00`, `0x04`, `0x06`,
`0x18`, and `0x1A`. Pixel payloads are in `ps3leveltexs.dat`. The tested
level uses `0x81`, `0x85`, `0x86`, `0x87`, `0x88`, and `0x8B`, all handled
by the shared R8, A8R8G8B8, DXT1/3/5, and RG8 decoders.

## Terrain and placements

Section `0x6200` contains `0x80`-byte region meshes. Region position and
primary material index are at `0x40` and `0x4C`. Index offset, vertex offset,
index count, and vertex count are at `0x54`, `0x58`, `0x5C`, and `0x5E`.

Region vertices have stride `0x1A`. Because the shared `TerrainMesh` output
does not carry a separate region transform, positions are baked as:

```text
(signed_position + region_position) * 0.9144 / 256
```

Section `0x9300` contains `0xC0`-byte Tie placements. A row-major 4x4 matrix
begins at `0x00`; the Tie header pointer is at `0x8C`. Translation is
converted from yards to meters.

The `0x25000` gameplay structure points to `GameplayInstances` at `0x20`.
The primary Moby group starts at `GameplayInstances + 0x40`; its records have
stride `0x40`. Position, ZYX Euler rotation, and Moby class ID are at `0x10`,
`0x1C`, and `0x3C`. Position is converted from yards to meters.

The six additional gameplay groups are not imported because their record
semantics are not yet corroborated.

## Current limitations

- `ps3levelcoll.dat` collision is not decoded.
- Directional lightmap textures in section `0x5500` are not imported.
- Shrubs, foliage, lighting, VFX, cubemaps, and the non-primary gameplay
  groups are not decoded.
- Deterministic numeric names are used until retail names can be recovered
  from a proven source.

The layouts were checked against the USA v01.00 disc and independently
corroborated with the GPL-3.0 InsomniaToolset and ReChimera implementations.
