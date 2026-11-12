# Known issues

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
are decoded. Some weapon/fire clips were authored for runtime layering over a
base idle. A standalone Blender Action may not match the final in-game layered
result until the corresponding base-clip association is documented.

### Bone names

This R2 chain does not expose a reliable bone-name table. Blender bones use
stable `bone_NNN` names and retain the original index/flags as properties.

### Materials

The default material mode supports diffuse textures and blue-channel emission
masks from R2's packed third map. The default-off
**Cycles-ready Resistance 2 materials** preference additionally applies safe
source color factors, reconstructs BC3 and RG8 tangent-space normals,
converts flagged packed-red gloss to roughness, and follows source alpha
modes. It affects future imports only and does not change the scene render
engine.

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
one selected level at a time. Rebuild additional levels to add their catalogs.
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

Package installation, registration, meshes, UVs, standard and Cycles-ready
materials, packed images, armatures, weights, Action slots, terrain,
placements, save, and reopen were validated with Blender 5.2.2 LTS on
Windows. A representative advanced material also compiled in an isolated
Cycles render without changing the imported scene's engine. Linux, macOS, and
later Blender releases have not yet been exercised.
