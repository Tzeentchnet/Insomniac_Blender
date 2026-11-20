# Resistance 3 character asset findings

Status: verified against the decrypted USA `BCUS98176` v01.00 disc, the
bundled catalog generated from that disc, decoded entity data, and Blender
5.2 imports.

This document records asset-level findings that are useful for character
assembly and future importer work. Catalog co-ownership, spatial
compatibility, and direct entity references are reported separately because
they are different kinds of evidence.

## Evidence and reference scan

The Ravager entity's 22 IGHW sections were scanned for aligned 64-bit values
matching every known R3 Moby, Tie, and Shader TUID. The same direct-Moby-TUID
check was applied to the Jack Capelli NPC body, Capelli R3 game head, and
Capelli hero body.

The scan found:

- normal Shader references;
- the Ravager's own Moby TUID in its entity;
- no external head Moby TUID in the Ravager;
- no matching body/head Moby TUID in the scanned Capelli entities.

This rules out a direct catalog Moby reference in those entity payloads. It
does not rule out associations stored in gameplay scripts, runtime class
data, another resource kind, or an indirect identifier that is not a catalog
TUID. Searches of the public Resistance recomp projects also did not expose a
character body/head attachment table.

## Asset index

| Role | Moby TUID | Catalog source name | Owner levels | Verified details |
|---|---|---|---:|---|
| Ravager | `0x06B70D22047FEB17` | `characters/enemy/ravager_r3/ravager_r3.entity.irb` | 4 | 78 bones, 7 render segments, 105 clips |
| Jack Capelli NPC body | `0x3AEFCD1803258CEB` | `characters/npc/jack_capelli/jack_capelli.entity.irb` | 2 | No direct reference to its game head |
| Jack Capelli game head | `0x121B2C0803C3950F` | `characters/gameheads/jack_capelli_game_head/jack_capelli_game_head.entity.irb` | 2 | Catalog and coordinate-space companion candidate for the NPC body |
| Capelli R3 game head | `0x6E7A8CE904E0DBC0` | `characters/gameheads/capelli_r3_game_head/capelli_r3_game_head.entity.irb` | 11 | 54 bones, 5,626 vertices, 9,711 triangles, 39 clips |
| Capelli hero body | `0xB36A079904EA5A7A` | `characters/hero/capelli_r3/capelli_r3.entity.irb` | 11 | 109 bones; no direct reference to the game head |
| Cinematic-low Capelli body | `0x80952DB404ABEB92` | `characters/cinematic/cine_low_capelli_r3/cine_low_capelli_r3.entity.irb` | 4 | Catalog co-ownership candidate only |
| Cinematic-low Capelli head | `0x83DD505404ABEC80` | `characters/cinematic/cine_low_capelli_r3_head/cine_low_capelli_r3_head.entity.irb` | 4 | Catalog co-ownership candidate only |

Owner-level membership comes from the bundled catalog and proves only that an
asset can be resolved from that level's archive set.

## Ravager composition

The Ravager does not have an external head asset. Its default Moby draw mask
is `0x7F`, enabling base segment 0 and all six non-base segments. The six
non-base segments occupy the head, eye, and upper attachment regions and
complete the source-authored character.

Its four catalog owner levels are:

- `mine_town_approach`
- `new_york_approach`
- `new_york_terraformer`
- `stlouis_rescue`

| Segment | Imported role | Vertices | Triangles | Source-space Y bounds | Default bit |
|---:|---|---:|---:|---:|---:|
| 0 | Base body | 10,322 | 14,085 | `-0.029572` to `2.679291` | 0 |
| 1 | Bangle 01 / head part | 544 | 814 | `2.545349` to `2.772308` | 1 |
| 2 | Bangle 02 / head part | 265 | 208 | `2.599274` to `2.775238` | 2 |
| 3 | Bangle 03 / head part | 527 | 774 | `2.566498` to `2.747498` | 3 |
| 4 | Bangle 04 / central head part | 1,096 | 1,470 | `2.429352` to `2.703918` | 4 |
| 5 | Bangle 05 / side attachment | 195 | 242 | `2.393921` to `2.836487` | 5 |
| 6 | Bangle 06 / side attachment | 219 | 275 | `2.355377` to `2.836487` | 6 |

The add-on still imports each non-base segment into an independently
selectable **Bangle NN** Collection. Enabling **Show source-default character
parts** reveals Collections whose bits are enabled in the source draw mask,
which assembles the complete Ravager without merging its meshes.

The option remains disabled by default because a Moby class draw mask is not
always the final runtime outfit. Multiplayer character classes can enable
several mutually exclusive helmet or customization bits before runtime
outfitter data replaces that mask.

### Ravager animation inventory

The decoded Ravager animset contains:

- 78 bones;
- 105 clips;
- 3,230 total source frames;
- 11 additive/partial clips that resolve against
  `ravager_r3_idle_sighted`;
- 110,149 tracked bone/frame combinations out of 251,940 possible
  combinations, or approximately 43.7 percent.

Tracked bones plus the ancestors required to calculate their poses account
for 127,104 combinations, approximately 50.5 percent of the original dense
pose-conversion work.

## Capelli body and head candidates

### Hero body and R3 game head

The hero body and Capelli R3 game head have the same 11 catalog owner levels:

- `haven_town_center`
- `haven_town_defense`
- `haven_town_ridge`
- `haven_traversal_boat`
- `mine_town_approach`
- `mine_traversal_train`
- `new_york_approach`
- `new_york_terraformer`
- `penn_prison_arena`
- `stlouis_rescue`
- `stlouisambush_01`

Their source geometry is authored in compatible character-space coordinates.
The hero body reaches source Y approximately `1.929`; the game head spans
source Y `1.622406` through `2.006653`. Importing both roots at the same
origin therefore gives a useful static spatial alignment.

They are not armature-index compatible:

- hero body: 109 bones;
- game head: 54 bones;
- no same-index bind matrices matched in the compared index range.

The head mesh must not be rebound or merged into the body armature by bone
index. Animated assembly needs an explicit attachment/retarget profile or
separate coordinated rigs.

### Jack Capelli NPC body and game head

The NPC body and Jack game head share these two owner levels:

- `haven_town_center`
- `haven_town_defense`

Their entity payloads do not reference each other, and their skeletons are
also not index-compatible. Their matching names, archive co-ownership, and
coordinate space make them a strong curated pairing candidate, not an
automatically proven relationship.

### Cinematic-low body and head

The cinematic-low assets share these four owner levels:

- `haven_town_defense`
- `haven_town_ridge`
- `penn_prison_arena`
- `stlouisambush_01`

This is currently a catalog co-ownership candidate. Its transform and
animation relationship have not been validated to the same level as the
hero/game-head pair.

## Capelli game-head material and animation data

The Capelli R3 game head uses Shader TUID `0xC89EC8F70464D875`.
Its 256 by 256 BC3 hair albedo, texture ID `0x8843ADA3`, has a zero-length
high-mip pointer but a valid 65,536-byte payload in the owner level's
`textures.dat`. Level-local bounded texture lookup must therefore run before
global or sibling fallbacks.

The imported game head has:

- one mesh with 5,626 vertices and 9,711 triangles;
- 54 bones;
- 39 Actions;
- source-space bounds of X `-0.127899` to `0.127899`,
  Y `1.622406` to `2.006653`, and Z `-0.105835` to `0.170013`.

The shared game-head animset contains both tracked clips and pose-only
blend-masked clips. Eye-direction Actions such as `eyes_left`, `eyes_right`,
`eyes_up`, and `eyes_down` can carry their intended rotations in blend-enabled
reference quaternions even when no per-frame rotation track exists.

## Assembly requirements

Automatic external companion assembly should use explicit data rather than
name or owner-level guessing. A profile needs, at minimum:

- primary and companion Moby TUIDs;
- the relationship type, such as body/head;
- an attachment transform or validated common-root rule;
- the body attachment bone when root-only placement is insufficient;
- an armature policy: separate rigs, constraints, or validated retargeting;
- the owner levels on which the pairing was verified;
- evidence distinguishing a proven runtime association from a curated
  importer convenience.

Multiple bodies may legitimately use one game head, so reverse inference from
a head to a single body is inherently ambiguous.

## Reproducibility

The catalog names and owner sets are stored in
[`resistance_3_us_catalog.json`](../../insomniac_asset_io/games/resistance_3/data/resistance_3_us_catalog.json).
The real Capelli and Ravager animation regressions are in
[`test_real_r3.py`](../../tests/test_real_r3.py). Broader import and parity
results are recorded in [validation](../validation.md).
