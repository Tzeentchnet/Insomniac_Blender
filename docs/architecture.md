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

Selecting a level archive opens its cached/uncached pair and, when present in
the standard disc hierarchy, the global pair. Global texture bundles are
indexed by their resource hashes using bounded slice reads; large texel files
are not loaded merely to build the index.

Selecting a decrypted R2 ISO resolves through the same source path after the
selected level/global archive pairs are extracted. A complete extraction is
reused, so inspect and import operators can accept the ISO directly without
repeating the copy.

The sidebar selectors are backed by a metadata-only disc catalog. The bundled
USA catalog records level IDs and Moby/Tie TUID ownership, but no geometry,
textures, or archive payloads. A selected asset first resolves against its
manual source override, then extracted owner levels, then the configured ISO.
Level selection follows the same cache-first resolution. A background catalog
worker can replace the bundled catalog with a locally generated catalog for a
different disc.

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
- Morton-unswizzled texture arrays.

## Blender bridge

Game coordinates remain untouched inside an imported hierarchy. When axis
conversion is enabled, one root Empty rotates the complete hierarchy from
Y-up game space to Blender Z-up space. This avoids applying slightly different
coordinate conversions to geometry, bind matrices, animations, and placement
matrices.

Meshes combine an asset's primitives into one datablock while retaining
material indices. Level placements reference shared mesh datablocks.

R2 materials retain their shader lookup paths, raw material control word, six
parameter vectors, texture IDs, and texture paths as Blender custom
properties. Diffuse images are color data. Reconstructed normal and packed
auxiliary images are non-color data.

The default material path remains lightweight: diffuse plus verified packed
blue-channel emission. A default-off Addon Preference selects an advanced
path for future imports. That path applies safe source color factors,
reconstructs R2 normals with NumPy, converts verified red-channel gloss to
roughness, and follows source alpha modes. It does not change the scene render
engine. Unverified packed-green and detail-map semantics remain disconnected.

Moby skeletons use stable `bone_NNN` names because R2 does not provide a
reliable bone-name table in this chain. The original index and flags are
stored as custom properties.

Animations are decoded to local translation/quaternion/scale samples, applied
to the armature in hierarchy order, and baked into Actions. Source FPS,
looping, and additive flags are stored on each Action. Blender 5.2 keyframe
insertion creates one compatible Action slot for each imported Action.

## Asset Browser cache

The Asset Browser cache is generated locally from a user-provided ISO. A
background factory-startup Blender process imports each selected-level Moby
and Tie independently, marks its containing Collection as an asset, generates
an optional preview, and writes that Collection plus dependencies to a
compressed `.blend`. One asset per file bounds memory use and lets Blender
index the cache incrementally.

Generation occurs in a staging directory. The prior level directory is
replaced only after every selected asset and its manifest have been written.
Catalog UUIDs are deterministic and catalog entries are merged without
discarding unrelated library catalogs. The main Blender process only
registers the generated library in Append mode and is never used as
generation scratch space.

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
