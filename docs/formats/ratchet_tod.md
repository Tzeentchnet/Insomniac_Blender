# Ratchet & Clank Future: Tools of Destruction formats

This adapter targets the USA `BCUS98127` v02.00 retail disc. It validates
`TITLE_ID` and `VERSION` from a bounded `PARAM.SFO` parse before extracting
any level.

No game payloads are distributed. The bundled catalog contains numeric
asset metadata and source names: 15,629 level-scoped references across 20
levels, comprising 3,012 Mobys, 2,048 Ties, 34 Shrubs, 14 Foliage assets, and
10,521 Materials. All 5,108 Moby, Tie, Shrub, and Foliage entries carry their
`DEBUG.DAT` prototype path, and all Materials carry their `0x5D00` shader
name. Shader names are unique within each level; across levels, 3,443
distinct names cover the 10,521 Materials. Regenerate it
with `python tools\build_tod_catalog.py <iso> <output.json>`. Catalog
extraction fetches each level's loose `DEBUG.DAT` through the adapter's
catalog dependency hook.

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
| Shrub | byte offset of its `0xB100` descriptor |
| Foliage | byte offset of its `0xA200` descriptor |
| Shader | index in `main.dat:0x5000` |
| Texture | byte offset of its `0x5200` record |

These values repeat in different levels. Catalog identifiers therefore use
`KIND:level:VALUE`; existing R2/R3 catalog identifiers remain unchanged.

### Retail names (`DEBUG.DAT`)

The packed `DEBUG.PSARC` holds only a table of contents. Retail names are
in a loose big-endian IGHW 1.1 file beside each level's archives,
`PS3_GAME/USRDIR/BUILT/LEVELS/<LEVEL>/DEBUG.DAT`, present for all 20 levels.
Its tables are index-parallel to `main.dat` and `gameplay.dat`:

| Section | Stride | Names | Parallel to |
|---|---:|---|---|
| `0x9480` | `0x10` | Moby prototypes | `main.dat` `0xD100` |
| `0x9280` | `0x10` | Tie prototypes | `main.dat` `0x3400` |
| `0x9580` | `0x10` | Shrub prototypes | `main.dat` `0xB100` |
| `0x9380` | `0x10` | Foliage prototypes | `main.dat` `0xA200` |
| `0x72C0` | `0x18` | Tie instances | `main.dat` `0x9240` |
| `0x7560` | `0x18` | Shrub instances | decoded `0x9540` cluster order |
| `0x74C0` | `0x18` | Foliage instances | `main.dat` `0x9340` |
| `0x6202` | `0x14` | UFrags (no names) | `main.dat` `0x6200` |
| `0x5D00` | `0x30` | shaders; four texture source paths at `+0x20` | `main.dat` `0x5000` |
| `0x73C0` | `0x18` | gameplay Mobys | `gameplay.dat` `0x7340` |
| `0x7720`, `0x7760`, `0x77A0`, `0x77D0` | `0x18` | paths, volumes, Clues, controllers | `0x7700`, `0x7740`, `0x7780`, `0x77C0` |
| `0x7920`, `0x7960`, `0x79A0` | `0x18` | pods, areas, scents | `0x7900`, `0x7940`, `0x7980` |

Prototype records are `{u64 tuid, u32 name, u32 0}`. Their names are source
paths such as `object/architecture/m_ambientbuildings/m_lamppost_low_1`. The
64-bit TUIDs do not occur in `main.dat`, so the join is by record index.
Instance records are `{u8 guid[16], u32 name, u16 zone, u16 0}`. The GUID is
printed as a little-endian (Microsoft) UUID; it equals the GUID comment in
`built/levels/<level>/instances.lua` inside `global_cached.psarc`.

Verification on all 20 levels: every table count equals its parallel
`main.dat` or `gameplay.dat` count. All 8 gameplay families agree with
`instances.lua` in name and GUID for every record. Index pairing was also
checked semantically: instance names match their referenced prototype (for
example `lampPost207` maps to `m_lamppost_low_1`). Shrub instances keep the
prototype of their decoded class on every record of Kerchu City (11,857
records, 15 classes). The rule offered elsewhere, name section = gameplay
section + `0x20`, does not hold for Mobys (`0x7340` to `0x73C0`) or
controllers (`0x77C0` to `0x77D0`).

The reader uses a table only when its count equals the decoded record count.
Otherwise it warns and keeps `Moby_XXXX`, `Tie_XXXXXXXX`, `Shrub_XXXXXXXX`,
`Foliage_XXXXXXXX`, and numeric instance names. Imported placements keep
their numeric `insomniac_instance_tuid` and add `insomniac_source_guid`.
Level meshes record the prototype path as `insomniac_source_name`. Shader
names become `insomniac_shader_lookup_path`.

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

Rendering modes follow ReLunacy's `RenderingMode` names. Counts are across
the 10,521 shaders of all 20 levels:

| Byte | Name | Import | Shaders |
|---:|---|---|---:|
| `0` | Opaque | opaque | 7,544 |
| `1` | Overlay | alpha blend | 239 |
| `2` | Additive | additive (`SRC_ALPHA, ONE`) | 601 |
| `3` | Scunge | alpha blend | 3 |
| `4` | Cutout | alpha clip | 285 |
| `5` | SoftEdge | alpha clip (drawn in the opaque pass) | 915 |
| `6` | Blended | alpha blend | 934 |

Additive materials replace the surface output with Transparent plus Emission
(colour times alpha) and use Blended rendering. Bytes `7` and `8` do not
occur; any other byte warns and imports opaque. It retains the complete flag/control word and all six vectors
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

The bitfield's high byte is the RSX `CELL_GCM_TEXTURE_*` format byte, and the
word at `0x10` is the GCM remap (`0xAAE4`, identity, on every checked
record). The reader uses the low nibble, `(bitfield >> 8) & 0xF`. Observed
values are `1` B8, `3` A4R4G4B4, `4` R5G6B5, `5` A8R8G8B8, `6` to `8`
DXT1/3/5, and `11` G8B8. Code `3` (eight levels, always
`armorpadglass_c.tga`) stores alpha in the high nibble; it previously decoded
with alpha in the low nibble. Code `4` (two `_e` maps on cragmite_ruins and
metropolis) is R5G6B5. ReLunacy's old-engine table, which lists `3` as
R5G6B5 and `4` as A1R5G5B5, does not match these descriptor bytes.

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

## Shrubs

Section `0xB100` contains `0x30`-byte embedded Shrub descriptors. A Shrub's
level-local identity is the descriptor byte offset.

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u32` | vertex byte offset in `vertices.dat:0x9000` |
| `0x04` | `u32` | index offset in `u16` units in `vertices.dat:0x9100` |
| `0x08` | `u32` | triangle-list index count |
| `0x20` | `u16` | level-global shader index |

TOD Shrub vertices have stride `0x0E`, unlike RFoM's `0x10`-byte format:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `i16[3]` | local position |
| `0x06` | `u16` | opaque runtime word |
| `0x08` | `f16[2]` | UV |
| `0x0C` | `u16` | opaque runtime word |

Positions use the same fixed-point yard scale as TOD UFrags:
`raw * 0.9144 / 256`. The USA v02.00 disc contains 34 descriptors across
11 levels, totaling 12,466 vertices and 15,085 triangles.

Section `0x9540` contains one open-ended clustered placement container:

| Header offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u32` | raw instance count |
| `0x04` | `u32` | instance-table byte offset |
| `0x08` | `u32` | cluster count |
| `0x0C` | `u32` | cluster-table byte offset |

Clusters have stride `0x60`. Four `(u8 count, u8 offset)` ranges begin at
`0x40`, a 32-bit Shrub-class mask is at `0x4C`, and a 16-bit first-instance
index is at `0x54`. Set class bits are consumed from high to low and paired
with the populated local ranges. Instances have stride `0x40`: position and
uniform scale are at `0x00`/`0x0C`, two basis rows are at `0x10`/`0x20`, and
the third row is their cross product. Placement positions are converted from
yards with `0.9144`.

Across the 11 Shrub-bearing levels, 10,997 clusters resolve all 103,300 raw
instances exactly once, without missing or duplicate ranges. The reader
rejects out-of-bounds ranges, class-mask references without a matching
descriptor, non-finite transforms, and unreferenced raw instances.

## Level geometry and placements

TOD has one logical zone per level.

- `main.dat:0x9240` contains `0x80`-byte Tie placements. The row-major matrix
  begins at `0x00`; the Tie header offset is at `0x50`.
- `main.dat:0x9340` contains `0xE0`-byte foliage placements. The row-major
  matrix begins at `0x00`, and the matching `0xA200` descriptor pointer is at
  `0xC4`.
- `main.dat:0x9540` contains the clustered Shrub placement container
  documented above.
- `main.dat:0x6200` contains `0x80`-byte UFrags. Index and vertex offsets are
  at `0x40`/`0x44`, counts at `0x48`/`0x4A`, shader index at `0x50`, and
  position at `0x60`.
- UFrag vertices use stride `0x18`; signed positions are scaled by
  `0.9144 / 256` and UVs are half floats.
- `gameplay.dat:0x7340` contains `0x48`-byte Moby placements. Position,
  ZYX Euler rotation, uniform scale, and local Moby index are at
  `0x18`, `0x24`, `0x30`, and `0x3C`.

## Level collision

`collision.dat` is a separate raw big-endian resource, not an IGHW
container. Its verified geometry header is:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u32` | record-table offset, observed `0x80` |
| `0x04` | `u32` | exact record count |
| `0x08` | `u32` | prototype-blob offset |
| `0x0C` | `u32` | prototype-blob length |
| `0x20` | `f32` | broad-phase quantization value, observed `40.0` |
| `0x24` | `f32` | reciprocal quantization value, observed `0.025` |
| `0x30` | `u32` | exact prototype-blob end |
| `0x38` | `u32` | observed `0x47` |
| `0x3C` | `u32` | later runtime spatial-structure offset |

The record table is contiguous:
`recordOffset + recordCount * 0x80 == prototypeOffset`. The runtime
structures after the prototype blob are not required to recover authored
geometry and remain unclassified.

Each record has stride `0x80`:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00`, `0x10`, `0x20` | padded `vec3` | stored linear-transform rows |
| `0x30` | `vec4` | translation, with W equal to `1.0` |
| `0x40` | `vec4` | bounding-sphere center and radius |
| `0x50` | `vec3` | AABB minimum |
| `0x60` | `vec3` | AABB maximum |
| `0x70` | `u8 + u24` | family flags and collision-component index |
| `0x74` | `u32` | prototype-relative offset |
| `0x78` | `u16` | collision-group value |
| `0x7A` | `u16` | exact aligned prototype extent |
| `0x7C` | `u32` | zero/reserved |

The stored `3x3` basis is transposed into the placement matrix. Prototype
positions and translations are authored in yards and are multiplied by
`0.9144`. Flag `0x00` denotes identity-transformed world collision and has a
zero component index. Flag `0x20` denotes Tie collision. Every tested
flag-`0x20` transform matches a `main.dat:0x9240` Tie placement after unit
conversion. The low 24 bits reset across Tie collision families and identify
the component within that family; they are not a Tie asset or placement
index.

All 2,630 referenced prototypes in the tested Metropolis and Kerchu City
files use marker `1` quantized triangles:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u16` | vertex count |
| `0x02` | `u16` | signed-base offset, observed `0x60` |
| `0x04` | `u16` | quantization marker, observed `1` |
| `0x06` | `u16` | source face count |
| `0x08` | `u16` | face-data offset |
| `0x0C` | `u16` | aligned geometry-data length |
| `0x60` | `i32[3]` | signed base position |
| `0x70` | grouped `u16` | eight X, eight Y, and eight Z deltas per group |

Each local coordinate is `(base + delta) * 0.9144 / 1024`. Face words use
the same compact fields as later PS3 collision:

- A: `(word >> 18) & 0x1FF`
- B: `(word >> 9) & 0x1FF`
- C: `word & 0x1FF`
- surface: `word >> 27`

The aligned geometry-data length ends after the face words; the larger
record extent includes trailing runtime acceleration data. Repeated-index,
geometrically zero-area, and duplicate triangles are removed before Blender
face attributes are assigned. All-degenerate prototypes are treated as
explicit no-op source data and reported once per resource.

Metropolis contains 15,247 source records and 1,572 prototypes. After seven
no-op prototypes remove 97 references, it imports 15,150 instances using
1,565 shared meshes. Kerchu City contains 9,913 source records and 1,058
prototypes; four no-op prototypes remove 209 references, leaving 9,704
instances and 1,054 meshes. Together the imported prototypes contain 157,142
vertices and 221,485 retained faces. The decoder discards 1,436 degenerate
and 1,573 duplicate source faces.

The current adapter does not decode unverified secondary-bangle/LOD
semantics or Moby collision. Runtime foliage wind, Shrub wind and packed
vertex metadata, Foliage custom normals, and camera-facing billboard
behavior are not reproduced.
