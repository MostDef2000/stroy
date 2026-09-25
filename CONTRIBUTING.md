# Contributing to STROY

## Branching

Use short-lived branches from `main`.

Suggested prefixes:

- `feat/`
- `fix/`
- `docs/`
- `chore/`
- `spike/`

## Pull requests

A pull request should:

- reference the GitHub issue it implements;
- keep domain contracts separate from runtime-specific code;
- include or update tests for changed behavior;
- update JSON Schemas when public contracts change;
- update an ADR when an architectural decision changes;
- document new model/runtime requirements and licensing notes.

## Architecture guardrails

1. Canonical scene state must not depend on Blender, Three.js or ComfyUI formats.
2. Qwen may call typed tools; it must not receive unrestricted persistence access.
3. Image generation never mutates physical scene geometry.
4. User-visible edits create commands/revisions.
5. Uploaded and generated binaries belong in object storage, not git or PostgreSQL.
6. Model/runtime selection uses profiles/adapters rather than hard-coded checkpoint paths.

## Contract changes

For changes under `schemas/`:

- preserve `schema_version`;
- make breaking changes explicit;
- add migration notes before changing persisted production data;
- provide a representative fixture once contract tests are implemented.

## Before requesting review

Run:

```bash
make check
```

Do not commit model weights, user apartment data, generated renders or secrets.
