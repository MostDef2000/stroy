# STROY

Personal, local-first AI interior design for renovating a **geometry-preserving digital twin** of a real apartment.

STROY is not an image-to-image interior generator. The product separates the immutable physical apartment from the editable design layer:

1. ingest plans, measurements, photos/video and later LiDAR/DWG/DXF;
2. reconstruct a canonical semantic 3D scene;
3. render geometry control passes (RGB, depth, normals, object/material IDs);
4. infer a reusable style profile from text and image references;
5. generate or edit photorealistic views without silently changing the apartment geometry;
6. keep every design operation versioned, inspectable and reversible.

## Architecture at a glance

```text
User / Web UI
    |
    v
API + Project Service
    |
    +--> Scene Core (canonical source of truth)
    |       +--> scene.json / schemas
    |       +--> revisions + design commands
    |
    +--> Qwen Agent Adapter (OpenAI-compatible local endpoint)
    |       +--> scene tools
    |       +--> style/reference analysis
    |
    +--> GPU Job Queue
            +--> Blender headless renderer
            |       +--> RGB / depth / normals / masks
            |
            +--> ComfyUI image runtime
                    +--> FLUX / compatible image-edit workflows
```

## Repository layout

```text
apps/                  Product applications (web, API)
core/                  Domain contracts and scene semantics
services/              Runtime adapters: LLM, image generation, renderer
schemas/               Versioned JSON Schemas
workflows/             Versioned generative workflows
blender/               Headless Blender integration
docs/                  Architecture, ADRs and product/engineering docs
scripts/               Repository utilities
.github/                CI and GitHub metadata
```

Directories are introduced incrementally by implementation issues. The contracts in `schemas/` and the ADRs in `docs/adr/` are the first source of truth.

## Initial technology direction

- **Scene/geometry:** canonical STROY scene model; Blender is an execution/render engine, not the source of truth.
- **LLM/agent:** Qwen 14B-class model behind an OpenAI-compatible adapter. Initial target profile: `Qwen/Qwen3-14B`.
- **Image runtime:** ComfyUI behind an internal adapter. Workflows are versioned and model-specific.
- **3D web:** Three.js / React Three Fiber is the intended viewer layer.
- **API:** Python + FastAPI/Pydantic is the intended control plane.
- **Persistence:** PostgreSQL for metadata/revisions, S3-compatible object storage for assets, Redis for job coordination.
- **Rendering:** Blender headless for deterministic camera/control-pass generation.

Model implementations are deliberately swappable. Product contracts must never depend directly on a specific ComfyUI graph or model checkpoint.

## Deployment and access

STROY is currently a **single-user, non-commercial personal project** running on the owner's computer for planning renovation of the owner's apartment.

- Qwen, FLUX/ComfyUI and Blender run locally.
- The web application and API require authentication.
- There is no public registration or multi-user/RBAC scope in v0.1.
- Database, Redis, object storage and model runtimes are not intended to be directly exposed to the public network.

## Model policy

The current personal non-commercial scope allows STROY to evaluate/use FLUX `[dev]` profiles under their applicable non-commercial terms. Exact model/profile provenance is still recorded for reproducibility. See `docs/model-policy.md`.

## MVP definition

The first vertical slice is intentionally narrow:

- one room;
- trusted/manual geometry import;
- one to three calibrated cameras;
- 3–5 style/reference images;
- generate a redesign while preserving locked geometry;
- edit a named object/material using text or a replacement image;
- compare revisions and undo/redo.

Automated whole-apartment reconstruction is **not** required for v0.1.

## Development

```bash
cp .env.example .env
make infra-up
make check
```

GPU runtimes (Qwen, ComfyUI and Blender) are installed separately because their setup is hardware-specific. See `docs/local-development.md`.

## Documentation

Start here:

- [Product scope](docs/product-scope.md)
- [System architecture](docs/architecture.md)
- [Canonical data model](docs/data-model.md)
- [Pipelines](docs/pipelines.md)
- [Local development](docs/local-development.md)
- [Model and license policy](docs/model-policy.md)
- [Roadmap](docs/roadmap.md)
- [Architecture decisions](docs/adr/)

## Project status

**Bootstrap complete; v0.1 implementation ready to start.**

Start with the umbrella epic: [#25 — One-room geometry-preserving AI interior design](https://github.com/MostDef2000/stroy/issues/25).

Critical path: Scene/API foundation (#1–#5) + single-user auth (#26) → Blender/camera/control passes (#6–#8) → Qwen/style/ComfyUI (#9–#13) → preservation + editing (#14–#16) → web editor (#17–#19) → end-to-end acceptance (#20).

