# Insomniac Asset IO

Insomniac Asset IO is a Blender 5.2+ extension for game-specific Insomniac
asset pipelines. It intentionally does not force assets from different engine
generations through one parser. Each supported game owns its archive,
resource, geometry, animation, material, and level-placement chain behind a
shared Blender-facing interface.

The extension supports **Resistance: Fall of Man**,
**Ratchet & Clank Future: Tools of Destruction**, **Resistance 2**, and
**Resistance 3** on PlayStation 3 through separate game readers and a shared
Blender workflow. It also includes an explicitly experimental static-model
reader for **Resistance: Burning Skies** on PlayStation Vita.

> [!NOTE]
> **Glossary**
>
> - **Moby:** A gameplay object, such as a character, vehicle, or interactive
>   prop. Individual Mobys can have skeletons and animations.
> - **Tie:** A static environment mesh, such as a building or scenery prop;
>   levels often reuse it in multiple placements.
> - **UFrag:** A fragment of terrain or other level geometry.
> - **Zone:** A portion of a level containing terrain and static-object placements.
> - **TUID:** An identifier used to locate an asset or resource.

## Resistance: Fall of Man support

Resistance: Fall of Man uses an older V1 layout inside one root
`GAME.PSARC`. Support targets the USA `BCUS98107` v01.00 disc and validates
both fields from `PARAM.SFO`.

| Feature | Status |
|---|---|
| Direct decrypted ISO source | Supported with exact disc-build validation |
| Root `GAME.PSARC` topology | Read in place across ISO multi-extents; only the selected level resources are decompressed |
| Moby models, skeletons, skin weights, and inline animations | Supported with an RFoM-specific playback profile |
| Tie static meshes | Supported |
| USA v01.00 import catalog | 36,034 level-scoped assets across 41 levels: 5,556 Mobys, 6,714 Ties, and 23,764 Materials |
| DXT1/3/5 and common uncompressed textures | Supported from raw `ps3leveltexs.dat` payloads |
| Diffuse, reconstructed normal, packed, detail, masked, and blended materials | Supported through the shared Blender material path |
| Blender Asset Browser | Moby/Tie Collections plus standalone Material and optional Action assets |
| Region terrain and Tie placement | Supported |
| Primary gameplay Moby placement | Supported as linked static mesh instances |
| Retail names | Deterministic `Moby_XXXX`, `Tie_XXXXXXXX`, and `Material_XXXX` names |
| Collision, lightmaps, shrubs, foliage, lighting, VFX, cubemaps, and secondary gameplay groups | Not yet implemented |

See [Resistance: Fall of Man formats](docs/formats/resistance_fom.md) and
[known issues](docs/known_issues.md) for the verified layouts and remaining
limitations.

## Ratchet & Clank Future: Tools of Destruction support

Tools of Destruction uses the older `main.dat`-centric PS3 layout. Support is
currently scoped to the USA `BCUS98127` v02.00 disc; the add-on validates both
fields from `PARAM.SFO` before extraction.

| Feature | Status |
|---|---|
| Direct decrypted ISO source | Supported with exact disc-build validation |
| Level archive topology | Cached, uncached, and texture PSARCs are paired automatically |
| Moby models, skeletons, and skin weights | Supported |
| Inline Moby animations | Supported, including compressed 8-bit tracks, TOD pair-frame clips, and selected-clip rig imports |
| Tie static meshes | Supported |
| USA v02.00 import catalog | 15,581 level-scoped assets across 20 levels: 3,012 Mobys, 2,048 Ties, and 10,521 Materials |
| DXT1/3/5 and common uncompressed textures | Supported, including streamed high-resolution mips |
| Diffuse, reconstructed normal, packed, masked, and blended materials | Supported |
| Blender Asset Browser | Moby/Tie Collections plus standalone Material and optional Action assets |
| Level terrain (`UFrag`) and Tie placement | Supported |
| Gameplay Moby placement | Supported as linked static mesh instances |
| Retail names | Deterministic `Moby_XXXX` and `Tie_XXXXXXXX` names; retail debug names are absent |
| Moby/level collision, shrubs, foliage, lighting, VFX, and cubemaps | Not yet implemented |

See [Tools of Destruction formats](docs/formats/ratchet_tod.md) and
[known issues](docs/known_issues.md) for the verified old-engine layouts and
remaining limitations.

## Resistance 2 support

Support targets the USA `BCUS98120` v01.00 disc. Selecting its ISO validates
both fields from `PARAM.SFO`.

| Feature | Status |
|---|---|
| PS3 zlib PSARC reading | Supported, including random entry slices |
| Direct decrypted ISO sources | Supported through a reusable local extraction cache |
| `assetlookup.dat` discovery | Supported |
| Moby models | Supported |
| Moby bangles/customizations | Each non-base segment imports into its own selectable Collection; authored source-default parts can optionally be shown |
| Moby damage permutations | Optional; disabled by default and kept in a separate Collection |
| Moby primitive and triangle collision | Optional wire helpers; disabled by default |
| Moby skeletons and skin weights | Supported |
| Moby animsets | Supported, including additive subset and blend masks; clips become Blender Actions |
| Tie static meshes | Supported |
| USA model/level import catalog | 4,130 named assets and 27 levels bundled as metadata |
| Blender Asset Browser library | Locally generated Moby/Tie Collections, standalone Materials, and optional Actions |
| Diffuse textures and albedo-colored packed-blue emission | Supported |
| Reconstructed normals and packed red-channel gloss | Supported in the default material mode |
| DXT1/3/5 and common uncompressed textures | Supported |
| Zone Tie placement | Supported with linked mesh data |
| Zone terrain (`UFrag`) | Supported |
| Gameplay Moby placement | Supported as linked static mesh instances |
| Shrub and foliage geometry | Not yet implemented |
| Lighting, level collision, VFX, and cubemaps | Not yet implemented |

See [Resistance 2 formats](docs/formats/resistance_2.md) and
[known issues](docs/known_issues.md) for precise layouts and caveats.

## Resistance 3 support

Support targets the USA `BCUS98176` v01.00 disc. Selecting its ISO validates
both fields from `PARAM.SFO`.

| Feature | Status |
|---|---|
| Hybrid ISO9660/UDF decrypted ISO sources | Supported without external extraction tools |
| PS3 zlib PSARC reading | Supported, including random entry slices |
| `assetlookup.dat` discovery | Supported |
| Moby models, skeletons, skin weights, and animations | Supported, including the R3 playback profile and standalone baking for matched runtime overlays |
| Moby bangles/customizations | Each non-base segment imports into its own selectable Collection; authored source-default parts can optionally be shown |
| Moby damage permutations and collision geometry | Optional; disabled by default |
| Tie static meshes | Supported |
| USA model/level import catalog | 5,820 named assets and 27 levels bundled as metadata |
| Blender Asset Browser library | Locally generated Moby/Tie Collections, standalone Materials, and optional Actions |
| Diffuse, reconstructed normal, packed red-gloss response, albedo-colored packed-blue emission, and the Goliath wet-leg overlay | Supported in the default material mode |
| Detail and additional shader references | Bound detail images are preserved as unconnected Non-Color nodes; R3 effect slots remain metadata-only because runtime composition is not yet proven |
| DXT and common uncompressed textures | Supported, including global texture bundles |
| Zone Tie placement and terrain (`UFrag`) | Supported |
| Gameplay Moby placement | Supported as linked static mesh instances |
| Shrub and foliage geometry | Not yet implemented |
| Lighting, level collision, VFX, and cubemaps | Not yet implemented |

See [Resistance 3 formats](docs/formats/resistance_3.md) for the verified
layout differences from Resistance 2 and
[Resistance 3 character asset findings](docs/assets/resistance_3_characters.md)
for Ravager composition and Capelli body/head evidence.

## Experimental Resistance: Burning Skies support

Burning Skies uses Nihilistic's little-endian `NSIRSRC` resources rather than
the PS3 IGHW formats. Its support is intentionally isolated behind a separate
experimental game adapter.

| Feature | Status |
|---|---|
| Decrypted `build.psa` PSARC source | Supported; the protected retail PFS file is not |
| Source-driven model catalog | Supported; no game-derived asset list is bundled |
| SMF model geometry | All 798 files with GBuffer packets decode in the tested European archive |
| Rigid and skinned vertex packets | Imported as static bind-pose geometry with UVs |
| Lower LODs | Authored groups decoded; optional, disabled by default, and kept in a separate Collection |
| Shadow-only and collision GBuffer packets | 156 auxiliary packets are excluded by default and available through the Collision geometry option |
| BMX material references | Exact serialized packet maps decoded for all 798 GBuffer SMFs |
| BMX surface channels | Diffuse tint, direct RGB normals, dedicated gloss/specular data, detail data, specular power, alpha, and dedicated emissive color/strength are supported |
| Vita GXT textures | NumPy-vectorized UBC1, UBC2, UBC3, 32-bit color, and PVRTC-II 4bpp supported |
| Skeleton-only SMFs | 17 files import as bind-pose armatures, including `CH_PHYS_ADrone` |
| Skin weights and animation | Not yet supported |
| Geometry-less SMFs | Six files have no recoverable skeleton and fail explicitly |
| Levels, native collision resources, cutscenes, and effects | Not yet supported |

See [Burning Skies formats](docs/formats/resistance_burning_skies.md) and
[known issues](docs/known_issues.md) for the verified layouts and strict
fallback behavior. No Burning Skies resource bytes or asset-name catalog are
distributed with the extension.

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
   `dist\insomniac_asset_io-0.7.6.zip`.
4. Open the **Insomniac** tab in the 3D View sidebar.

Blender includes NumPy; no external Python package is required at runtime.

## Use

### Import an asset

1. Select **Ratchet & Clank Future: Tools of Destruction**,
   **Resistance 2**, or **Resistance 3** in the sidebar. In Add-on
   Preferences, select the corresponding decrypted ISO and a local
   extraction-cache directory.
2. In the **Insomniac** sidebar, click the asset button to open a scrollable
   chooser listing every asset alphabetically. Use its search field to find
   an asset by name, path, kind, TUID, or owner level.
3. Optionally narrow what the popup lists first. All three filters are
   optional and combine:
   - **All / Mobys / Ties / Materials** restricts the asset kind;
   - the level list restricts to assets used by one level;
   - the text field searches the complete source self-path, readable name,
     kind, TUID, and owning levels. Space-separated fragments are all
     required, so `entities objects architecture nb_lobby_skyline_1` resolves
     the matching Tie.
4. Click **Import Asset**. Model imports select and frame the new asset in the
   3D View, including when the viewport is in Local View.

Material entries create a persistent Blender Material with decoded textures
and source metadata but do not add a placeholder object. Select a Moby and use
**Import One Animation...** to choose a clip; this creates only its compatible
armature and selected Action. Ordinary Moby import still creates the model,
rig, and every decoded Action by default.

For experimental Burning Skies imports, select **Resistance: Burning Skies
(Experimental)**, choose a decrypted `build.psa` whose first four bytes are
`PSAR`, and click **Inspect Source**. The model chooser is built directly from
that archive. Burning Skies does not use the ISO, level, or Asset Browser
cache workflow.

The sidebar remembers the active game and a separate last-selected ISO,
archive, or extracted folder for each game in Blender preferences. These
selections are restored after opening another `.blend` file or restarting
Blender.

A status line reports how many assets pass the filters, and its **X** button
clears them all. Filters only change what the search popup lists: the current
selection is kept, stays importable, and is flagged when it falls outside the
filters.

Sidebar source, filter, asset-selection, and level-selection choices persist
for the Blender session when creating or opening another file, including with
**File > New** (`Ctrl+N`). Transient progress and result messages reset with
the file.

The bundled USA catalogs contain 4,130 Resistance 2 assets and 5,820
Resistance 3 assets, each across 27 disc levels. The importer reuses an
extracted owner level when available and otherwise extracts that level's
cached/uncached and global archive pairs from the selected game's configured
ISO on demand. A manually selected PSARC or ISO remains an optional source
override.

Moby imports create the intact/base mesh, armature, weights, materials, and
every decoded animset clip as a persistent Blender Action by default. The rig
opens in its neutral rest pose; choose an Action when animation playback is
wanted. Actions retain their source-derived playback ranges, and the imported
pose bones start selected so Blender's default **Only Show Selected** filter
shows their keyframes immediately in the Action Editor. Selecting an imported
Action automatically binds its compatible armature slot and updates the scene
playback range, and the preview range when it is enabled, to that clip's
bounds. Sampled transform keys use linear interpolation and respect source
scale-inheritance flags.

Matched Resistance 3 partial/additive clips are baked over a compatible idle
clip so each imported Action is useful without recreating the game's runtime
animation layers. These Actions retain
`insomniac_additive_source = true` and identify the baked base through the
`insomniac_overlay_baked` and `insomniac_overlay_base` custom properties.

R2/R3 Moby bangles and outfitter add-ons are always preserved under
**Customizations**. Each non-base render segment gets its own **Bangle NN**
Collection, hidden from the viewport and renders by default but selectable
and independently revealable in the Outliner. Enable **Show source-default
character parts** to reveal only the segments enabled by the Moby's authored
default draw mask. This assembles models such as the Resistance 3 Ravager,
whose head is stored in six internal customization segments, while retaining
the default-hidden behavior needed by multiplayer outfitter models.

Damage permutations, lower LODs, and collision geometry are independently
disabled by default. Enabling them in Add-on Preferences puts each damage
segment in the asset's **Damage Permutations** child Collection, each authored
lower-detail group in **Lower LODs**, and decoded box, sphere, capsule,
triangle, or Burning Skies auxiliary helpers in **Collision Geometry**.
Collision helpers display as in-front wire geometry and are excluded from
renders.

### Import a level

Choose an entry in the sidebar's **Level** list and click **Import Level**.
The list follows the active game's catalog. The extraction cache is checked
first and the selected level is extracted from that game's configured ISO
when needed. Terrain is created per zone. Tie and gameplay placements share
mesh datablocks instead of duplicating geometry.

Gameplay Mobys are intentionally placed as static linked meshes during a
level import. Import an individual Moby to edit its rig and animations.

### Use an ISO directly

The ISO source path uses a bounded internal ISO9660 reader, with a
libarchive-compatible `tar` executable as a fallback for other valid image
variants. This handles Resistance 3's hybrid image and duplicated layer files
without mounting the disc. Configure each game's default ISO in Add-on
Preferences or select an ISO as the optional sidebar source override, then use
the catalog selectors. The sidebar source also accepts a directory containing
exactly one `.iso` file. The importer extracts only the resources declared by
the active adapter. Selecting a supported ISO detects its `TITLE_ID` and
version, switches the active game and catalog when necessary, and rejects
unknown or wrong-version discs before extraction. RFoM reads selected entries
directly from the root `GAME.PSARC`; later games extract the required
level/global archive set. Each prepared level records the source disc identity
and declared resource set in its cache. Verified complete extractions are
reused; global archives from another verified level cache for the same disc
are reused with NTFS hard links instead of being extracted and duplicated for
every level. Legacy caches or caches produced from a different ISO are
refreshed once before use.
**Extract Selected Level Now**
remains available when an explicit extracted source is useful for external
tools. When **Inspect Source** needs ISO extraction, the sidebar and status
bar show a busy message before processing starts; preparing the resources can
take several minutes.

The extension never modifies the ISO.

### Import defaults and catalog regeneration

Model/static-mesh, selector, material, and level options are shared between
the games and live in Add-on Preferences rather than the sidebar. ISO paths
and generated catalogs remain game-specific:

- model materials, Moby animations, animation limit, axis conversion, and
  default-off source-default character parts, damage-permutation, lower-LOD,
  and collision-geometry import;
- asset-selector duplicate suppression, TUID visibility, and owner-level
  visibility;
- level materials, terrain, static props, gameplay instances, and axis
  conversion;
- default-off enhanced source-material processing.

Asset labels show only the readable source name by default, such as
`Mp Scientist Head`. **Hide duplicate asset names** defaults on and collapses
matching display basenames after the path filter is applied. TUIDs and owner
levels remain in tooltips and can be restored to labels in Add-on Preferences.

The extension ships metadata-only USA catalogs containing source entity
paths, level ownership, Moby/Tie IDs, and verified shader IDs, not models,
textures, or other game payloads. The bundled TOD catalog includes Material
entries, as does the RFoM catalog; existing R2/R3 bundled catalogs remain
model-only until regenerated. Choose **Catalog / Asset Browser game** before
using **Regenerate Import Catalog from ISO**. It scans that game's legally
obtained disc into the local extraction cache and replaces only that game's
selector catalog. Catalog generation reads only the metadata resources needed
for asset identities and names; it does not decode every mesh or texture.

### Build the local Asset Browser cache

The extension registers an **Insomniac Assets** library with Blender's Asset
Browser. The library is initially empty because game models and textures are
copyrighted data and are not distributed with the extension.

To populate or regenerate it:

1. Open **Edit > Preferences > Add-ons > Insomniac Asset IO**.
2. Select your legally obtained decrypted ISO and choose **Catalog / Asset
   Browser game**.
3. Choose the local cache directory and a level from that game. `lobby` is the
   compact default cache.
4. Choose whether to cache materials, Moby animations, and previews.
5. Click **Regenerate Selected Level Cache**.

Generation runs in an isolated background Blender process, so it does not
alter the open scene. Each Moby or Tie becomes a marked Collection asset in
its own compressed `.blend` file. Verified shaders become standalone Material
assets under `<game>/<level>/Materials`. When animation caching is enabled,
each decoded clip is also marked as an Action under
`<game>/<level>/Animations/<Moby>`. The library uses Blender's **Append**
import mode. Rebuilding a level is transactional: a failed generation leaves
its previous cache intact.

Action assets are off by default to keep the browser cache compact. They
retain the source Moby identity needed to choose a compatible rig. Direct
Moby import continues to include every decoded Action by default.

### Choose the material mode

The extension has a standard path plus one shared enhancement preference:

- **Standard**, the default, imports diffuse color, source alpha modes, and
  verified albedo-colored blue-channel emission masks. Supported PS3 readers
  reconstruct flagged BC3/RG8 tangent normals and convert flagged packed-red
  gloss to Principled roughness in Eevee and Cycles. R3 also preserves the
  base legs under its opaque-BC1 Goliath wet overlay. The standard path
  recognizes the verified R2 mode-zero `ch_hex_glass_glowy` alpha exception
  as well.
- **Enhanced source materials** additionally applies the source base-color
  multiplier while keeping the same normal, packed-map, and alpha behavior.

To enable the enhanced path, open **Edit > Preferences > Add-ons**, expand
**Insomniac Asset IO**, and enable **Enhanced source materials**. The
preference defaults to off and affects future imports only. It does not
rebuild existing materials or change the active scene render engine.

Packed green remains disconnected because the corresponding R2 specular flag
is absent from every shader in both current whole-disc corpora. Neither game
provides a verified metalness channel. Bound detail maps are imported as
unconnected Non-Color image nodes so their signed/packed values remain
available without inventing a blend. R3 effect-overlay slots remain metadata
rather than speculative node connections.

## Project layout

```text
insomniac_asset_io/
  core/                   Archive, source overlay, binary, and ISO helpers
  games/
    resistance_fom/       Resistance: Fall of Man reader and catalog
    ratchet_tod/           Tools of Destruction reader and catalog
    resistance_2/         Resistance 2 reader and bundled catalog
    resistance_3/         Resistance 3 reader and bundled catalog
    resistance_burning_skies/  Experimental Vita reader
  blender/                Operators, importer, and Asset Browser cache worker
docs/                     Format, contributor, and validation documentation
tests/                    Pure-Python unit and optional real-asset tests
tools/                    Packaging and smoke-test utilities
```

## Validation

See [Validation](docs/validation.md) for test commands, smoke tests, and
verified results.

## Research and attribution

This project builds on public Insomniac-engine research, especially:

- [InsomniaToolset](https://github.com/PredatorCZ/InsomniaToolset)
- [ReChimera](https://github.com/Mrroboto9819/ReChimera)
- [Resistance: Fall of Man Recomp (PC)](https://github.com/miroslavdanutalexandru-alt/Resistance-Fall-of-Man-Recomp)
- [resistance-recomp (macOS)](https://github.com/erikomis/resistance-recomp)
- [Resistance3Port](https://github.com/miroslavdanutalexandru-alt/Resistance3Port)
- [Insomniac Engine AllExtractor](https://lukascone.wordpress.com/2017/12/03/insomniac-engine-allextractor/)
- [WebWorksTools](https://github.com/okangel12345/WebWorksTools)
- [Luna Engine IO Tools](https://github.com/Pcniado/luna_engine_io_tools)
- [PowerVR Native SDK](https://github.com/powervr-graphics/Native_SDK)
- [Vita3K](https://github.com/Vita3K/Vita3K)

The implementation is GPL-3.0-or-later. It is not affiliated with Insomniac
Games or Sony Interactive Entertainment. Users must provide their own legally
obtained game data.
