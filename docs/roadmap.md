# Roadmap

The implementation umbrella is [GitHub issue #25](https://github.com/MostDef2000/stroy/issues/25).

## Phase 0 - Bootstrap — complete

Architecture, ADRs, schemas, local infrastructure, CI and issue backlog.

## Phase 1 - Scene Core and secure remote access — #1–#5, #26–#27

Python API skeleton, Pydantic/schema parity, project/assets, scene repository, command validation and immutable revisions.

**Exit:** one-room scene can be created, read and edited through typed commands.

**Remote-access exit:** `https://stroy.mostdef.ru` reaches only the authenticated owner-facing STROY origin; internal model/storage services remain private.

## Phase 2 - Deterministic renderer — #6–#8

Blender adapter, camera contract, RGB/depth/normals/object/material passes, RenderManifest and golden-scene tests.

**Exit:** one scene revision + camera produces aligned control assets.

## Phase 3 - Agent and style analysis — #9–#11

Qwen adapter, typed tools, StyleProfile extraction, command proposal/validation and provenance.

**Exit:** natural language reliably becomes valid design commands in tests.

## Phase 4 - Image generation and edits — #12–#16

ComfyUI adapter, versioned workflow manifests, FLUX bootstrap profile, geometry conditioning, diagnostics and localized text/image edits.

**Exit:** one-room redesign preserves protected geometry well enough for interactive review.

## Phase 5 - Web editor — #17–#19

Project browser, upload, 3D viewer, camera view, references, chat/edit panel and revision comparison.

**Exit:** complete v0.1 workflow works without internal tooling.

## Phase 6 - v0.1 acceptance — #20

Execute the full one-room scenario and record reproducibility, geometry-preservation and hardware/runtime measurements.

## Follow-up tracks

- #21 hardware/runtime benchmarking;
- #22 model-profile validation and license provenance maintenance;
- #23 floor-plan reconstruction;
- #24 multi-view/video/LiDAR/DWG/DXF reconstruction evaluation.

Automated reconstruction intentionally follows the working design/edit loop rather than blocking it.
