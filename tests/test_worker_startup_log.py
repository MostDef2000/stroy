"""Worker startup log line (#74): one structured, secret-free record."""

from stroy.worker.main import format_startup_line


def test_startup_line_reports_identity_capabilities_executors_and_blender() -> None:
    line = format_startup_line(
        pid=4321,
        worker_id="home-gpu-01",
        worker_name="Home GPU worker",
        mode="local",
        server="http://api:8000",
        capabilities=["llm", "blender_render"],
        executors={"render.blender": object(), "llm.complete": object()},
        blender_bin="/opt/blender/blender",
        blender_timeout_seconds=900,
    )

    assert line.startswith("worker.startup ")
    assert "\n" not in line
    assert "pid=4321" in line
    assert "worker_id=home-gpu-01" in line
    assert 'worker_name="Home GPU worker"' in line
    assert "mode=local" in line
    assert "server=http://api:8000" in line
    assert "capabilities=llm,blender_render" in line
    assert "executors=llm.complete,render.blender" in line
    assert "blender_bin=/opt/blender/blender" in line
    assert "blender_timeout_s=900" in line
    # The formatter takes no token/secret argument and must never echo one.
    assert "token" not in line.lower()


def test_startup_line_omits_git_revision_when_unknown() -> None:
    line = format_startup_line(
        pid=1,
        worker_id="w",
        worker_name="n",
        mode="fake",
        server="http://127.0.0.1:8000",
        capabilities=[],
        executors={},
        blender_bin="blender",
        blender_timeout_seconds=900,
    )

    assert "git_revision" not in line


def test_startup_line_includes_git_revision_when_available() -> None:
    line = format_startup_line(
        pid=1,
        worker_id="w",
        worker_name="n",
        mode="local",
        server="http://api:8000",
        capabilities=["llm"],
        executors={"llm.complete": object()},
        blender_bin="blender",
        blender_timeout_seconds=900,
        git_revision="abc1234",
    )

    assert "git_revision=abc1234" in line
