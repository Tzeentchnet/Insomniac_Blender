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
5,820 unique named assets: 969 Mobys and 4,851 Ties.

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
- zone terrain, Tie instances, and gameplay Moby placement.

Resistance 3 has game-specific reader constants for the layouts below rather
than weakening Resistance 2 validation.

## Animation profile

Resistance 3 retains the V2 animset container but does not use every
Resistance 2 playback rule unchanged. ReChimera's independently developed R3
profile identifies these differences:

- header `+0x0C` can point to a `u16` logical-to-stored frame table;
- non-delta positions are rebased from the clip reference to the target
  skeleton bind position;
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
  `bind + (decoded - clipReference)`;
- treats animated additive rotations as direct clip values and uses skeleton
  bind only for untracked standalone channels;
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
are not universal PBR channels, so their rendering semantics remain metadata
rather than speculative node connections.

Shader material section `0x5000` remains `0x80` bytes but shifts its control
word and parameter values:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `i32[6]` | texture-slot indices; `-1` means unused |
| `0x18` | `u32[2]` | unknown pre-control values |
| `0x20` | `u32` | material control word |
| `0x24` | `f32[5][4]` | material parameter vectors |
| `0x74` | `f32[3]` | trailing material parameters |

The two pre-control words, five vectors, and three trailing values are
retained separately as Blender custom properties. Base color uses the first
complete vector after the control word in enhanced source-material mode.

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
- preserve bound detail images as unconnected Non-Color nodes;
- keep Principled Metallic at zero.

The only normal-slot format outlier is `dark_cable/dark_cable`, which binds a
BC1 diffuse `*_c` texture. It is reported and left disconnected. Packed green,
packed alpha, detail-map composition, and the two effect slots remain
unconnected because their R3 semantics are not independently proven. Unlike
the two effect slots, bound detail textures are still imported as data-only
nodes.

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

## Verified import coverage

The real-data regression and Blender 5.2 smoke tests cover:

- all 170 Mobys in `haven_town_defense`;
- a 33-bone Moby with 18 decoded Actions;
- local and global DXT textures;
- 404 Ties, 13 zones, terrain, and 843 gameplay placements;
- a complete level import producing 6,500+ Blender objects with shared mesh
  datablocks.

Shrub and foliage geometry, lighting, level collision, VFX, and cubemaps are
not imported.
