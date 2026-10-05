"""Tests for the Stage-3 deploy gate + bundle/manifest helpers (issue #130).

These are unit tests: no docker, no network.  They import the pure logic from
``scripts/deploy`` by inserting the repo root on ``sys.path`` (pytest only adds
``tests/`` because there is no ``tests/__init__.py``).
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.deploy import bundle_utils, deploy_gate  # noqa: E402

SHA = "a" * 40
IMAGE_ID = "sha256:" + "b" * 64
RUN_URL = "https://github.com/MostDef2000/stroy/actions/runs/123456"


# --------------------------------------------------------------------------- #
# sha validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sha", [SHA, "0" * 40, "abcdef0123456789abcdef0123456789abcdef01"])
def test_validate_sha_accepts_full_lowercase_hex(sha: str) -> None:
    assert deploy_gate.validate_sha(sha) is True


@pytest.mark.parametrize(
    "sha",
    [
        "",
        "a" * 12,
        "a" * 39,
        "a" * 41,
        "main",
        "A" * 40,
        "g" * 40,
        "a" * 39 + "Z",
        None,
        12345,
    ],
)
def test_validate_sha_rejects_everything_else(sha) -> None:
    assert deploy_gate.validate_sha(sha) is False


# --------------------------------------------------------------------------- #
# run selection
# --------------------------------------------------------------------------- #
def _run(**overrides) -> dict:
    base = {
        "id": 1,
        "head_sha": SHA,
        "event": "push",
        "head_branch": "main",
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-10-05T00:00:00Z",
        "html_url": RUN_URL,
        "run_number": 10,
    }
    base.update(overrides)
    return base


def test_parse_runs_filters_by_sha() -> None:
    payload = {"total_count": 2, "workflow_runs": [_run(), _run(id=2, head_sha="c" * 40)]}
    runs = deploy_gate.parse_runs(payload, SHA)
    assert [r["id"] for r in runs] == [1]


@pytest.mark.parametrize(
    "payload",
    ["not json", "[]", '{"workflow_runs": "nope"}', "{}"],
)
def test_parse_runs_rejects_malformed_payload(payload: str) -> None:
    with pytest.raises((ValueError, json.JSONDecodeError)):
        deploy_gate.parse_runs(payload, SHA)


def test_parse_runs_rejects_invalid_sha() -> None:
    with pytest.raises(ValueError):
        deploy_gate.parse_runs({"total_count": 1, "workflow_runs": []}, "short")


@pytest.mark.parametrize("total_count", [0, -1, None, "3", True])
def test_parse_runs_fails_closed_on_bad_total_count(total_count) -> None:
    payload: dict = {"workflow_runs": []}
    if total_count is not None:
        payload["total_count"] = total_count
    with pytest.raises(ValueError, match="total_count"):
        deploy_gate.parse_runs(payload, SHA)


def test_select_run_picks_latest_success() -> None:
    runs = [
        _run(id=1, created_at="2026-10-05T00:00:00Z"),
        _run(id=2, created_at="2026-10-05T02:00:00Z"),
        _run(id=3, created_at="2026-10-05T01:00:00Z"),
    ]
    selected = deploy_gate.select_run(runs)
    assert selected is not None and selected["id"] == 2


def test_select_run_latest_tiebreak_by_id() -> None:
    runs = [
        _run(id=7, created_at="2026-10-05T00:00:00Z"),
        _run(id=9, created_at="2026-10-05T00:00:00Z"),
    ]
    selected = deploy_gate.select_run(runs)
    assert selected is not None and selected["id"] == 9


@pytest.mark.parametrize(
    "overrides",
    [
        {"event": "pull_request"},
        {"head_branch": "feature/x"},
        {"status": "in_progress"},
        {"conclusion": "failure"},
        {"conclusion": "cancelled"},
        {"status": "queued", "conclusion": None},
    ],
)
def test_select_run_rejects_wrong_run(overrides: dict) -> None:
    assert deploy_gate.select_run([_run(**overrides)]) is None


def test_select_run_rejects_no_runs() -> None:
    assert deploy_gate.select_run([]) is None


# --------------------------------------------------------------------------- #
# job verification
# --------------------------------------------------------------------------- #
def _jobs(*conclusions: str) -> dict:
    return {
        "total_count": len(conclusions),
        "jobs": [
            {"name": name, "conclusion": conclusion}
            for name, conclusion in zip(deploy_gate.REQUIRED_JOBS, conclusions, strict=False)
        ],
    }


def test_verify_jobs_all_success() -> None:
    ok, reason = deploy_gate.verify_jobs(_jobs(*["success"] * 5))
    assert ok is True, reason


@pytest.mark.parametrize("bad", ["skipped", "cancelled", "failure", None])
def test_verify_jobs_rejects_non_success(bad) -> None:
    conclusions = ["success", "success", bad, "success", "success"]
    ok, reason = deploy_gate.verify_jobs(_jobs(*conclusions))
    assert ok is False
    assert "deployment-config" in reason


def test_verify_jobs_rejects_missing_job() -> None:
    payload = _jobs(*["success"] * 5)
    payload["jobs"] = [j for j in payload["jobs"] if j["name"] != "security-gates"]
    payload["total_count"] = len(payload["jobs"])
    ok, reason = deploy_gate.verify_jobs(payload)
    assert ok is False
    assert "security-gates" in reason


def test_verify_jobs_rejects_incomplete_pagination_total_count() -> None:
    payload = _jobs(*["success"] * 5)
    payload["total_count"] = 6  # one page short of the API total
    ok, reason = deploy_gate.verify_jobs(payload)
    assert ok is False
    assert "pagination" in reason


def test_verify_jobs_rejects_incomplete_flag() -> None:
    payload = _jobs(*["success"] * 5)
    payload["incomplete"] = True
    ok, _ = deploy_gate.verify_jobs(payload)
    assert ok is False


def test_verify_jobs_rejects_empty_and_malformed() -> None:
    for payload in ({}, {"jobs": []}, {"jobs": [{"conclusion": "success"}]}, "not-json"):
        ok, _ = deploy_gate.verify_jobs(payload)
        assert ok is False


def test_verify_jobs_allows_extra_jobs() -> None:
    # Extra success jobs are tolerated as long as every required job is present.
    payload = _jobs(*["success"] * 5)
    payload["jobs"].append({"name": "some-other-job", "conclusion": "success"})
    payload["total_count"] = 6
    assert deploy_gate.verify_jobs(payload)[0] is True


def test_verify_jobs_requires_every_required_job_present() -> None:
    # Sanity: the complete required set is accepted.
    payload = _jobs(*["success"] * len(deploy_gate.REQUIRED_JOBS))
    assert deploy_gate.verify_jobs(payload)[0] is True


@pytest.mark.parametrize("missing", deploy_gate.REQUIRED_JOBS)
def test_verify_jobs_rejects_each_missing_required_job(missing: str) -> None:
    # Removing ANY required job must fail closed, even with extras present.
    payload = _jobs(*["success"] * 5)
    payload["jobs"] = [j for j in payload["jobs"] if j["name"] != missing]
    payload["jobs"].append({"name": "some-other-job", "conclusion": "success"})
    payload["total_count"] = len(payload["jobs"])
    ok, reason = deploy_gate.verify_jobs(payload)
    assert ok is False
    assert missing in reason


# --------------------------------------------------------------------------- #
# manifest verification
# --------------------------------------------------------------------------- #
def _write_bundle(tmp_path: Path) -> Path:
    base = tmp_path / "bundle"
    base.mkdir()
    for name in bundle_utils.REQUIRED_ARCHIVES:
        (base / name).write_bytes(b"payload-" + name.encode())
    (base / bundle_utils.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "git_sha": SHA,
                "platform": "linux/amd64",
                "api_image_archive_sha256": bundle_utils.sha256_file(
                    base / bundle_utils.API_IMAGE_ARCHIVE
                ),
                "api_image_id": IMAGE_ID,
                "web_archive_sha256": bundle_utils.sha256_file(
                    base / bundle_utils.WEB_ARCHIVE
                ),
                "deploy_config_archive_sha256": bundle_utils.sha256_file(
                    base / bundle_utils.DEPLOY_CONFIG_ARCHIVE
                ),
                "compose_project": "vps",
                "ci_run_id": 123456,
                "ci_run_url": RUN_URL,
                "built_at": "2026-10-05T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    return base


def test_verify_manifest_accepts_valid_bundle(tmp_path: Path) -> None:
    data = bundle_utils.verify_manifest(_write_bundle(tmp_path))
    assert data["git_sha"] == SHA


def test_verify_manifest_rejects_tampered_archive(tmp_path: Path) -> None:
    base = _write_bundle(tmp_path)
    target = base / bundle_utils.WEB_ARCHIVE
    blob = bytearray(target.read_bytes())
    blob[0] ^= 0xFF  # flip one byte
    target.write_bytes(bytes(blob))
    with pytest.raises(bundle_utils.BundleError, match="mismatch"):
        bundle_utils.verify_manifest(base)


def test_verify_manifest_rejects_missing_field(tmp_path: Path) -> None:
    base = _write_bundle(tmp_path)
    manifest = json.loads((base / bundle_utils.MANIFEST_NAME).read_text())
    del manifest["platform"]
    (base / bundle_utils.MANIFEST_NAME).write_text(json.dumps(manifest))
    with pytest.raises(bundle_utils.BundleError, match="platform"):
        bundle_utils.verify_manifest(base)


def test_verify_manifest_rejects_recorded_sha_mismatch(tmp_path: Path) -> None:
    base = _write_bundle(tmp_path)
    manifest = json.loads((base / bundle_utils.MANIFEST_NAME).read_text())
    manifest["api_image_archive_sha256"] = "c" * 64
    (base / bundle_utils.MANIFEST_NAME).write_text(json.dumps(manifest))
    with pytest.raises(bundle_utils.BundleError, match="api-image.tar.gz"):
        bundle_utils.verify_manifest(base)


def test_check_manifest_sha_accepts_matching(tmp_path: Path) -> None:
    base = _write_bundle(tmp_path)
    data = bundle_utils.check_manifest_sha(base, SHA)
    assert data["git_sha"] == SHA


def test_check_manifest_sha_rejects_mismatch(tmp_path: Path) -> None:
    base = _write_bundle(tmp_path)
    with pytest.raises(bundle_utils.BundleError, match="git_sha"):
        bundle_utils.check_manifest_sha(base, "b" * 40)


def test_check_manifest_sha_rejects_invalid_requested_sha(tmp_path: Path) -> None:
    base = _write_bundle(tmp_path)
    with pytest.raises(bundle_utils.BundleError, match="requested sha"):
        bundle_utils.check_manifest_sha(base, "short")


def test_build_manifest_recomputes_and_validates(tmp_path: Path) -> None:
    base = tmp_path / "b"
    base.mkdir()
    for name in bundle_utils.REQUIRED_ARCHIVES:
        (base / name).write_bytes(name.encode())
    data = bundle_utils.build_manifest(base, SHA, IMAGE_ID, 42, RUN_URL)
    assert data["platform"] == "linux/amd64"
    assert bundle_utils.verify_manifest(base)["ci_run_id"] == 42


def test_build_manifest_rejects_missing_archive(tmp_path: Path) -> None:
    base = tmp_path / "b"
    base.mkdir()
    with pytest.raises(bundle_utils.BundleError, match="missing archive"):
        bundle_utils.build_manifest(base, SHA, IMAGE_ID, 42, RUN_URL)


# --------------------------------------------------------------------------- #
# tar safety
# --------------------------------------------------------------------------- #
def _write_tar(tmp_path: Path, name: str, member: tarfile.TarInfo, data: bytes = b"") -> Path:
    path = tmp_path / name
    with tarfile.open(path, "w:gz") as archive:
        archive.addfile(member, io.BytesIO(data) if data else None)
    return path


def _symlink_tar(tmp_path: Path, member_name: str, linkname: str) -> Path:
    info = tarfile.TarInfo(member_name)
    info.type = tarfile.SYMTYPE
    info.linkname = linkname
    info.mode = 0o777
    return _write_tar(tmp_path, "symlink.tar.gz", info)


@pytest.mark.parametrize(
    "member_name",
    ["../escape.txt", "a/../../escape.txt", "/tmp/escape.txt"],
)
def test_extract_safe_rejects_traversal(tmp_path: Path, member_name: str) -> None:
    info = tarfile.TarInfo(member_name)
    info.size = 4
    archive = _write_tar(tmp_path, "hostile.tar.gz", info, b"boom")
    with pytest.raises(bundle_utils.BundleError):
        bundle_utils.extract_safe(archive, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_extract_safe_rejects_symlink_escape(tmp_path: Path) -> None:
    archive = _symlink_tar(tmp_path, "dir/link", "../../etc/passwd")
    with pytest.raises(bundle_utils.BundleError, match="escapes"):
        bundle_utils.extract_safe(archive, tmp_path / "out")


def test_extract_safe_rejects_absolute_symlink(tmp_path: Path) -> None:
    archive = _symlink_tar(tmp_path, "link", "/etc/passwd")
    with pytest.raises(bundle_utils.BundleError):
        bundle_utils.extract_safe(archive, tmp_path / "out")


def test_extract_safe_rejects_device_member(tmp_path: Path) -> None:
    info = tarfile.TarInfo("dev/null")
    info.type = tarfile.CHRTYPE
    info.devmajor = 1
    info.devminor = 3
    archive = _write_tar(tmp_path, "dev.tar.gz", info)
    with pytest.raises(bundle_utils.BundleError, match="device"):
        bundle_utils.extract_safe(archive, tmp_path / "out")


def test_extract_safe_allows_benign_internal_symlink(tmp_path: Path) -> None:
    path = tmp_path / "benign.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        payload = b"hello"
        info = tarfile.TarInfo("dir/file.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
        link = tarfile.TarInfo("dir/link.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "file.txt"
        link.mode = 0o777
        archive.addfile(link)
    dest = tmp_path / "out"
    bundle_utils.extract_safe(path, dest)
    assert (dest / "dir" / "file.txt").read_bytes() == b"hello"


# --------------------------------------------------------------------------- #
# decompression-bomb caps
# --------------------------------------------------------------------------- #
def _bomb_tar(tmp_path: Path, sizes: list[int], name: str = "bomb.tar.gz") -> Path:
    path = tmp_path / name
    with tarfile.open(path, "w:gz") as archive:
        for index, size in enumerate(sizes):
            info = tarfile.TarInfo(f"f{index}.bin")
            info.size = size
            archive.addfile(info, io.BytesIO(b"\0" * size))
    return path


def test_extract_safe_rejects_member_size_bomb(tmp_path: Path) -> None:
    archive = _bomb_tar(tmp_path, [4096])
    dest = tmp_path / "out"
    with pytest.raises(bundle_utils.BundleError, match="per-member"):
        bundle_utils.extract_safe(archive, dest, max_member_bytes=1024,
                                  max_total_bytes=1 << 20)
    assert not dest.exists()


def test_extract_safe_rejects_total_size_bomb(tmp_path: Path) -> None:
    # Each member is under the per-member cap, but the sum exceeds the total.
    archive = _bomb_tar(tmp_path, [1000, 1000, 1000])
    dest = tmp_path / "out"
    with pytest.raises(bundle_utils.BundleError, match="total decompressed"):
        bundle_utils.extract_safe(archive, dest, max_member_bytes=1024,
                                  max_total_bytes=1500)
    assert not dest.exists()


def test_extract_safe_removes_partial_output_on_breach(tmp_path: Path) -> None:
    # A valid first member is written before the bomb member trips the cap; the
    # whole staging dir must be removed so no partial release survives.
    archive = _bomb_tar(tmp_path, [512, 4096])
    dest = tmp_path / "out"
    with pytest.raises(bundle_utils.BundleError):
        bundle_utils.extract_safe(archive, dest, max_member_bytes=1024,
                                  max_total_bytes=1 << 20)
    assert not dest.exists()


def test_extract_safe_rejects_nonpositive_caps(tmp_path: Path) -> None:
    archive = _bomb_tar(tmp_path, [1])
    with pytest.raises(bundle_utils.BundleError, match="positive"):
        bundle_utils.extract_safe(archive, tmp_path / "out", max_member_bytes=0)


def test_extract_safe_defaults_are_documented_caps() -> None:
    assert bundle_utils.DEFAULT_MAX_MEMBER_BYTES == 2 * 1024 ** 3
    assert bundle_utils.DEFAULT_MAX_TOTAL_BYTES == 8 * 1024 ** 3


def test_cli_check_manifest_sha_binds_requested_sha(tmp_path: Path,
                                                    capsys: pytest.CaptureFixture) -> None:
    base = _write_bundle(tmp_path)
    assert bundle_utils.main(["check-manifest-sha", str(base), SHA]) == 0
    assert "manifest-sha-ok" in capsys.readouterr().out
    assert bundle_utils.main(["check-manifest-sha", str(base), "b" * 40]) == 1
    assert "BUNDLE-ERR" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# stdin cap + web layout
# --------------------------------------------------------------------------- #
def test_read_stdin_enforces_cap(tmp_path: Path) -> None:
    with pytest.raises(bundle_utils.BundleError, match="size cap"):
        bundle_utils.read_stdin_to_file(
            tmp_path / "e.tar.gz", 10, stream=io.BytesIO(b"x" * 11)
        )


def test_read_stdin_writes_within_cap(tmp_path: Path) -> None:
    out = tmp_path / "e.tar.gz"
    size = bundle_utils.read_stdin_to_file(out, 10, stream=io.BytesIO(b"x" * 10))
    assert size == 10 and out.read_bytes() == b"x" * 10


def _web_tar(tmp_path: Path, names: list[str]) -> Path:
    path = tmp_path / "web.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name in names:
            info = tarfile.TarInfo(name)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    return path


def test_verify_web_dist_accepts_index_and_assets(tmp_path: Path) -> None:
    bundle_utils.verify_web_dist(_web_tar(tmp_path, ["./index.html", "./assets/app.js"]))


def test_verify_web_dist_rejects_well_known(tmp_path: Path) -> None:
    archive = _web_tar(tmp_path, ["./index.html", "./assets/app.js", ".well-known/acme"])
    with pytest.raises(bundle_utils.BundleError, match="well-known"):
        bundle_utils.verify_web_dist(archive)


def test_verify_web_dist_rejects_missing_index(tmp_path: Path) -> None:
    with pytest.raises(bundle_utils.BundleError, match="index.html"):
        bundle_utils.verify_web_dist(_web_tar(tmp_path, ["./assets/app.js"]))
