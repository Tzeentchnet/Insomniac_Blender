# Known issues

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
not yet proven and they remain metadata-only.

Blend mode 1 is used by several deferred decal and surface-overlay families,
but its exact source framebuffer equation is not named by the available
public tools. The Goliath wet-leg shader `0x5966249FD0D6149B` uses an opaque
BC1 overlay map, so ordinary alpha blending incorrectly replaces the base
feet. It has a narrowly scoped luminance-coverage approximation that
preserves the underlying leg plus the overlay's verified normal and gloss.
Other mode-1 shaders retain their existing source-alpha behavior until their
families can be validated independently.

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

### Shared unsupported level resources

As with Resistance 2, shrub and foliage geometry, lighting, level collision,
VFX, and cubemaps are not imported. Terrain, Tie placement, and gameplay Moby
placement are supported.

## Resistance 2

### Shrubs and foliage

Zone records are counted, but shrub and foliage geometry is not imported.
They use separate resource kinds and vertex layouts rather than Tie geometry.
The level importer reports skipped counts.

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
red gloss becomes roughness and nonzero blue becomes emission. The default-off
**Enhanced source materials** preference additionally applies safe source
color factors. Source alpha modes are handled in both paths. The preference
affects future imports only and does not change the scene render engine.

BC3 albedo alpha is sampled as an independent packed channel. This preserves
RGB under zero-alpha texels for opaque materials while still exposing the
same alpha channel to authored mask and blend modes.

Packed green remains disconnected because the public `useSpecular` bit is
absent from every shader in the whole-disc corpus. Detail maps are
multi-channel in most tested cases; their exact blend and scale semantics are
not proven, so they also remain disconnected. Foliage uses a distinct
material-vector family, but foliage geometry is not imported yet.

The source of authored emission tint and intensity within the six `0x5000`
material vectors is not yet proven. Emissive masks therefore use neutral
grayscale at strength `1.0`; the raw material control word and vectors are
preserved as Blender custom properties for later refinement.

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

Asset Browser generation currently caches the Mobys and Ties referenced by
one selected level and game at a time. Rebuild additional levels to add their
catalogs.
Level terrain and placed-instance scenes remain direct imports rather than
Asset Browser entries. Moby Actions can be included, but are disabled by
default because they substantially increase generation time and cache size.

Generated models, packed textures, and previews are local derivatives of the
user's game data. They are intentionally excluded from extension packages and
must not be redistributed.

### Import catalog labels

R2 stores a source entity path in Moby section `0xD200` and Tie section
`0x3410`. The bundled USA catalog includes paths for all 4,130 unique assets
on the tested disc. Selector labels turn the final path component into a
readable display name, while the full source path, kind, TUID, and owner
levels remain in the tooltip.

These are internal source names, so abbreviations, duplicate basenames, and
development-era labels remain. Existing schema-1 local catalogs without the
optional `names` mapping remain valid and use the deterministic TUID labels
until regenerated from an ISO.

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
