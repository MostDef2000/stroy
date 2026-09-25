# STROY Product Scope

## Product thesis

STROY creates a persistent, editable digital twin of a real apartment and uses local AI to explore interior-design variants without silently changing the physical apartment.

The product is intentionally split into two concerns:

- **truth layer:** geometry, openings, camera calibration, dimensions and technical constraints;
- **design layer:** materials, furniture, lighting intent, style references and generated appearance.

The truth layer is protected by explicit locks and revision rules. Generative models may interpret or visualize it, but may not mutate locked geometry implicitly.

## Core user workflow

1. Create a project.
2. Upload trusted apartment data: plan, measurements, photos and optional video.
3. Build or correct the canonical scene.
4. Calibrate one or more cameras against real photos.
5. Upload style references and/or describe a style in text.
6. Generate design variants.
7. Edit a variant with text or image references.
8. Compare revisions, undo/redo, and render multiple views from the same scene state.

## v0.1 acceptance target

v0.1 proves a single vertical slice for **one room**.

### Required

- trusted/manual room geometry can be represented in the canonical scene;
- walls, floor, ceiling, doors and windows have stable semantic IDs;
- at least one calibrated camera can render deterministic control passes;
- 3-5 style references can be reduced into a structured StyleProfile;
- image generation receives geometry control data;
- locked geometry survives design generation;
- material/color edit, furniture removal and reference replacement work;
- every accepted edit creates a revision;
- undo/redo works at the design-command level.

### Explicitly out of scope for v0.1

- fully automatic whole-apartment reconstruction;
- production-grade DWG/DXF/BIM import;
- construction documentation;
- cost estimation and procurement;
- code-compliance validation;
- automatic electrical/plumbing design.

## Product invariants

1. **Scene before pixels.** Generated images are projections of scene state, not the source of truth.
2. **Stable IDs.** Editable scene entities have durable semantic IDs.
3. **No hidden geometry mutation.** A design edit must not alter locked geometry.
4. **Reproducibility.** Generation records model profile, workflow version, inputs and seed when supported.
5. **Non-destructive editing.** User-visible edits are represented as commands and revisions.
6. **Replaceable AI runtimes.** Domain objects do not depend on a specific LLM or diffusion checkpoint.
7. **Local-first.** The default architecture assumes models and assets can run locally.
