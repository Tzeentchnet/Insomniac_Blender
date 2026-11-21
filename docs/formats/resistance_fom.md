# Resistance: Fall of Man formats

This adapter targets the USA `BCUS98107` v01.00 retail disc. It validates
`TITLE_ID` and `VERSION` from `PARAM.SFO` before extracting data.

No game payloads are distributed. The bundled metadata catalog contains
36,321 level-scoped references across 41 levels: 5,556 Mobys, 6,714 Ties,
184 Shrubs, 103 Foliage assets, and 23,764 Materials.

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
- `ps3levelcoll.dat`
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

## Shrubs and foliage

Section `0xC700` contains `0x30`-byte shrub mesh records. Vertex-buffer byte
offset, index-buffer `u16` offset, index count, and material index are at
`0x00`, `0x04`, `0x08`, and `0x20`. Shrub vertices have stride `0x10`:
four half-float position components, four color bytes, and two half-float UV
components. Position XYZ is converted from yards to meters.

Section `0xC650` contains one open-ended shrub placement container. Its
instance count/offset and cluster count/offset are at `0x00`/`0x04` and
`0x08`/`0x0C`. Each `0x50`-byte cluster contains four local ranges at
`0x40`, an instance base at `0x48`, and a 16-bit shrub mask at `0x4C`.
Set mask bit `i` selects shrub record `15 - i`. The referenced `0x40`-byte
instance records contain world-space position, uniform scale, and two basis
rows; the third row is reconstructed with a cross product.

Section `0xC200` contains `0x120`-byte foliage descriptors. Direct
`0x5300` texture-table ordinal, shared index-buffer offset, branch vertex
offset, and sprite vertex offset are at `0x08`, `0x10`, `0x18`, and `0x40`.
Foliage does not route this value through the ordinary `0x5001` material
table. The importer builds a dedicated double-sided, alpha-masked material
from the selected texture. Four branch LOD ranges begin at `0x20`; the
importer uses the first non-empty range. Branch vertices have stride `0x14`
with half-float position and UV fields.

Foliage sprite LOD metadata begins at `0x44`, the absolute sprite-center
pointer is at `0xB8`, and up to eight sprite ranges begin at `0xC0`.
`0x0C`-byte sprite vertices store half-float corner sizes and UVs. The first
sprite LOD is expanded into ordinary triangle vertices around each authored
center. These are static XY-plane cards: runtime wind and camera-facing
billboard rotation are not reproduced.

Section `0x9700` contains `0xE0`-byte foliage placements. Its matrix starts at
`0x00`, and the pointer to the matching `0xC200` descriptor is at `0xC4`.
Translations and all shrub/foliage vertex positions use the same
`0.9144` yard-to-meter conversion as the other RFoM level geometry.

The `0x25000` gameplay structure points to `GameplayInstances` at `0x20`.
The primary Moby group starts at `GameplayInstances + 0x40`; its records have
stride `0x40`. Position, ZYX Euler rotation, and Moby class ID are at `0x10`,
`0x1C`, and `0x3C`. Position is converted from yards to meters.

The six additional gameplay groups are not imported because their record
semantics are not yet corroborated.

## Level collision

`ps3levelcoll.dat` is a raw big-endian resource rather than an IGHW
container. The selected-level extraction path includes it alongside the
render and gameplay resources. Its geometry header is:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u32` | record-table offset, always `0x80` |
| `0x04` | `u32` | exact record count |
| `0x08` | `u32` | prototype-blob offset |
| `0x0C` | `u32` | prototype-blob length |
| `0x20` | `f32` | positive broad-phase quantization value |
| `0x24` | `f32` | reciprocal broad-phase quantization value |
| `0x30` | `u32` | exact prototype-blob end |
| `0x38` | `u32` | format marker `0x75` |
| `0x3C` | `u32` | later runtime spatial-structure offset |

The 41 USA retail levels use broad-phase values from `8.0` through `24.0`;
their paired value is always the reciprocal. Geometry does not require the
post-prototype spatial structures.

Records have stride `0x80`:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00`, `0x10`, `0x20` | padded `vec3` | stored linear-transform rows |
| `0x30` | `vec4` | translation, with W equal to `1.0` |
| `0x40` | `vec4` | bounding-sphere center and radius |
| `0x50` | `vec3` | AABB minimum |
| `0x60` | `vec3` | AABB maximum |
| `0x70` | packed `u32` | family, source index, and component index |
| `0x74` | `u32` | prototype-relative offset |
| `0x78` | `u16` | collision-group value |
| `0x7A` | `u16` | exact aligned prototype extent |
| `0x7C` | `u32` | zero/reserved |

The packed word uses its top four bits for the family, the next 16 bits for
a source index, and the low 12 bits for a component index:

- family `0`: world collision. The packed word and transform are identity;
- family `2`: Tie/static-prop collision. In level20, every non-sentinel
  source index selects the exact `0x9300` Tie placement whose transformed
  basis and translation appear in the collision record;
- family `3`: collision-only placed geometry. Its transform and geometry are
  complete, but no external render or gameplay owner is proven.

Some retail family-2 and family-3 records use source index `0xFFFF`; the
decoder retains that sentinel rather than inventing an owner. Stored `3x3`
bases are transposed into placement matrices. Prototype positions and record
translations are converted from yards with `0.9144`.

All 90,766 referenced retail prototypes use one marker-1 triangle layout:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u16` | vertex count, at most 512 |
| `0x02` | `u16` | signed-base offset, always `0x50` |
| `0x04` | `u16` | quantization marker, always `1` |
| `0x06` | `u16` | source face count |
| `0x08` | `u16` | aligned face-data offset |
| `0x0A` | `u16` | sentinel `0xFFFF` |
| `0x0C` | `u16` | aligned geometry-data length |
| `0x50` | `i32[3]` | signed base position |
| `0x60` | repeated `u16[3]` | array-of-structures XYZ deltas |

Each coordinate is `(base + delta) * 0.9144 / 1024`. Face words use:

- A: `(word >> 18) & 0x1FF`
- B: `(word >> 9) & 0x1FF`
- C: `word & 0x1FF`
- surface: `word >> 27`

The larger prototype extent includes trailing runtime acceleration data.
Repeated-index, exactly zero-area, and duplicate triangles are removed before
face-domain metadata reaches Blender.

Across all 41 retail files, the decoder validates 446,785 records: 47,191
world, 398,805 Tie, and 789 collision-only placed instances. They reference
90,766 shared meshes with 8,783,640 vertices and 12,837,454 source faces.
After removing 1,695 degenerate and 73,452 duplicate faces, 12,762,307 faces
remain. Observed surface IDs are `0`, `1`, `2`, `4`, `5`, `6`, `7`, `16`,
`17`, and `20`.

Level20 contains 13,291 instances and 2,491 meshes: 995 world meshes, 1,491
Tie meshes, and five collision-only placed meshes. The retained geometry has
252,515 vertices and 363,870 faces.

## Current limitations

- The family-3 `ps3levelcoll.dat` source table has not been correlated with a
  proven gameplay owner.
- Directional lightmap textures in section `0x5500` are not imported.
- Lighting, VFX, cubemaps, and the non-primary gameplay groups are not
  decoded.
- Shrub vertex colors, vegetation wind, foliage custom normals, and
  camera-facing billboard behavior are not reproduced.
- Deterministic numeric names are used until retail names can be recovered
  from a proven source.

The layouts were checked against the USA v01.00 disc and independently
corroborated with the GPL-3.0 InsomniaToolset and ReChimera implementations.
