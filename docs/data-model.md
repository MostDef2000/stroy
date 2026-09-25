# Canonical Data Model

## Goals

The scene model must be deterministic, serializable, versioned, runtime-independent, semantically addressable by an LLM, and safe to migrate.

## Coordinate conventions

Initial convention:

- persisted architectural units: millimetres;
- right-handed coordinate system;
- +Z is up;
- transforms are explicit;
- camera intrinsics/extrinsics are persisted.

Runtime adapters may convert units internally but persisted values retain declared units.

## Stable entity IDs

Examples:

```text
room.living
surface.wall.living.north
opening.window.living.01
opening.door.living.hall
camera.living.entry
object.sofa.main
```

Display names may change; stable IDs should not.

## Geometry vs appearance

Geometry and appearance are separate. An entity can have geometry and transform locked while material remains editable.

## Commands

User and agent edits are typed domain commands rather than arbitrary JSON patches. Initial operations:

- set_material
- set_color
- add_object
- remove_object
- replace_object_from_reference
- move_object
- set_light_intent

Each command carries the expected base revision, target, parameters, origin and optional reference assets. Locks are validated before mutation.

## Revisions

Revisions are immutable. v0.1 may store complete snapshots for simplicity. Later versions may introduce snapshots plus deltas/events.

Revision metadata includes parent revision, command ID, origin, timestamp, schema version and content hash.

## StyleProfile

A StyleProfile is structured interpretation of text/images: labels, palette, materials, form vocabulary, lighting intent, negative constraints and source assets.

It is design input, not physical truth.

## Provenance

Derived facts should record provenance such as user, imported, measured, estimated or model_inferred. Estimated dimensions must never be indistinguishable from trusted measurements.
