# Geometry-preservation diagnostics

The baseline v0 metric is deliberately advisory. It compares protected structural edges in a reference render/image against the generated image and stores precision, recall and F1.

## Inputs

Each diagnostic is tied to:

- exact Scene revision;
- exact canonical camera;
- reference image Asset;
- generated image Asset;
- optional protected-region mask Asset;
- pixel tolerance and edge threshold.

The worker downloads all input Assets through the normal lease-scoped worker URLs.

## Baseline metric

Images are converted to grayscale, edge-filtered and thresholded. Matching allows a small configurable pixel tolerance by dilating the edge maps. The reported score is edge F1.

A protected-region mask can limit evaluation to walls/openings or other trusted structural regions.

The metric never mutates canonical geometry. A low score is a review signal only.

## Limitations

This baseline is intentionally simple:

- it is sensitive to texture and lighting edges;
- it does not infer metric depth;
- occlusion can reduce recall even if geometry is correct;
- image-generation artifacts can introduce unrelated edges;
- thresholds must be calibrated on the real apartment dataset.

Golden tests only verify ordering: an aligned synthetic sample must score materially better than a deliberately shifted sample.
