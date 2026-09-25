# Blender Renderer

Blender is a deterministic execution/render backend, not canonical storage.

Planned responsibilities:

- translate a STROY scene revision into Blender objects/materials/cameras;
- render aligned RGB, depth, normal, object-ID and material-ID passes;
- export optional GLB preview artifacts;
- emit a RenderManifest that references the exact scene/design revision and camera.

Scripts should run headlessly and receive explicit input/output paths. No user project state should exist only inside a .blend file.
