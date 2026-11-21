# Known issues

## Resistance: Fall of Man

Support is restricted to the USA `BCUS98107` v01.00 disc. Other builds fail
disc validation instead of reusing unverified V1 offsets.

Moby class IDs, Tie header offsets, Material indices, and Texture record
offsets are level-local. Catalog identifiers and Blender material/image names
include the level scope so equal numeric values from unrelated levels do not
alias. The disc chain does not expose a proven retail name table; deterministic
numeric names are used.

The primary gameplay Moby group is imported. Six additional gameplay groups
have unverified record semantics and are skipped rather than guessed.
`ps3levelcoll.dat`, directional lightmaps, lighting, VFX, cubemaps, and audio
are not decoded.

Shrub meshes and clustered placements are imported. Foliage branch geometry
and sprite cards are imported from the first authored LOD. Runtime vegetation
wind, shrub vertex colors, foliage custom normals, and camera-facing
billboarding are not reproduced.

## Ratchet & Clank Future: Tools of Destruction

Support is intentionally restricted to the USA `BCUS98127` v02.00 disc.
Other regions or revisions fail disc validation rather than being parsed with
unverified offsets.

The retail disc's `DEBUG.PSARC` has no usable name database. Catalog and
Blender names therefore use stable level-local fallbacks (`Moby_XXXX` and
`Tie_XXXXXXXX`, and `Shrub_XXXXXXXX`). Numeric identities can collide between
levels; catalog identifiers include the owner level to keep those assets
distinct.

Inline animations are decoded in both observed forms. Complex clips apply
the runtime's signed 8-bit delta scaling, while simple clips discard paired
filler frames and use the effective half frame rate. Gameplay instances in a
level remain static linked meshes, as with R2/R3; import an individual Moby
to edit its skeleton and Actions.

Old Moby secondary-bangle semantics, damage permutations, authored lower
LODs, and Moby collision are not yet independently verified. `collision.dat`
also uses a separate non-IGHW level layout and is not imported. Foliage
branch meshes, sprite cards, and placements are imported from the first
authored LOD with masked, double-sided materials. Shrub meshes and clustered
placements are imported from the embedded old-engine layout. Runtime
vegetation wind, packed Shrub vertex metadata, Foliage custom normals, and
camera-facing billboarding are not reproduced. Lighting, VFX, cubemaps, and
audio remain outside the current level importer.

## Resistance: Burning Skies (experimental)

Burning Skies render packets import as static bind-pose geometry; skin
weights and animation are not decoded. Seventeen resources without render
packets expose valid bind matrices and import as armature-only assets. Six
geometry-less resources contain no recoverable skeleton.

Vita PVRTC-II 4bpp textures are decoded, including hard-transition palettes
and punch-through alpha. Burning Skies normal maps are direct tangent-space
RGB data rather than the reconstructed R2/R3 encoding. Dedicated gloss maps
drive inverted Principled roughness, and authored emissive maps use the BMX
color and strength parameters. Separate alpha maps are preserved but remain
disconnected unless the BMX also identifies a supported translucent or
masked render state.

The serialized `0x00020500` external-reference table now provides an exact
mesh-entry-to-BMX dependency map for every one of the 798 GBuffer SMFs in
the tested archive. This includes repeated material slots in destructible
physics models and non-leading slots such as the first-person axe.
Five debug, marker, or environment models reference seven BMX identifiers
that are not present in the archive; they report the missing dependencies and
remain geometry-only.

The authored multi-LOD table is decoded with its full-detail group as LOD 0.
Lower groups are disabled by default and require the **Lower LODs** import
option. The tested archive also contains 154 shadow-only packets and two
`WorldCollision` packets; these are excluded from the visible base mesh and
require **Collision geometry**. Other native collision resource families are
not decoded.

## Resistance 3

### Additional shader slots

Resistance 3 shaders contain six texture references. The first four use the
verified diffuse, normal, packed, and detail roles shared by the material
bridge. The six signed map indices determine which references are active;
unused slots can retain stale nonzero IDs. The raw references, paths, indices,
and active-binding state are preserved as material custom properties.

A whole-disc scan found the fifth slot bound by 967 of 4,579 unique shaders.
Its common paths identify runtime effects such as gradient dissolve, fire,
frozen overlays, pickup sheen, and weapon upgrades rather than one universal
surface channel. The sixth slot is bound by only four shaders. Neither slot is
connected until its per-family composition is independently verified.

### Materials

The 27-level R3 corpus contains 3,916 bound normal slots, and all 3,916 match
control bit `0x08`; 3,892 use BC3 and 23 use RG8. Standard R3 imports therefore
reconstruct and connect these tangent normals. One source shader,
`dark_cable/dark_cable`, binds a BC1 diffuse `*_c` texture as its normal; that
unsupported outlier is reported and left disconnected rather than producing
an invalid tangent vector.

All 3,232 shaders with control bit `0x04` bind a packed third map. Standard R3
materials invert its red gloss channel into Principled roughness. The legacy
specular bit `0x02` does not occur in any of the 4,579 unique shaders, so
packed green remains disconnected. Packed alpha varies in some BC3 maps but
its role is not proven. R3 uses a legacy specular/gloss workflow; no source or
corpus evidence identifies a metalness channel, so Metallic remains zero.

Packed blue is a scalar emission mask. Low material-control-byte bit `0x08`
disables it, as verified by exact on/off shader pairs sharing the same packed
texture. Enabled R3 emission uses authored albedo and the valid base-color
factor for its color. No separate source strength scalar has been identified;
the normalized blue mask drives intensity at Principled strength `1.0`.

Detail maps are often signed multi-channel data and likely use values from the
material parameter block for scale and blending. Their exact composition is
not yet proven. Bound maps are preserved as unconnected Non-Color image nodes
instead of being blended speculatively.

R3 Tie primitives select their shader group with byte `+0x26`. With that
game-specific selector, the source-authored
`defaults/default_pink_shader/default_pink_shader` material is limited to the
faces that actually reference it. R3's instance of that placeholder contains
an opaque gray-grid texture, unlike R2's magenta texture; it is retained on
those remaining source faces rather than globally replaced.

Blend mode 1 is used by several deferred decal and surface-overlay families,
but its exact source framebuffer equation is not named by the available
public tools. The Goliath wet-leg shader `0x5966249FD0D6149B` uses an opaque
BC1 overlay map, so ordinary alpha blending incorrectly replaces the base
feet. It has a narrowly scoped luminance-coverage approximation that
preserves the underlying leg plus the overlay's verified normal and gloss.
Other mode-1 shaders retain their existing source-alpha behavior until their
families can be validated independently.

### Character body/head assembly

R3 entity files inspected for the Ravager and Capelli do not contain direct
references to a separate companion Moby. The Ravager head is internal and can
be revealed with **Show source-default character parts**. Capelli body and
head assets are separate, have no embedded pairing, and use incompatible bone
indices and bind skeletons. They are not automatically combined until a
validated attachment profile can identify both the intended companion and its
animation relationship.

Placed gameplay Mobys use their class default draw mask, restoring intact
source-default parts such as Warden Jeep wheels and body panels. Per-instance
scripted draw-mask changes and multiplayer outfitter selections are not
decoded, so placements that change parts at runtime retain the class default.

### R3-specific animation playback

Resistance 3 uses the shared V2 animset structures with a separate playback
profile. The reader applies logical-frame remapping, rebases non-delta
positions to the target skeleton, preserves direct animated overlay
rotations, honors blend masks, and applies `0x0200` bind-relative position and
scale deltas.

Runtime partial/additive clips do not contain a complete standalone pose.
When the animset provides an unambiguous compatible base, the reader samples
that base by source time and bakes the overlay onto it. This covers the
Ravager's aiming, hit, attack, roar, and weapon overlays using
`ravager_r3_idle_sighted`, as well as known weapon-idle and head-idle naming
patterns. The resulting Action records its base in
`insomniac_overlay_base`.

The reader deliberately does not choose an arbitrary idle when multiple
bases are plausible. An unmatched additive Action therefore still represents
only the available layer and can require a manually selected locomotion or
idle base for an exact in-game result.

### Level rendering limitations

Resistance 3's game-specific foliage sprite cards and placements are
imported, but runtime wind, custom normals, and camera-facing billboarding
are not reproduced. UFrag terrain imports its packed per-vertex light,
secondary UV, and used `0x5400` baked zone lightmaps from the matching
`lighting.dat` asset. R3 Ties likewise import packed vertex light, optional
UV2, and per-instance `0x5400` lighting. Tie maps use a level-wide atlas
downsampled by four in each dimension to preserve mesh sharing and avoid
thousands of Blender images and materials. The parallel `0x5410` shadow/directional texture and dynamic scene lighting
are not composed. Shrub meshes and placements use the shared R2 layout and
are imported, but their packed vertex metadata is not decoded. Level
collision, VFX, and cubemaps are not imported. Terrain, Tie placement, and
gameplay Moby placement are supported.

## Resistance 2

### Shrubs and foliage

Foliage `0xA000` sprite buffers, `0xA200` descriptors, and
`0x7400`/`0x7440` placements are imported with shared mesh datablocks and
masked materials. The first authored LOD is expanded into static cards.
Shrub `0xB300`/`0xB400` meshes and `0x7500`/`0x7540` placements are likewise
imported with shared mesh datablocks and their source masked materials.
Runtime wind, packed shrub vertex metadata, custom normals, and camera-facing
foliage billboarding are not reproduced.

### Gameplay Mobys are static in level imports

An entire level can contain many copies of dynamic assets. Level import shares
mesh datablocks and applies placement transforms, but does not duplicate an
armature and Action library per gameplay instance. Import the individual Moby
to edit its rig and animation set.

### Animation overlays

Ordinary R2 clips, packed clips, quantization shifts, and additive composition
are decoded. Additive subset controls use the full skeleton layout, and blend
masks keep bones outside an overlay at their underlying pose. Some weapon/fire
and hit clips were authored for runtime layering over a locomotion or idle
Action. They are composed over the bind pose for standalone Blender playback,
so their upper-body pose may not match the final in-game layered result until
base-clip associations are documented.

### Root motion and animation metadata

The animation header's root-motion pointer is not decoded. It is zero in the
2,585 clips sampled from four available level caches, so no exercising R2
asset is available for an independent implementation. Linear-speed metadata
is present in many headers but is not applied or exposed on Blender Actions.

### Bone names

This R2 chain does not expose a reliable bone-name table. Blender bones use
stable `bone_NNN` names and retain the original index/flags as properties.

### Materials

The default material mode supports diffuse textures, reconstructs BC3 and RG8
tangent-space normals, and uses verified channels from R2's packed third map:
red gloss becomes roughness and nonzero blue masks the corresponding diffuse
color into emission. The default-off **Enhanced source materials** preference
additionally applies safe source color factors. Source alpha modes are handled
in both paths. The preference affects future imports only and does not change
the scene render engine.

BC3 albedo alpha is sampled as an independent packed channel. This preserves
RGB under zero-alpha texels for opaque materials while still exposing the
same alpha channel to authored mask and blend modes.

Packed green remains disconnected because the public `useSpecular` bit is
absent from every shader in the whole-disc corpus. Detail maps are
multi-channel in most tested cases; their exact blend and scale semantics are
not proven, so their imported Non-Color image nodes remain disconnected.
Foliage uses a distinct material-vector family. Its shader textures remain
available, but the unsafe family-specific vectors are not interpreted as
ordinary surface color controls.

The source of any additional emission tint and intensity within the six
`0x5000` material vectors is not yet proven. Emissive color therefore uses the
verified diffuse hue and packed-blue mask at strength `1.0`; the raw material
control word and vectors are preserved as Blender custom properties for later
refinement.

Shader `0x19ECCFD6FFE54E65` is R2's source-authored default pink placeholder.
Its magenta texture is present and correctly decoded, but the game data does
not identify the materials the affected asset faces were intended to use.
The importer preserves and labels it rather than applying a speculative
global replacement.

Some shaders contain stale texture IDs even though another shader references
the intended payload through the same authored lookup path. The importer
accepts that path fallback only when all available candidates decode
identically; conflicting candidates are reported as ambiguous instead of
being guessed.

### Cross-level texture references

The global per-TUID texture layout is supported when the global archives are
available beside an extracted `packed/levels` tree. Some multiplayer/co-op
level shaders reference texture IDs that are instead stored in a different
level archive. Already-extracted sibling level pairs are discovered
automatically. The ISO preparation operator still extracts only the selected
level and global pairs because dependency ownership is not encoded in the
selected level. If a required sibling has not been extracted, the importer
reports the reference and uses a deterministic placeholder material.

### Asset Browser cache scope

Asset Browser generation caches the Mobys, Ties, supported Shrub and Foliage
assets, and verified Materials referenced by one selected level and game at a
time.
Rebuild additional levels to add their assets. R2/R3 global TUIDs resolve to
one canonical game-wide entry even when multiple generated levels reference
them; RFoM/TOD identities remain level-scoped because their numeric IDs
repeat.
Level terrain and placed-instance scenes remain direct imports rather than
Asset Browser entries. Moby Actions can be included as standalone assets
under per-Moby catalogs, but are disabled by default because they
substantially increase generation time and cache size. An Action remains
skeleton-specific; use it with the source Moby rig identified in its metadata,
or use **Import One Animation...** to create the compatible rig automatically.

Cache generation remains selected-level only; there is no full-disc batch
queue. The modal worker reports progress and supports cancellation before its
atomic commit. Older schema-1 R2/R3 caches are migrated one level at a time
when those levels are regenerated.

Generated models, packed textures, and previews are local derivatives of the
user's game data. They are intentionally excluded from extension packages and
must not be redistributed.

### Import catalog labels

R2 stores a source entity path in Moby section `0xD200`, Tie section `0x3410`,
and Shrub section `0xB700`. The bundled USA catalog includes 4,207 named
Mobys, Ties, and Shrubs on the tested disc, plus 26 deterministically named
Foliage assets and 3,355 Materials. Selector labels turn the final path
component into a readable display name, while the full source path, kind,
TUID, and owner levels remain in the tooltip.

These are internal source names, so abbreviations, duplicate basenames, and
development-era labels remain. Existing schema-1 local catalogs without the
optional `names`, `shrubs`, `foliage`, or `materials` fields remain valid and
use deterministic TUID labels for unnamed assets until regenerated from an
ISO.

The sidebar asset search popup lists every asset that passes the kind, level,
and path filters, sorted by name. Blender's search popup matches the typed
text against labels only; use the sidebar path filter to search source paths
or TUIDs. Duplicate-name suppression defaults on and compares readable basenames after filtering; the
USA catalog has three such collisions but no duplicate complete self-paths.
Disable the preference to expose every TUID. TUID and owner-level label
suffixes are also optional and default off.

### Blender platform coverage

Package installation, registration, meshes, UVs, standard and enhanced
materials, packed images, armatures, weights, Action slots, terrain,
placements, save, and reopen were validated with Blender 5.2.2 LTS on
Windows. Resistance 3 validation includes a textured animated Moby, 18
Actions, a full campaign level, and the global texture bundle. A
representative Resistance 2 advanced material also compiled in an isolated
Cycles render without changing the imported scene's engine. Linux, macOS, and
later Blender releases have not yet been exercised.
