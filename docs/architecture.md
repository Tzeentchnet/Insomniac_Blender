# Architecture

## Principle: adapters, not a universal binary parser

Insomniac games reuse concepts—resource hashes, models, materials, zones—but
their serialized layouts change across engine eras and sometimes across games
on the same broad engine family. The extension therefore separates:

1. **Core transport**: PSARC, bounded byte views, source overlays, ISO helper.
2. **Game adapter**: exact archive/resource/file structures and quirks.
3. **Blender bridge**: meshes, images, materials, rigs, Actions, and instances.

The Blender bridge consumes typed data. It does not seek into game files or
guess which game produced a buffer.

## Core

`GameSource` overlays either:

- recursively extracted files; or
- one or more PSARC manifests.

Resource lookup is case-insensitive. Exact paths win; ambiguous suffix
lookups fail rather than choosing an arbitrary level. PSARC reads operate on
individual compression blocks, so a Moby pointer can read a small slice from
a large `mobys.dat` entry.

Selecting a level archive opens its cached/uncached pair and, when present,
the level texture archive and standard global pair. Global texture bundles
are indexed by their resource hashes using bounded slice reads; large texel
files are not loaded merely to build the index.

Selecting a supported decrypted PS3 ISO resolves through the same source path
after the adapter-declared archive set is extracted. A bounded internal
ISO9660 reader handles ordinary images and R3's hybrid image; `tar` remains a
fallback. Adapters can require exact `PARAM.SFO` title and version values.
A complete game-specific extraction is reused, so inspect and import
operators can accept the ISO directly without repeating the copy.

The ISO reader also exposes a bounded seekable view over one file's ordered
extents. `PsarcArchive` accepts that view as well as an ordinary disk file.
The RFoM adapter uses this path to parse the root `GAME.PSARC` in place and
decompress only selected level entries. Catalog preparation can declare a
smaller metadata-only resource set than interactive imports.

The sidebar selectors are backed by per-game metadata-only disc catalogs. The
bundled USA catalogs record level IDs, source names, supported
Moby/Tie/Shrub/Foliage identities, and verified shader identities, but no
geometry, textures, or archive payloads. A selected asset first resolves
against its manual source override, then extracted owner levels, then the
active game's configured ISO. Level selection follows the same cache-first
resolution. A background catalog worker can replace a bundled catalog with a
locally generated catalog for a different disc.

Catalog identity is adapter-driven. R2 and R3 retain globally meaningful
TUIDs, while RFoM and Tools of Destruction include the owner level in each
catalog identifier because their Moby IDs/indices, Tie and vegetation
offsets, shader indices, and texture record offsets are level-local.

If a shader texture is absent from both domains, the R2 adapter lazily indexes
already-extracted sibling `level_cached.psarc` files under the same
`packed/levels` tree. Only a matching archive's cached/uncached pair is opened
for payload decoding. Duplicate texture IDs must decode to identical metadata
and pixels or the lookup fails as ambiguous.

`BinaryView` and `IghwFile` bounds-check every access and preserve the
container's detected endianness.

## Resistance 2 adapter

`Resistance2Reader` selects the level `assetlookup.dat`, records its resource
prefix, and resolves all siblings relative to that prefix. Asset pointer
tables are cached by kind. Individual assets are read by `(offset, length)`
from their companion `.dat` resource.

NumPy is used for:

- strided vertex and UV views;
- endian conversion;
- index buffers;
- skin palettes and weights;
- bind matrices;
- animation tracks;
- Morton-unswizzled texture arrays;
- shared vectorized BC1, BC2, and BC3 block decoding.

## Tools of Destruction adapter

`RatchetTodReader` is isolated from the R2/R3 pointer-table reader. It loads
the selected level's `main.dat` metadata and uses bounded slices from
`vertices.dat`, `textures.dat`, and `texstream.dat`. Mobys are addressed by
their local table index; Ties use the byte offset of their `main.dat` header.
Embedded Shrubs likewise use the byte offset of their `0xB100` descriptor.
Mesh shader indices are localized before entering the shared Blender bridge,
so an import only builds materials it actually uses.

TOD animations are inline in `main.dat`. They reuse the shared typed
animation output but have a dedicated playback profile: signed 8-bit deltas
are shifted left by two before adding their 16-bit bases, frame strides are
16-byte aligned, and simple zero-8-bit-track clips discard alternating filler
records and play at half the disk rate. No bind-pose fallback is used for
compressed clips.

The old level layout has one zone. Its UFrag terrain, Tie placements, and
clustered Shrub placements are decoded from `main.dat` plus the level
vertex/index sections; `gameplay.dat` provides one `art` region of placed
Mobys.

## Resistance: Fall of Man adapter

`ResistanceFomReader` shares typed geometry, skeleton, animation, texture,
and Blender-facing outputs with the other PS3 readers but owns its V1 record
layouts. The selected level directory contains `ps3levelmain.dat`,
`ps3levelverts.dat`, raw `ps3leveltexs.dat`, and `ps3gameplay.dat`.

Mobys use class IDs from their `0xC0` headers and `0x20`-byte primitive
records. Their geometry and texture pixels share the raw texel file. Ties and
`0x1A`-stride region terrain instead address IGHW vertex/index sections.
Materials use section `0x5001`, textures use `0x5300`, and Tie placements use
`0x9300`.

RFoM inline animations use a dedicated profile without the Tools of
Destruction 8-bit delta shift or pair-frame reduction. The adapter exposes
only the corroborated primary gameplay Moby group; unknown secondary groups
remain unparsed.

## Blender bridge

Game coordinates remain untouched inside an imported hierarchy. When axis
conversion is enabled, one root Empty rotates the complete hierarchy from
Y-up game space to Blender Z-up space. This avoids applying slightly different
coordinate conversions to geometry, bind matrices, animations, and placement
matrices.

Meshes combine an asset's primitives into one datablock while retaining
material indices. Level placements reference shared mesh datablocks.

Materials retain their shader lookup paths, raw material control word,
parameter vectors, texture IDs, texture paths, and texture-map indices as
Blender custom properties. The indices distinguish active bindings from stale
or runtime-only reference fields. R3's two additional texture slots, two
pre-control words, and three trailing material values are retained without
assigning speculative rendering semantics. Diffuse images are color data.
Reconstructed normal, packed auxiliary, dedicated scalar, and detail images
are non-color data. Blender 5.2 creates these datablocks with `is_data=True`
so their colorspace is correct before pixel upload. Authored lookup paths and
the active reader's level-local scope participate in image reuse, preventing
equal 32-bit IDs from aliasing unrelated textures.
Packed blue gates the corresponding diffuse color into emission for both
games, preserving authored hues while suppressing all-zero masks.

Standard R2 and R3 materials reconstruct verified BC3/RG8 tangent normals with
NumPy and convert flagged packed-red gloss to Principled roughness. These
nodes work in Eevee and Cycles. A default-off **Enhanced source materials**
preference additionally applies safe source color factors; it does not change
the scene render engine. No metalness channel is inferred. Packed green is
connected only when the shader's specular flag is present; that flag is absent
from the current R2 corpus. Bound detail maps are exposed as unconnected
Non-Color image nodes; unverified packed-alpha, detail composition, and R3
overlay semantics remain disconnected. Decoded RGBA reader caches are cleared
after each material while compact format/emission analysis and Blender image
references remain cached for reuse.

Moby skeletons use stable `bone_NNN` names because R2 does not provide a
reliable bone-name table in this chain. The original index and flags are
stored as custom properties.

Animations are decoded to local translation/quaternion/scale samples, applied
to the armature in hierarchy order, and baked into Actions. Source matrix
accumulation honors the skeleton's scale-inheritance flags, and sampled keys
use linear interpolation. Source FPS, looping, and additive flags are stored
on each Action. Blender 5.2 keyframe insertion creates one compatible Action
slot for each imported Action. The Action synchronization handler binds that
slot when the user selects an imported clip, then updates the scene playback
range to the clip bounds.

A Moby can also expose a clip picker. Importing one selected clip creates its
source skeleton and one compatible Action without creating mesh geometry.
Every Action records the game, source Moby identity and name, clip index, and
clip name as custom properties.

## Resistance 3 adapter

`Resistance3Reader` inherits the verified common PS3 geometry, animation,
texture, zone, and placement chain while overriding exact game-specific
constants. R3 shader records contain six texture references instead of four,
shift the material control word and parameter vectors, and use an additional
collision quantization marker. The R2 reader retains its original strict
validation.

The Blender bridge reads game identity, display labels, datablock prefixes,
and engine metadata from the active adapter. Import options remain shared;
ISO paths, extraction roots, local catalogs, and Asset Browser paths are
separate per game.

## Asset Browser cache

The Asset Browser cache is generated locally from a user-provided ISO. A
factory-startup Blender subprocess imports each supported selected-level
Moby, Tie, Shrub, or Foliage model independently, marks its containing
Collection as an asset, generates an optional preview, and writes that
Collection plus dependencies to a compressed `.blend`. The UI operator polls
JSON progress from a modal timer, leaving Blender responsive; cancellation
terminates the worker while all output is still staged. When requested,
decoded Actions in the same file are marked under a deterministic per-Moby
Animation catalog.

Asset identity follows the game adapter. R2 and R3 TUIDs are global, so
schema-2 manifests point every owning level at one canonical file under the
game's `_shared` directory and one game-wide catalog entry. RFoM and TOD IDs
repeat between levels, so their files, logical IDs, and catalogs remain
level-scoped. Input pointer tables are de-duplicated by TUID before generation.
Verified shaders are standalone Material assets; global games use one
canonical file per shader, while level-local games can keep their Materials
together in one level file.

Generation occurs in a staging directory. A commit first backs up every
replaced or deleted path, installs the generated files, rewritten manifests,
and rebuilt catalog, and only then deletes the backup. Any exception restores
the prior files. Catalog UUIDs are deterministic; catalogs are reconstructed
from committed manifests so stale generated categories disappear, while
paths outside the supported game namespaces are preserved. The main Blender
process only registers the generated library in Append mode and is never used
as generation scratch space.

No source-derived game asset is part of the extension package. The default
library registration points at the user's local cache and remains empty until
the user generates it from legally obtained game data.

## Adding a game

A new adapter should:

1. Define typed output structures or reuse common semantic structures.
2. Detect its own source layout.
3. Parse exact game layouts without weakening another adapter's validation.
4. Return mesh, rig, animation, material, and placement data to the Blender
   bridge.
5. Add format notes and test fixtures that contain no copyrighted assets.

See [contributing](contributing.md).
