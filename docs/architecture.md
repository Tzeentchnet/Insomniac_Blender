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
geometry or archive payloads. The R2 catalog additionally stores compact
texture ownership metadata and payload digests, never texel data. A selected asset first resolves
against its manual source override, then extracted owner levels, then the
active game's configured ISO. Level selection follows the same cache-first
resolution. A background catalog worker can replace a bundled catalog with a
locally generated catalog for a different disc.

Catalog identity is adapter-driven. R2 and R3 retain globally meaningful
TUIDs, while RFoM and Tools of Destruction include the owner level in each
catalog identifier because their Moby IDs/indices, Tie and vegetation
offsets, shader indices, and texture record offsets are level-local.

If an R2 shader's exact texture ID is absent from the selected and global
domains, the adapter consults the catalog's ID/path-to-owner index. Descriptor
and SHA-256 metadata reject conflicting candidates before extraction.
Byte-identical duplicates are collapsed with a greedy owner cover, then only
those sibling cached/uncached pairs are materialized inside the selected
verified cache. Existing verified owner caches are hard-linked when possible.
`GameSource` exposes only sibling archives named by that cache's provenance.
The reader still compares duplicate decoded pixels if more than one available
archive contains an ID.

`BinaryView` and `IghwFile` bounds-check every access and preserve the
container's detected endianness.

Level collision crosses adapters through a game-neutral `CollisionScene`.
It separates reusable mesh prototypes from placed instances, carries affine
transforms and source groups, and preserves mesh/instance metadata plus
per-face surface and material IDs. Shared validation rejects duplicate or
missing prototype IDs, malformed triangle arrays, out-of-range indices,
non-finite positions/transforms, and unsupported metadata values before the
Blender bridge creates any datablocks.

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

TOD's separate big-endian `collision.dat` is a raw, non-IGHW resource. Its
header points to one exact `0x80`-byte record table and a shared prototype
blob. The dedicated backend validates those ranges, decodes marker-1
quantized triangle prototypes, converts local positions and translations
from yards to meters, and emits the same game-neutral collision scene used
by R2/R3. Flag-0 records are world collision; flag-`0x20` records carry
transforms that match `main.dat:0x9240` Tie placements. The low 24 packed
bits are retained as a per-Tie collision-component index rather than
misreported as an asset identity.

## Resistance: Fall of Man adapter

`ResistanceFomReader` shares typed geometry, skeleton, animation, texture,
and Blender-facing outputs with the other PS3 readers but owns its V1 record
layouts. The selected level directory contains `ps3levelmain.dat`,
`ps3levelverts.dat`, raw `ps3leveltexs.dat`, `ps3levelcoll.dat`, and
`ps3gameplay.dat`.

Mobys use class IDs from their `0xC0` headers and `0x20`-byte primitive
records. Their geometry and texture pixels share the raw texel file. Ties and
`0x1A`-stride region terrain instead address IGHW vertex/index sections.
Materials use section `0x5001`, textures use `0x5300`, and Tie placements use
`0x9300`.

RFoM inline animations use a dedicated profile without the Tools of
Destruction 8-bit delta shift or pair-frame reduction. Its IGHW 0.2 gameplay
reader exposes the primary Moby placements plus six typed contextual groups:
paths, trigger volumes, matrix-backed effects, typed transform markers, Moby
groups, and named areas. Only the primary records use renderable Moby class
IDs. The other groups become non-rendering curves or empties with their
source indices, class selectors, flags, memberships, and payload sizes
preserved as Blender metadata.

RFoM collision uses the same raw header/record family as TOD but a distinct
prototype profile. Its vertices are array-of-structures `u16` triples after
a signed base at `0x50`, rather than TOD's grouped SIMD-oriented streams.
The backend validates world, Tie, and collision-only placed families,
preserves their 16-bit source and 12-bit component indices, converts yards
to meters, and emits the shared collision scene. The collision-only placed
family remains separate because its external gameplay owner has not been
proven.

## R2/R3 level collision

The shared R2 reader discovers every regional `collision.dat` beneath the
selected level prefix. Each file is a big-endian IGHW 1.1 container. The
backend bounds all reads to section `0x2900`, validates the aligned Tie TUID
table, the `0x80`-byte instance table, and the prototype blob, then emits the
game-neutral collision scene. R3 inherits this backend.

Records with flag byte `0x00` reference terrain prototypes whose quantized
positions are already in world space. Flag `0x20` records select a Tie TUID
and carry the serialized Tie linear transform and translation. Prototype
offsets are stable within one regional resource, so every repeated reference
uses one shared collision mesh. The backend decodes compact quantized
triangles and the verified SIMD box, sphere, capsule, and cylinder groups.
Index-degenerate, zero-area, and duplicate source faces are omitted before
the scene reaches Blender; source and discarded counts remain mesh metadata.
All-degenerate no-op prototypes are skipped with one explicit warning per
regional resource.

Sections `0x8200` and `0x8300` are not needed to recover the authored
collision geometry. They remain unclassified spatial/runtime data and are not
used as a speculative fallback.

## Blender bridge

Game coordinates remain untouched inside an imported hierarchy. When axis
conversion is enabled, one root Empty rotates the complete hierarchy from
Y-up game space to Blender Z-up space. This avoids applying slightly different
coordinate conversions to geometry, bind matrices, animations, and placement
matrices.

Meshes combine an asset's primitives into one datablock while retaining
material indices. Level placements reference shared mesh datablocks.

Level-collision instances are sorted by source group and identity before
creation. Each group receives a deterministic non-rendering child Collection,
and every instance of one collision prototype references the same Blender
mesh datablock. Surface and material IDs become face-domain integer
attributes; source IDs and JSON-encoded adapter metadata remain available as
custom properties. Collision objects are wire helpers and never participate
in renders.

Materials retain their shader lookup paths, raw material control word,
material-program identity, parameter vectors, texture IDs, texture paths, and
texture-map indices as Blender custom properties. The indices distinguish
active bindings from stale or runtime-only reference fields. R3's two
additional texture slots, two pre-control words, and three trailing material
values are retained without assigning global rendering semantics. Diffuse
images are color data.
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
from the current R2 corpus.

Standard R2/R3 detail composition has an equally strict gate: an active detail
slot, control bit `0x10`, texture-address bit `0x1000` for the RSX biased
signed remap, a finite vector-4-Z UV scale, a bound packed map, and no custom
R3 material program. The detail sampler is tiled from the primary UV and each
8-bit channel is recentered as approximately
`(round(channel * 255) - 128) / 127`. Packed alpha masks all four channels.
Detail R/G perturb the reconstructed tangent normal, B multiplies diffuse as
`base * (1 + B)`, and A multiplies packed-red gloss as
`gloss * (1 + A)`. The original vertex program also supplies a runtime
distance fade beside the scaled UV; that value is not serialized, so Blender
uses a documented unit fade and does not invent a camera-distance function.
Records outside the exact gate retain an unconnected Non-Color detail node and
an explicit data-only reason.

The verified R3 frozen family is an explicit additional-program exception:
the reader requires the frozen program identity, active fifth slot, exact
effect path, control byte, frozen shader name, and embedded program sections.
Its graph samples the effect map with the camera-space shading normal, applies
the decompiled blue and grazing-angle response, screens that result over base
color, and lowers roughness under the same coverage. Packed-alpha meanings
outside the strictly gated detail path and all other R3 overlay families
remain disconnected.

The R3 reader also identifies the gradient-dissolve family from its exact
program identity, fifth-slot path, control byte, authored suffix, and four
embedded program descriptors. The Blender bridge preserves that family and
program metadata but does not connect its texture. Decompiled variants depend
on a vertex-stage/runtime transition value that is absent from the serialized
material, so inventing a static dissolve amount would change normal imports.
The on-fire family follows the same metadata-only boundary. Its exact program
identity, authored suffix, two verified fifth-slot paths, and three embedded
program descriptors are classified, but its decompiled dual-sample projection
depends on a runtime animation phase absent from the material. Blender does
not freeze that animation at an invented phase or load the disconnected map.
The `flash_002` program family follows that boundary as well. Program and
texture identity classify pickup sheen, journal, sketchbook, pickup-body, and
shield specializations without relying on a shader-name suffix. Its moving
sweep coordinate is runtime state, so Blender preserves `R3_FLASH_OVERLAY`
metadata while leaving the fifth slot unloaded.
The weapon-upgrade family similarly requires its exact program, active
`weaponupgrade_c` path, authored upgrade suffix, and three embedded program
descriptors. Decompiled specializations sample the map twice with fixed
`0.75` and `0.15` factors, but zero-valued inline constant placeholders feed
the sample offset and effect response. Those draw-time values are absent from
the material, so Blender preserves `R3_WEAPON_UPGRADE` metadata without
loading or freezing the overlay.
Decoded RGBA reader caches are cleared after each material while compact
format/emission analysis and Blender image references remain cached for reuse.

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

The shared mesh representation also carries optional R2/R3 Shrub position-W,
height-weight, and runtime-remap arrays. The Blender bridge writes them as
point-domain attributes named `insomniac_shrub_position_w`,
`insomniac_shrub_height_weight`, and `insomniac_shrub_runtime_remap`.
They preserve verified source data for later vegetation work but do not alter
positions, normals, or materials. Runtime wind remains deliberately separate
from static mesh import.

R2/R3 Foliage geometry likewise carries optional repeated card-center,
card-offset, decoded normal, and two-word runtime arrays. The Blender bridge
writes `insomniac_foliage_center`,
`insomniac_foliage_corner_offset`, `insomniac_foliage_normal`,
`insomniac_foliage_runtime_word_0`, and
`insomniac_foliage_runtime_word_1` point attributes. The decoded
`R11G11B10` signed-normalized vectors also become Blender custom split
normals. Centers and offsets remain separate so a future camera-aware
billboard system does not need to reverse the static expansion. The raw
runtime words remain intentionally uninterpreted, and
`insomniac_foliage_runtime_billboarding` is false because import does not
invent or bind a scene camera.

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
