# Resistance 2 format notes

Status: verified against a decrypted US Resistance 2 PS3 disc image and the
public InsomniaToolset/ReChimera research listed below.

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

Bits set in the default segment draw mask identify the intact/default model.
Clear bits identify optional damage/bangle alternatives. A zero mask is
treated as unspecified and keeps every segment in the default model. Ordinary
Moby and gameplay imports use the default geometry. When explicitly enabled,
Blender keeps each alternate segment as a separate object so mutually
exclusive runtime permutations are not merged together.

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
| `0x04` | `u16` | quantization marker, `256` |
| `0x06` | `u16` | face count |
| `0x08` | `u16` | vertex section length |
| `0x0C` | `u16` | aligned total data length |

The signed `i32[3]` base position is at prototype `+0x90`. Quantized vertices
begin at `+0xA0` in groups of eight: eight X `u16` values, then eight Y, then
eight Z, for 48 bytes per group. Position decoding is:

```text
position = (base + quantizedOffset) / 1024
```

The vertex section length is `0x20 + ceil(vertexCount / 8) * 0x30`, measured
from the `+0x80` header. Face words immediately follow it. Each big-endian
`u32` stores face type in bits 27-31 and three nine-bit vertex indices in bits
18-26, 9-17, and 0-8.

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
connects blue to Principled BSDF Emission Color with a conservative strength
of `1.0`.

The default material mode converts red gloss to roughness as
`roughness = 1 - red` when flag `0x04` is set. Green would map to specular
only when flag `0x02` is set, but that flag is absent from every shader in the
whole-disc corpus, so no R2 material currently connects green. Authored
emission tint and strength have not been identified conclusively in the six
parameter vectors, so the importer does not guess them.

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
composition and per-family differences remain unverified. Detail images and
metadata are preserved but intentionally left out of the Blender node graph.

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
indexes already-extracted sibling level lookups lazily and reads the matching
`highmips.dat` payload. It does not assume that equal low-32 IDs are equal:
duplicate candidates are decoded and compared before use.

## Zones and gameplay placement

Zones contain:

- `0x6200` UFrag terrain descriptors;
- `0x6000` / `0x6100` UFrag vertex/index buffers;
- `0x7200` Tie TUID table;
- `0x7240` Tie instance transforms;
- `0x72C0` instance TUID/name metadata, which names placements rather than
  Tie asset classes.

UFrag world positions simplify to `(rawVertex + fragmentPosition) / 256` in
the same game units used by placements.

`gameplay.dat` section `0x25000` identifies region directories. Each region
uses:

- `gp_prius.dat` `0x25048`: `0x50`-byte Moby instances;
- `gp_prius.dat` `0x2504C`: `0x10`-byte instance metadata;
- `region.dat` `0x1C600`: Moby TUID lookup.

Gameplay Euler values are composed in ZYX order.

## Known unknowns

- Shrub and foliage geometry and exact value-14 material-vector semantics.
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
