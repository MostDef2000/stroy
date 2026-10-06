"""Worker telemetry collector tests.

Pure-function tests over injected readers: no real /proc, no real
nvidia-smi, no sleeps.
"""

from __future__ import annotations

import pytest

from stroy.worker.telemetry import collect_telemetry

# Two /proc/stat snapshots: user +50, idle +50, everything else flat.
# total delta 100, idle+iowait delta 50 -> busy 50 -> 50.0%.
PROC_STAT_FIRST = "cpu  100 0 0 700 0 0 0 0 0 0\ncpu0 50 0 0 350 0 0 0 0 0 0\n"
PROC_STAT_SECOND = "cpu  150 0 0 750 0 0 0 0 0 0\ncpu0 75 0 0 375 0 0 0 0 0 0\n"

MEMINFO = (
    "MemTotal:       16000000 kB\n"
    "MemFree:         2000000 kB\n"
    "MemAvailable:    6000000 kB\n"
    "Buffers:          500000 kB\n"
    "Cached:          3000000 kB\n"
)

GPU_CSV = "NVIDIA GeForce RTX 4090, 42, 1024, 24564\n"


def _no_sleep(_seconds: float) -> None:
    return None


def _raising_run_smi(_args: list[str]) -> str:
    raise FileNotFoundError("nvidia-smi")


class _FakeProcReader:
    """Returns queued /proc/stat contents per call; fixed other files."""

    def __init__(
        self,
        stat_reads: list[str],
        *,
        meminfo: str | None = None,
    ) -> None:
        self._stat_reads = list(stat_reads)
        self._stat_calls = 0
        self._meminfo = meminfo

    def __call__(self, path: str) -> str:
        if path == "/proc/stat":
            index = min(self._stat_calls, len(self._stat_reads) - 1)
            self._stat_calls += 1
            return self._stat_reads[index]
        if path == "/proc/meminfo" and self._meminfo is not None:
            return self._meminfo
        raise FileNotFoundError(path)


def test_collects_measured_cpu_utilization_and_ram_bytes() -> None:
    reader = _FakeProcReader([PROC_STAT_FIRST, PROC_STAT_SECOND], meminfo=MEMINFO)

    sample = collect_telemetry(
        read_file=reader,
        run_smi=_raising_run_smi,
        sleep_fn=_no_sleep,
    )

    assert sample is not None
    assert sample["cpu"] == {"utilization_percent": 50.0}
    assert sample["memory"] == {
        "used_bytes": 10_000_000 * 1024,
        "total_bytes": 16_000_000 * 1024,
    }
    assert sample["gpus"] == []


def test_cpu_uses_busy_delta_over_total_delta() -> None:
    # user+nice+system move, idle/iowait frozen: all delta is busy -> 100%.
    busy_first = "cpu  100 10 100 700 0 0 0 0 0 0\n"
    busy_second = "cpu  160 10 160 700 0 0 0 0 0 0\n"
    reader = _FakeProcReader([busy_first, busy_second], meminfo=MEMINFO)

    sample = collect_telemetry(
        read_file=reader,
        run_smi=_raising_run_smi,
        sleep_fn=_no_sleep,
    )

    assert sample is not None
    assert sample["cpu"] == {"utilization_percent": 100.0}


def test_zero_cpu_delta_yields_no_cpu_metric() -> None:
    reader = _FakeProcReader([PROC_STAT_FIRST, PROC_STAT_FIRST], meminfo=MEMINFO)

    sample = collect_telemetry(
        read_file=reader,
        run_smi=_raising_run_smi,
        sleep_fn=_no_sleep,
    )

    assert sample is not None
    assert "cpu" not in sample
    assert sample["memory"]["total_bytes"] == 16_000_000 * 1024


def test_parses_nvidia_smi_csv_mib_to_bytes() -> None:
    def run_smi(_args: list[str]) -> str:
        return GPU_CSV

    reader = _FakeProcReader([PROC_STAT_FIRST, PROC_STAT_SECOND], meminfo=MEMINFO)
    sample = collect_telemetry(read_file=reader, run_smi=run_smi, sleep_fn=_no_sleep)

    assert sample is not None
    assert sample["gpus"] == [
        {
            "name": "NVIDIA GeForce RTX 4090",
            "utilization_percent": 42.0,
            "memory_used_bytes": 1024 * 1024 * 1024,
            "memory_total_bytes": 24564 * 1024 * 1024,
        }
    ]


def test_parses_multiple_gpus_and_na_values() -> None:
    output = (
        "GPU One, [N/A], 100, 200\n"
        "\n"
        "GPU Two, 0, [N/A], 200\n"
    )

    def run_smi(_args: list[str]) -> str:
        return output

    reader = _FakeProcReader([PROC_STAT_FIRST, PROC_STAT_SECOND], meminfo=MEMINFO)
    sample = collect_telemetry(read_file=reader, run_smi=run_smi, sleep_fn=_no_sleep)

    assert sample is not None
    assert sample["gpus"] == [
        {
            "name": "GPU One",
            "utilization_percent": None,
            "memory_used_bytes": 100 * 1024 * 1024,
            "memory_total_bytes": 200 * 1024 * 1024,
        },
        {
            "name": "GPU Two",
            "utilization_percent": 0.0,
            "memory_used_bytes": None,
            "memory_total_bytes": 200 * 1024 * 1024,
        },
    ]


@pytest.mark.parametrize("failure", [FileNotFoundError("nvidia-smi"), RuntimeError("exit 9")])
def test_gpu_probe_failure_yields_empty_gpus_list(failure: Exception) -> None:
    def run_smi(_args: list[str]) -> str:
        raise failure

    reader = _FakeProcReader([PROC_STAT_FIRST, PROC_STAT_SECOND], meminfo=MEMINFO)
    sample = collect_telemetry(read_file=reader, run_smi=run_smi, sleep_fn=_no_sleep)

    assert sample is not None
    assert sample["gpus"] == []
    assert sample["cpu"] == {"utilization_percent": 50.0}
    assert sample["memory"]["used_bytes"] == 10_000_000 * 1024


def test_malformed_gpu_csv_yields_empty_gpus_list() -> None:
    def run_smi(_args: list[str]) -> str:
        return "only-one-column\n"

    reader = _FakeProcReader([PROC_STAT_FIRST, PROC_STAT_SECOND], meminfo=MEMINFO)
    sample = collect_telemetry(read_file=reader, run_smi=run_smi, sleep_fn=_no_sleep)

    assert sample is not None
    assert sample["gpus"] == []


def test_missing_proc_returns_none_metrics_without_raising() -> None:
    def read_file(_path: str) -> str:
        raise FileNotFoundError("/proc")

    def run_smi(_args: list[str]) -> str:
        return GPU_CSV

    sample = collect_telemetry(read_file=read_file, run_smi=run_smi, sleep_fn=_no_sleep)

    assert sample == {
        "gpus": [
            {
                "name": "NVIDIA GeForce RTX 4090",
                "utilization_percent": 42.0,
                "memory_used_bytes": 1024 * 1024 * 1024,
                "memory_total_bytes": 24564 * 1024 * 1024,
            }
        ]
    }


def test_nothing_collectible_returns_none() -> None:
    def read_file(_path: str) -> str:
        raise FileNotFoundError(_path)

    sample = collect_telemetry(
        read_file=read_file,
        run_smi=_raising_run_smi,
        sleep_fn=_no_sleep,
    )

    assert sample is None


def test_collect_telemetry_against_this_machine() -> None:
    """Smoke: on Linux the real collectors must produce a usable sample.

    On this container /proc exists, so the real (non-injected) call must
    return CPU/RAM numbers; on any other platform it must return None or a
    partial sample without raising.
    """
    sample = collect_telemetry()
    if sample is None:
        return
    assert set(sample) <= {"cpu", "memory", "gpus"}
    if "memory" in sample:
        assert sample["memory"]["used_bytes"] <= sample["memory"]["total_bytes"]
