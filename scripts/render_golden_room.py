#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from stroy.domain.models import Scene
from stroy.rendering import BlenderAdapter, build_blender_plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scene",
        default="fixtures/golden-room.scene.json",
        help="Canonical STROY scene JSON",
    )
    parser.add_argument(
        "--revision-id",
        default="revision.golden-room",
    )
    parser.add_argument(
        "--camera-id",
        default="camera.living.entry",
    )
    parser.add_argument(
        "--output-dir",
        default="renders/golden-room",
    )
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument(
        "--resolution-scale",
        type=float,
        default=1.0,
        help="Scale camera resolution and intrinsics for smoke renders.",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    scene = Scene.model_validate_json(
        Path(args.scene).read_text(encoding="utf-8")
    )
    if args.resolution_scale <= 0:
        raise ValueError("resolution scale must be positive")
    if args.resolution_scale != 1.0:
        scene = scene.model_copy(deep=True)
        for camera in scene.cameras:
            camera.width_px = max(1, round(camera.width_px * args.resolution_scale))
            camera.height_px = max(1, round(camera.height_px * args.resolution_scale))
            camera.intrinsics.fx *= args.resolution_scale
            camera.intrinsics.fy *= args.resolution_scale
            camera.intrinsics.cx *= args.resolution_scale
            camera.intrinsics.cy *= args.resolution_scale
    plan = build_blender_plan(
        scene,
        scene_revision_id=args.revision_id,
        camera_id=args.camera_id,
    )
    adapter = BlenderAdapter(
        os.getenv("STROY_BLENDER_BIN", "blender"),
        script_path=os.getenv("STROY_BLENDER_SCRIPT", "blender/stroy_blender.py"),
        timeout_seconds=int(os.getenv("STROY_BLENDER_TIMEOUT_SECONDS", "900")),
    )
    outputs = await adapter.run(
        plan,
        output_dir=args.output_dir,
        render=not args.build_only,
    )
    print(
        json.dumps(
            {name: str(path) for name, path in outputs.items()},
            indent=2,
            sort_keys=True,
        )
    )


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
