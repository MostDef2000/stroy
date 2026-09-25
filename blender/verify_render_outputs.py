from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def _args() -> argparse.Namespace:
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args(argv)


def main() -> int:
    args = _args()
    output = Path(args.output_dir)
    metadata = json.loads(
        (output / "scene_metadata.json").read_text(encoding="utf-8")
    )
    camera = metadata["camera"]
    expected = (int(camera["width_px"]), int(camera["height_px"]))

    files = {
        "rgb": output / "rgb.png",
        "depth": output / "depth.exr",
        "normals": output / "normals.exr",
        "object_ids": output / "object_ids.exr",
        "material_ids": output / "material_ids.exr",
    }
    sizes = {}
    for name, path in files.items():
        image = bpy.data.images.load(str(path), check_existing=False)
        size = (int(image.size[0]), int(image.size[1]))
        sizes[name] = size
        bpy.data.images.remove(image)
        if size != expected:
            raise RuntimeError(
                f"{name} pass has size {size}, expected {expected}"
            )

    print(json.dumps({"expected": expected, "passes": sizes}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
