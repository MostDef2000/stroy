"""Tests for the Stage-3 deploy gate + bundle/manifest helpers (issue #130).

These are unit tests: no docker, no network.  They import the pure logic from
``scripts/deploy`` by inserting the repo root on ``sys.path`` (pytest only adds
``tests/`` because there is no ``tests/__init__.py``).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
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


@pytest.mark.parametrize("total_count", [0, -1, None, "3", True, 2.5, 3.0])
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


@pytest.mark.parametrize(
    "member_name, linkname",
    [
        ("dir/link", "../../etc/passwd"),   # escape
        ("link", "/etc/passwd"),            # absolute
        ("dir/link", "file.txt"),           # benign-looking internal link
        ("a", "."),                         # chained-link primitive
    ],
)
def test_extract_safe_rejects_every_symlink(tmp_path: Path, member_name: str,
                                            linkname: str) -> None:
    archive = _symlink_tar(tmp_path, member_name, linkname)
    with pytest.raises(bundle_utils.BundleError, match="links are not permitted"):
        bundle_utils.extract_safe(archive, tmp_path / "out")


def test_extract_safe_rejects_hardlink(tmp_path: Path) -> None:
    info = tarfile.TarInfo("hard")
    info.type = tarfile.LNKTYPE
    info.linkname = "target.txt"
    archive = _write_tar(tmp_path, "hardlink.tar.gz", info)
    with pytest.raises(bundle_utils.BundleError, match="links are not permitted"):
        bundle_utils.extract_safe(archive, tmp_path / "out")


def test_extract_safe_rejects_chained_symlink_escape(tmp_path: Path) -> None:
    # Validator chain: a -> '.', b -> 'a/..', then regular member b/escape.
    # Prevalidation alone passes (a does not exist yet), so links must be
    # rejected outright or b/escape lands OUTSIDE dest.
    archive = tmp_path / "chain.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        first = tarfile.TarInfo("a")
        first.type = tarfile.SYMTYPE
        first.linkname = "."
        tar.addfile(first)
        second = tarfile.TarInfo("b")
        second.type = tarfile.SYMTYPE
        second.linkname = "a/.."
        tar.addfile(second)
        payload = b"pwned"
        escape = tarfile.TarInfo("b/escape")
        escape.size = len(payload)
        tar.addfile(escape, io.BytesIO(payload))
    dest = tmp_path / "out"
    with pytest.raises(bundle_utils.BundleError, match="links are not permitted"):
        bundle_utils.extract_safe(archive, dest)
    # Nothing may exist outside dest (or inside it).
    assert not (tmp_path / "escape").exists()
    assert not dest.exists()


def test_extract_safe_rejects_device_member(tmp_path: Path) -> None:
    info = tarfile.TarInfo("dev/null")
    info.type = tarfile.CHRTYPE
    info.devmajor = 1
    info.devminor = 3
    archive = _write_tar(tmp_path, "dev.tar.gz", info)
    with pytest.raises(bundle_utils.BundleError, match="device"):
        bundle_utils.extract_safe(archive, tmp_path / "out")


# --------------------------------------------------------------------------- #
# hash-file CLI
# --------------------------------------------------------------------------- #
def test_cli_hash_file_prints_sha256(tmp_path: Path,
                                     capsys: pytest.CaptureFixture) -> None:
    target = tmp_path / "blob.bin"
    target.write_bytes(b"hello world")
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert bundle_utils.main(["hash-file", str(target)]) == 0
    assert capsys.readouterr().out.strip() == expected


def test_cli_hash_file_missing_file_errors(tmp_path: Path,
                                           capsys: pytest.CaptureFixture) -> None:
    missing = tmp_path / "nope.bin"
    assert bundle_utils.main(["hash-file", str(missing)]) == 1
    captured = capsys.readouterr()
    assert "BUNDLE-ERR" in captured.err
    assert "not a file" in captured.err


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


# --------------------------------------------------------------------------- #
# wrapper apply-v1: same-sha web-release reuse (hermetic: no docker, no network)
# --------------------------------------------------------------------------- #
# This is an integration smoke test of deploy/vps/deploy-wrapper through its
# public dispatch (SSH_ORIGINAL_COMMAND) with every external side effect faked:
#   * docker  -> image inspect prints the manifest api_image_id; all else exit 0
#   * nginx   -> `-t` exits 0        * systemctl -> `reload` exits 0
#   * curl    -> always prints a 200 + the five security headers
#   * flock/find/install/mktemp/... -> real coreutils
# The wrapper uses tmp-dir STROY_DEPLOY_* overrides and the real bundle_utils.py.
WRAPPER = ROOT / "deploy" / "vps" / "deploy-wrapper"
FAKE_HEADERS = (
    "HTTP/1.1 200 OK\r\n"
    "X-Content-Type-Options: nosniff\r\n"
    "Strict-Transport-Security: max-age=63072000; includeSubDomains\r\n"
    "Content-Security-Policy: default-src 'self'\r\n"
    "X-Frame-Options: DENY\r\n"
    "Referrer-Policy: strict-origin-when-cross-origin\r\n"
    "\r\n"
)


def _write_fake_bins(bin_dir: Path) -> None:
    scripts = {
        "docker": (
            "#!/usr/bin/env bash\n"
            'echo "docker $*" >> "$FAKE_LOG"\n'
            'case "$1" in\n'
            "  image) printf '%s\\n' \"$FAKE_IMAGE_ID\" ;;\n"
            "  compose)\n"
            '    if [ "${FAKE_DOCKER_COMPOSE_FAIL:-0}" = "1" ]; then\n'
            '      echo "fake docker compose failure" >&2\n'
            "      exit 1\n"
            "    fi ;;\n"
            "esac\n"
            "exit 0\n"
        ),
        "nginx": (
            "#!/usr/bin/env bash\n"
            'echo "nginx $*" >> "$FAKE_LOG"\n'
            'if [ "${FAKE_NGINX_T_FAIL:-0}" = "1" ] && [ "${1:-}" = "-t" ]; then\n'
            '  echo "fake nginx -t failure" >&2\n'
            "  exit 1\n"
            "fi\n"
            "exit 0\n"
        ),
        "systemctl": (
            "#!/usr/bin/env bash\n"
            'echo "systemctl $*" >> "$FAKE_LOG"\n'
            "exit 0\n"
        ),
        "curl": (
            "#!/usr/bin/env bash\n"
            'echo "curl $*" >> "$FAKE_LOG"\n'
            'cat "$FAKE_HEADERS"\n'
            "exit 0\n"
        ),
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)


def _stage_release(
    releases_dir: Path,
    sha: str = SHA,
    *,
    compose_body: bytes = b"services: {}\n",
    web_files: dict[str, bytes] | None = None,
) -> Path:
    release = releases_dir / sha
    release.mkdir(parents=True)
    (release / bundle_utils.API_IMAGE_ARCHIVE).write_bytes(b"api-image")
    config_members = {
        "docker-compose.yml": compose_body,
        "nginx/stroy.mostdef.ru.conf.example": b"server {}\n",
        "nginx/stroy-ratelimit.conf.example": b"limit_req_zone $binary_remote_addr "
                                             b"zone=stroy:10m rate=10r/s;\n",
    }
    with tarfile.open(release / bundle_utils.DEPLOY_CONFIG_ARCHIVE, "w:gz") as tar:
        for name, data in config_members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    if web_files is None:
        web_files = {"./index.html": b"x", "./assets/app.js": b"x"}
    with tarfile.open(release / bundle_utils.WEB_ARCHIVE, "w:gz") as tar:
        for name, data in web_files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    bundle_utils.build_manifest(release, sha, IMAGE_ID, 123456, RUN_URL)
    return release


def _wrapper_env(tmp: Path, bin_dir: Path, log: Path, headers: Path,
                 sha: str = SHA) -> dict:
    env_dir = tmp / "opt"
    env_dir.mkdir(exist_ok=True)
    (tmp / "staging").mkdir(exist_ok=True)  # receive-v1 normally creates this
    env_file = env_dir / ".env"
    env_file.write_text("STROY_API_IMAGE=placeholder\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "SSH_ORIGINAL_COMMAND": f"apply-v1 {sha}",
            "PATH": f"{bin_dir}:{env['PATH']}",
            "FAKE_LOG": str(log),
            "FAKE_IMAGE_ID": IMAGE_ID,
            "FAKE_HEADERS": str(headers),
            "STROY_DEPLOY_STAGING": str(tmp / "staging"),
            "STROY_DEPLOY_RELEASES": str(tmp / "releases"),
            "STROY_DEPLOY_LOCK": str(tmp / "apply.lock"),
            "STROY_DEPLOY_CURRENT": str(tmp / "current.json"),
            "STROY_DEPLOY_CONFIG_DIR": str(tmp / "config"),
            "STROY_DEPLOY_CONFIG_HISTORY": str(tmp / "config-history"),
            "STROY_DEPLOY_ENV_FILE": str(env_file),
            "STROY_DEPLOY_WEB_ROOT": str(tmp / "web"),
            "STROY_DEPLOY_ACME_ROOT": str(tmp / "acme"),
            "STROY_DEPLOY_NGINX_AVAILABLE": str(tmp / "nginx/available"),
            "STROY_DEPLOY_NGINX_ENABLED": str(tmp / "nginx/enabled"),
            "STROY_DEPLOY_NGINX_CONFD": str(tmp / "nginx/confd"),
            "STROY_DEPLOY_HEALTH_URL": "http://127.0.0.1:8000/health",
            "STROY_DEPLOY_PUBLIC_URL": "https://stroy.mostdef.ru/",
            "STROY_DEPLOY_PUBLIC_HOST": "stroy.mostdef.ru",
            "STROY_DEPLOY_PYTHON": sys.executable,
            "STROY_DEPLOY_BUNDLE_UTILS": str(ROOT / "scripts" / "deploy" / "bundle_utils.py"),
            "STROY_DEPLOY_KEEP_RELEASES": "5",
        }
    )
    return env


def _run_wrapper(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(WRAPPER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_wrapper_apply_v1_reuses_existing_web_release(tmp_path: Path) -> None:
    releases_dir = tmp_path / "releases"
    releases_dir.mkdir()
    _stage_release(releases_dir)
    # Existing tree is byte-identical to the verified archive, plus an extra
    # file that must be tolerated.  Reuse is now allowed only after content
    # verification.
    web_release = tmp_path / "web" / "releases" / SHA
    (web_release / "assets").mkdir(parents=True)
    (web_release / "index.html").write_bytes(b"x")
    (web_release / "assets" / "app.js").write_bytes(b"x")
    (web_release / "MARKER.txt").write_text("keep me", encoding="utf-8")

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    log = tmp_path / "fakes.log"
    headers = tmp_path / "headers.txt"
    headers.write_text(FAKE_HEADERS, encoding="utf-8")
    _write_fake_bins(bin_dir)
    env = _wrapper_env(tmp_path, bin_dir, log, headers)

    proc = _run_wrapper(env)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "web release reused (content verified)" in proc.stdout
    assert "apply-ok" in proc.stdout

    # The pre-existing release tree (and its extra file) must survive.
    assert (web_release / "MARKER.txt").read_text(encoding="utf-8") == "keep me"
    assert os.readlink(tmp_path / "web" / "current") == str(web_release)
    fake_calls = log.read_text(encoding="utf-8")
    assert "docker image inspect" in fake_calls
    assert "docker compose" in fake_calls
    assert "nginx -t" in fake_calls


def test_wrapper_apply_v1_rejects_divergent_web_release(tmp_path: Path) -> None:
    # A tampered existing tree must NOT be published under the verified identity:
    # content mismatches fail closed, the live tree is left untouched, and the
    # wrapper exits non-zero.
    releases_dir = tmp_path / "releases"
    releases_dir.mkdir()
    _stage_release(releases_dir)
    web_release = tmp_path / "web" / "releases" / SHA
    (web_release / "assets").mkdir(parents=True)
    (web_release / "index.html").write_text("tampered index", encoding="utf-8")
    (web_release / "assets" / "app.js").write_bytes(b"x")
    (web_release / "MARKER.txt").write_text("keep me", encoding="utf-8")

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    log = tmp_path / "fakes.log"
    headers = tmp_path / "headers.txt"
    headers.write_text(FAKE_HEADERS, encoding="utf-8")
    _write_fake_bins(bin_dir)
    env = _wrapper_env(tmp_path, bin_dir, log, headers)

    proc = _run_wrapper(env)
    assert proc.returncode != 0
    assert "content mismatch on reuse" in proc.stderr
    assert "apply-ok" not in proc.stdout
    # The live tree is untouched (no file deleted or overwritten).
    assert (web_release / "index.html").read_text(encoding="utf-8") == "tampered index"
    assert (web_release / "MARKER.txt").read_text(encoding="utf-8") == "keep me"
    assert (web_release / "assets" / "app.js").read_bytes() == b"x"


def test_wrapper_apply_v1_fails_closed_when_web_enumeration_fails(tmp_path: Path) -> None:
    # Regression: the reuse comparison must materialize its file list with a
    # CHECKED find.  A find that fails (or returns a partial listing) must fail
    # the apply before "content verified"/"apply-ok"; the live tree is left
    # untouched and no staging directory survives.
    releases_dir = tmp_path / "releases"
    releases_dir.mkdir()
    _stage_release(releases_dir)
    web_release = tmp_path / "web" / "releases" / SHA
    (web_release / "assets").mkdir(parents=True)
    (web_release / "index.html").write_bytes(b"x")
    (web_release / "assets" / "app.js").write_bytes(b"x")
    (web_release / "MARKER.txt").write_text("keep me", encoding="utf-8")

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    log = tmp_path / "fakes.log"
    headers = tmp_path / "headers.txt"
    headers.write_text(FAKE_HEADERS, encoding="utf-8")
    _write_fake_bins(bin_dir)
    # A `find` that fails ONLY on the web-stage enumeration (identified by the
    # -print0 output mode) and delegates all other calls to the real binary, so
    # config install / prune are unaffected.
    real_find = shutil.which("find")
    assert real_find is not None
    failing_find = bin_dir / "find"
    failing_find.write_text(
        "#!/usr/bin/env bash\n"
        'echo "find $*" >> "$FAKE_LOG"\n'
        'for arg in "$@"; do\n'
        '  if [ "$arg" = "-print0" ]; then\n'
        '    echo "fake find: enumeration failed" >&2\n'
        "    exit 1\n"
        "  fi\n"
        "done\n"
        f'exec "{real_find}" "$@"\n',
        encoding="utf-8",
    )
    failing_find.chmod(0o755)
    env = _wrapper_env(tmp_path, bin_dir, log, headers)

    proc = _run_wrapper(env)
    assert proc.returncode != 0
    assert "web reuse enumeration failed" in proc.stderr
    assert "web release reused (content verified)" not in proc.stdout
    assert "apply-ok" not in proc.stdout

    # The live tree is untouched (no file deleted or overwritten) and the
    # failed apply left no staging directory behind.
    assert (web_release / "index.html").read_bytes() == b"x"
    assert (web_release / "assets" / "app.js").read_bytes() == b"x"
    assert (web_release / "MARKER.txt").read_text(encoding="utf-8") == "keep me"
    assert list((tmp_path / "web" / "releases").glob(".staging.*")) == []
    assert list((tmp_path / "staging").glob("web-filelist.*")) == []


def test_wrapper_nginx_t_failure_restores_config_and_rollback(tmp_path: Path) -> None:
    # Regression: config install -> nginx -t failure must restore the pre-apply
    # deploy config, not leave the rejected candidate in CONFIG_DIR (a later
    # apply would otherwise snapshot the rejected config as its baseline).
    shas = {"A": "a" * 40, "B": "b" * 40, "C": "c" * 40}
    releases_dir = tmp_path / "releases"
    releases_dir.mkdir()
    for tag, sha in shas.items():
        _stage_release(releases_dir, sha, compose_body=f"# config {tag}\n".encode())

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    log = tmp_path / "fakes.log"
    headers = tmp_path / "headers.txt"
    headers.write_text(FAKE_HEADERS, encoding="utf-8")
    _write_fake_bins(bin_dir)

    config_file = tmp_path / "config" / "docker-compose.yml"
    nginx_vhost = tmp_path / "nginx" / "available" / "stroy.mostdef.ru"
    current_json = tmp_path / "current.json"

    # (1) apply A cleanly.
    proc_a = _run_wrapper(_wrapper_env(tmp_path, bin_dir, log, headers, sha=shas["A"]))
    assert proc_a.returncode == 0, f"stdout:\n{proc_a.stdout}\nstderr:\n{proc_a.stderr}"
    assert "apply-ok" in proc_a.stdout
    sentinel_a = config_file.read_bytes()
    assert b"config A" in sentinel_a
    nginx_a = nginx_vhost.read_bytes()
    state = json.loads(current_json.read_text(encoding="utf-8"))
    assert state["status"] == "deployed"
    assert state["sha"] == shas["A"]

    # (2) apply B with nginx -t failing: candidate config must be rolled back to
    # A and current.json must still describe A (transition marker never written).
    env_b = _wrapper_env(tmp_path, bin_dir, log, headers, sha=shas["B"])
    env_b["FAKE_NGINX_T_FAIL"] = "1"
    proc_b = _run_wrapper(env_b)
    assert proc_b.returncode != 0
    assert "nginx -t failed" in proc_b.stderr
    assert config_file.read_bytes() == sentinel_a
    assert nginx_vhost.read_bytes() == nginx_a
    state = json.loads(current_json.read_text(encoding="utf-8"))
    assert state["status"] == "deployed"
    assert state["sha"] == shas["A"]

    # (3) apply C with docker compose failing post-switch: automatic rollback
    # must restore config A from its unique history snapshot.
    env_c = _wrapper_env(tmp_path, bin_dir, log, headers, sha=shas["C"])
    env_c["FAKE_DOCKER_COMPOSE_FAIL"] = "1"
    proc_c = _run_wrapper(env_c)
    assert proc_c.returncode != 0
    assert "compose up failed" in proc_c.stderr
    assert config_file.read_bytes() == sentinel_a
    assert nginx_vhost.read_bytes() == nginx_a
    state = json.loads(current_json.read_text(encoding="utf-8"))
    assert state["status"] in {"failed", "rolled_back_to"}
