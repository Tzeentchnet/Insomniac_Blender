# Ratchet & Clank Future: Tools of Destruction formats

This adapter targets the USA `BCUS98127` v02.00 retail disc. It validates
`TITLE_ID` and `VERSION` from a bounded `PARAM.SFO` parse before extracting
any level.

No game payloads are distributed. The bundled catalog contains only numeric
asset metadata and deterministic fallback names: 3,012 Mobys, 2,048 Ties,
and 10,521 level-scoped Materials across 20 levels.

## Archive topology

Each of the 20 levels contains three archives:

- `level_cached.psarc`
- `level_uncached.psarc`
- `level_textures.psarc`

Opening any member of the trio overlays all available siblings. The cached
archive contains `main.dat`, `vertices.dat`, `textures.dat`, and
`gameplay.dat`; the texture archive supplies `texstream.dat`.

TOD does not use the R2/R3 `assetlookup.dat` plus per-kind resource chain.
Most metadata is stored directly in a big-endian IGHW 1.1 `main.dat`.

## Level-local identities

| Kind | Identity passed to the reader |
|---|---|
| Moby | index in `main.dat:0xD100` |
| Tie | byte offset of its `0x3400` header |
| Foliage | byte offset of its `0xA200` descriptor |
| Shader | index in `main.dat:0x5000` |
| Texture | byte offset of its `0x5200` record |

These values repeat in different levels. Catalog identifiers therefore use
`KIND:level:VALUE`; existing R2/R3 catalog identifiers remain unchanged.

The retail debug archive exposes no usable Moby, Tie, or Material names. The
reader uses `Moby_XXXX`, `Tie_XXXXXXXX`, and level-scoped shader identities.

## Mobys

Section `0xD100` contains `count` records with stride `0xC0`.

| Header offset | Type | Meaning |
|---:|---|---|
| `0x00` | `vec3 + f32` | bounding sphere |
| `0x16` | `u16` | animation count |
| `0x18` | `u16` | primary bangle count |
| `0x1A` | `u16` | secondary bangle value |
| `0x20` | `u32` | skeleton pointer |
| `0x24` | `u32` | inline animation pointer table |
| `0x28` | `u32` | bangle table |
| `0x34` | `u32` | index stream offset and source flag |
| `0x38` | `u32` | vertex stream offset and source flag |
| `0x3C` | `f32` | mesh position scale |

The high bit of either stream offset selects the matching section in
`vertices.dat` (`0x9100` indices or `0x9000` vertices). Without that bit the
offset addresses `textures.dat` directly.

Bangles are `0x08` bytes: a `u32` mesh-table pointer and `u32` mesh count.
Mesh records have stride `0x40`.

| Mesh offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u32` | index index, in `u16` units |
| `0x04` | `u32` | vertex byte offset |
| `0x08` | `u16` | level-global shader index |
| `0x0A` | `u16` | vertex count |
| `0x0C` | `u8` | bone palette count |
| `0x0D` | `u8` | vertex type (`1` = skinned stride `0x1C`) |
| `0x12` | `u16` | index count |
| `0x20` | `u32` | bone palette pointer |

The static vertex stride is `0x14`; skinned vertices use `0x1C`. Position,
half-float UV, skin palette, and weight decoding reuse the verified semantic
mesh output used by the newer PS3 adapters.

## Inline animations

Moby animation tables contain absolute `main.dat` pointers to the same
`0x3A`-byte control header family used by later engines. TOD has a distinct
playback profile:

- each signed 8-bit frame value is shifted left by two and then added to its
  signed 16-bit track base with 16-bit wrapping;
- frame strides are rounded up to 16 bytes;
- when there are no 8-bit tracks and the stride is the minimum aligned frame
  size, disk records are `(filler, real)` pairs. The decoder starts at the
  second record, doubles the stride, halves the frame count, and halves the
  frame rate.

The second rule decodes the observed `animate_spin` example from 8,001 disk
records into 4,000 real keys at 15 fps. Compressed clips decode directly to
local translation/quaternion/scale samples and become Blender Actions; they
do not fall back to bind pose.

## Ties

Section `0x3400` contains `0x80`-byte Tie headers. A Tie's identity is its
header byte offset.

| Header offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u32` | mesh table |
| `0x0F` | `u8` | mesh count |
| `0x14` | `u32` | vertex-buffer start in `vertices.dat:0x9000` |
| `0x18` | `u32` | authored allocation size |
| `0x20` | `vec3` | per-axis position scale |

Tie mesh records use stride `0x40`, static vertices use stride `0x14`, and
the level-global shader index is at mesh offset `0x28`. Indices address
`vertices.dat:0x9100` in `u16` units.

## Shaders and textures

Section `0x5000` contains `0x80`-byte shaders:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u32` | albedo texture-record offset |
| `0x04` | `u32` | normal texture-record offset |
| `0x08` | `u32` | packed/expensive texture-record offset |
| `0x0C` | `u32` | detail texture-record offset |
| `0x10` | `u8` | surface flags: specular, gloss, normal, and detail |
| `0x11` | `u8` | rendering mode |
| `0x12` | `u8[2]` | unknown material controls |
| `0x20` | `vec4[6]` | raw material parameter vectors |

The adapter maps rendering modes `0`-`2` to opaque, `4`-`5` to masked, and
`6` to blended. It retains the complete flag/control word and all six vectors
as material metadata. Verified normal and flagged packed channels use the
shared Blender material path. Bound detail images are imported as unconnected
Non-Color nodes because their ToD composition is not yet proven; the raw
vectors are not treated as a base-color factor.

Section `0x5200` contains `0x20`-byte texture records:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u32` | pixel offset |
| `0x04` | `u16` | mip count |
| `0x06` | `u16` | format bitfield |
| `0x18` | `u16` | width |
| `0x1A` | `u16` | height |

The format code is `(bitfield >> 8) & 0xF`. Supported observed values cover
R8, R5G6B5, A8R8G8B8, DXT1, DXT3, DXT5, and RG8.

Optional section `0x9800` contains `0x10`-byte streamed references. A record's
texture index is at `0x06` and its `texstream.dat` pixel offset is at `0x00`.
Matching references double both dimensions and provide the high-resolution
base mip; otherwise the reader uses `textures.dat`.

## Foliage

Section `0xA200` contains `0xB0`-byte embedded foliage descriptors. The
verified sprite-card fields are:

| Offset | Type | Meaning |
|---:|---|---|
| `0x08` | `u32` | direct ordinal in the `0x5200` texture table |
| `0x18` | `u32` | branch-vertex offset in `vertices.dat:0x9000` |
| `0x20` | `4 * 0x08` | `(u32 indexIndex, u16 count, u16 runtime)` branch LODs |
| `0x40` | `u32` | sprite-corner offset in `vertices.dat:0x9000` |
| `0x44` | `u32` | sprite-center offset in `vertices.dat:0x9000` |
| `0x48` | `u32` | used sprite LOD count |
| `0x50` | `5 * 0x08` | `(u16 begin, u16 end, f32 threshold)` LOD ranges |

Branch vertices occupy the range from the branch offset to the sprite-corner
offset and use stride `0x14`: half-float `position[4]`, half-float `uv[2]`,
and packed tangent/normal bytes. Branch LOD indices address
`vertices.dat:0x9100` in `u16` units. The first non-empty authored branch LOD
is imported.

Corner records have stride `0x08` and store half-float `size[2]` and `uv[2]`.
Center records also have stride `0x08`: half-float position followed by one
runtime `u16`. Four corners share one center. The first authored LOD is
expanded into ordinary triangles. Branch and sprite positions are converted
with `0.9144`; sprite corners use
`(center + (size.x, size.y, 0)) * 0.9144`.

Unlike ordinary TOD shaders, the descriptor's material selector directly
indexes the texture table. The reader gives it a separate foliage shader
identity with masked alpha and no unrelated normal, packed, or detail slots.
Blender's material remains double-sided.

## Level geometry and placements

TOD has one logical zone per level.

- `main.dat:0x9240` contains `0x80`-byte Tie placements. The row-major matrix
  begins at `0x00`; the Tie header offset is at `0x50`.
- `main.dat:0x9340` contains `0xE0`-byte foliage placements. The row-major
  matrix begins at `0x00`, and the matching `0xA200` descriptor pointer is at
  `0xC4`.
- `main.dat:0x6200` contains `0x80`-byte UFrags. Index and vertex offsets are
  at `0x40`/`0x44`, counts at `0x48`/`0x4A`, shader index at `0x50`, and
  position at `0x60`.
- UFrag vertices use stride `0x18`; signed positions are scaled by
  `0.9144 / 256` and UVs are half floats.
- `gameplay.dat:0x7340` contains `0x48`-byte Moby placements. Position,
  ZYX Euler rotation, uniform scale, and local Moby index are at
  `0x18`, `0x24`, `0x30`, and `0x3C`.

The current adapter does not decode the separate non-IGHW `collision.dat`
layout or unverified secondary-bangle/LOD semantics. Runtime foliage wind,
custom normals, and camera-facing billboard behavior are not reproduced.
