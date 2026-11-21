# Resistance 2 format notes

Status: verified against the decrypted USA `BCUS98120` v01.00 Resistance 2
PS3 disc image and the public InsomniaToolset/ReChimera research listed below.
The add-on validates both `PARAM.SFO` identity fields before extraction.

The USA disc contains 27 levels. Its bundled metadata catalog has 7,588
unique assets: 923 Mobys, 3,207 Ties, 77 Shrubs, 26 Foliage assets, and 3,355
Materials. Source names are available for the 4,207 Mobys, Ties, and Shrubs.

## Disc and archive layout

Levels are split into:

```text
PS3_GAME/USRDIR/packed/levels/<level>/level_cached.psarc
PS3_GAME/USRDIR/packed/levels/<level>/level_uncached.psarc
```

The global pair lives under `packed/game/`. A level PSARC manifest typically
maps to `built/levels/<level>/...`.

### PSARC

All integers in the PS3 PSARC header and TOC are big-endian.

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `char[4]` | `PSAR` |
| `0x04` | `u16` | major version |
| `0x06` | `u16` | minor version |
| `0x08` | `char[4]` | compression, R2 uses `zlib` |
| `0x0C` | `u32` | total TOC size |
| `0x10` | `u32` | TOC entry size, normally 30 |
| `0x14` | `u32` | entry count |
| `0x18` | `u32` | uncompressed block size, normally 65536 |
| `0x1C` | `u32` | flags |

A 30-byte entry contains MD5, starting block index, 40-bit uncompressed size,
and 40-bit physical file offset. Entry zero is the filename manifest.
Following entries are resolved by hashing manifest names. Stored block length
zero means an uncompressed full block.

## IGHW

R2 resources use big-endian `IGHW` version 1.1 containers.

```text
0x00  "IGHW"
0x04  u16 major = 1
0x06  u16 minor = 1
0x08  u32 section count
0x20  Section[section count]
```

Each section is:

```text
u32 id
u32 offset
u32 count  (bit 0x10000000 is a flag and is masked)
u32 length
```

Offsets inside an asset slice are relative to that slice.

## `assetlookup.dat`

Resource-pointer sections contain 16-byte entries:

```text
u64 TUID
u32 byte offset in companion .dat
u32 byte length
```

| Section | Kind | Companion |
|---:|---|---|
| `0x1D100` | shader | `shaders.dat` |
| `0x1D180` | texture resource | `textures.dat` |
| `0x1D1C0` | high mip payload | `highmips.dat` |
| `0x1D200` | cubemap | `cubemaps.dat` |
| `0x1D300` | Tie | `ties.dat` |
| `0x1D400` | foliage | `foliages.dat` |
| `0x1D500` | shrub | `shrubs.dat` |
| `0x1D600` | Moby | `mobys.dat` |
| `0x1D700` | animset | `animsets.dat` |
| `0x1D800` | cinematic | `cinematics.dat` |
| `0x1DA00` | zone | `zones.dat` |
| `0x1DB00` | lighting | `lighting.dat` |

Texture metadata is a parallel `0x1D140` section with four bytes per entry:
format, mip count, log2 width, log2 height.

## Moby

Important sections:

| Section | Meaning |
|---:|---|
| `0xD100` | MobyV2 header |
| `0xD200` | null-terminated source entity path |
| `0xD300` | skeleton |
| `0xD900` | primitive collision prototype |
| `0xDA00` | triangle collision prototype |
| `0x5600` | shader TUID table |
| `0xE100` | big-endian `u16` index buffer |
| `0xE200` | vertex buffer |

Section `0xD200` contains paths such as
`entities/character/gameheads/multiplayer_heads/mp_scientist_head/mp_scientist_head.entity.irb`.
The import catalog reads this section directly without decoding the Moby's
geometry, skeleton, or animations.

MobyV2 fields used:

| Header offset | Type | Meaning |
|---:|---|---|
| `0x00` | `f32[4]` | bounding sphere |
| `0x10` | `i16` | bind-pose inverse offset |
| `0x18` | `u16` | segment count |
| `0x24` | `u32` | segment table pointer |
| `0x30` | `u32` | primitive collision prototype pointer |
| `0x34` | `u32` | triangle collision prototype pointer |
| `0x50` | `u64` | animset TUID |
| `0x70` | `f32` | quantized position scale |
| `0x90` | `u32` | bangle count |
| `0x98` | `u64` | default segment draw mask |
| `0xB0` | `u64` | Moby TUID |

A segment is `(u32 primitivePointer, u32 primitiveCount)`. A primitive is
`0x40` bytes. Index offset is in `u16` elements; vertex offset is in bytes.
The index count at `+0x10` is `u32`, not `u16`.

The default segment draw mask is the Moby class default, not the multiplayer
outfitter's final selection. Multiplayer heads can set several mutually
exclusive helmet bits at once because runtime customization replaces that
mask before drawing.

When `bangle count` is nonzero, segment zero is imported as the base model and
every nonzero render segment is preserved under its own hidden **Bangle NN**
Collection. This intentionally exposes render segments rather than guessing
the undocumented logical grouping used by complex outfitter Mobys. For Mobys
without bangle metadata, clear default-draw bits continue to identify optional
damage alternatives; a zero mask remains unspecified.

Standalone Moby imports keep those Collections independently selectable and
show mask-enabled parts only when **Show source-default character parts** is
enabled. Level gameplay placements instead combine mask-enabled segments into
the class's shared placed mesh automatically. This restores authored intact
defaults for vehicles, enemies, and destructible props while excluding
disabled alternatives and known damage permutations. Runtime script or
outfitter changes to the class mask are not decoded.

### Collision

Moby collision prototypes are independent from render segments.

Primitive collision begins with `0x20` bytes of bounds. Its header at `+0x80`
contains a `u16` alignment value of `16` and a `u16` primitive count. Records
start at `+0x90`, are `0x50` bytes each, and contain:

```text
u8 kind
u8 unknown[2]
u8 boneIndex             // 0xFF means no joint
u8 unknown[12]
f32 payload[4][4]
```

Observed primitive kinds are:

- `4`, oriented box: payload rows 0-2 contain axis XYZ and half-extent W;
  row 3 XYZ is the center;
- `5`, sphere: row 0 is center XYZ and radius W;
- `6`, capsule: row 0 is endpoint A XYZ and radius W; row 1 XYZ is endpoint B.

Triangle collision also starts with `0x20` bytes of bounds. Its header at
prototype `+0x80` contains:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u16` | vertex count, at most 512 |
| `0x02` | `u16` | group-width marker, `16` |
| `0x04` | `u16` | quantization marker, `256` or `257` |
| `0x06` | `u16` | face count |
| `0x08` | `u16` | vertex section length |
| `0x0C` | `u16` | aligned total data length |

The signed `i32[3]` base position is at prototype `+0x90`. Quantized vertices
begin at `+0xA0` in groups of eight: eight X `u16` values, then eight Y, then
eight Z, for 48 bytes per group. Position decoding is:

```text
position = (base + quantizedOffset) / 1024
```

Marker `257` is independently verified on the R2 Chimeran Shuttle Moby
`0xFC9676A49C0A7C0E`. It uses the same physical vertex grouping, position
conversion, and face packing as marker `256`, but its two length fields are
measured from the base-position block at `+0x90` instead of the collision
header at `+0x80`.

Both observed length conventions resolve to the same physical face offset:

```text
header-relative  = 0x20 + ceil(vertexCount / 8) * 0x30  // from +0x80
payload-relative = 0x10 + ceil(vertexCount / 8) * 0x30  // from +0x90
```

Face words immediately follow the vertex groups. Each big-endian `u32` stores
face type in bits 27-31 and three nine-bit vertex indices in bits 18-26, 9-17,
and 0-8.

## Regional level collision

Each regional `collision.dat` is a big-endian IGHW 1.1 container with
sections `0x2900`, `0x8200`, and `0x8300`. The verified geometry and placement
data are in `0x2900`; `0x8200` and `0x8300` remain unclassified spatial or
runtime structures and are not required by the importer.

The `0x2900` header fields used by the reader are:

| Offset | Type | Meaning |
|---:|---|---|
| `+0x00` | `u32` | `0x80`-aligned byte length of the Tie TUID table |
| `+0x04` | `u32` | collision record count plus one |
| `+0x08` | `u32` | section-relative prototype-blob offset |
| `+0x0C` | `u32` | prototype-blob byte length |
| `+0x80` | `u64[]` | big-endian Tie TUID table |

The `0x80`-byte collision records begin immediately after the TUID table. The
prototype blob must begin immediately after the complete record table.

| Record offset | Type | Meaning |
|---:|---|---|
| `+0x00` | `f32[3]` | broad-phase AABB minimum |
| `+0x10` | `f32[3]` | broad-phase AABB maximum |
| `+0x20` | `u32` | prototype-relative byte offset |
| `+0x24` | `u16` | prototype extent minus `0x10` |
| `+0x26` | `u16` | collision grouping value, preserved as metadata |
| `+0x28` | `u32` | high-byte flags and low-24-bit Tie TUID-table index |
| `+0x30` | `f32[3][4]` | transposed linear transform with zero padding |
| `+0x60` | `f32[3]` | translation |
| `+0x70` | `f32[4]` | broad-phase bounding sphere |

Flag byte `0x00` identifies world-space terrain records. Their low 24 bits
and serialized transform are zero, and the decoded instance uses identity.
Flag byte `0x20` identifies Tie collision. Its low 24 bits index the TUID
table, and transposing the stored `3x3` basis exactly reproduces the matching
Tie placement transform.

Triangle prototypes place the compact collision header at prototype `+0x00`
rather than Moby prototype `+0x80`. Retail level files use marker `256`.
Vertices retain the group-of-eight unsigned-delta layout and `/ 1024`
conversion documented above; face words retain the same type and three
nine-bit indices. The record extent also covers a trailing acceleration
payload, so it can be larger than the aligned vertex/face data length.

Primitive prototypes use this header:

| Offset | Type | Meaning |
|---:|---|---|
| `+0x00` | `u16` | data offset marker, `0x20` |
| `+0x02` | `u16` | total primitive count |
| `+0x10` | `u8[4]` | box, sphere, capsule, and cylinder counts |

Each primitive family is stored in groups of four lanes. Four `0x10`-byte
descriptors are followed by structure-of-arrays vectors. Active descriptors
repeat the family kind (`4`, `5`, `6`, or `8`), store the face surface value
at byte `+0x01`, and use `0xFF` at `+0x03`. A box batch is `0x130` bytes and
stores center XYZ, a `3x3` basis, and half extents XYZ. A sphere batch is
`0x80` bytes and stores center XYZ plus radius. Capsule and cylinder batches
are `0xB0` bytes and store two endpoints plus radius.

The importer emits one reusable mesh per prototype and one instance per
non-empty record. Degenerate and duplicate face words are no-op source data;
they are removed before Blender mesh validation, with original and discarded
counts retained as mesh metadata. Prototypes containing no valid faces are
skipped with an explicit per-resource warning.

### Vertices

Static stride `0x14`:

```text
i16 position[3]
i16 purpose
f16 uv[2]
u32 packed normal
u32 packed tangent
```

Skinned stride `0x1C`:

```text
i16 position[3]
i16 unknown
u8 localBone[4]
u8 weight[4]
f16 uv[2]
u32 packed normal
u32 packed tangent
```

Primitive-local bone indices map through the primitive's `u16` bone palette.
Slots with zero weight are normalized to joint zero for Blender/glTF
compatibility.

## Skeleton

The `0xD300` header supplies bone count, root, bone table, world-bind (`tms0`)
and inverse-world-bind (`tms1`) matrices, plus animation quantization shifts.

Matrices are serialized column-major with translation in flat elements
12–14. The reader converts them to conventional 4x4 NumPy matrices.

Some roots use `parent == own index`; treat this as no parent. Local bind is:

```text
local[i] = inverseWorld[parent] @ world[i]
```

Bone flag bit `0x01` disables parent-scale inheritance. Blender imports map
that flag to the bone's `NONE` scale-inheritance mode. Because Blender rest
bones cannot retain every scale component from the source matrices, animated
poses are retargeted as `sourcePose @ inverseWorldBind @ blenderRest`; this
keeps a bind-equivalent frame deformation-free. Each retargeted pose is
converted to a local basis against the desired parent pose before keyframing,
so Blender evaluation order cannot apply an ancestor transform twice.

The unsigned child index uses zero as its terminal/no-child sentinel. Blender
therefore gives those terminal bones a short display tail instead of extending
them back to bone zero.

The two shift fields were not byte-swapped by the original serializer. Values
outside 0–15 are byte-swapped; remaining outliers use the x86 five-bit shift
mask observed by public implementations.

## Animation

Each animset is an IGHW slice selected by Moby animset TUID. Section `0xF000`
can occur repeatedly or be an array (`count > 1`).

The header points to:

- reference quaternions;
- static reference values and packed masks;
- 16-bit per-frame track masks;
- 8-bit delta track masks and bases;
- blend masks;
- per-frame track bytes.

Packed track mask:

```text
bits  0..1  expected marker
bits  2..3  component
bits  4..5  kind: rotation, scale, position
bits 6..15  bone index
```

Non-packed clips round frame stride up to `0x80`. Packed clips retain their
stored stride. Translation and scale use:

```text
1 / (0x8000 >> skeletonShift)
```

Flag `0x01` is looping, `0x02` additive, and `0x04` packed frames. Public
research also identifies `0x0200` position/scale delta behavior in related V2
data; the reader preserves that path for R2 overlays.

For additive clips, the header bone count is the number of bones in the layer,
not the control-buffer array length. Reference rotations, subsequent control
offsets, and blend masks use the full skeleton bone count. A zero blend mask
omits the static reference rotation for an untracked bone, leaving the
underlying pose unchanged.

### InsomniaToolset behavior comparison

The animation path was compared with
[InsomniaToolset revision `529729a`](https://github.com/PredatorCZ/InsomniaToolset/tree/529729af2db172607a39795277cb7406dfcbf139)
as a behavioral reference. Its GPL implementation was not copied, and behavior
was not accepted without checking the R2 bytes and Blender evaluation.

The local evidence set contained 101 unique animsets and 2,585 clips available
from the `axbridge_coop`, `badlands`, `bayou`, and `lobby` caches. It is not a
whole-disc survey.

| Area | Comparison and evidence | Classification |
|---|---|---|
| Header and control layout | Both paths use the stored packed stride, round non-packed strides to `0x80`, and treat additive header bone counts as subset counts while sizing control arrays from the full skeleton. | Match |
| Track-mask marker | InsomniaToolset asserts that bits 0..1 equal `2`. All 301,565 sampled masks use that marker; this importer now rejects invalid markers, kinds, components, and bone indices with a format error. | Match, with explicit validation |
| Rotation reconstruction | Both seed partial quaternion tracks from the reference quaternion and normalize after replacing animated components. No negative adjacent quaternion dot products occurred in the 2,585 sampled clips, so no additional hemisphere rewrite is currently justified. | Match |
| Position/scale component fallback | InsomniaToolset's GLTF decode path does not mark 16-bit position/scale components as present and initializes 8-bit-only tracks without their static reference components. In the sample that would lose 76 dynamic 16-bit position components, 909 static position components, and 4 static scale components; every affected value differs from bind. The importer tracks component presence for both widths and preserves static components. | Intentional difference; raw data favors this importer |
| Additive composition | InsomniaToolset defines bind rotation multiplication, translation addition, and scale multiplication, but neither inspected GLTF loader calls that helper. The Resistance 2 profile retains the same non-premultiplied bind/delta math for standalone Action playback. Resistance 3 uses a separate decoded profile and matched base-layer bake rather than changing validated R2 clips. | Toolset export gap; game-specific importer adaptation |
| Blend masks | Both treat zero as excluding an untracked static additive rotation. All 818 sampled additive clips use only `0` and `255`; the nonzero count equals the additive header subset count in every case, and no tracked bone has a zero mask. | Match |
| Scale inheritance | InsomniaToolset splits scale from transform nodes for GLTF and propagates animated scale and scaled translations through inheriting descendants until a `DONT_INHERIT_SCALE` bone. Blender represents the flag directly. The importer now also removes ancestor scale while accumulating the source target matrix for such bones; 675 sampled clips exercise this boundary. | Equivalent Blender adaptation |
| Timing | InsomniaToolset writes a final sample time of `(numFrames - 1) / frameRate`. Blender keys use the same duration mapped onto the scene FPS, and imported curves now use linear rather than Blender's default Bezier interpolation. | Match with Blender time mapping |
| Root motion | InsomniaToolset reads the pointer at header offset `0x1C` and attempts to compose those frames onto skeleton roots. This importer currently skips it. None of the 2,585 sampled R2 clips has a nonzero pointer, and the Toolset's separate root-motion paths are internally inconsistent, so a future implementation must be validated independently against an exercising asset. | Unexercised gap |
| Linear speed | The sampled headers contain 1,001 nonzero linear-speed values. Neither implementation applies that metadata to exported motion; this importer parses it but does not expose it on the Action. | Metadata gap |
| Duplicate names | Six sampled animsets contain duplicate names. Some duplicates have different frame data or pair a full-body clip with an additive clip. One InsomniaToolset loader drops later duplicates while its other loader retains them; Blender retains every clip and applies its normal numeric Action suffix. | Intentional preservation |
| Flag `0x0200` | InsomniaToolset does not interpret it. The importer's position/scale delta branch comes from related V2 research, but no sampled R2 clip sets the flag. | Unconfirmed R2 hypothesis |

Eight sampled facial clips also set combinations of `0x0010`, `0x0020`, and
`0x0400`. Both implementations currently decode their ordinary transform
tracks without assigning additional meaning to those bits.

## Tie

Important sections:

| Section | Meaning |
|---:|---|
| `0x3000` | vertices |
| `0x3200` | indices |
| `0x3400` | Tie header |
| `0x3410` | null-terminated source entity path |
| `0x5600` | shader TUIDs |

Tie vertices use stride `0x14`; positions are `i16[3]` multiplied by the
header's per-axis scale. Ties have no skeleton. Section `0x3410` contains the
Tie's source path, for example
`entities/object/architecture/nb_lobby_skyline_1/nb_lobby_skyline_1.entity.irb`.
This is distinct from the per-instance names in zone section `0x72C0`.

## Shaders and textures

### Whole-disc shader corpus

`tools/analyze_r2_shaders.py` was run over all 27 cached level archives in the
tested disc image. The corpus contains 17,497 shader references and 3,355
unique shader TUIDs. Repeated TUIDs had identical payload hashes; no
conflicting duplicate was found.

The shader section layout is unusually consistent:

| Section | Presence | Shape |
|---:|---:|---|
| `0x5000` | 3,355 | one 128-byte material record |
| `0x5D00` | 3,355 | one 48-byte resource-reference record |
| `0x11300` | 3,355 | one variable-length string block |
| `0x5200` | 3,354 | one to four 32-byte texture descriptors |
| `0x5A00` | 3,354 | one to four 8-byte texture-resource records |

One shader has no textures. Descriptor counts among the remaining shaders are
1,899 with three maps, 885 with four, 300 with one, and 270 with two. Slot
presence is:

| Slot | Shaders |
|---|---:|
| diffuse | 3,354 |
| tangent normal | 2,864 |
| packed material | 2,923 |
| detail | 936 |

Descriptor formats reinforce the slot interpretation:

| Slot | Most common descriptor formats |
|---|---|
| diffuse | 2,487 BC1; 850 BC3; 18 uncompressed |
| tangent normal | 2,829 BC3; 35 RG8 |
| packed material | 2,793 BC1; 118 BC3; 12 uncompressed |
| detail | 824 RGBA8; 83 BC3; 28 BC1; 1 RGBA4 |

The most common material map-index patterns are `(0,1,2,-1)` (1,856),
`(0,1,2,3)` (885), `(0,-1,-1,-1)` (300), `(0,-1,1,-1)` (162), and
`(0,1,-1,-1)` (100). Every checked descriptor/resource hash matches the
texture ID in `0x5D00`.

### Shader resource references

Shader section `0x5D00` is a 48-byte `MaterialResourceNameLookup`:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u64` | shader hash/TUID |
| `0x08` | `u32` | shader lookup-path pointer |
| `0x0C` | `u32` | null/reserved |
| `0x10` | `u32[4]` | diffuse, normal, packed expensive, detail texture IDs |
| `0x20` | `u32[4]` | corresponding texture lookup-path pointers |

Most third-slot paths end in `_e`. Here `_e` identifies the engine's packed
"expensive" map; it is not sufficient evidence that the whole RGB texture is
emissive.

Some shipped shaders retain a stale texture ID while another shader in the
same level references an available payload through the identical authored
lookup path. The Attackdrone body is one example. If direct, global, and
sibling-ID lookup all fail, the reader indexes these paths and accepts a
path-equivalent payload only when every available candidate decodes
identically. Different payloads sharing a path remain an ambiguity error.

Section `0x5000` is the 128-byte material parameter record:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `i32[4]` | diffuse, normal, packed, and detail map indices |
| `0x10` | `u32` | control word containing flags/blend/unknown bytes |
| `0x14` | `byte[12]` | alignment/reserved |
| `0x20` | `vec4[6]` | material parameter vectors |

The parameter-vector semantics are not yet named reliably. The reader
preserves the control word and all six vectors, and Blender stores them as
custom properties.

### Material control bytes

Because R2 stores the word big-endian, its serialized bytes are:

| Byte | Meaning |
|---:|---|
| `0` | feature flags |
| `1` | blend mode |
| `2` | shader-family discriminator, exact name unknown |
| `3` | shader-variant byte, exact bits unknown |

Verified feature bits are:

| Bit | Public name | Whole-disc correlation |
|---:|---|---|
| `0x02` | use specular | never set; packed slot present in 2,923 shaders |
| `0x04` | use gloss | flag+slot 2,763; neither 432; slot-only 160 |
| `0x08` | use normal | flag+slot 2,850; neither 489; 16 mismatches |
| `0x10` | use detail | flag+slot 903; neither 2,419; slot-only 33 |

Bit `0x01` remains unnamed. The most common complete flag bytes are `0x0C`
(1,580), `0x1C` (629), `0x00` (364), `0x0D` (228), `0x1D` (195), `0x08`
(157), and `0x04` (113).

Byte 2 is zero in 3,322 shaders. Its 19 value-14 records are foliage
materials, while its 14 value-15 records are specialized BRDF eye, mouth,
skin, and character materials. Foliage reuses parts of the six-vector block:
several value-14 records contain NaNs where standard materials store color.
The importer therefore does not interpret an unsafe foliage-family vector as
a color multiplier.

Blend modes in the corpus are:

| Mode | Count | Import behavior |
|---:|---:|---|
| `0` | 2,711 | opaque; albedo alpha is ignored |
| `4` | 190 | alpha mask at threshold `0.5` |
| `1` | 254 | blended alpha |
| `2` | 44 | blended alpha |
| `3` | 2 | blended alpha |
| `5` | 25 | blended alpha |
| `6` | 129 | blended alpha |

This follows the public tool's rule that mode 0 is opaque, mode 4 is masked,
and every other nonzero value is blended, with one verified R2 exception:
shader `0x3B7E88419D2AC85A`,
`legacy/ch_hex_glass_glowy_c_01/ch_hex_glass_glowy_c_01`, stores mode 0 but is
authored as emissive glass. Its BC3 albedo alpha spans 121–255, with a mean
near 153, and its related non-glowing `ch_hex_glass_c_01` shader
`0x972C05119D2AC6BF` uses blend mode 6 with albedo alpha spanning 0–255.
Blender therefore treats the glowy shader as a narrowly enumerated blended
alpha override rather than weakening the mode-0 rule for unrelated shaders.

### Material parameter vectors

Vector 0 is strongly supported as a base-color multiplier for standard
materials: nearly all shaders use `(1,1,1,1)`, while non-default colors
correlate with shields, energy, blood, metals, and character materials.
Enhanced imports multiply albedo by vector 0 only when all components are
finite and within `[0,1]`; otherwise they report the record and use neutral
white.

Other correlations are not strong enough to drive nodes:

- vector 1 Z is mostly integer-like, commonly 32 or 16, and may be a classic
  specular exponent;
- vector 4 Z strongly correlates with detail-map scale/tiling;
- vectors 2 and 3 likely contain lighting, specular, or emission constants;
- vector layouts vary for foliage and specialized BRDF families.

All six raw vectors remain available as Blender custom properties.

### Source-authored default pink material

Shader `0x19ECCFD6FFE54E65` resolves normally to:

```text
defaults/default_pink_shader/default_pink_shader
defaults/default_pink_shader/default_pink_shader_c
```

Its diffuse dependency `0x6078A29C` is a present 128x128 BC1 texture with an
opaque patterned magenta image. It is not Blender's missing-image color and
not the importer's deterministic missing-resource fallback. R2 Tie V2
primitives explicitly assign this shader in several shipped assets, including
`ml_chimera_boiler_door_02`.

No source record identifies an intended replacement. The importer therefore
preserves the authored pixels instead of guessing another slot, and labels
the Blender material with:

```text
insomniac_source_placeholder = true
insomniac_source_placeholder_kind = "DEFAULT_PINK"
```

### Packed material channels and emission

Public InsomniaToolset extraction for the related material layout interprets
the third texture as:

- red, inverted: gloss;
- green: specular;
- blue: emission mask.

It also suppresses emission when every blue texel is zero. Real R2 data
supports that rule. In the tested `chicxulub_multiplayer` archive, 315 shaders
reference a packed third texture: 88 decoded maps contain nonzero blue texels,
219 have an all-zero blue channel, and 8 dependencies were unavailable in the
extracted level set. Obvious lights, console glows, and glowing weapon parts
consistently occur in the nonzero group.

The two previously questioned shaders both reference packed texture
`0x691C68AC`, whose blue channel is entirely zero:

- `0xA0BF95900B24D210`
- `0x749B20B80B99B950`

They must not emit. Their packed map still carries red/green gloss/specular
data.

Blender imports a packed map as Non-Color data when its blue channel has
content or another verified flagged channel is needed. A Separate Color node
multiplies blue by the corresponding diffuse color and connects the result to
Principled BSDF Emission Color with a conservative strength of `1.0`.

The Chimeran Shuttle provides direct color-source evidence. Its top and bottom
shaders have the same material vectors except for detail scale, while diffuse
pixels selected by their packed-blue masks contain the distinct orange, cyan,
and smaller orange accents seen in game. The packed texture therefore supplies
coverage/intensity and the diffuse texture supplies the authored hue.

The default material mode converts red gloss to roughness as
`roughness = 1 - red` when flag `0x04` is set. Green would map to specular
only when flag `0x02` is set, but that flag is absent from every shader in the
whole-disc corpus, so no R2 material currently connects green. Authored
additional emission tint and strength have not been identified conclusively
in the six parameter vectors, so the importer does not guess them.

### Tangent-space normal maps

Most R2 normals are BC3. Their tangent X component is stored in alpha and Y
is stored in inverted green:

```text
x = alpha * 2 - 1
y = 1 - green * 2
z = sqrt(max(0, 1 - x*x - y*y))
```

The default material mode reconstructs an RGB tangent-space normal image with
NumPy, marks it as Non-Color data, and connects it through a Normal Map node.
Clamping the Z radicand is required because compression and effect maps can
produce XY vectors slightly outside the unit circle.

The corpus also contains 35 shader records using 31 distinct two-channel
normal textures. Cross-checking the two format tables proved that local
metadata code `0x0B` pairs with descriptor code `0x8B` (RG8), not linear
BC1. For RG8 normals, X comes from red and Y from inverted green because no
alpha channel exists. A real 512x512 lobby RG8 normal is centered near
`(128,128)` and produces the expected XY unit-vector distribution with that
interpretation.

### Detail maps

The detail flag and slot agree for 903 shaders, while another 33 have a detail
slot without the flag. In a decoded 24-map Chicxulub sample, only one detail
texture was grayscale. Most were multi-channel data centered near 128,
consistent with detail normals or other signed/packed perturbations rather
than a simple color overlay. Vector 4 Z likely controls scale, but the exact
composition and per-family differences remain unverified. Bound detail images
are created as unconnected Non-Color nodes, preserving the source data in the
Blender material without assigning speculative rendering semantics.

R2 texture format bytes:

| Byte | Format |
|---:|---|
| `0x01` / `0x81` | R8 |
| `0x02` / `0x82` | RGB5A1 |
| `0x03` / `0x83` | RGBA4 |
| `0x04` / `0x84` | R5G6B5 |
| `0x05` / `0x85` | A8R8G8B8 |
| `0x06` / `0x86` | DXT1 |
| `0x07` / `0x87` | DXT3 |
| `0x08` / `0x88` | DXT5 |
| `0x0B` / `0x8B` | RG8 |
| `0xA5` | linear A8R8G8B8 |
| `0xA6` | linear BC1 |
| `0xA7` | linear DXT3 |
| `0xA8` | linear DXT5 |

Uncompressed images use the engine's Morton ordering and are unswizzled.

The global cached archive uses a separate per-TUID layout:

```text
built/tuids/<prefix>/<full-tuid>/header.dat
built/tuids/<prefix>/<full-tuid>/texel.dat
```

`header.dat` section `0x5200` contains the 32-byte NV4097 texture descriptor.
On the tested R2 USA disc, `texel.dat` section `0x5300` contains the texel
buffer and `0x5A00` contains the low-32 resource hash and total size. Some
public notes identify the buffer as `0x5400`; that ID is accepted as a
compatibility fallback, but `0x5300` is the observed R2 layout. Texture
descriptors and `0x5A00` resource records are parallel arrays. Each resource
record's first `u32` is the texture ID referenced by shaders, so both
single-texture folders and large texture bundles can be indexed without
guessing from the folder TUID.

R2 also contains cross-level references. For example,
`chicxulub_multiplayer` references diffuse texture `0x1585EA44` from
Chicago/Iceland2 and `0x6782D663` from Bay Area/Scotia Co-op. The reader
indexes provenance-approved or manually co-located sibling level lookups
lazily and reads the matching `highmips.dat` payload. It does not assume that
equal low-32 IDs are equal: duplicate candidates must have the same decoded
format, dimensions, and pixels before use. Distinct full TUID prefixes are
accepted only when those payload checks agree.

R2 import catalogs add a compact `texture_index` keyed by the shader's
low-32 texture ID. Records group the full TUID, descriptor dimensions and
format, high-mip length, normalized authored paths, owner levels, and SHA-256
of the compressed mip-zero bytes. The index therefore contains metadata and
fingerprints but no copyrighted texel payload.

ISO preparation prefers an exact indexed ID over an unverified same-path
alias. A local alias is retained without extraction only when its indexed
descriptor and SHA-256 match the exact payload. Byte-identical duplicate
owners are collapsed with a greedy set cover; conflicting descriptors or
fingerprints fail explicitly. The chosen sibling pairs are added to the
selected cache's resource list and dependency provenance. On the USA disc the
index contains 42,658 level-owner records, 7,440 unique IDs, and 7,506 paths.

## Zones and gameplay placement

Zones contain:

- `0x6200` UFrag terrain descriptors;
- `0x6000` / `0x6100` UFrag vertex/index buffers;
- `0x7200` Tie TUID table;
- `0x7240` Tie instance transforms;
- `0x72C0` instance TUID/name metadata, which names placements rather than
  Tie asset classes;
- `0x7400` foliage TUID lookup records;
- `0x7440` `0xB0`-byte foliage instances. The row-major transform begins at
  `0x00`, and the foliage lookup index is at `0x94`;
- `0x7500` Shrub TUID lookup records;
- `0x7540` `0x40`-byte Shrub instances.

UFrag world positions simplify to `(rawVertex + fragmentPosition) / 256` in
the same game units used by placements.

Foliage assets come from the `0x1D400` pointers into `foliages.dat`. Each
standalone IGHW contains:

- `0xA000`: one combined sprite buffer;
- `0xA200`: one `0xC0`-byte descriptor;
- `0x5600`: the foliage shader TUID lookup.

The descriptor stores the used LOD count at `0x50`, the sprite-center byte
offset at `0x58`, and five `0x08`-byte LOD ranges beginning at `0x60`. Each
range is `(u16 cornerBegin, u16 cornerEnd, f32 threshold)`. The importer uses
the first authored LOD.

The start of `0xA000` contains `0x08`-byte corner records with half-float
`size[2]` and `uv[2]`. The center stream begins at the descriptor's
sprite-center offset and uses `0x0E`-byte records: half-float position plus
four signed 16-bit runtime fields. Four consecutive corners share one center.
Imported card positions are
`(center + (size.x, size.y, 0)) * 0.9144`.

Foliage uses its asset's first `0x5600` shader. A foliage-only shader identity
preserves those source textures and parameters while forcing masked alpha, so
the same source shader can remain opaque when used by a non-foliage asset.
Blender mesh datablocks are shared by all placements of the same foliage TUID.
Runtime wind, custom normals, and camera-facing billboard behavior are not
reproduced.

Shrub assets come from `0x1D500` pointers into `shrubs.dat`. Each standalone
IGHW contains:

- `0xB100`: one `0x80`-byte header;
- `0xB300`: `0x10`-byte vertices;
- `0xB400`: big-endian `u16` triangle indices;
- `0x5600`: the source shader TUID;
- `0xB700`: the null-terminated source entity path;
- `0x3150`: a per-vertex runtime remap that is not needed for static geometry.

The header stores vertex and index offset fields at `+0x00`/`+0x04`, the
index count at `+0x08`, the authored position extent at `+0x18`, and the
asset TUID at `+0x28`. Both offsets are zero in every verified R2 and R3
asset; the bounded reader treats them as byte offsets if present. Each vertex
stores signed-normalized `i16 position[4]` at `+0x00`, half-float UVs at
`+0x08`, and one packed runtime word at `+0x0C`. Imported positions are
`clamp(raw / 32767, -1, 1) * extent * 0.9144`. The packed word remains
unidentified and is not imported as a Blender normal.

Zones use `0x7500` as an eight-byte Shrub TUID lookup and `0x7540` as
`0x40`-byte placement records. A placement stores position at `+0x00`, scale
at `+0x0C`, two basis rows at `+0x10` and `+0x20`, and the lookup index at
`+0x3C`. The third basis row is their cross product and translation is
converted from yards to meters. All placements of one TUID share a Blender
mesh datablock.

The real Scotia Co-op regression covers all ten Shrub assets, 2,250 vertices,
2,538 triangles, and 8,426 placements across three of its nine zones. Every
placement resolves to one of the ten level TUIDs, all transforms are finite,
and every source Shrub shader resolves to a masked material.

`gameplay.dat` section `0x25000` identifies region directories. Each region
uses:

- `gp_prius.dat` `0x25048`: `0x50`-byte Moby instances;
- `gp_prius.dat` `0x2504C`: `0x10`-byte instance metadata;
- `region.dat` `0x1C600`: Moby TUID lookup.

Gameplay Euler values are composed in ZYX order.

## Known unknowns

- Exact value-14 foliage material-vector semantics and Shrub packed vertex
  metadata.
- Packed normals/tangents for custom-normal import.
- Packed green/specular behavior; the public `useSpecular` bit is absent from
  all 3,355 unique shaders in the current corpus.
- Detail-map composition and exact use of vector 4.
- Authored emission tint/strength and the remaining parameter-vector fields.
- Lighting, level collision, VFX, cubemap, and sky import.
- Runtime-specific layered animation blending for every weapon overlay.

## References

- [PredatorCZ/InsomniaToolset](https://github.com/PredatorCZ/InsomniaToolset)
- [Mrroboto9819/ReChimera](https://github.com/Mrroboto9819/ReChimera)
- [Insomniac Engine AllExtractor](https://lukascone.wordpress.com/2017/12/03/insomniac-engine-allextractor/)
