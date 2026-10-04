#!/usr/bin/env python3
"""Manual bare-apartment Blender benchmark harness (issue #21, phase B).

This is a manual decision tool, not a CI job. It renders a synthetic bare-twin
apartment across renderer profiles and records wall time plus peak GPU memory,
so the team can choose between real Blender and depth-conditioned FLUX for the
design loop.

Examples:
    python3 scripts/benchmark_bare_twin.py --build-only
    python3 scripts/benchmark_bare_twin.py \
        --profiles blender-eevee-v0,blender-cycles-gpu-v0

Environment:
    STROY_BLENDER_BIN     Blender executable (default: "blender").
    STROY_BLENDER_SCRIPT  bpy entrypoint (default: "blender/stroy_blender.py").
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.1.0"
SCENE_REVISION_ID = "revision.bare-twin-bench"
FX = 900.0
FY = 900.0

BLENDER_VERSION_RE = re.compile(r"Blender\s+(\d+\.\d+(?:\.\d+)?)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", default="fixtures/bare-twin-bench.scene.json")
    parser.add_argument("--output-dir", default="renders/bench/bare-twin")
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument(
        "--profiles",
        default="blender-eevee-v0,blender-cycles-gpu-v0",
        help="Comma-separated renderer profiles.",
    )
    parser.add_argument(
        "--cameras",
        default="",
        help="Comma-separated camera ids (default: every camera in the scene).",
    )
    parser.add_argument("--runs", type=int, default=2, help="Runs per profile x camera.")
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Launch Blender without --render (geometry/metadata smoke).",
    )
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    return parser.parse_args()


def csv_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def resolve_blender_bin(requested: str) -> str | None:
    found = shutil.which(requested)
    if found:
        return found
    candidate = Path(requested).expanduser()
    if candidate.is_file():
        return str(candidate)
    return None


def parse_blender_version(text: str) -> str | None:
    """Extract the ``x.y[.z]`` token from ``blender --version`` output."""
    match = BLENDER_VERSION_RE.search(text)
    return match.group(1) if match else None


def detect_blender_version(blender_bin: str) -> str | None:
    """Query ``blender_bin --version`` once and return its version token."""
    try:
        result = subprocess.run(
            [blender_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first_line = result.stdout.splitlines()[0] if result.stdout.splitlines() else ""
    return parse_blender_version(first_line)


class VramSampler:
    """Samples ``nvidia-smi`` GPU memory while Blender runs in another thread."""

    def __init__(self, interval: float) -> None:
        self.interval = max(interval, 0.05)
        self.available = shutil.which("nvidia-smi") is not None
        self.peak_mib: int | None = None
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "VramSampler":
        if self.available:
            self._thread = threading.Thread(target=self._sample, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval * 4 + 1)

    def _sample(self) -> None:
        command = [
            "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ]
        while not self._stop.is_set():
            try:
                result = subprocess.run(
                    command, capture_output=True, text=True, timeout=5, check=False
                )
                for line in result.stdout.splitlines():
                    token = line.strip()
                    if not token:
                        continue
                    value = int(token.split()[0])
                    self.peak_mib = (
                        value if self.peak_mib is None else max(self.peak_mib, value)
                    )
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                self.error = str(exc)
                self.available = False
                return
            self._stop.wait(self.interval)


def configure_scene(scene: Any, resolution: int) -> Any:
    """Deep-copy the scene and force benchmark camera intrinsics/resolution."""
    configured = scene.model_copy(deep=True)
    for camera in configured.cameras:
        camera.width_px = resolution
        camera.height_px = resolution
        camera.intrinsics.fx = FX
        camera.intrinsics.fy = FY
        camera.intrinsics.cx = resolution / 2.0
        camera.intrinsics.cy = resolution / 2.0
    return configured


def run_one(
    *,
    adapter: Any,
    build_plan: Any,
    scene: Any,
    profile: str,
    camera_id: str,
    run_index: int,
    run_dir: Path,
    render: bool,
    poll_seconds: float,
) -> dict[str, Any]:
    plan = build_plan(
        scene,
        scene_revision_id=SCENE_REVISION_ID,
        camera_id=camera_id,
        renderer_profile=profile,
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_path = run_dir / "plan.json"
    plan_path.write_text(plan.canonical_json(), encoding="utf-8")

    command = adapter.command(plan_path=plan_path, output_dir=run_dir, render=render)
    sampler = VramSampler(poll_seconds)
    started = time.perf_counter()
    with sampler:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    wall_seconds = time.perf_counter() - started

    (run_dir / "blender.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "blender.stderr.log").write_text(completed.stderr, encoding="utf-8")

    return {
        "profile": profile,
        "camera_id": camera_id,
        "run_index": run_index,
        "warm": run_index > 0,
        "wall_seconds": round(wall_seconds, 3),
        "peak_vram_mib": sampler.peak_mib,
        "nvidia_smi_available": sampler.available,
        "return_code": completed.returncode,
        "output_dir": str(run_dir),
    }


def write_outputs(
    output_dir: Path,
    *,
    scene: str,
    resolution: int,
    blender_bin: str,
    profiles: list[str],
    runs: list[dict[str, Any]],
    warnings: list[str],
    runs_per: int,
    blender_version: str | None = None,
) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "scene": scene,
        "resolution": resolution,
        "blender_bin": blender_bin,
        "blender_version": blender_version,
        "profiles": profiles,
        "runs": runs,
        "warnings": warnings,
    }
    (output_dir / "benchmark.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines = [
        "# Bare-twin Blender benchmark",
        "",
        f"- Scene: `{scene}`",
        f"- Resolution: {resolution}",
        f"- Blender: `{blender_bin}`",
        f"- Blender version: {blender_version or 'unknown'}",
        f"- Profiles: {', '.join(f'`{p}`' for p in profiles)}",
        f"- Runs per profile x camera: {runs_per}",
        "",
        "| Profile | Camera | Run | Cold/Warm | Wall s | Peak VRAM MiB | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run in runs:
        peak = "n/a" if run["peak_vram_mib"] is None else str(run["peak_vram_mib"])
        note = "ok" if run["return_code"] == 0 else f"rc={run['return_code']}"
        temperature = "Warm" if run["warm"] else "Cold"
        lines.append(
            f"| `{run['profile']}` | `{run['camera_id']}` | {run['run_index']} | "
            f"{temperature} | {run['wall_seconds']:.3f} | {peak} | {note} |"
        )
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    (output_dir / "benchmark.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")
    if args.resolution < 1:
        raise SystemExit("--resolution must be >= 1")

    requested_bin = os.getenv("STROY_BLENDER_BIN", "blender")
    blender_bin = resolve_blender_bin(requested_bin)
    if blender_bin is None:
        raise SystemExit(
            f"Blender binary not found: {requested_bin}. Install Blender or set "
            "STROY_BLENDER_BIN=/path/to/blender"
        )
    script_path = os.getenv("STROY_BLENDER_SCRIPT", "blender/stroy_blender.py")
    if not Path(script_path).is_file():
        raise SystemExit(f"Blender script not found: {script_path}")

    # Record the Blender version once; per-run metadata from the renderer only
    # lands on successful renders, so the benchmark report needs its own copy.
    blender_version = detect_blender_version(blender_bin)

    # Imported only after the fail-fast check so the harness reports a clear
    # message even when the project virtualenv is not active.
    from stroy.domain.models import Scene
    from stroy.rendering import BlenderAdapter, build_blender_plan

    scene_path = Path(args.scene)
    base_scene = Scene.model_validate_json(scene_path.read_text(encoding="utf-8"))
    profiles = csv_list(args.profiles)
    if not profiles:
        raise SystemExit("--profiles must list at least one renderer profile")
    known_cameras = {camera.id for camera in base_scene.cameras}
    camera_ids = csv_list(args.cameras) or [camera.id for camera in base_scene.cameras]
    unknown = [camera_id for camera_id in camera_ids if camera_id not in known_cameras]
    if unknown:
        raise SystemExit(f"unknown camera(s): {', '.join(unknown)}")

    adapter = BlenderAdapter(blender_bin, script_path=script_path)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    if shutil.which("nvidia-smi") is None:
        warnings.append("nvidia-smi not found; peak VRAM is reported as null")

    records: list[dict[str, Any]] = []
    failed = 0
    for profile in profiles:
        for camera_id in camera_ids:
            for run_index in range(args.runs):
                run_dir = output_dir / profile / camera_id / f"run-{run_index}"
                record = run_one(
                    adapter=adapter,
                    build_plan=build_blender_plan,
                    scene=configure_scene(base_scene, args.resolution),
                    profile=profile,
                    camera_id=camera_id,
                    run_index=run_index,
                    run_dir=run_dir,
                    render=not args.build_only,
                    poll_seconds=args.poll_seconds,
                )
                if record["return_code"] != 0:
                    failed += 1
                    warnings.append(
                        f"{profile}/{camera_id}/run-{run_index} exited "
                        f"{record['return_code']} (see {run_dir / 'blender.stderr.log'})"
                    )
                records.append(record)
                print(
                    json.dumps(
                        {
                            key: record[key]
                            for key in (
                                "profile",
                                "camera_id",
                                "run_index",
                                "warm",
                                "wall_seconds",
                                "peak_vram_mib",
                                "return_code",
                            )
                        },
                        sort_keys=True,
                    )
                )

    write_outputs(
        output_dir,
        scene=str(scene_path),
        resolution=args.resolution,
        blender_bin=blender_bin,
        blender_version=blender_version,
        profiles=profiles,
        runs=records,
        warnings=warnings,
        runs_per=args.runs,
    )
    print(f"wrote {output_dir / 'benchmark.json'}")
    print(f"wrote {output_dir / 'benchmark.md'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
