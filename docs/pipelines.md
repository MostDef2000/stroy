# Pipelines

## Asset ingestion

```text
upload -> validate -> checksum -> store original -> metadata -> Asset record
```

Originals are immutable. Assets carry a role (`apartment`, `reference`, or `derived`), SHA-256 checksum, basic media metadata, provenance, and source-asset lineage. Same-project checksum duplicates are retained as immutable records but explicitly point at the first matching asset through dedup metadata.

Production MinIO remains private. Browser downloads are authenticated through the STROY API. Home-worker transfer URLs are object-scoped by job lease and require the worker credential; they expire logically when the lease is invalidated or expires.

## Scene construction

v0.1 uses trusted/manual geometry first:

```text
measurements/manual editor -> normalized geometry -> semantic IDs -> validation -> scene revision
```

Future reconstruction systems only propose geometry; accepted geometry enters the same validation/revision path.

## Camera calibration

Input: canonical geometry + real photograph + candidate camera parameters.

Output: persisted intrinsics, transform, quality/residual and provenance.

## Deterministic rendering

```text
scene revision -> Blender adapter -> RGB/depth/normals/object IDs/material IDs -> RenderManifest
```

Every pass uses the same camera and exact scene revision.

## Reference analysis

```text
reference images + user text -> multimodal analysis -> StyleProfile proposal -> persisted profile
```

## Design generation

```text
scene revision
+ design revision
+ camera
+ control passes
+ StyleProfile
+ user instruction
        |
        v
generation adapter / versioned workflow
        |
        v
generated image + GenerationManifest
```

## Edit loop

```text
user text/image
 -> Qwen intent parsing
 -> typed tool calls
 -> lock validation
 -> design commands
 -> new revision
 -> targeted render/generation
```

Localized edits should prefer masks/inpainting when supported.

## Geometry-preservation diagnostics

Generated output is never canonical geometry. At minimum, the pipeline should compare protected control edges/alignment and record a diagnostic preservation score for review.


## Durable job execution

```text
API enqueue
 -> PostgreSQL Job row (durable truth)
 -> optional Redis wakeup notification
 -> worker claim + bounded lease
 -> progress / lease renewal
 -> output Asset registration
 -> succeeded | failed | cancelled
```

An optional idempotency key prevents duplicate logical jobs for the same project/job family. Redis is only coordination/wakeup infrastructure; losing Redis never deletes the PostgreSQL job record. GPU jobs without a compatible claimant remain `waiting_for_worker`. Lease expiry requeues the same durable Job with an incremented attempt on the next claim.
