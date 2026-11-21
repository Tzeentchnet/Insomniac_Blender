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

Known root types are `0x1C` for Model, `0x09` for Material, `0x0A` or
`0x0E` for the observed texture variants, and `0x08` for animation clip
metadata. Root type `0x05` is context-dependent: resources with type-`0x02`
dependencies are animation sets, while direct `.col` files and 12 empty
level-metadata records use the same numeric root type.

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

## RIP metadata bundles

The archive's `.rip` files are `RESOURCEINFOPCK` version 1 metadata bundles.
Their outer `uint32` at `+0x00` points to an inner base, normally `0x10`.
The inner header has this verified layout:

| Inner offset | Type | Meaning |
|---:|---|---|
| `0x00` | 16 bytes | `RESOURCEINFOPCK\0` |
| `0x10` | `uint32` | Version, currently `1` |
| `0x18` | pointer | Resource-table root |

Pointers in the inner structure are relative to the inner base. The root
stores the resource count at `+0x00`, the resource-offset table pointer at
`+0x04`, and the end of the packed resource region at `+0x08`. Resource
offsets are strictly ordered. Consecutive offsets, or the final region end,
bound each complete `NSIRSRC` resource without scanning for magic bytes.

The tested archive contains 18 RIP files with 24,237 packed resources. The
reader bounds the inner header, root, offset table, every resource range, and
each nested `NSIRSRC` before exposing it.

## SPK payload bundles

Sibling `.spk` files are `SPK\0` version 2 stream bundles. Their outer
`uint32` also points to an inner base, normally `0x10`. The inner header
stores the version at `+0x04` and a root pointer at `+0x0C`. The verified
root fields are:

| Root offset | Type | Meaning |
|---:|---|---|
| `0x00` | `uint32` | Resource-entry count |
| `0x0C` | pointer | Resource table |
| `0x1C` | pointer | Name table |

The name table stores its count and record-table pointer. One leading word is
followed by fixed `0x0C`-byte records containing the null-terminated string
length, string pointer, and name hash. Resource records begin eight bytes
after the resource-table pointer and use a `0x54`-byte stride. Each retail
record reports one stream descriptor with a name index, stream class, and
bounded primary and secondary payload pointers and sizes.

Duplicate names are valid within and across SPKs, so lookup preserves every
matching entry rather than assuming global name uniqueness. The tested
archive contains 59 SPKs, 75,746 resource entries, and 40,290 globally
distinct names.

## Animation metadata and raw tracks

Within a RIP, a root type `0x05` resource with one or more type-`0x02`
dependencies is an animation set. Those dependencies enumerate the set's clip
names, but do not identify a model or skeleton owner. Empty type-`0x05`
resources are rejected by the animation-set inspector because the retail
archive uses that shape for level metadata as well. Root type `0x08` is clip
metadata. Its packed-stream descriptor begins at resource offset `0x8C`:

| Descriptor offset | Type | Meaning |
|---:|---|---|
| `0x00` | `float32` | Scale, exactly `1.0` |
| `0x04` | `uint32` | Primary size |
| `0x08` | `uint32` | Primary offset, exactly zero |
| `0x0C` | `uint32` | Reserved, zero |
| `0x10` | `uint32` | Secondary size |
| `0x14` | `uint32` | Secondary offset, equal to primary size |
| `0x18` | `uint32` | Reserved, zero |
| `0x1C` | `uint32` | Reserved, zero |

All 6,458 root-`0x08` resources map uniquely to one sibling SPK entry when
matched by exact resource name, stream class `0x00020000`, primary size, and
secondary size. Name-only matching is insufficient because duplicate SPK
names are valid.

The normal primary animation stream has a `0x30`-byte header:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `uint32` | Track count |
| `0x04` | `uint32` | Frame rate |
| `0x08..0x14` | 4 x `uint32` | Reserved, zero |
| `0x18` | `uint32` | Auxiliary/event count |
| `0x1C` | `uint32` | Auxiliary/event offset |
| `0x20` | `uint32` | Reserved, zero |
| `0x24` | `float32` | Duration in authored frame units |
| `0x28..0x2F` | 2 x `uint32` | `0xDDDDDDDD` fill |

Track records begin at `0x30` and have a `0x10`-byte stride. Each record
stores a bone index, raw kind (`0`, `1`, or `2`), key count, and key-data
offset. Each `0x0C`-byte key stores a `float32` time followed by four signed
16-bit values. The inspector bounds every table and key range, requires
finite nondecreasing times, and verifies that each track begins at zero and
ends at the clip duration. It retains the auxiliary/event location but does
not decode that table; the secondary payload also remains opaque.

The complete retail scan found:

- 6,433 timed clips, all at 48 frames per second;
- 25 zero-rate, zero-track resources, comprising 23 empty 40-byte
  placeholders and two event-only resources;
- 384,720 tracks and 11,423,760 keys;
- 361,733 raw-kind-0 tracks, 22,888 raw-kind-1 tracks, and 99 raw-kind-2
  tracks;
- bone indices through 120 and durations from zero through 3,754 authored
  frames.

Raw kind 0 is rotation-like, kind 1 is strongly associated with positional
motion, and kind 2 is scale-like, but those observations are not sufficient
for transform decoding. Quaternion component order and track-specific scale,
the fourth signed value in kinds 1 and 2, translation reference space,
coordinate conversion, looping, additive behavior, and broad model ownership
remain unproven. `Ellie_CampAlert` reaches bone 120 and exactly fits the
121-bone `CH_BASE_Ellie` skeleton, but names alone do not establish a general
ownership rule.

For that reason the raw inspector does not create shared decoded clips, the
Burning Skies reader does not expose animations, and Blender Actions are not
created.

## Native COL collision

The 12 `levels/*.col` resources are direct NSIRSRC version 7 files with the
context-dependent root type `0x05`. Their serialized data base begins a
32-bit little-endian Havok packfile with:

- magic words `0x57E0E057` and `0x10C0C010`;
- packfile version 9 and layout bytes `04 01 01 01`;
- contents version `hk_2011.3.0-r1`; and
- `__classnames__`, empty `__types__`, and `__data__` sections.

The bounded packfile reader validates all section ranges and local, global,
and virtual pointer-fixup tables. The contents object is one `hkpRigidBody`.
Its global shape fixup resolves to one `hkpBvCompressedMeshShape`; all 12
rigid-body transforms are identity. The decoder rejects other object graphs,
non-identity transforms, populated serialized pointers, invalid fixups, and
unsupported compressed-mesh modes rather than treating them as this family.

The compressed mesh stores:

- one global quantization domain and root acceleration tree;
- fixed `0x60`-byte section records with local domains and tree nodes;
- packed vertices, shared vertices, shared-vertex indices, and four-byte
  primitives;
- per-primitive data runs; and
- a palette of full 32-bit collision-filter values.

Section-local packed vertices use unsigned `11:11:10` XYZ fields. Each section
provides the three offsets and scales that convert those integers into source
coordinates. Global shared vertices use unsigned `21:21:22` XYZ fields over
the mesh domain, and each section selects them through a bounded `uint16`
index range. A primitive contains four local `uint8` indices. Equal third and
fourth indices encode one triangle; otherwise the record is a quad and
expands to `(0, 1, 2)` and `(0, 2, 3)`. The reconstructed triangle total must
equal the shape's primitive-key count.

Primitive-data runs cover each section contiguously. The low eight bits select
the collision-filter palette, while the upper 24 bits preserve per-primitive
user data. Quad expansion duplicates both values for the second triangle.
The decoder retains these fields as raw integers because gameplay meanings
for the five observed filter words and user-data values zero through 14 are
not proven.

The complete European archive contains:

- 12 COL files and identity rigid bodies;
- 3,831 compressed sections;
- 288,164 packed vertices, 77,140 global shared vertices, and 162,027
  section shared-vertex references;
- 450,191 decoded section-local vertices;
- 287,211 primitives, including 263,485 quads, producing 550,696 triangles;
- 45,718 primitive-data runs and 39 per-file palette entries; and
- five filter words: `0x00800000`, `0x00800001`, `0x00800100`,
  `0x00800800`, and `0x00800900`.

This decoder is an evidence and validation layer. The experimental adapter
still has level support disabled, so it does not combine the 12 independent
level meshes into one scene or expose them through Blender. Level selection,
placement context, collection naming, and any gameplay interpretation remain
part of the separate level-support project.

## Unsupported resource families

The experimental reader does not currently import:

- decoded animation transforms or Blender Actions;
- native `.col` collision as a selectable Blender level scene;
- level `.geo` resources;
- cutscenes, effects, lighting, or audio resources.
