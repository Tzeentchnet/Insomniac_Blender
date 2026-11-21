# Resistance 3 format notes

Status: verified against the decrypted USA `BCUS98176` v01.00 Resistance 3
PS3 disc (`En,Fr,Es`) and real campaign and lobby archives. The add-on
validates both `PARAM.SFO` identity fields before extraction.

## Disc and archive layout

Resistance 3 uses the same logical archive locations as Resistance 2:

```text
PS3_GAME/USRDIR/packed/game/global_cached.psarc
PS3_GAME/USRDIR/packed/game/global_uncached.psarc
PS3_GAME/USRDIR/packed/levels/<level>/level_cached.psarc
PS3_GAME/USRDIR/packed/levels/<level>/level_uncached.psarc
```

The tested image is a hybrid ISO9660/UDF disc with duplicated layer files
such as `global_cached2.psarc`. Some libarchive builds reject its UDF
descriptors before reaching the valid ISO9660 tree. The extension therefore
reads bounded ISO9660 directory records and file extents directly, strips
numeric `;1` versions, and resolves paths case-insensitively. A
libarchive-compatible `tar` remains a fallback for other valid images.

The USA disc contains 27 level archive pairs. Its bundled metadata catalog has
10,482 unique assets: 969 Mobys, 4,851 Ties, 70 Shrubs, 13 Foliage assets,
and 4,579 Materials. Source names are available for the 5,890 Mobys, Ties,
and Shrubs.

## Resistance3Port corroboration

The independently implemented
[Resistance3Port resource override table](https://github.com/miroslavdanutalexandru-alt/Resistance3Port/blob/cdef34525b15fd1da60fb252ff3a1d0712ce8f61/project/mods_psarc.cpp#L231-L249)
corroborates the PSARC header, block table, manifest ordering, IGHW pointer
sections, and 16-byte asset-pointer records used by this reader. Comparison
against the real R2 and R3 archives also corrected the section `0x1D400`
companion name to `foliages.dat`.

Resistance 3 additionally exposes section `0x1DB80` for `highlight.dat`.
The tested `mine_town_approach` lookup contains 50 highlight pointers, and its
payload is stored in `level_uncached.psarc`. The reader now recognizes the
resource kind and can resolve its bounded asset slices, but the highlight
payload semantics are not yet decoded.

Resistance3Port generates lifted game code locally from a user's decrypted
EBOOT and does not commit that derived code. Its public tree therefore
provides no additional animation structures beyond the resource/archive
metadata above.

## Shared engine structures

The following verified Resistance 3 structures match the bounded Resistance 2
reader layouts documented in [Resistance 2 format notes](resistance_2.md):

- PS3 big-endian zlib PSARC headers, manifests, and block tables;
- IGHW 1.1 containers and `assetlookup.dat` pointer sections;
- Moby and Tie render geometry;
- skeletons, skin palettes, and bind matrices;
- the V2 animset container, control arrays, track masks, and quantization;
- texture metadata, high mips, local textures, and global texture bundles;
- zone terrain vertex/index records, Tie instances, and gameplay Moby
  placement.

Resistance 3 has game-specific reader constants for the layouts below rather
than weakening Resistance 2 validation.

## Tie material groups

R3 Tie primitives retain the shared `0x40`-byte record stride, but select
their local shader group with the byte at primitive `+0x26`. R2's `u16`
selector at `+0x28` and legacy fallback at `+0x0C` are not shader indices in
R3. Treating them as such routed grass cards, conifer branches, bark, track
geometry, and helper faces through unrelated materials.

The exact `mine_traversal_train` regression covers:

- `ceg_lowpoly_grass_field_2_85` (`0x398B4420E2ACF15B`);
- `ceg_treeclump_conifer_alive49` (`0xE18BF091E27042FC`);
- the representative track Tie (`0xA62D61B7D19B863D`).

All 4,767 tested R3 Tie primitives have an in-range `+0x26` selector. The
reader rejects an out-of-range value rather than silently assigning material
zero.

## Tie vertex and baked-light data

R3 Tie primary vertices use a `0x14`-byte layout:

| Offset | Type | Meaning |
|---:|---|---|
| `+0x00` | `i16[3]` | Quantized local position |
| `+0x06` | `i16` | Packed per-vertex light value |
| `+0x08` | `f16[2]` | Base-material UV |

Tie header `u32 +0x18` gives the primary-buffer byte length. Any secondary
UV buffer begins after that range within section `0x3000` and stores packed
`f16[2]` values at a `0x04`-byte stride. Primitive `u16 +0x06` selects its
secondary-vertex start and byte `+0x0E` indicates that UV2 is present. The
grass and representative track assets use this buffer; the tested conifer
does not.

The primary vertex light uses the same `abs(purpose)` decode documented for
UFrags below. Blender preserves it in the point-domain
`InsomniacVertexColor` attribute and preserves UV2 as `LightmapUV`.

Each `TieInstanceV2` stores a signed zone-lightmap ID at `i16 +0x5A`;
negative values mean no lightmap. Non-negative IDs address the zone's
`0x5400` descriptor table and the matching same-TUID `lighting.dat` asset,
just like UFrag lightmaps. Blender retains shared Tie meshes while applying
the per-instance maps through one level-wide atlas and object-specific UV
scale/offset properties. The atlas downsamples each baked-light texture by
four in each dimension using linear-light averaging; this keeps the full
level practical without creating thousands of images, materials, or mesh
copies.

In `mine_traversal_train`, 6,730 Tie instances reference distinct baked
lightmaps. They pack into one 4096x2116 atlas. The named
`ceg_lowpoly_grass_field_2_85` instance uses zone
`0x58E8829DE249D884` lightmap 173, a 256x256 BC3 image whose RGB range
includes approximately 26% through 100% intensity.

## UFrag terrain position

R3 retains the `0x6000` vertex buffer, `0x6100` index buffer, and `0x6200`
`0x80`-byte UFrag descriptors, but moves the descriptor's 256-scaled world
position from R2's `+0x60` to `+0x70`. The signed 16-bit local vertex
positions remain divided by 256 before adding that world position.

Reading the R3 vector at `+0x60` produces denormalized values near zero and
collapses each zone's terrain around the origin. In `mine_traversal_train`,
the corrected `0xEF2A7A01E249D922` zone spans roughly `x=2493..3244` and
`z=-610..1234`, matching its modular terrain Ties and train-track placements.

## UFrag vertex and baked-light data

R3 UFrags use the `0x18`-byte `RegionVertexV2` layout:

| Offset | Type | Meaning |
|---:|---|---|
| `+0x00` | `i16[3]` | Local position, divided by 256 |
| `+0x06` | `i16` | Packed per-vertex light value |
| `+0x08` | `f16[2]` | Base-material UV |
| `+0x0C` | `f16[2]` | Zone-lightmap UV |
| `+0x10` | `u32` | Packed normal |
| `+0x14` | `u32` | Packed tangent |

The signed light value is decoded using the independently documented engine
rule:

```text
value = abs(purpose)
rgb = min(value / 0x4000, 1)
alpha = fract(value / 0x80)
```

Blender stores the result as the point-domain
`InsomniacVertexColor` attribute and multiplies its RGB into the terrain
albedo. `LightmapUV` preserves UV1.

The UFrag descriptor's `u16 +0x4E` selects a zone-local lightmap; `0xFFFF`
means no texture. Section `0x5400` contains parallel `0x20`-byte NV4097
texture descriptors. Their texel offsets are **not** relative to the zone
asset and do not address `textures.dat`. They are relative to the
same-TUID `0x1DB00` lighting asset in `lighting.dat`. Only lightmaps used by
terrain or placed Ties are decoded.

Section `0x5410` is a one-to-one table with the same `0x20`-byte descriptor
layout and indices. Across all 18 `mine_traversal_train` zones, every
`0x5400` entry has exactly one `0x5410` owner. The paired texture uses the
same UV set but may be one, two, or four times smaller in either dimension;
some small Tie maps instead have a larger directional texture. The tested
textures are BC1 and range from 4x4 through 512x512.

`0x5410` is not an RGB color multiplier. It packs the three monochrome
radiosity-normal-map basis coefficients into RGB. This is the single-texture
optimization documented for
[*Resistance: Fall of Man*](http://wiki.polycount.com/wiki/Radiosity_normal_map):
colored texels record different directional responses, while equal RGB
values are the neutral-normal response and white is neutral. Blender reads
the texture as Non-Color data and evaluates the squared/clamped
[radiosity-normal-map basis](https://cdn.cloudflare.steamstatic.com/apps/valve/2006/SIGGRAPH06_Course_ShadingInValvesSourceEngine_Slides.pdf)

```text
b0 = ( sqrt(2/3),          0, sqrt(1/3))
b1 = (-sqrt(1/6),  sqrt(1/2), sqrt(1/3))
b2 = (-sqrt(1/6), -sqrt(1/2), sqrt(1/3))

wi = max(dot(tangent_normal, bi), 0)^2
directional = map.r * w0 + map.g * w1 + map.b * w2
```

The imported material multiplies base albedo, packed per-vertex light, the
sRGB `0x5400` map, and this scalar directional response. For materials with a
normal map, the reconstructed tangent normal drives the basis weights;
otherwise `(0, 0, 1)` produces the coefficient average. Terrain retains
individual paired images. Ties pack both maps into equal-layout compact
atlases so their existing per-object UV transforms and shared mesh/material
datablocks remain valid. Directional coefficients are block-averaged in
linear data space when the Tie atlas is reduced; smaller integral 4x4
sentinel maps are repeated to the paired tile size without interpolation.

In `mine_traversal_train`, zone `0xEF2A7A01E249D922` has 972 UFrags. Every
one references lightmap 109, a 1024x1024 BC3 image at relative lighting
offset `0x9BE380`, paired with a neutral-white 256x256 BC1 directional map.
Its UV1 range is exactly 0 through 1. Across the level, terrain uses 34
zone-scoped pairs and 51 shader/lightmap material pairs. The
`TRACK_CHECKPOINT_7` Tie uses baked map 120 and a nontrivial 128x128
directional map whose independent RGB channels exercise the basis blend.
The independent `haven_town_center` check found exact pairing for all 11,094
descriptors across 32 zones, including 31 terrain-used pairs.

## Animation profile

Resistance 3 retains the V2 animset container but does not use every
Resistance 2 playback rule unchanged. ReChimera's independently developed R3
profile identifies these differences:

- header `+0x0C` can point to a `u16` logical-to-stored frame table;
- non-delta positions, including additive facial-overlay positions, are
  rebased from the clip reference to the target skeleton bind position;
- additive rotations use R3-specific bind fallback and blend-mask behavior;
- facial, expression, and some other additive clips are runtime overlays over
  an idle Action.

See
[ReChimera's game profiles](https://github.com/Mrroboto9819/ReChimera/blob/68f187bea5177302e78418e645bd5359654af7f8/crates/lunalib/src/game.rs#L42-L117)
and
[animation decoder](https://github.com/Mrroboto9819/ReChimera/blob/68f187bea5177302e78418e645bd5359654af7f8/crates/lunalib/src/animation.rs#L1094-L1338).

The reader selects an explicit R3 animation profile without changing the
validated Resistance 2 path. It:

- accepts a header `+0x0C` frame map only when the table ends in the alignment
  block immediately before the frame data and every mapped index is in range;
- rebases authored non-delta positions as
  `bind + (decoded - clipReference)`, including additive expression channels
  whose reference values are absolute local facial-bone positions;
- treats animated additive rotations as direct clip values and uses skeleton
  bind only for untracked standalone channels;
- retargets untracked rotations in `characters/gameheads` animsets to the
  imported character's bind skeleton; their shared clip reference
  quaternions can describe a different source head and otherwise snap the
  complete head when an Action starts;
- retains clip-reference rotations on blend-enabled bones for pose-only
  additive game-head clips such as `eyes_left`, `eyes_right`, `eyes_up`, and
  `eyes_down`; those Actions intentionally contain no per-frame tracks;
- decodes `0x0200` position and scale values as bind-relative additive
  offsets;
- retains per-channel authored masks, per-bone blend weights, source flags,
  and clip reference rotations for layer composition;
- performs signed 16-bit wrapping when adding 8-bit track deltas.

For matched runtime overlays, the importer creates a complete standalone
Action by sampling a compatible base at the overlay's source times. Animated
rotation is applied as
`base * (overlay * inverse(overlayReference))`. Translation and scale deltas
are applied over the sampled base, and the per-bone blend mask gates every
channel. Known mappings include weapon/fire `*_p` clips, head expressions,
and a unique same-character `*_idle_sighted` fallback. Ambiguous or missing
bases are not guessed.

The exact 78-bone Ravager animset from `mine_town_approach` exercises this
path. All 11 additive clips bake over `ravager_r3_idle_sighted`, including
the nine-frame `ravager_partial_aiming_horizontal` and vertical variants with
flags `0x0206`. The seven zero-weight control bones retain the sampled base,
while the authored upper-body channels produce a continuous left-to-right aim
instead of the previous collapsed or T-shaped limbs. The already-correct
55-bone Stalker locomotion remains complete under the R3 profile.

Baked Actions preserve `insomniac_additive_source = true` and add
`insomniac_overlay_baked = true` plus the
`insomniac_overlay_base` clip name.

Imported Actions omit channels that remain at Blender's default value and use
one key for constant non-default channels. Varying channels retain every
decoded sample. Pose conversion after the first frame evaluates only tracked
bones and their required ancestors. On the 78-bone Ravager this reduces the
Action library from 81,900 to 29,053 FCurves and from 1,144,360 to 428,826
keys without changing sampled Action values.

## Character segments and companion models

See [Resistance 3 character asset findings](../assets/resistance_3_characters.md)
for the asset TUIDs, owner-level sets, segment topology, direct-reference scan,
and body/head compatibility evidence.

The Ravager Moby does not reference an external head Moby. Its model has seven
render segments and a default draw mask of `0x7F`; segments 1 through 6 contain
the authored head, eyes, and upper attachments. **Show source-default character
parts** exposes those six Collections on import while leaving them separately
selectable.

Raw entity scans also found no direct body-to-head Moby reference in the
Capelli hero body, NPC body, or game-head entities. Compatible bodies and
heads can share owner levels and character-space placement, but their
skeleton indices and bind poses differ. The importer therefore does not guess
a companion or merge head weights into a body rig. A future animated assembly
feature needs an explicit data-driven pairing and attachment profile.

## Shader references

Shader section `0x5D00` is `0x40` bytes in Resistance 3 and contains six
texture slots:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u64` | shader TUID |
| `0x08` | `u32` | shader lookup-path pointer |
| `0x0C` | `u32` | unknown |
| `0x10` | `u32[6]` | texture IDs |
| `0x28` | `u32[6]` | texture lookup-path pointers |

The first four slots retain the diffuse, normal, packed, and detail mappings
used by the Blender material bridge. A nonzero reference is not sufficient to
prove that a slot is active: the corresponding signed index in `0x5000` must
also be nonnegative. Blender preserves each raw ID, path, map index, and bound
state as custom properties.

The fifth slot is actively bound by 967 of the 4,579 unique shaders on the
USA disc. Common paths identify shader-family effects such as
`gradientdissolve`, `fire_customa`, `ig_frozen_customa`, pickup sheen, and
weapon-upgrade overlays. The sixth slot is bound by only four shaders. These
are not universal PBR channels, so they receive semantics only after
independent family analysis.

Shader material section `0x5000` remains `0x80` bytes but shifts its control
word and parameter values:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `i32[6]` | texture-slot indices; `-1` means unused |
| `0x18` | `u32[2]` | unknown pre-control values |
| `0x20` | `u32` | material control word |
| `0x24` | `f32[5][4]` | preserved raw parameter window |
| `0x48` | `u64` | material-program TUID within that raw window |
| `0x74` | `f32[3]` | trailing material parameters |

The raw parameter window includes mixed float and identity fields; the reader
preserves its historical five-vector view while also exposing the proven
program TUID at `0x48`. The two pre-control words, program identity, five raw
vectors, and three trailing values are retained as Blender custom properties.
Base color uses the first complete vector after the control word in enhanced
source-material mode.

### Frozen overlay family

The verified frozen program identity is `0x5C8677F5D69FF338`. In
`new_york_terraformer`, 38 shaders satisfy the complete family signature:

- the shader basename ends in `frozen`;
- control byte `material_unknown_0` is `0x13`;
- texture slot five is active and its authored path is
  `artdepartment/environment/decals/effects/ig_frozen_customa`;
- material section `0x5000 + 0x48` contains the frozen program TUID;
- embedded sections `0x33001`, `0x33002`, and `0x33003` are present.

Section `0x33001` stores three concatenated RSX fragment-program chunks.
Section `0x33002` stores three `0x10`-byte offset/size descriptors; its IGHW
count is `1`, so records are counted from section length instead. Classification
also requires the three consecutive program identities and bounded,
non-overlapping chunk ranges that cover `0x33001`. Section `0x33003` stores the
corresponding selector bytes. Two frozen binary variants occur in the reviewed
level, with `0x33001` SHA-256 values
`60db6353d77fc3072cbb7d553079818b1f38d8fb7c9baff772f3bcc0db8a4ed4`
and
`d9f3adbec5f71884c3eb0c31eb92029a313265708ab425b6362a8d45ce7d9ad5`.

RSX decompilation proves that the custom map is sampled from the normalized
transformed surface normal rather than mesh UVs. The program adds a powered
grazing-angle response, processes the sampled RGB, and contains a fixed
`(0.55, 0.65, 0.75)` ice tint. The Blender graph mirrors those verified
operations with camera-space normal projection, repeated texture addressing,
a screen response over base color, and lower roughness under the same
coverage.

Texture path alone is not a family discriminator. The molten Hulk program
`0x2B4B1424D6F60B51` uses the same `ig_frozen_customa` map but has a distinct
embedded program and additional molten color/emission math. Conversely,
`cap_hair_planes_frozen` uses the frozen program identity with `fire_customa`.
Both cases are excluded from frozen composition.

### Gradient-dissolve family

The gradient-dissolve program identity is `0x13C601140542FB72`. A complete
27-level USA-disc scan classified 3,475 references to 247 unique shaders in 26
levels. The family requires all of the following:

- a shader basename ending in `_dissolve`, or the authored
  `em_herbert_head_downres_dissovle` spelling;
- control byte `material_unknown_0` equal to `0x13`;
- an active fifth slot whose path is exactly `ssharit/gradientdissolve`;
- material-program TUID `0x13C601140542FB72`;
- a `0x2E30`-byte `0x33001` program section;
- four bounded, non-overlapping `0x33002` descriptors with consecutive TUIDs
  `0x13C601140542FB72` through `0x13C601140542FB75` that cover the program;
- a `0x0F`-byte `0x33003` selector section.

The `0x33002` header again reports count `1`; its `0x40`-byte section actually
contains four `0x10`-byte records. Three program variants occur across the
corpus, with `0x33001` SHA-256 values:

- `39d84740b1a671f17fcad6bd5d2943972ed97fe51ea3bacdc146078ed5dfdba8`;
- `57be81638271f417c7cc8db4481a9819581303230ee9119e5a13b6b348146c4f`;
- `77c073faca8456d5b85e83442eb713fd8414416306c2f7172bfdd9bd0858bc68`.

Across the three decoded texture fixtures, four level-local IDs resolve to the
same source texture TUID `0x12278F4B908A4115`: a 512x512 format-`0x05`
A8R8G8B8, opaque grayscale map whose RGB channels are identical. Decompiled
specialized fragments sample it either from the normalized reconstructed
surface-normal XY or from a vertex-provided secondary varying. They shape that
sample into the surface response, include a fixed `(0.2, 0.25, 1.0)` blue
color, and scale the response with a value derived from vertex-stage input.

That controlling transition value is not serialized in material section
`0x5000`, and the observed effect-map sequence does not establish a static
alpha threshold that Blender can reproduce faithfully. The reader therefore
classifies the family and preserves its program and effect metadata, but the
Blender bridge intentionally leaves the gradient texture disconnected.
Sixty-seven retail references have a `_dissolve` name without the verified
program, bound slot, path, or embedded sections and are excluded.

### On-fire family

The on-fire program identity is `0xB2DAFFA9D7AF976A`. A complete 27-level
USA-disc scan classified 3,322 references to 239 unique shaders in 26 levels.
The family requires all of the following:

- a shader basename ending in `_onfire`;
- control byte `material_unknown_0` equal to `0x13`;
- an active fifth slot whose path is either
  `artdepartment/environment/decals/effects/onfire/fire_customa` or
  `graphshaders/iang/onfire_01.graphshader`;
- material-program TUID `0xB2DAFFA9D7AF976A`;
- a `0x2DB0`- or `0x2E30`-byte `0x33001` program section;
- three bounded, non-overlapping `0x33002` descriptors with consecutive TUIDs
  `0xB2DAFFA9D7AF976B` through `0xB2DAFFA9D7AF976D`;
- a `0x15`-byte `0x33003` selector section.

The two program layouts have `0x33001` SHA-256 values:

- `1d66ff9f988dac320c7aeff5f4b7669869930fe222566c4f394a596d2756f0c7`;
- `b8f1186f6660b21c69bc775d69925f9828715854fc3be1fc09c27e7f63902f19`.

The primary path accounts for 3,307 references to 238 unique shaders. Its
decoded source texture TUID `0xC72BA28A17A2716A` is a 256x256 format-`0x05`
A8R8G8B8 map with distinct color channels and alpha from 0 through 70. The
alternate path occurs on one multiplayer shader repeated in 15 archives and
resolves to a uniform 32x32 format-`0x06` DXT1 texture. Five name-only
`_onfire` shaders lack the verified active path or program and are excluded.
Two frozen-named hair shaders bind `fire_customa` but use the frozen program
identity and are also excluded.

RSX decompilation shows two normal-projected samples whose coordinates are
offset from a dynamic phase at different rates. The common fragment includes
fixed warm colors `(1.0, 0.75, 0.5)` and `(1.0, 0.15, 0.0)`. Comparison
against 3,251 same-level base-material pairs found no serialized animation
phase in material section `0x5000`; the phase is runtime draw state. The
reader therefore preserves `R3_ON_FIRE` family and program metadata, while
the Blender bridge leaves the animated texture disconnected rather than
freezing every imported material at an invented phase.

### Flash-overlay and pickup-sheen family

Program `0x22A146AAE58E49A4` and the exact fifth-slot path
`d_action_pickup/textures/flash_002` identify a broader flash-overlay family,
not just shader names ending in `_sheen`. A 27-level scan found 755 references
to 52 unique shaders in 26 levels:

- 44 unique `_sheen` shaders, accounting for 695 references;
- five journal or sketchbook shaders, accounting for 29 references;
- two pickup-body shaders, accounting for 30 references;
- one scanner-drone shield shader.

Every retail `_sheen` shader satisfies the complete family signature, and no
active `flash_002` binding uses a conflicting program. Classification requires
control byte `0x13`, the active exact path, the program TUID, a `0x2A10`- or
`0x2A50`-byte `0x33001` section, three valid descriptors beginning at
`0x22A146AAE58E49A5`, and a `0x09`- or `0x0B`-byte selector section. The three
observed `0x33001` SHA-256 values are:

- `f9761daa36417cf0301ba12deb418feab92451ca051423855fd97d5d8f2b4fa1`;
- `d8705c78e617c59f2dae7945af36a7c92a17c735b959a3c9d1c96d32b2a5f39a`;
- `3fee5c159746640d6638b35e3bdfefb520f15dfb557248368634c18a7b0592c3`.

The decoded source texture TUID is `0xA9B64E9A38B08640`, a 256x256,
seven-mip format-`0x08` DXT5 sweep map. Its four channels vary independently;
decoded alpha ranges from 0 through 208. Decompiled program variants sample
the map twice: one lookup derives from a fractional moving phase, while the
other offsets the surface lookup with that same phase. Constants `0.8`,
`0.909091`, `2.18182`, and `0.5` shape the sweep, but the phase itself is
runtime state absent from material section `0x5000`.

The reader classifies these shaders as `R3_FLASH_OVERLAY` and preserves their
program metadata. Blender intentionally leaves `flash_002` disconnected
rather than freezing pickup sheen, journal fades, and shield effects at one
invented instant.

### Weapon-upgrade family

Program `0x747A316CD67FBD5D` and the exact fifth-slot path
`artdepartment/environment/decals/effects/weaponupgrade/weaponupgrade_c`
identify the weapon-upgrade family. A complete 27-level scan found 1,118
references to 43 unique shaders in 26 levels. Forty unique names end in
`_upgrading`, and three end in `_weaponupgrading`; all 43 recur in every
level containing the family. No matching name has a conflicting signature,
and no active `weaponupgrade_c` path uses another program.

Classification also requires control byte `0x13`, an active fifth slot, a
`0x2A00`- or `0x2A80`-byte `0x33001` section, three valid descriptors
beginning at `0x747A316CD67FBD5E`, and an `0x0B`-byte selector containing
`01 0c 13 00 01 0c 13 00 01 0c 13`. The `0x2A80` layout uses chunk
lengths `0x0F60`, `0x0DC0`, and `0x0D40` at offsets `0`, `0x0F80`, and
`0x1D40`. The `0x2A00` layout shortens the first chunk to `0x0EE0` and
places the latter chunks at `0x0F00` and `0x1CC0`. Three complete-program
variants occur:

- `e9c787d205af2cc71f78f9c92770d1b96e2b5065199d288fa42807e184e3ca35`
  (`0x2A80`, 676 references);
- `58ea9bd8adecb737bc393a450b123113ca85a5cc6d3d3613af56e962f25a1f4f`
  (`0x2A80`, 338 references);
- `93a6a4ca163cd28ca933e987f71bf14e523dea7b5673c6059ffef3321e6678f3`
  (`0x2A00`, 104 references).

Level-local IDs `0x4FC58D4F` and `0x116431D2` both resolve to source texture
TUID `0xEB23A2284FC58D4F`. It is a 256x256, nine-mip format-`0x05`
A8R8G8B8 map. Decoded channel minima are `(36, 0, 3, 0)`, maxima are
`(255, 148, 255, 116)`, and the mip-zero RGBA SHA-256 is
`e6d1439f0a59e67544421f79b20bf7c9581711976cbde801c48cccbb2623440e`.

All three fragment specializations sample slot eight twice. The first sample's
result is scaled by `0.75`; a second coordinate uses a zero-valued inline
constant placeholder with a fixed `0.15` factor, and other specializations
include a cyan `(0, 0.5, 1.0)` response. Every retail upgrade reference has
one same-level base shader after removing its upgrade suffix, but neither
those paired material records nor section `0x5000` serialize the runtime
offset or upgrade strength. The reader therefore preserves
`R3_WEAPON_UPGRADE` and program metadata while Blender leaves the animated
map unloaded.

Some level-local textures have a zero-length entry in the high-mip pointer
table while their texture TUID still owns a complete mip-zero payload in
`textures.dat`. The reader uses that bounded asset payload before trying
global or sibling-level fallbacks. Capelli's
`cap_hair_planes/cap_hair_planes` shader (`0xC89EC8F70464D875`) exercises this
layout: its 256x256 BC3 albedo is a 65,536-byte embedded texture rather than a
`highmips.dat` slice.

### Material channel evidence

A scan of all 27 cached level archives on the USA disc found 33,860 shader
references and 4,579 unique shader payloads, with no conflicting duplicate
payloads:

- 3,916 shaders bind a normal slot, and all 3,916 set control bit `0x08`;
- 3,892 of those normal descriptors are BC3 and 23 are RG8;
- 3,638 shaders bind a packed third map;
- all 3,232 shaders setting gloss bit `0x04` bind that packed map;
- the legacy specular bit `0x02` is absent from the complete corpus;
- 1,281 shaders bind detail maps, while the detail bit and slot agree in
  4,575 of 4,579 records.

The four detail-slot exceptions are foliage shadow-map bindings rather than
detail data. Their samplers are unsigned `0x00030101`; every one of the 1,277
flag-enabled detail records sets biased signed-remap bit `0x1000` through
address `0x00031101` (1,149 records) or `0x00031121` (128). Of the flagged
records, 992 use the standard material path and 285 carry a custom embedded
material program. Vector 4 Z is the shared authored UV scale: 958 of the 992
standard records use `4.0`, with finite authored exceptions.

InsomniaToolset's related material path independently identifies the packed
channels as red gloss, green specular, and blue emission, with red inverted
for PBR roughness and metallic fixed to zero. The exact R3 flag/slot
correlations preserve the normal, gloss, and detail meanings from that
lineage. ReChimera likewise keeps metallic at zero and treats the third map as
mixed data rather than a metalness texture.

A seven-level, 3,099-shader follow-up isolated the R3 emission control:

- 310 of the 313 packed-map shaders with low control-byte bit `0x08` clear
  contain nonzero blue, while only 409 of 2,073 shaders with the bit set do;
- exact shared-texture variants identify the bit as an emission disable:
  hanging-light on/off records use `0x00`/`0x08`, flicker uses `0x20`,
  and illuminated/unilluminated candles use `0x02`/`0x0A`;
- active blue masks predominantly select colored albedo texels. On the
  Longleg, blue values of at least 64 select mean sRGB albedo values of about
  `(137, 99, 13)` for the wing machinery and `(170, 124, 32)` for the eyes.

R3 packed blue is therefore treated as a scalar emission mask only when
low-byte bit `0x08` is clear. Its emission color is the authored albedo
multiplied by the valid first material-vector RGB factor and then by packed
blue. This restores the Longleg's yellow-orange eyes and wing strips and
prevents stale blue data, including the base Ravager's sparse blue texels,
from creating false emission. No independent scalar intensity parameter was
identified: normalized packed blue remains the source intensity and
Principled Emission Strength stays `1.0`.

Standard R3 Blender materials therefore:

- reconstruct BC3 tangent normals from alpha X plus inverted-green Y, and RG8
  normals from red X plus inverted-green Y;
- connect the result through a tangent-space Normal Map node;
- invert packed red into Principled roughness when gloss bit `0x04` is set;
- color enabled packed-blue emission with the authored albedo and base-color
  factor;
- tile verified signed detail maps from vector 4 Z, mask them with packed
  alpha, apply R/G to the tangent normal, B to diffuse, and A to gloss;
- keep Principled Metallic at zero.

The only normal-slot format outlier is `dark_cable/dark_cable`, which binds a
BC1 diffuse `*_c` texture. It is reported and left disconnected. Packed green,
packed-alpha meanings outside the verified detail mask, custom-program detail
maps, and the two effect slots remain unconnected because their semantics are
not independently proven. The standard detail graph recenters each channel as
approximately `(round(channel * 255) - 128) / 127`, uses packed alpha as a
shared weight, and applies the same R/G normal, B diffuse, and A gloss
responses found in multiple decompiled embedded programs. The accompanying
vertex-stage distance fade is runtime-only; Blender uses unit fade rather
than inventing a distance curve.

The Mine plaster fixture `0x26257DF8E54423E8` validates scale `4.0`, signed
sampler `0x00031101`, a 0-255 packed-alpha mask, and the four-channel
`concretedetail` texture. Any bound detail slot outside the exact standard
gate remains an unconnected Non-Color node with a data-only reason.

### Goliath wet-leg overlay

Goliath Moby `0x3F0861D604F7EC6E` assigns shader
`0x5966249FD0D6149B`,
`decals/goliath_wet_legs_overlay/goliath_wet_legs_overlay`, to a separate
lower-leg shell over the ordinary `goliath_r3_leg` geometry. Its mode-1
material binds:

- an opaque BC1 cellular albedo;
- a BC3 cellular tangent normal;
- an all-white packed map, which makes the layer fully glossy;
- the shared `oil_stain` detail map.

Connecting the BC1 texture's constant alpha directly made that shell an
opaque replacement and hid the base feet. Mode 1 occurs on decals, dirt,
snow, blood, wet surfaces, and other overlay families, but public
InsomniaToolset collapses every nonzero mode except alpha clip to generic
alpha blending. ReChimera names only mode 4 as alpha clip and mode 6 as
ordinary alpha blend. Neither source identifies mode 1's framebuffer
equation, and its mixed BC1/BC3 corpus is not safe to reinterpret globally.

The importer therefore applies a narrow Blender approximation only to this
verified shader. Coverage is the opaque BC1 albedo's luminance multiplied by
the authored first-vector alpha `1.2744`, clamped to `[0,1]`. The same layer
continues to supply its cellular normal and zero-roughness wet response. This
preserves the base leg beneath dark texels while retaining localized wet
highlights. The material records
`insomniac_alpha_approximation = "R3_MODE_1_BC1_LUMINANCE"` so the
approximation remains explicit and inspectable.

## Collision

Resistance 3 Moby triangle-collision prototypes use both `256` and `257` in
the quantization-marker field at prototype `+0x84`. Both variants retain the
same verified group-of-eight vertex layout, `/ 1024` position conversion,
face words, header-relative section lengths, and bounds checks. Marker `257`
is also verified in Resistance 2 on the Chimeran Shuttle, whose physical
layout is identical but whose lengths are measured from `+0x90`. The shared
reader accepts both exact length conventions for both games.

The marker-`257` variant was verified on unrelated Resistance 3 assets with 8
and 70 vertices and on every Moby referenced by the tested
`haven_town_defense` archive.

Regional R3 `collision.dat` uses the complete R2 `0x2900` level-collision
layout documented in
[Resistance 2 format notes](resistance_2.md#regional-level-collision).
Retail R3 level prototypes in the current corpus use triangle marker `256`
and additionally exercise oriented boxes, spheres, capsules, and kind-`8`
cylinders. The serialized Tie transforms, world-space terrain records,
surface values, prototype reuse, and no-op face handling are shared without
an R3 override.

## Verified import coverage

The real-data regression and Blender 5.2 smoke tests cover:

- all 170 Mobys in `haven_town_defense`;
- a 33-bone Moby with 18 decoded Actions;
- local and global DXT textures;
- 404 Ties, 13 zones, terrain, and 843 gameplay placements;
- a complete level import producing 6,500+ Blender objects with shared mesh
  datablocks.

Resistance 3 retains the R2 foliage card and center records, but changes both
the descriptor and placement layouts. Each standalone foliage IGHW contains:

- `0xA000`: a combined card-corner and sprite-center buffer;
- `0xA200`: one `0x80`-byte V3 descriptor;
- `0x5600`: the foliage shader TUID lookup.

The descriptor stores the used LOD count as a byte at `+0x37`, the
sprite-center byte offset at `+0x50`, and LOD ranges beginning at `+0x58`.
Each range remains `(u16 cornerBegin, u16 cornerEnd, f32 threshold)`.
The `0x08`-byte half-float corner and `0x0E`-byte center records match R2:
four consecutive corners share one center, and imported positions are
`(center + (size.x, -size.y, 0)) * 0.9144`. V3's vertical corner sign is the
opposite of R2; using the R2 sign places the card tops on the terrain and
extends the foliage downward. Only the first authored LOD is expanded.

Zones retain the `0x7400` array of foliage TUIDs, but `0x7440` uses
`0x80`-byte V3 instance records. The row-major transform starts at `+0x00`
and the `0x7400` lookup index is at `+0x70`.

The real `mine_traversal_train` regression covers all 11 foliage assets and
1,050 placements across eight zones. Its first LODs contain 5,232 unique
vertices and 2,616 triangles. All placements reuse those 11 Blender mesh
datablocks. Two foliage-only shader identities preserve the source textures
while forcing masked alpha; both decoded albedos contain the full `0`-`255`
alpha range.

Shrubs retain R2's `0xB100`/`0xB300`/`0xB400` standalone geometry and
`0x7500`/`0x7540` zone placement layouts. The same signed-normalized vertex
scaling, source `0x5600` masked shader, and two-row placement basis therefore
apply without a V3 override. `mine_traversal_train` contains six Shrub assets
with 394 vertices, 356 triangles, and 661 placements; every placement resolves
to one of those six TUIDs.

Two R3 powerline Shrubs store zero in the ordinary header `+0x18` extent:
`0xD6318617D6281400` and `0x2F55AF72D6E181F8`. Their raw position bounds and
serialized bounding spheres independently resolve the missing extent to the
R3 default `32.0`. For example, applying `32 / 32767` to the first asset's
raw Z range places its midpoint at `8.495`, matching the header sphere's
`8.495` Z center. The R3 reader uses that default only for an exact zero;
positive authored extents retain the shared R2 path.

A scan of all 27 R3 cached-level archives found 215 Shrub references to 70
unique, conflict-free payloads containing 15,126 vertices and 14,760
triangles. The shared trailing vertex field is again one finite half-float
height weight followed by a zero half in all 15,126 vertices. Raw Y is the
strongest positional correlation for all 69 nonconstant assets, with median
absolute correlation `0.9985`, and 14,333 triangles contain varying weights.
Every `0x3150` section is exactly four bytes per vertex and uses the same
little-endian `u16` remap plus zero padding as R2.

Blender preserves position W, the height weight, and the remap as the shared
non-rendering point attributes documented for R2. Runtime wind, custom
normals, and camera-facing billboarding are not reproduced.

Dynamic scene lighting, VFX, and cubemaps are not imported.
