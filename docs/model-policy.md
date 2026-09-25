# Model and License Policy

## Current project scope

STROY is a **personal, non-commercial, single-user renovation project**. Qwen and FLUX run locally on the owner's computer and are used for the owner's apartment-design experiments.

Model selection is configuration, not architecture. License metadata is retained mainly so the exact model provenance and permitted scope remain clear if the project changes later.

## Principles

1. Every model is referenced through a model profile ID.
2. A profile records model name, source, license note, runtime requirements and intended use.
3. Workflows record the exact model profile used.
4. The current approved use mode is `personal-non-commercial`.
5. Model weights are never committed to this repository.
6. If STROY later becomes commercial, shared with end users, or operated as a service, model terms must be re-reviewed before that change.

## Initial LLM profile

### qwen3-14b

- upstream: `Qwen/Qwen3-14B`
- purpose: scene/design agent, structured extraction and tool calling
- license: Apache-2.0 according to the upstream model card
- runtime: OpenAI-compatible local server
- current use: personal non-commercial local use

Quantized derivatives must record source and quantization method.

## Image profiles

### flux1-schnell

- upstream: `black-forest-labs/FLUX.1-schnell`
- runtime: ComfyUI adapter
- license metadata: Apache-2.0 according to the upstream model card
- current use: personal non-commercial local use

### flux-dev-family

FLUX `[dev]` is an acceptable STROY target for the current project scope because the project is personal and non-commercial. The current BFL non-commercial terms explicitly include personal study/private entertainment/hobby-project style uses when they are not connected to commercial activity.

- runtime: ComfyUI adapter
- current use: personal non-commercial local use
- status: allowed for the current project scope, subject to the exact model's applicable BFL terms

This is project configuration documentation, not a substitute for re-checking the applicable license if the project's purpose changes.

## Provenance

Generation manifests should still record:

- exact model profile;
- workflow version;
- model/quantization source when relevant;
- seed when supported;
- inputs and outputs.

The purpose is reproducibility and future-proofing, not commercial licensing enforcement for v0.1.


## CI enforcement

`scripts/validate_model_profiles.py` validates every entry in
`config/model-profiles.json` against `schemas/model-profile.schema.json` and
also requires:

- a unique profile ID;
- an explicit license source;
- a review date;
- `personal-non-commercial` in the approved-use list;
- `flux-dev-family` to remain marked `restricted`;
- an explicit license re-review note for the FLUX dev family.

The configuration reviewed on 2026-09-25 records Qwen3-14B and FLUX.1-schnell
as Apache-2.0 upstream profiles and the FLUX dev family under the applicable
BFL non-commercial terms. These are provenance records, not a substitute for
reviewing terms again if the project purpose changes.
