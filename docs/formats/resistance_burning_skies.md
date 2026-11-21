# Resistance: Burning Skies formats

Resistance: Burning Skies support is experimental and independent of the
Resistance 2 and Resistance 3 PS3 readers. The tested game uses Nihilistic's
little-endian `NSIRSRC` version 7 containers inside a standard PSARC 1.4
archive named `build.psa`.

The extension requires a decrypted archive beginning with `PSAR`. It cannot
read the PFS-encrypted retail `build.psa`, and it does not distribute game
resources or a derived asset-name catalog.

## Verified archive coverage

The tested European archive contains:

- 821 SMF model resources;
- 6,835 P2T texture resources; and
- 4,020 BMX material resources.

The source-driven catalog hashes normalized archive paths into stable,
synthetic 64-bit identifiers. The identifiers are local metadata keys, not
identifiers stored by the game.

Burning Skies model coordinates are already Z-up. Unlike the Resistance 2
and Resistance 3 PS3 readers, the Blender bridge does not apply a 90-degree
Y-up conversion to these SMFs.

## NSIRSRC container

The verified container fields are:

| Offset | Type | Meaning |
|---|---:|---|
| `0x00` | 8 bytes | `NSIRSRC\0` |
| `0x08` | `uint32` | Version, currently `7` |
| `0x0C` | `uint32` | Serialized data base |
| `0x28` | `uint32` | Resource-name string pointer |
| `0x5C` | `uint32` | Root resource type |
| `0x7C` | `uint32` | Dependency count |
| `0x80` | `uint32` | Dependency-pointer table offset |

Internal virtual pointers resolve to `serialized_data_base + pointer`.
The parser bounds-checks every pointer, table, vertex range, index range, and
texture range before reading it.

Known root types are `0x1C` for Model, `0x09` for Material, and `0x0A` or
`0x0E` for the observed texture variants.

## SMF models

The Model root contains these verified render fields:

| Virtual offset | Type | Meaning |
|---:|---|---|
| `0x00` | `uint32` | Material dependency count |
| `0x04` | pointer | Runtime material-pointer array |
| `0x0C` | `uint32` | Primary bone count; may be smaller than the record count |
| `0x10` | `uint32` | Total `0x70`-byte bone-record count |
| `0x14` | pointer | Bone-record array |
| `0x18` | `uint32` | LOD-record count |
| `0x1C` | pointer | First `0x10`-byte LOD record |

Each LOD record stores its mesh-entry count at `+0x00` and a pointer to its
first `0x90`-byte mesh entry at `+0x04`. The table is stored from lower detail
to full detail; the importer reverses that authored order so the full-detail
group is Blender LOD 0. Each group has a corresponding GPU stream descriptor.
Its leading scale is `0.5` for the lower group and `1.0` for the full-detail
group in `GE_OB_LOD_TEST`.

Each mesh entry begins with runtime type and material pointers at `+0x00` and
`+0x04`. Its geometry payload begins at `+0x08`. Each supported payload leads
to:

1. a GBuffer stream summary;
2. a geometry descriptor;
3. the primary GPU stream; and
4. separate vertex and little-endian `uint16` index ranges.

The geometry descriptor is accepted only when its allocation counts,
triangle/index relationship, summary counts, and vertex bounds agree.

### Supported GBuffer vertices

| Format | Stride | Use |
|---:|---:|---|
| `0x15` | 36 bytes | Rigid GBuffer vertex |
| `0x17` | 44 bytes | Skinned GBuffer vertex |

For both formats, the verified leading attributes are:

| Offset | Type | Meaning |
|---:|---|---|
| 0 | `float32[3]` | Position |
| 12 | `float32` | Texture U |
| 16 | `float16[3]` | Normal |
| 22 | `float16` | Stored negative texture V |
| 24 | `float16[3]` | Tangent |
| 30 | `float16` | Tangent handedness |

The parser restores source V as `1 + stored_v`; the shared Blender mesh bridge
then performs its normal `1 - source_v` image-coordinate flip. The effective
Blender coordinate is therefore `-stored_v`, matching the live axe atlas.

Rigid format `0x15` stores an opaque packed `uint32` at `+32`. Its vertices
have an implicit local bone index zero. Skinned format `0x17` instead ends
with:

| Offset | Type | Meaning |
|---:|---|---|
| 32 | `float16[4]` | Four non-negative skin weights |
| 40 | `uint8[4]` | Four indices local to the mesh palette |

For every GBuffer mesh entry, byte `+0x46` is the palette count and the
corresponding local-to-model `uint8` palette begins at `+0x70`. All 2,330
rigid packets in the corpus have exactly one palette entry, and every entry is
below the root's total bone-record count. The lower word at `+0x44` and the
independent flags byte at `+0x47` are not part of palette indexing. The lower
word retains its separate damage-segment role where trauma metadata verifies
that classification.

Rigid vertices receive weight one for their sole palette bone. For skinned
vertices, weights equal to zero make their paired local byte inactive; 895
inactive slots in the retail corpus retain a nonzero byte and are
canonicalized to model bone zero during decoding.
Active local indices are mapped through the palette. If a palette repeats a
model bone, its weights are coalesced before the four-slot row is normalized
to `float32`.

The complete 821-SMF European corpus contains 240 skinned models, 1,219
skinned packets, and 955,468 skinned vertices. Palette counts span 2 through
20. Every active local index is in range and every mapped value is below the
root's total bone-record count. Source half-float weights span 0 through 1;
their row sums span `0.999176025390625` through `1.0001475811004639`, with all
rows within the enforced `0.001` tolerance. The corpus uses one through four
active influences per vertex. Sixteen palettes repeat one or more model bones,
affecting 477 vertices and requiring the coalescing step above.

Positions are already in bind pose. The importer creates the full model
armature, parents the combined mesh to it, adds an Armature modifier, and
assigns the decoded model-bone groups. The tested archive therefore exposes
10,202 bone records across all 821 SMFs and imports all 3,549 GBuffer packets,
covering 2,142,432 vertices, with normalized ownership.

The verified archive results are:

- 798 SMFs decode through this direct packet graph;
- all 23 SMFs without render packets expose a valid bind skeleton and import
  as armature-only assets; and
- `models/GE_OB_LOD_TEST.smf` decodes into two full-detail packets and two
  optional lower-detail packets.

### Damage-state gib packets

Physics metadata begins at the root pointer at virtual `0x28` and ends before
the material-record pointer at virtual `0x04`. Character trauma records name
authored gib properties such as `prp_traumaBase_small_gib`,
`prp_traumaBase_medium_gib`, and `prp_traumaBase_large_gib`.

Rigid mesh packets store a packed bone range at mesh-entry `+0x44`. A rigid
single-bone packet whose bone is assigned one of the authored trauma gib
properties is a damage permutation rather than part of the default intact
model. These packets use the shared default-off **Damage permutations**
option and import as separate child-Collection objects when enabled.
`models/CH_PHYS_Hybrid.smf`, for example, contains 13 authored gib packets
and nine default intact packets.

### Skeleton-only models

The total skeleton record count is stored at root virtual `0x10`, and root
virtual `0x14` points to `0x70`-byte bone records. The primary count at
`0x0C` can describe only a leading subset and cannot bound either records or
skin palettes. Each record starts with a transposed inverse-world bind matrix
and stores its signed parent index at `+0x60`. Inverting those matrices
reproduces every serialized local translation within floating-point
tolerance. This path imports recoverable bind armatures such as the 37-bone
`CH_PHYS_ADrone`; it does not fabricate surface or collision geometry that is
absent from the resource.

### Material packet assignment

SMFs serialize the packet-to-material map near the end of the object region.
The table has this layout:

1. mesh-reference count;
2. tag `0x00020500`;
3. dependency type `0x04` for BMX materials; and
4. one `(mesh_entry_virtual_offset, material_dependency_index)` pair per
   packet.

Pairs are commonly stored in reverse packet order, so the parser resolves
them by entry offset rather than table order. It requires every mesh entry
exactly once and bounds every material index. This recovers repeated
assignments for models with more packets than materials, as well as assets
whose only rendered packet uses a non-leading dependency. All 798 GBuffer
SMFs in the tested archive now receive a validated packet map.

## BMX materials

BMX resources group `0x18`-byte parameter records by type. Each record stores
name, type, default-value, and current-value pointers. Texture stems are
unique in the tested archive, allowing deterministic archive-wide resolution
of current texture parameters such as:

- `g_DiffuseMapTexture`;
- `g_NormalMapTexture`;
- `g_GlossMapTexture`;
- `g_SpecularMapTexture`;
- `g_EmissiveMapTexture`;
- `g_AlphaMapTexture`; and
- `g_DetailMapTexture`.

The bridge applies `diffuse_tint`, preserves `specular_power` as a fallback
roughness, connects direct RGB tangent normals, inverts the dedicated gloss
map's red channel into Principled roughness, and connects dedicated emissive
maps with `emissive_color` and `emissive_factor`. `Use_Emissive_Texture`
disables stale emissive references. Explicit specular maps remain Non-Color
and drive Principled specular level rather than being misread as gloss.
Detail maps are preserved as unconnected Non-Color nodes because their blend
is not yet proven. Separate alpha maps are connected only when a supported
authored render state identifies blending or masking.

`OpaqueEmissive` and `GunEmissive` materials remain emissive when
`Use_Emissive_Texture` is false. In that mode, `diffuse_color` supplies the
base surface color and `emissive_color` with `emissive_factor` supplies a
constant glow. The disabled serialized texture reference is not connected.
If an emissive material enables a texture but no map can be resolved, the
importer reports a warning instead of substituting a constant glow.

Materials marked `WorldCollision`, `OnlyCastShadows`,
`GE_Shadowcaster_*`, or explicitly non-renderable are auxiliary. A whole-model
sweep finds 154 shadow-only packets and two `WorldCollision` packets across
45 models. They are removed from the visible base mesh and exposed as
default-off wire helpers through the top-level **Collision geometry** option.

Five debug, marker, or environment models reference seven BMX identifiers
that are absent from the tested archive. Their geometry still decodes, but
the reader reports the unavailable dependencies and keeps those models
geometry-only rather than collapsing their validated material-slot indices.

## P2T and GXT textures

P2T primary streams embed standard `GXT\0` containers. Some resources contain
a complete lower-resolution mip chain and a second GXT stream carrying the
highest-resolution mip. The reader selects the largest valid embedded
texture and reads only the serialized top mip.

Supported texture formats are:

| Format | Meaning |
|---:|---|
| `0x85000000` | Vita UBC1 / BC1 |
| `0x86000000` | Vita UBC2 / BC2 |
| `0x87000000` | Vita UBC3 / BC3 |
| `0x0C001000` | 32-bit ABGR/BGRA-style color |
| `0x83000000` | Vita PVRTC-II 4bpp |

Linear, swizzled, and arbitrary-swizzled top-level layouts are handled.
Swizzled pixels or compression blocks use the Vita rectangular Morton order,
with Y in the even Morton bits and X in the odd bits. Reversing that order
produces a coherent but transposed atlas and therefore visibly incorrect UV
mapping. UBC1, UBC2, and UBC3 use the same shared NumPy-vectorized BC decoder
as the PS3 adapters after Vita block layout is resolved.
PVRTC-II decoding implements interpolated endpoints, hard-transition local
endpoints and neighbor palettes, and punch-through alpha. Its output matches
the PowerVR/Vita3K reference decoder byte-for-byte on the verified car normal
map and synthetic hard-transition fixture. The 2,108 PVRTC-II resources
include many direct RGB tangent-space normal maps.

`AP_BASE_AMShip.smf` serializes the all-zero default BMX even though its
runtime object is textured. The archive's otherwise-unreferenced
`E1A653EE_5D4C7E4E_8E0B6D8A_A677550B.bmx` material supplies the matching
`RS_CLR_alienmothership2` atlas. The source adapter applies this verified
asset-specific runtime material override rather than emitting the default
white material.

## Unsupported resource families

The experimental reader does not currently import:

- animation;
- collision geometry not represented by GBuffer packets;
- level `.geo` resources;
- cutscenes, effects, lighting, or audio resources.
