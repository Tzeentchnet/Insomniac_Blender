# Validation

Run the commands below from the repository root.

Run the portable test suite:

```powershell
python -m unittest discover -s tests -v
```

Run a real extracted level smoke test:

```powershell
python tools\smoke_rfom.py "C:\path\to\level20" --moby 0x03C5
python tools\smoke_r2.py "C:\path\to\level_cached.psarc"
python tools\smoke_r3.py "C:\path\to\level_cached.psarc"
```

The smoke test reports resource counts and decodes one Moby, skeleton,
animset, diffuse texture, Tie, zone, and gameplay region without writing game
data to the repository.

Run the Capelli R3 Game Head material and facial-animation regression against
an extracted owner level:

```powershell
$env:R3_CAPELLI_SOURCE = "C:\path\to\haven_town_center\level_cached.psarc"
python -m unittest discover -s tests -p "test_real_r3.py" -v
Remove-Item Env:R3_CAPELLI_SOURCE
```

This decodes the embedded 256x256 BC3 albedo for shader
`0xC89EC8F70464D875` and verifies that `exp_dead_intro_upper` is baked over
`head_idle` without double-applying the absolute eyebrow-bone reference
positions.

Run the environment-gated Resistance: Fall of Man level 20 regression:

```powershell
$env:RFOM_LEVEL_SOURCE = "E:\tests\InsomniacAssetIO\resistance_fom\cache\resistance_fom\level20\PS3_GAME\USRDIR\packed\levels\level20"
$env:RFOM_ISO_PATH = "E:\path\to\Resistance-Fall-of-Man-v01.00.dec.iso"
python -m unittest discover -s tests -p "test_real_resistance_fom.py" -v
Remove-Item Env:RFOM_LEVEL_SOURCE
Remove-Item Env:RFOM_ISO_PATH
```

This checks the exact `BCUS98107`/`01.00` identity, the 41-level inventory,
an animated 76-bone Moby, material and texture decoding, a Tie, region
terrain, 4,247 Tie placements, and 533 primary gameplay Moby placements.
Each source-specific test is skipped when its environment variable is absent.

Validate RFoM imports with Blender 5.2:

```powershell
& "D:\Blender Foundation\Blender 5.2\blender.exe" --background `
  --factory-startup --python-exit-code 1 `
  --python tools\blender_smoke_rfom.py -- `
  --source "E:\tests\InsomniacAssetIO\resistance_fom\level20" `
  --output "E:\tests\InsomniacAssetIO\resistance_fom\moby.blend" `
  --kind moby --tuid 0x03C5 --materials --animations

& "D:\Blender Foundation\Blender 5.2\blender.exe" --background `
  --factory-startup --python-exit-code 1 `
  --python tools\blender_smoke_rfom.py -- `
  --source "E:\tests\InsomniacAssetIO\resistance_fom\level20" `
  --output "E:\tests\InsomniacAssetIO\resistance_fom\level.blend" `
  --kind level --materials
```

Run the environment-gated Tools of Destruction Metropolis regression against
an extracted archive trio:

```powershell
$env:TOD_LEVEL_SOURCE = "E:\tests\InsomniacAssetIO\ratchet_tod\cache\ratchet_tod\metropolis\PS3_GAME\USRDIR\packed\levels\metropolis"
$env:TOD_ISO_PATH = "E:\path\to\Ratchet-and-Clank-Future-Tools-of-Destruction-v02.00.dec.iso"
python -m unittest discover -s tests -p "test_real_tod.py" -v
Remove-Item Env:TOD_LEVEL_SOURCE
Remove-Item Env:TOD_ISO_PATH
```

This checks the representative compressed and pair-frame animations, a shader
texture, a Tie, UFrag terrain, Tie placements, gameplay Moby placements, the
exact `BCUS98127`/`02.00` disc identity, and the 20-level disc inventory.
Each source-specific test is skipped when its corresponding environment
variable is not set.

Validate Tools of Destruction imports with Blender 5.2. All generated
`.blend` files must use a disposable output directory outside the repository,
for example `E:\tests\InsomniacAssetIO\ratchet_tod`:

```powershell
& "D:\Blender Foundation\Blender 5.2\blender.exe" --background `
  --factory-startup --python-exit-code 1 `
  --python tools\blender_smoke_tod.py -- `
  --source "E:\tests\InsomniacAssetIO\ratchet_tod\cache\...\level_cached.psarc" `
  --output "E:\tests\InsomniacAssetIO\ratchet_tod\imports\moby.blend" `
  --kind moby --tuid 22 --materials --animations

& "D:\Blender Foundation\Blender 5.2\blender.exe" --background `
  --factory-startup --python-exit-code 1 `
  --python tools\blender_smoke_tod.py -- `
  --source "E:\tests\InsomniacAssetIO\ratchet_tod\cache\...\level_cached.psarc" `
  --output "E:\tests\InsomniacAssetIO\ratchet_tod\imports\level.blend" `
  --kind level
```

The TOD smoke tool also accepts `--kind tie --tuid <header-offset>`. It fails
if a requested import produces no mesh, no material, or no Action.

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

The third shader texture is packed material data, not a full-color emissive
image. Its blue channel masks the corresponding diffuse color into emission
only when that channel contains nonzero texels, preserving authored colors
such as the Chimeran Shuttle's orange and cyan glows. Its red channel is
inverted into roughness when the shader enables gloss in either game. Packed
green connects to Principled specular only when the corresponding shader flag
is present; that flag is absent from the current R2 corpus. Additional
material-vector emission tint, authored emission strength, packed alpha, and
detail-map composition remain under research. Bound detail textures are still
imported as unconnected Non-Color nodes for inspection and downstream authoring.

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

Run the standalone Material/Action catalog metadata regression:

```powershell
& "C:\path\to\blender.exe" --background --factory-startup `
  --python-exit-code 1 --python tests\test_blender_asset_cache.py
```

Validate every generated Asset Browser entry, catalog assignment, preview,
object dependency, and packed game image:

```powershell
& "C:\path\to\blender.exe" --background --factory-startup `
  --python-exit-code 1 --python tools\validate_asset_cache.py -- `
  "C:\path\to\InsomniacAssetIOCache\asset_library" `
  --expected-mobys 3 --expected-ties 3 --expected-materials 3 `
  --expected-animations 1
```

Add `--skip-previews` when validating a cache generated with previews
disabled.

The current implementation was validated in Blender 5.2.2 LTS with:

- Resistance: Fall of Man USA v01.00 level 20: all 155 Mobys (961 meshes),
  222 Ties (1,664 meshes), 633 Materials, 1,384 textures, 1,675 region
  meshes, 4,247 Tie placements, and 533 primary gameplay Moby placements;
- all 1,717 inline clips across 37 animated RFoM Mobys, including 155
  skeletons and a 186-bone asset, decoded without errors;
- representative RFoM texture decoding for every observed format
  (`0x81`, `0x85`, `0x86`, `0x87`, `0x88`, and `0x8B`);
- selective RFoM level extraction directly from the ISO-backed 6.5 GB
  `GAME.PSARC`, plus a 41-level, 36,034-reference metadata catalog;
- a saved and reopened animated RFoM Moby with three Materials, ten decoded
  game images, a 76-bone rig, and one Action;
- a saved and reopened RFoM level with 4,782 objects, 303 mesh datablocks,
  486 source Materials, and 1,060 decoded game images, without import
  warnings;
- Tools of Destruction USA v02.00 Metropolis: 201 Mobys, 193 Ties,
  631 shaders, 1,297 textures, 1,987 UFrags, 4,848 Tie placements, and
  1,378 gameplay Moby placements;
- a compressed TOD Moby Action using 8-bit tracks, with 151 finite decoded
  frames across a 38-bone skeleton;
- TOD `animate_spin`, reduced from 8,001 paired disk records to 4,000 Action
  frames at 15 fps;
- a bounded Metropolis Asset Browser cache containing three Moby Collections,
  three Ties, three standalone Materials, and one standalone `animate_spin`
  Action, all reopened with their catalog assignments and metadata intact;
- a selected `crumbling` import containing only its 38-bone compatible rig
  and one Action, plus a separately saved/reopened standalone TOD Material;
- a saved TOD Metropolis level containing 6,228 objects and 313 mesh
  datablocks, plus separate saved Moby and Tie material imports;
- a 16,150-vertex Moby containing 44 bones and 21 Actions;
- a 4,129-vertex Moby containing 62 bones, 2 Actions, and an RG8 tangent
  normal, including Cycles render compilation before and after reopening;
- lobby placement containing 11 Tie and 7 gameplay Moby instances;
- `chicxulub_multiplayer`, containing a combined 34,879-vertex UFrag terrain
  mesh, 585 Tie instances, 69 gameplay Moby instances, and 32 detected
  emissive materials among the 83 shaders used by the imported scene;
- an enhanced `chicxulub_multiplayer` import containing 656 objects, 62
  mesh datablocks, 83 materials, and 189 images, saved and reopened
  without warnings;
- all 27 cached level archives on the tested disc: 17,497 shader references,
  3,355 unique shader payloads, and no mismatched duplicate payloads.
- all 27 R3 cached level archives: 33,860 shader references, 4,579 unique
  shader payloads, exact normal-flag/binding agreement, and no mismatched
  duplicate payloads.
