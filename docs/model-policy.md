# Model and License Policy

Model selection is configuration, not architecture.

## Principles

1. Every model is referenced through a model profile ID.
2. A profile records model name, source, license note, runtime requirements and intended use.
3. Workflows record the exact model profile used.
4. Production/commercial deployment requires explicit license review for non-permissive model dependencies.
5. Model weights are never committed to this repository.

## Initial LLM profile

### qwen3-14b

- upstream: Qwen/Qwen3-14B
- purpose: scene/design agent, structured extraction and tool calling
- license: Apache-2.0 according to the upstream model card
- runtime: OpenAI-compatible local server

Quantized derivatives must record source and quantization method.

## Initial image profiles

### flux1-schnell

- upstream: black-forest-labs/FLUX.1-schnell
- bootstrap/local generation profile
- upstream model card publishes Apache-2.0
- runtime: ComfyUI adapter

### flux-dev-family

Do not treat FLUX [dev] profiles as interchangeable with schnell for licensing. Before commercial production use, confirm the exact checkpoint and applicable license, archive the reviewed license/version, and record the decision in an ADR.

## Future manifest

A future models/profiles.yaml should record profile ID, upstream source, runtime, license metadata, intended use and approval state.
