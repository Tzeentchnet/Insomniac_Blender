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

`test_collision.py` validates the shared level-collision contract, including
triangle bounds, face metadata lengths, prototype references, affine
transforms, and malformed metadata. `test_level_collision_r23.py` builds
bounded synthetic `0x2900` resources for world terrain, shared Tie
prototypes, triangle surfaces, boxes, spheres, capsules, and cylinders. It
also covers malformed table relationships, AABBs, transforms, face indices,
degenerate/duplicate faces, and all-degenerate no-op prototypes.
`test_level_collision_tod.py` independently builds the raw TOD header,
`0x80`-byte records, marker-1 triangle prototypes, yard-scaled transforms,
surface words, malformed ranges, and no-op prototypes so the older format is
not tested through R2/R3 assumptions.
`test_level_collision_rfom.py` covers RFoM's separate marker-1 AoS vertex
profile, world/Tie/collision-only placed families, packed source/component
indices, header scales, extraction configuration, malformed records, and
no-op handling.

`test_blender_optional_geometry.py` imports two collision instances that
share one prototype, verifies source-group Collections and face-domain
surface/material attributes, then saves and reopens the `.blend` to confirm
mesh sharing and metadata persistence.

Set `R2_LEVEL_SOURCE` or `R3_LEVEL_SOURCE` to run the corresponding
environment-gated retail collision regression:

```powershell
$env:R2_LEVEL_SOURCE = "C:\path\to\r2\level_cached.psarc"
python -m unittest `
  tests.test_real_r2.RealResistance2Tests.test_decodes_level_collision -v

$env:R3_LEVEL_SOURCE = "C:\path\to\r3\level_cached.psarc"
python -m unittest `
  tests.test_real_r3.RealResistance3Tests.test_decodes_level_collision -v

$env:TOD_LEVEL_SOURCE = "C:\path\to\tod\level_cached.psarc"
python -m unittest `
  tests.test_real_tod.RealRatchetTodTests.test_decodes_representative_metropolis_chains -v
```

The current local retail corpus covers 58 regional files from representative
R2 and R3 archives: 704,226 source records, including 663,776 Tie records and
40,450 world-terrain records. It contains 86,393 quantized triangle
prototypes and 1,729 primitive prototypes. After explicitly removing source
no-op geometry, 682,256 instances and 87,331 shared meshes validate through
the game-neutral scene contract.

Run a collision-only Blender import, save, reopen, and revalidate shared mesh
and face-attribute persistence with:

```powershell
& "D:\Blender Foundation\Blender 5.2\blender.exe" --background `
  --factory-startup --python-exit-code 1 `
  --python tools\blender_smoke_level_collision.py -- r2 `
  "C:\path\to\r2\level_cached.psarc" `
  --resource-suffix "/default/collision.dat" `
  --output "E:\Tests\collision_review\r2_collision.blend"

& "D:\Blender Foundation\Blender 5.2\blender.exe" --background `
  --factory-startup --python-exit-code 1 `
  --python tools\blender_smoke_level_collision.py -- r3 `
  "C:\path\to\r3\level_cached.psarc" `
  --resource-suffix "/a_start/collision.dat" `
  --output "E:\Tests\collision_review\r3_collision.blend"
```

The retained review files contain 2,201 R2 instances using 558 meshes and
7,261 R3 instances using 550 meshes. Both were reopened in Blender 5.2 with
exact object, shared-mesh, metadata, and face-domain surface-attribute counts.

Run the equivalent TOD collision-only review against the raw non-IGHW
resource with:

```powershell
& "D:\Blender Foundation\Blender 5.2\blender.exe" --background `
  --factory-startup --python-exit-code 1 `
  --python tools\blender_smoke_level_collision.py -- tod `
  "C:\path\to\tod\level_cached.psarc" `
  --output "E:\Tests\collision_review\tod_collision.blend"
```

The retained Kerchu City review file reopens with exactly 9,704 collision
instances, 1,054 shared meshes, non-rendering wire settings, metadata, and
face-domain surface attributes. The Metropolis real-data regression validates
15,150 instances, 1,565 meshes, 93,733 vertices, and 129,382 retained faces.

The RFoM smoke test reports resource counts and decodes one Moby, skeleton,
animset, diffuse texture, Tie, zone, shrub and foliage placement counts,
all seven gameplay groups, and the complete level-collision scene without
writing game data to the repository.

The portable `test_foliage.py` fixture separately exercises the R2 and R3
`0xA000` card buffers, their distinct `0xA200` descriptors, and their
distinct `0x7440` placement records. A real R2 source with foliage can be
checked by setting `R2_LEVEL_SOURCE`; Scotia Co-op contains three foliage
assets and 200 placements. The portable `test_shrubs.py` fixture exercises
the shared R2/R3 `0xB100` geometry and `0x7540` placement records. Scotia
Co-op contains ten Shrub assets with 2,250 vertices, 2,538 triangles, and
8,426 placements.

Run the real R3 vegetation regression against `mine_traversal_train`:

```powershell
$env:R3_FOLIAGE_SOURCE = "C:\path\to\mine_traversal_train\level_cached.psarc"
python -m unittest discover -s tests -p "test_real_r3.py" -v
Remove-Item Env:R3_FOLIAGE_SOURCE
```

This decodes all 11 foliage assets, both masked leaf materials and their
albedo textures, and all 1,050 placements, plus all six Shrub assets and 661
Shrub placements. It verifies that every vegetation placement resolves to a
level asset. The same regression checks the exact grass, conifer, and track
Tie material groups selected by R3 primitive byte `+0x26`, their vertex light
and optional UV2, and 6,730 placed-Tie lightmap IDs. It decodes the target
grass instance's 256x256 BC3 lightmap from zone `0x58E8829DE249D884`. For all
972 UFrag meshes in zone `0xEF2A7A01E249D922`, it verifies world bounds, the
complete 0-to-1 UV1 range, packed vertex-light values, lightmap index 109, and
the decoded
1024x1024 BC3 lightmap read from the matching `lighting.dat` asset.
Every zone's `0x5400` and `0x5410` descriptor identities must match. The
regression also decodes `TRACK_CHECKPOINT_7`'s nontrivial 128x128 BC1
directional map and terrain map 109's neutral 256x256 directional pair.
It also verifies that `JEEP_REINFORCEMENT_6` resolves to the Warden Jeep
Moby, whose `0x03FFFFFF` class draw mask enables all 35 source meshes and
16,978 raw triangles.

Run a complete material-enabled R3 level import and assert its Blender
terrain-light data:

```powershell
& "D:\Blender Foundation\Blender 5.2\blender.exe" --background `
  --python-exit-code 1 --python tools\blender_smoke_r3.py -- `
  "C:\path\to\mine_traversal_train\level_cached.psarc" --level `
  --level-materials --expect-terrain-lightmaps 34 `
  --expect-tie-lightmaps 6730 `
  --output "C:\path\to\r3_mine_traversal_train.blend"
```

This checks point-domain vertex-light attributes, `LightmapUV`, packed sRGB
zone-lightmap images, Non-Color directional images, paired 4096x2116 Tie
atlases, all per-object atlas transforms, and the radiosity-normal-map basis
graphs for both terrain and Ties. The tool saves and reopens the file, then
revalidates packed images and node graphs.

For visual comparison, retain a pre-composition scene and the validated
result as `mine_traversal_train_before.blend` and
`mine_traversal_train_after.blend` under a disposable `E:\Tests` review
directory.

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
positions or applying source-head reference rotations to Capelli's untracked
root/head bones. It also verifies that the trackless, blend-masked
`eyes_left` reference pose remains active after retargeting.

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
terrain, 4,247 Tie placements, 533 primary gameplay Moby placements, and the
level20 secondary gameplay counts of 9 paths, 480 triggers, 43 effects, 1,123
markers, 65 Moby groups, and 93 named areas, plus the
13,291-instance/2,491-mesh collision scene. Each source-specific test is
skipped when its environment variable is absent.

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

& "D:\Blender Foundation\Blender 5.2\blender.exe" --background `
  --factory-startup --python-exit-code 1 `
  --python tools\blender_smoke_level_collision.py -- rfom `
  "E:\tests\InsomniacAssetIO\resistance_fom\level20" `
  --output "E:\tests\InsomniacAssetIO\collision_review\rfom_collision.blend"
```

For level imports, the Blender smoke test compares every decoded shrub,
foliage, and gameplay placement with the corresponding imported Blender
object count. The saved and reopened level20 gameplay review contains the six
named contextual Collections and exact counts of 9 path curves, 480 trigger
empties, 43 effect empties, 1,123 typed marker empties, 65 Moby-group empties,
and 93 named-area empties. All 1,813 secondary objects remain in-front,
non-rendering context, and none are routed to a Moby mesh. The collision-only
review saves, reopens, and revalidates all 13,291 wire objects, 2,491 shared
meshes, instance metadata, and face-domain surface attributes.

The full USA collision corpus contains 41 `ps3levelcoll.dat` files with
446,785 records, 90,766 shared prototypes, 8,783,640 vertices, and
12,762,307 retained faces. Every file validates through the game-neutral
scene contract without warnings.

The full USA gameplay corpus contains 41 `ps3gameplay.dat` files with 16,422
primary Mobys, 642 paths containing 12,017 points, 6,514 triggers, 4,202
effects, 21,543 typed markers, 1,869 Moby groups containing 13,965
memberships, and 2,340 named areas. The areas contain 429 path and 3,552
trigger references. Every descriptor, nested table, embedded IGHW payload,
transform, ordinal, membership range, and cross-group index validates.

Run the environment-gated Tools of Destruction regressions against an
extracted Metropolis archive trio and the decrypted ISO:

```powershell
$env:TOD_LEVEL_SOURCE = "E:\tests\InsomniacAssetIO\ratchet_tod\cache\ratchet_tod\metropolis\PS3_GAME\USRDIR\packed\levels\metropolis"
$env:TOD_ISO_PATH = "E:\path\to\Ratchet-and-Clank-Future-Tools-of-Destruction-v02.00.dec.iso"
python -m unittest discover -s tests -p "test_real_tod.py" -v
Remove-Item Env:TOD_LEVEL_SOURCE
Remove-Item Env:TOD_ISO_PATH
```

This checks the representative compressed and pair-frame animations, a shader
texture, a Tie, UFrag terrain, Tie placements, both Metropolis foliage assets,
all 757 foliage placements, gameplay Moby placements, raw level collision,
the exact
`BCUS98127`/`02.00` disc identity, and the 20-level disc inventory. The
ISO-gated Kerchu City regression additionally checks all 15 Shrub meshes,
4,387 vertices, 5,998 triangles, masked/opaque/blended material routing, and
all 11,857 finite clustered placements with exact per-class counts, plus
9,704 retained collision instances using 1,054 shared prototypes. Each
source-specific test is skipped when its corresponding environment variable
is not set.

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
  --output "E:\tests\InsomniacAssetIO\ratchet_tod\imports\shrub.blend" `
  --kind shrub --tuid 0x017EB070 --materials

& "D:\Blender Foundation\Blender 5.2\blender.exe" --background `
  --factory-startup --python-exit-code 1 `
  --python tools\blender_smoke_tod.py -- `
  --source "E:\tests\InsomniacAssetIO\ratchet_tod\cache\...\level_cached.psarc" `
  --output "E:\tests\InsomniacAssetIO\ratchet_tod\imports\level.blend" `
  --kind level --materials
```

The TOD smoke tool also accepts `--kind tie`, `--kind shrub`, and
`--kind foliage` with the corresponding level-local `--tuid`. Level imports
compare both Shrub and Foliage placement totals with the imported Blender
objects. The tool fails if a requested import produces no mesh, no requested
material, or no requested Action.

Diffuse lookup first uses the level texture tables, then the global archive's
per-TUID texture bundles, then already-extracted sibling level pairs under the
same `packed/levels` tree. A missing/stale texture ID can also resolve through
another available ID with the identical authored lookup path. Selected-level
and global candidates take precedence over sibling-only aliases; candidates
within the first available domain must decode to the same image. Ambiguous
matches remain errors. References that remain unavailable are reported and
receive a deterministic placeholder material.

The regenerated USA R2 catalog contains 42,658 level-owner records for 7,440
unique texture IDs and 7,506 normalized authored paths. Each compact owner
record includes descriptor metadata and the SHA-256 of the compressed
mip-zero payload; it contains no texel payload. A metadata-only scan of all 27
retail levels planned 401 exact cross-level dependencies without an ambiguous
or conflicting payload.

Clean-cache ISO preparation was also exercised with no sibling archives
present initially:

- `chicxulub_multiplayer` selected six non-alias exact dependencies across
  two owner levels. Exact `0x1585EA44` and `0x6782D663` resolved from sibling
  archives rather than their available path aliases, and no texture reference
  remained unresolved; all 870 shader texture references decoded.
- `scotia_coop` selected 24 non-alias exact dependencies across seven owner
  levels. Its resulting provenance listed 18 resources: two global archives,
  the selected pair, and seven sibling pairs. All remaining stale IDs had
  available authored-path aliases, so all 2,047 shader texture references
  decoded and no avoidable placeholder remained.

Portable regressions cover compact-index parsing, disc identity, conflicting
descriptor and payload fingerprints, path fallback through sibling shader
metadata, provenance filtering, selective extraction, and verified NTFS
hard-link reuse.

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

Run the optional-geometry and source-default-parts regression:

```powershell
& "C:\path\to\blender.exe" --background --factory-startup `
  --python-exit-code 1 --python tests\test_blender_optional_geometry.py
```

This additionally verifies that level gameplay placement combines only
mask-enabled source-default customization segments into a shared Moby mesh,
while excluding disabled segments and damage permutations.

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
  --expected-mobys 3 --expected-ties 3 --expected-shrubs 3 `
  --expected-foliage 3 --expected-materials 3 `
  --expected-animations 1
```

Add `--skip-previews` when validating a cache generated with previews
The report distinguishes manifest `asset_references` from unique `assets`.
Repeated schema-2 references must share one logical ID, canonical `.blend`,
name, and catalog assignment; `deduplicated_references` reports how many
cross-level references were collapsed. The validator also checks that catalog
paths map to their deterministic UUIDs.

The current implementation was validated in Blender 5.2.2 LTS with:

- Resistance: Fall of Man USA v01.00 level 20: all 155 Mobys (961 meshes),
  222 Ties (1,664 meshes), 633 Materials, 1,384 textures, 1,675 region
  meshes, 4,247 Tie placements, 533 primary gameplay Moby placements, and
  all 1,813 secondary gameplay records;
- all 1,717 inline clips across 37 animated RFoM Mobys, including 155
  skeletons and a 186-bone asset, decoded without errors;
- representative RFoM texture decoding for every observed format
  (`0x81`, `0x85`, `0x86`, `0x87`, `0x88`, and `0x8B`);
- selective RFoM level extraction directly from the ISO-backed 6.5 GB
  `GAME.PSARC`, plus a 41-level, 36,321-reference metadata catalog including
  Shrubs and Foliage;
- a saved and reopened animated RFoM Moby with three Materials, ten decoded
  game images, a 76-bone rig, and one Action;
- a saved and reopened RFoM gameplay review with 13,994 objects, 325 mesh
  datablocks, exact counts for all seven gameplay groups, preserved
  contextual metadata, and no import warnings;
- Tools of Destruction USA v02.00 Metropolis: 201 Mobys, 193 Ties,
  631 shaders, 1,297 textures, 1,987 UFrags, 4,848 Tie placements, and
  1,378 gameplay Moby placements;
- a regenerated 20-level TOD catalog with 15,629 references, including all
  34 Shrub descriptors from the 11 levels that contain them;
- all 34 TOD Shrub meshes with 12,466 vertices and 15,085 triangles, plus
  10,997 clusters that resolve all 103,300 placements exactly once;
- a compressed TOD Moby Action using 8-bit tracks, with 151 finite decoded
  frames across a 38-bone skeleton;
- TOD `animate_spin`, reduced from 8,001 paired disk records to 4,000 Action
  frames at 15 fps;
- bounded Asset Browser caches for all four supported PS3 games, including
  all four games' Shrub Collections and supported Foliage Collections,
  canonical cross-level R3 reuse, standalone Materials, packed images, and
  generated previews, all reopened with their catalog assignments and
  metadata intact;
- a capped real Kerchu City Asset Browser cache containing one TOD Moby, Tie,
  Shrub, Foliage, and Material, reopened with five catalog assignments, nine
  object dependencies, and 20 packed image dependencies;
- direct catalog dispatch of isolated Shrub and Foliage assets for all four
  games, preserving the selected vegetation kind through Blender mesh
  creation;
- a selected `crumbling` import containing only its 38-bone compatible rig
  and one Action, plus a separately saved/reopened standalone TOD Material;
- a saved TOD Metropolis level containing 6,228 objects and 313 mesh
  datablocks, plus separate saved Moby and Tie material imports;
- a saved/reopened isolated Kerchu City Shrub with two objects, one shared
  mesh, one game Material, and one packed game image;
- a saved/reopened Kerchu City level with 23,683 objects, 349 mesh
  datablocks, 620 game Materials, and 1,321 game images, including exactly
  11,857 finite Shrub placements sharing 15 mesh datablocks;
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
  duplicate payloads;
- a full Ravager import with 9 objects, 7 meshes, 19 materials, 23 images,
  105 Actions, and all six source-default head/attachment Collections shown;
- sampled parity for all 105 Ravager Actions across 245,700 dense-reference
  values, with maximum absolute error below `1e-6`;
- sampled parity for all 39 Capelli game-head Actions across 63,180
  dense-reference values, including eye direction, blink, expressions, and
  visemes, with maximum absolute error below `1e-6`;
- cached Ravager import time reduced from 4.53 seconds to 3.03-3.51 seconds,
  and its saved file reduced from 21,668,857 to 16,229,606 bytes;
- real `mine_town_approach` preparation reduced from 29.88 to 9.37 seconds
  when the 1.68 GB global archives were already present in a verified sibling
  level cache.
