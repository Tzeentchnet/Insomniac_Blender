# Insomniac Asset IO

Insomniac Asset IO is a Blender 5.2+ extension for game-specific Insomniac
asset pipelines. It intentionally does not force assets from different engine
generations through one parser. Each supported game owns its archive,
resource, geometry, animation, material, and level-placement chain behind a
shared Blender-facing interface.

The first adapter targets **Resistance 2 (PlayStation 3 / Insomniac Engine
v2)**.

## Resistance 2 support

| Feature | Status |
|---|---|
| PS3 zlib PSARC reading | Supported, including random entry slices |
| Direct decrypted ISO sources | Supported through a reusable local extraction cache |
| `assetlookup.dat` discovery | Supported |
| Moby models | Supported |
| Moby damage permutations | Optional; disabled by default and kept in a separate Collection |
| Moby primitive and triangle collision | Optional wire helpers; disabled by default |
| Moby skeletons and skin weights | Supported |
| Moby animsets | Supported, including additive subset and blend masks; clips become Blender Actions |
| Tie static meshes | Supported |
| USA model/level import catalog | 4,130 named assets and 27 levels bundled as metadata |
| Blender Asset Browser library | Locally generated Moby/Tie Collection assets |
| Diffuse textures and packed blue-channel emission masks | Supported |
| Reconstructed normals and packed red-channel gloss | Optional Cycles-ready material mode |
| DXT1/3/5 and common uncompressed textures | Supported |
| Zone Tie placement | Supported with linked mesh data |
| Zone terrain (`UFrag`) | Supported |
| Gameplay Moby placement | Supported as linked static mesh instances |
| Shrub and foliage geometry | Not yet implemented |
| Lighting, level collision, VFX, and cubemaps | Not yet implemented |

See [Resistance 2 formats](docs/formats/resistance_2.md) and
[known issues](docs/known_issues.md) for precise layouts and caveats.

## Install

1. Build an installable archive:

   ```powershell
   python tools\build_extension.py
   ```

   With Blender 5.2 installed, the canonical alternative is:

   ```powershell
   blender --command extension build
   ```

2. In Blender, open **Edit > Preferences > Get Extensions**.
3. Use **Install from Disk** and choose
   `dist\insomniac_asset_io-0.3.4.zip`.
4. Open the **Insomniac** tab in the 3D View sidebar.

Blender includes NumPy; no external Python package is required at runtime.

## Use

### Import a model or static mesh

1. In Add-on Preferences, select a decrypted Resistance 2 ISO and local
   extraction-cache directory.
2. In the **Insomniac** sidebar, click the asset button to open a scrollable
   chooser listing every asset alphabetically. Use its search field to find
   an asset by name, path, kind, TUID, or owner level.
3. Optionally narrow what the popup lists first. All three filters are
   optional and combine:
   - **All / Mobys / Ties** restricts the asset kind;
   - the level list restricts to assets used by one level;
   - the text field searches the complete source self-path, readable name,
     kind, TUID, and owning levels. Space-separated fragments are all
     required, so `entities objects architecture nb_lobby_skyline_1` resolves
     the matching Tie.
4. Click **Import Asset**.

A status line reports how many assets pass the filters, and its **X** button
clears them all. Filters only change what the search popup lists: the current
selection is kept, stays importable, and is flagged when it falls outside the
filters.

Sidebar source, filter, asset-selection, and level-selection choices persist
for the Blender session when creating or opening another file, including with
**File > New** (`Ctrl+N`). Transient progress and result messages reset with
the file.

The bundled USA catalog contains 4,130 unique importable TUIDs across all 27
disc levels. The importer reuses an extracted owner level when available and
otherwise extracts that level's cached/uncached and global archive pairs from
the configured ISO on demand. A manually selected PSARC or ISO remains an
optional source override.

Moby imports create the intact/base mesh, armature, weights, materials, and
every decoded animset clip as a persistent Blender Action by default. The rig
opens in its neutral rest pose; choose an Action when animation playback is
wanted. Actions retain their source-derived playback ranges, and the imported
pose bones start selected so Blender's default **Only Show Selected** filter
shows their keyframes immediately in the Action Editor. Selecting an imported
Action also updates the scene playback range, and the preview range when it is
enabled, to that clip's bounds. Sampled transform keys use linear interpolation
and respect source scale-inheritance flags.

Damage permutations and collision geometry are independently disabled by
default. Enabling them in Add-on Preferences puts each damage segment in the
asset's **Damage Permutations** child Collection and puts decoded box, sphere,
capsule, and triangle collision helpers in its **Collision Geometry** child
Collection. Collision helpers display as in-front wire geometry and are
excluded from renders.

### Import a level

Choose one of the 27 entries in the sidebar's **Level** list and click
**Import Level**. The extraction cache is checked first and the selected level
is extracted from the configured ISO when needed. Terrain is created per
zone. Tie and gameplay placements share mesh datablocks instead of duplicating
geometry.

Gameplay Mobys are intentionally placed as static linked meshes during a
level import. Import an individual Moby to edit its rig and animations.

### Use an ISO directly

The ISO source path calls a libarchive-compatible `tar` executable without
using a shell. Configure the default ISO in Add-on Preferences or select an
ISO as the optional sidebar source override, then use the catalog selectors.
The sidebar source also accepts a directory containing exactly one `.iso`
file. The importer extracts only the required level pair and global pair.
Complete existing extractions are reused. **Extract Selected Level Now**
remains available when an explicit PSARC path is useful for external tools.
When **Inspect Source** needs ISO extraction, the sidebar and status bar show
a busy message before processing starts; preparing the archives can take
several minutes.

The extension never modifies the ISO.

### Import defaults and catalog regeneration

Model/static-mesh and level options live in Add-on Preferences rather than
the sidebar:

- model materials, Moby animations, animation limit, axis conversion, and
  default-off damage-permutation and collision-geometry import;
- asset-selector duplicate suppression, TUID visibility, and owner-level
  visibility;
- level materials, terrain, static props, gameplay instances, and axis
  conversion;
- default-off Cycles-ready material processing.

Asset labels show only the readable source name by default, such as
`Mp Scientist Head`. **Hide duplicate asset names** defaults on and collapses
matching display basenames after the path filter is applied. TUIDs and owner
levels remain in tooltips and can be restored to labels in Add-on Preferences.

The extension ships a metadata-only USA catalog containing source entity
paths, level ownership, and TUIDs, not models, textures, or other game
payloads. **Regenerate Import Catalog from ISO** scans another legally
obtained disc into the local extraction cache and replaces the active
selector catalog. Catalog generation reads only IGHW headers, section tables,
and bounded name sections; it does not decode every mesh.

### Build the local Asset Browser cache

The extension registers an **Insomniac Assets** library with Blender's Asset
Browser. The library is initially empty because Resistance 2 models and
textures are copyrighted game data and are not distributed with the
extension.

To populate or regenerate it:

1. Open **Edit > Preferences > Add-ons > Insomniac Asset IO**.
2. Select your legally obtained decrypted Resistance 2 ISO.
3. Choose the local cache directory and disc level. `lobby` is the compact
   default cache.
4. Choose whether to cache materials, Moby animations, and previews.
5. Click **Regenerate Selected Level Cache**.

Generation runs in an isolated background Blender process, so it does not
alter the open scene. Each Moby or Tie becomes a marked Collection asset in
its own compressed `.blend` file under the local cache. Assets are cataloged
as `Resistance 2/<level>/Mobys` and `Resistance 2/<level>/Ties`, and the
library uses Blender's **Append** import mode. Rebuilding a level is
transactional: a failed generation leaves its previous cache intact.

Animations are off by default to keep the browser cache compact. Direct Moby
import continues to include every decoded Action by default.

### Choose the Resistance 2 material mode

The extension has two material paths:

- **Standard**, the default, imports diffuse color and verified blue-channel
  emission masks with a small node graph.
- **Cycles-ready** additionally applies the source base-color multiplier,
  reconstructs tangent-space normal maps, converts packed red gloss to
  roughness, and follows the source blend mode for alpha masking and blending.
  It also recognizes the verified mode-zero `ch_hex_glass_glowy` exception
  and uses its authored albedo alpha.

To enable the advanced path, open **Edit > Preferences > Add-ons**, expand
**Insomniac Asset IO**, and enable **Cycles-ready Resistance 2 materials**.
The preference defaults to off and affects future imports only. It does not
rebuild existing materials or change the active scene render engine.

Packed green remains disconnected because the corresponding R2 specular flag
is absent from every shader in the current whole-disc corpus. Detail maps are
also preserved as metadata rather than connected speculatively.

## Project layout

```text
insomniac_asset_io/
  core/                   Archive, source overlay, binary, and ISO helpers
  games/
    resistance_2/         Engine-v2 formats and decoders
  blender/                Operators, importer, and Asset Browser cache worker
docs/                     Format and contributor documentation
tests/                    Pure-Python unit and optional real-asset tests
tools/                    Packaging and smoke-test utilities
```

## Validation

Run the portable test suite:

```powershell
python -m unittest discover -s tests -v
```

Run a real extracted level smoke test:

```powershell
python tools\smoke_r2.py "C:\path\to\level_cached.psarc"
```

The smoke test reports resource counts and decodes one Moby, skeleton,
animset, diffuse texture, Tie, zone, and gameplay region without writing game
data to the repository.

Diffuse lookup first uses the level texture tables, then the global archive's
per-TUID texture bundles, then already-extracted sibling level pairs under the
same `packed/levels` tree. A missing/stale texture ID can also resolve through
another available ID with the identical authored lookup path, provided every
available candidate decodes to the same image. Ambiguous path matches remain
errors. References that remain unavailable are reported and receive a
deterministic placeholder material.

R2 also contains an authored `defaults/default_pink_shader` material. Its
magenta texture is real source data, not Blender's missing-image indicator.
The importer preserves it and marks the Blender material with
`insomniac_source_placeholder = true` and placeholder kind `DEFAULT_PINK`.

Resistance 2's third shader texture is packed material data, not a full-color
emissive image. Its blue channel is connected as emission only when that
channel contains nonzero texels. In Cycles-ready mode its red channel is
inverted into roughness when the shader enables gloss. Authored emission tint,
authored emission strength, packed green, and detail-map composition remain
under research.

Analyze one extracted cached archive, or every cached level archive in an ISO:

```powershell
python tools\analyze_r2_shaders.py "C:\path\to\level_cached.psarc"
python tools\analyze_r2_shaders.py "C:\path\to\game.iso" `
  --work-directory "C:\path\to\scan-cache"
```

Search an ISO for the level lookups that own one or more texture IDs:

```powershell
python tools\find_r2_textures.py "C:\path\to\game.iso" 0x1585EA44 0x6782D663
```

Run the installed extension inside Blender:

```powershell
& "C:\path\to\blender.exe" --background --python-exit-code 1 `
  --python tools\blender_smoke.py -- --help
```

`tools\blender_smoke.py` can prepare an ISO fixture, import and save a Moby or
level, and reopen a saved file to verify that Blender data survives
serialization. It validates UVs, materials, packed images, armatures, weights,
Blender 5.2 Action slots, keyframes and playback ranges, terrain, placement
counts, axis conversion, and shared mesh datablocks. It can also assert
reconstructed normals, conversion from gloss to roughness, source color
tints, blend modes, blue-channel emission, all-zero non-emissive packed maps,
and compile a representative material in a small Cycles render without
changing the imported scene's engine.

Run the synthetic Blender rig regression test without game data:

```powershell
& "C:\path\to\blender.exe" --background --factory-startup `
  --python-exit-code 1 --python tests\test_blender_rig.py
```

Validate every generated Asset Browser entry, catalog assignment, preview,
object dependency, and packed game image:

```powershell
& "C:\path\to\blender.exe" --background --factory-startup `
  --python-exit-code 1 --python tools\validate_asset_cache.py -- `
  "C:\path\to\InsomniacAssetIOCache\asset_library"
```

The current implementation was validated in Blender 5.2.2 LTS with:

- a 16,150-vertex Moby containing 44 bones and 21 Actions;
- a 4,129-vertex Moby containing 62 bones, 2 Actions, and an RG8 tangent
  normal, including Cycles render compilation before and after reopening;
- lobby placement containing 11 Tie and 7 gameplay Moby instances;
- `chicxulub_multiplayer`, containing a combined 34,879-vertex UFrag terrain
  mesh, 585 Tie instances, 69 gameplay Moby instances, and 32 detected
  emissive materials among the 83 shaders used by the imported scene;
- a Cycles-ready `chicxulub_multiplayer` import containing 656 objects, 62
  mesh datablocks, 83 materials, and 189 images, saved and reopened
  without warnings;
- all 27 cached level archives on the tested disc: 17,497 shader references,
  3,355 unique shader payloads, and no mismatched duplicate payloads.

## Research and attribution

This project builds on public Insomniac-engine research, especially:

- [InsomniaToolset](https://github.com/PredatorCZ/InsomniaToolset)
- [ReChimera](https://github.com/Mrroboto9819/ReChimera)
- [Insomniac Engine AllExtractor](https://lukascone.wordpress.com/2017/12/03/insomniac-engine-allextractor/)
- [WebWorksTools](https://github.com/okangel12345/WebWorksTools)
- [Luna Engine IO Tools](https://github.com/Pcniado/luna_engine_io_tools)

The implementation is GPL-3.0-or-later. It is not affiliated with Insomniac
Games or Sony Interactive Entertainment. Users must provide their own legally
obtained game data.
