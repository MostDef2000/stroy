"""Worker telemetry collection.

Collects real CPU/RAM numbers from Linux ``/proc`` and GPU numbers from
``nvidia-smi``. Every metric fails soft: a missing file, a missing binary or
a parse error yields ``None`` fields (or an empty ``gpus`` list), never an
exception — a heartbeat must never fail because telemetry collection failed.

The collectors are pure functions over injectable readers so tests can stub
``/proc`` contents and the ``nvidia-smi`` subprocess without touching the
real machine:

- ``read_file(path) -> str`` defaults to reading real files;
- ``run_smi(args) -> str`` defaults to running the real ``nvidia-smi``;
- ``sleep_fn(seconds)`` defaults to :func:`time.sleep` (used only for the
  measured CPU utilization interval).
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from typing import Any

_SMI_TIMEOUT_SECONDS = 0.5
_MIB = 1024 * 1024
_CPU_SAMPLE_SECONDS = 0.1


def _default_read_file(path: str) -> str:
    with open(path, encoding="ascii") as handle:
        return handle.read()


def _default_run_smi(args: list[str]) -> str:
    result = subprocess.run(
        ["nvidia-smi", *args],
        capture_output=True,
        text=True,
        timeout=_SMI_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"nvidia-smi exited with code {result.returncode}")
    return result.stdout


def _parse_cpu_times(content: str) -> tuple[int, ...] | None:
    for line in content.splitlines():
        if line.startswith("cpu "):
            fields = line.split()[1:]
            if not fields:
                return None
            return tuple(int(value) for value in fields)
    return None


def _cpu_utilization_percent(
    read_file: Callable[[str], str],
    sleep_fn: Callable[[float], None],
) -> float | None:
    """Measured utilization over a real interval (busy delta / total delta).

    Mirrors the standard psutil formula: busy time is everything except
    ``idle`` and ``iowait``; the two counters come from ``/proc/stat``'s
    aggregate ``cpu`` line read before and after a short sleep.
    """
    try:
        first = _parse_cpu_times(read_file("/proc/stat"))
        if first is None or len(first) < 4:
            return None
        sleep_fn(_CPU_SAMPLE_SECONDS)
        second = _parse_cpu_times(read_file("/proc/stat"))
        if second is None or len(second) < 4:
            return None
    except Exception:
        return None

    width = max(len(first), len(second))
    first = first + (0,) * (width - len(first))
    second = second + (0,) * (width - len(second))
    deltas = [after - before for before, after in zip(first, second, strict=True)]
    total = sum(deltas)
    if total <= 0:
        return None
    idle = deltas[3] + (deltas[4] if len(deltas) > 4 else 0)
    busy = total - idle
    percent = busy / total * 100.0
    return max(0.0, min(100.0, percent))


def _parse_meminfo_kb(line: str) -> int:
    return int(line.split(":")[1].strip().split()[0])


def _memory_usage(read_file: Callable[[str], str]) -> dict[str, int] | None:
    try:
        content = read_file("/proc/meminfo")
    except Exception:
        return None
    total_kb: int | None = None
    available_kb: int | None = None
    try:
        for line in content.splitlines():
            if line.startswith("MemTotal:"):
                total_kb = _parse_meminfo_kb(line)
            elif line.startswith("MemAvailable:"):
                available_kb = _parse_meminfo_kb(line)
            if total_kb is not None and available_kb is not None:
                break
    except (ValueError, IndexError):
        return None
    if total_kb is None or available_kb is None:
        return None
    used_kb = max(0, total_kb - available_kb)
    return {"used_bytes": used_kb * 1024, "total_bytes": total_kb * 1024}


def _percent_or_none(raw: str) -> float | None:
    try:
        return max(0.0, min(100.0, float(raw)))
    except ValueError:
        return None


def _bytes_or_none(raw: str) -> int | None:
    try:
        return int(raw) * _MIB
    except ValueError:
        return None


def _query_gpus(run_smi: Callable[[list[str]], str]) -> list[dict[str, Any]]:
    try:
        output = run_smi(
            [
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ]
        )
    except Exception:
        return []
    gpus: list[dict[str, Any]] = []
    try:
        for line in output.splitlines():
            if not line.strip():
                continue
            name, utilization, memory_used, memory_total = (
                part.strip() for part in line.split(",")
            )
            gpus.append(
                {
                    "name": name or None,
                    "utilization_percent": _percent_or_none(utilization),
                    "memory_used_bytes": _bytes_or_none(memory_used),
                    "memory_total_bytes": _bytes_or_none(memory_total),
                }
            )
    except (ValueError, TypeError):
        return []
    return gpus


def collect_telemetry(
    *,
    read_file: Callable[[str], str] | None = None,
    run_smi: Callable[[list[str]], str] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> dict[str, Any] | None:
    """Collect one telemetry sample.

    Returns ``None`` only when nothing at all could be collected. A
    CPU/RAM-only machine reports an empty ``gpus`` list.
    """
    read_file = read_file or _default_read_file
    run_smi = run_smi or _default_run_smi
    sleep_fn = sleep_fn or time.sleep

    cpu_percent: float | None = None
    memory: dict[str, int] | None = None
    gpus: list[dict[str, Any]] = []

    try:
        cpu_percent = _cpu_utilization_percent(read_file, sleep_fn)
    except Exception:
        cpu_percent = None
    try:
        memory = _memory_usage(read_file)
    except Exception:
        memory = None
    try:
        gpus = _query_gpus(run_smi)
    except Exception:
        gpus = []

    if cpu_percent is None and memory is None and not gpus:
        return None
    sample: dict[str, Any] = {}
    if cpu_percent is not None:
        sample["cpu"] = {"utilization_percent": cpu_percent}
    if memory is not None:
        sample["memory"] = memory
    sample["gpus"] = gpus
    return sample
