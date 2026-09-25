# Roadmap

## Phase 0 - Bootstrap

Architecture, ADRs, schemas, local infrastructure, CI and issue backlog.

## Phase 1 - Scene Core

Python API skeleton, Pydantic/schema parity, project/assets, scene repository, command validation and immutable revisions.

**Exit:** one-room scene can be created, read and edited through typed commands.

## Phase 2 - Deterministic renderer

Blender adapter, camera contract, RGB/depth/normals/object/material passes, RenderManifest and golden-scene tests.

**Exit:** one scene revision + camera produces aligned control assets.

## Phase 3 - Agent and style analysis

Qwen adapter, typed tools, StyleProfile extraction, command proposal/validation and provenance.

**Exit:** natural language reliably becomes valid design commands in tests.

## Phase 4 - Image generation

ComfyUI adapter, versioned workflow manifests, FLUX bootstrap profile, geometry conditioning, GenerationManifest and localized edits.

**Exit:** one-room redesign preserves protected geometry well enough for interactive review.

## Phase 5 - Web editor

Project browser, upload, 3D viewer, camera view, references, chat/edit panel and revision comparison.

**Exit:** complete v0.1 workflow works without internal tooling.

## Phase 6 - Reconstruction experiments

Only after the editing loop works: plan parsing, depth, multi-view calibration, video/LiDAR ingestion and geometry proposals with confidence/provenance.
