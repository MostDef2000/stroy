# Generative Workflows

Versioned image-generation workflows live here.

Each workflow must have a STROY-owned manifest defining stable semantic inputs/outputs such as RGB, depth, normals, masks, reference images, style profile and output image.

Product/API code must never depend directly on ComfyUI node IDs. A workflow adapter is responsible for mapping the stable manifest to a concrete ComfyUI graph/version.
