"""Bundle + manifest helpers for STROY Stage-3 deploys (issue #130).

Shared by the CI build/deploy jobs, the on-server deploy wrapper (invoked via
``python3``) and the unit tests.  Stdlib only: the wrapper runs on a minimal
VPS host with no repo importable.

Security posture: every parser/verifier is fail-closed and raises
``BundleError`` on anything untrusted.  Archive extraction rejects absolute
paths, ``..`` traversal, **all** symlink/hardlink members (chained links can
resolve outside the destination even when each lexical target looks safe) and
device/FIFO members *before* anything is written, so hostile tars are rejected
deterministically on every Python version (independent of ``filter="data"``
availability).  Legitimate bundles contain plain files/dirs only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "release-manifest.json"
API_IMAGE_ARCHIVE = "api-image.tar.gz"
WEB_ARCHIVE = "web-dist.tar.gz"
DEPLOY_CONFIG_ARCHIVE = "deploy-config.tar.gz"
REQUIRED_ARCHIVES = (API_IMAGE_ARCHIVE, WEB_ARCHIVE, DEPLOY_CONFIG_ARCHIVE)

PLATFORM = "linux/amd64"
COMPOSE_PROJECT = "vps"

# Decompression-bomb guards (peer finding, issue #130).  Enforced per member
# and across the whole archive while streaming; the wrapper reads the
# STROY_DEPLOY_MAX_MEMBER_B / STROY_DEPLOY_MAX_TOTAL_B env overrides and passes
# them through as CLI flags.
DEFAULT_MAX_MEMBER_BYTES = 2 * 1024 ** 3  # 2 GiB
DEFAULT_MAX_TOTAL_BYTES = 8 * 1024 ** 3   # 8 GiB

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class BundleError(Exception):
    """Untrusted or invalid bundle content (fail closed)."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_hex40(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX40.fullmatch(value))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BundleError(message)


# --------------------------------------------------------------------------- #
# stdin / extraction
# --------------------------------------------------------------------------- #
def read_stdin_to_file(out_path: str | Path, cap_bytes: int, stream=None) -> int:
    """Stream stdin into *out_path*, enforcing a hard byte cap.

    Reads at most ``cap_bytes + 1`` bytes; exceeding the cap raises without
    consuming the rest of the stream (the peer's write aborts instead of
    filling the disk).  Returns the number of bytes written.
    """
    source = stream if stream is not None else sys.stdin.buffer
    total = 0
    with open(out_path, "wb") as handle:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > cap_bytes:
                raise BundleError(f"envelope exceeds size cap ({cap_bytes} bytes)")
            handle.write(chunk)
    return total


def _reject_unsafe_name(name: str) -> PurePosixPath:
    _require(bool(name), "empty member name")
    _require(not name.startswith(("/", "\\")), f"absolute member path: {name!r}")
    _require("\\" not in name, f"backslash in member path: {name!r}")
    posix = PurePosixPath(name)
    _require(not posix.is_absolute(), f"absolute member path: {name!r}")
    _require(".." not in posix.parts, f"path traversal in member: {name!r}")
    return posix


def _validate_members(members, dest: Path) -> None:
    del dest  # retained in the signature for callers/tests; links are rejected outright
    for member in members:
        _reject_unsafe_name(member.name)
        _require(
            not (member.isdev() or member.isfifo()),
            f"device/FIFO member rejected: {member.name!r}",
        )
        if member.issym() or member.islnk():
            # Chained links defeat lexical validation: a -> '.', b -> 'a/..'
            # passes prevalidation (a does not exist yet) but makes a later
            # regular member land outside dest.  No legitimate bundle carries
            # links, so reject ALL of them.
            raise BundleError(f"archive links are not permitted: {member.name!r}")


def _cleanup_partial(dest: Path, created: bool, extracted: list[Path]) -> None:
    """Remove partial output after a failed extraction (fail-flat).

    When this call created *dest* the whole tree is removed; otherwise only the
    members we streamed are unlinked so a caller-supplied directory keeps its
    pre-existing content.
    """
    if created:
        shutil.rmtree(dest, ignore_errors=True)
        return
    for path in reversed(extracted):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        except OSError:
            pass


def _stream_extract(
    archive: tarfile.TarFile,
    members: list[tarfile.TarInfo],
    dest: Path,
    max_member_bytes: int,
    max_total_bytes: int,
    extracted: list[Path],
) -> None:
    """Extract validated members while enforcing decompression caps.

    Both the declared ``TarInfo.size`` (fast fail before reading a bomb) and the
    actually streamed byte count (authoritative, defeats a lying header) are
    checked; ``total`` accumulates real bytes across the archive.
    """
    total = 0
    for member in members:
        posix = PurePosixPath(member.name)
        target = dest / posix
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            extracted.append(target)
            continue
        if member.issym() or member.islnk():
            # Defence in depth: validation already rejects every link member.
            raise BundleError(f"archive links are not permitted: {member.name!r}")
        _require(member.isfile(), f"unsupported member type: {member.name!r}")
        _require(
            member.size <= max_member_bytes,
            f"member {member.name!r} exceeds per-member cap ({max_member_bytes} bytes)",
        )
        source = archive.extractfile(member)
        if source is None:
            raise BundleError(f"cannot read member: {member.name!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        extracted.append(target)
        written = 0
        with open(target, "wb") as handle:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                total += len(chunk)
                _require(
                    written <= max_member_bytes,
                    f"member {member.name!r} exceeds per-member cap "
                    f"({max_member_bytes} bytes)",
                )
                _require(
                    total <= max_total_bytes,
                    f"archive exceeds total decompressed cap ({max_total_bytes} bytes)",
                )
                handle.write(chunk)


def extract_safe(
    tar_path: str | Path,
    dest_dir: str | Path,
    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> None:
    """Safely extract *tar_path* into *dest_dir* (created if missing).

    Rejects traversal/link/device members before writing anything and aborts on
    a per-member or total decompressed-size breach, removing partial output.
    """
    _require(max_member_bytes > 0, "max_member_bytes must be positive")
    _require(max_total_bytes > 0, "max_total_bytes must be positive")
    _require(max_member_bytes <= max_total_bytes,
             "max_member_bytes must not exceed max_total_bytes")
    dest = Path(dest_dir)
    created = not dest.exists()
    dest.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    try:
        with tarfile.open(tar_path, "r:*") as archive:
            members = archive.getmembers()
            # Manual validation below is authoritative for traversal/link/device
            # rejection on every Python version.
            _validate_members(members, dest)
            _stream_extract(archive, members, dest, max_member_bytes,
                            max_total_bytes, extracted)
    except BundleError:
        _cleanup_partial(dest, created, extracted)
        raise
    except (tarfile.TarError, OSError) as exc:
        _cleanup_partial(dest, created, extracted)
        raise BundleError(f"extraction failed: {exc}") from exc


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #
def load_manifest(bundle_dir: str | Path) -> dict:
    path = Path(bundle_dir) / MANIFEST_NAME
    _require(path.is_file(), f"missing {MANIFEST_NAME}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BundleError(f"invalid {MANIFEST_NAME}: {exc}") from exc
    _require(isinstance(data, dict), f"{MANIFEST_NAME} must be a JSON object")
    validate_manifest(data)
    return data


def validate_manifest(data: dict) -> None:
    _require(isinstance(data, dict), "manifest must be an object")
    _require(is_hex40(data.get("git_sha")), "git_sha must be 40 lowercase hex")
    _require(data.get("platform") == PLATFORM, f"platform must be {PLATFORM}")
    _require(isinstance(data.get("api_image_id"), str) and bool(data["api_image_id"]),
             "api_image_id must be a non-empty string")
    for field in ("api_image_archive_sha256", "web_archive_sha256",
                  "deploy_config_archive_sha256"):
        _require(
            isinstance(data.get(field), str) and bool(_HEX64.fullmatch(data[field])),
            f"{field} must be 64 lowercase hex",
        )
    _require(data.get("compose_project") == COMPOSE_PROJECT,
             f"compose_project must be {COMPOSE_PROJECT}")
    run_id = data.get("ci_run_id")
    _require(isinstance(run_id, int) and not isinstance(run_id, bool) and run_id > 0,
             "ci_run_id must be a positive integer")
    _require(isinstance(data.get("ci_run_url"), str) and bool(data["ci_run_url"]),
             "ci_run_url must be a non-empty string")
    built_at = data.get("built_at")
    _require(isinstance(built_at, str) and bool(built_at),
             "built_at must be an ISO8601 string")
    try:
        datetime.fromisoformat(str(built_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleError(f"built_at is not ISO8601: {exc}") from exc


def build_manifest(bundle_dir: str | Path, git_sha: str, api_image_id: str,
                   ci_run_id: int, ci_run_url: str) -> dict:
    """Compute archive digests and write ``release-manifest.json``."""
    base = Path(bundle_dir)
    _require(is_hex40(git_sha), "git_sha must be 40 lowercase hex")
    _require(isinstance(api_image_id, str) and bool(api_image_id),
             "api_image_id must be a non-empty string")
    for archive in REQUIRED_ARCHIVES:
        _require((base / archive).is_file(), f"missing archive: {archive}")
    data = {
        "git_sha": git_sha,
        "platform": PLATFORM,
        "api_image_archive_sha256": sha256_file(base / API_IMAGE_ARCHIVE),
        "api_image_id": api_image_id,
        "web_archive_sha256": sha256_file(base / WEB_ARCHIVE),
        "deploy_config_archive_sha256": sha256_file(base / DEPLOY_CONFIG_ARCHIVE),
        "compose_project": COMPOSE_PROJECT,
        "ci_run_id": int(ci_run_id),
        "ci_run_url": ci_run_url,
        "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    validate_manifest(data)
    (base / MANIFEST_NAME).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def verify_manifest(bundle_dir: str | Path) -> dict:
    """Rehash every archive against the manifest; never trust recorded digests."""
    base = Path(bundle_dir)
    data = load_manifest(base)
    pairs = (
        (API_IMAGE_ARCHIVE, data["api_image_archive_sha256"]),
        (WEB_ARCHIVE, data["web_archive_sha256"]),
        (DEPLOY_CONFIG_ARCHIVE, data["deploy_config_archive_sha256"]),
    )
    for archive, expected in pairs:
        path = base / archive
        _require(path.is_file(), f"manifest member missing: {archive}")
        actual = sha256_file(path)
        _require(actual == expected, f"sha256 mismatch for {archive}")
    return data


def check_manifest_sha(bundle_dir: str | Path, sha: str) -> dict:
    """Bind a bundle's manifest to the requested release *sha* (fail closed).

    Rejects an invalid requested sha, invalid manifest, and any manifest whose
    ``git_sha`` does not exactly match *sha*.
    """
    _require(is_hex40(sha), "requested sha must be 40 lowercase hex")
    data = load_manifest(bundle_dir)
    _require(
        data["git_sha"] == sha,
        f"manifest git_sha {data['git_sha']} != requested {sha}",
    )
    return data


def verify_web_dist(archive_path: str | Path) -> None:
    """Require a root ``index.html`` + ``assets/`` and forbid ``.well-known``."""
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            names = [m.name for m in archive.getmembers()]
    except (tarfile.TarError, OSError) as exc:
        raise BundleError(f"invalid web archive: {exc}") from exc
    _require(any(n.strip("./") == "index.html" for n in names),
             "web archive missing index.html")
    _require(any(n.startswith("./assets/") or n.startswith("assets/") for n in names),
             "web archive missing assets/")
    for name in names:
        _require(".well-known" not in PurePosixPath(name).parts,
                 f"web archive must not contain .well-known: {name!r}")


# --------------------------------------------------------------------------- #
# image identity: manifest + config digests of a saved docker/OCI archive
# --------------------------------------------------------------------------- #
_OCI_INDEX_MTS = frozenset({
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
})
_OCI_MANIFEST_MTS = frozenset({
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
})
# index.json / manifest.json / config blobs are KB-sized; a larger member in
# one of these roles is either a bomb or a malformed archive - reject both.
_IMAGE_BLOB_CAP = 16 * 1024 * 1024


def _read_tar_member(archive: tarfile.TarFile, member: tarfile.TarInfo,
                     cap: int = _IMAGE_BLOB_CAP) -> bytes:
    handle = archive.extractfile(member)
    _require(handle is not None, f"member is not a regular file: {member.name!r}")
    data = handle.read(cap + 1)
    _require(len(data) <= cap, f"member exceeds {cap}B cap: {member.name!r}")
    return data


def _member_sha256(archive: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    return "sha256:" + hashlib.sha256(_read_tar_member(archive, member)).hexdigest()


def _parse_digest(digest: object, what: str) -> str:
    _require(isinstance(digest, str) and digest.startswith("sha256:"),
             f"{what} must be a sha256 digest: {digest!r}")
    hex_part = digest[len("sha256:"):]
    _require(bool(_HEX64.fullmatch(hex_part)), f"{what} must be sha256 hex: {digest!r}")
    return hex_part


def _resolve_image_descriptor(archive: tarfile.TarFile, members: dict[str, tarfile.TarInfo],
                              descriptor: object, depth: int = 0) -> tuple[str, str]:
    """Walk one (one level max) descriptor to a leaf manifest.

    Returns ``(manifest_digest, config_digest)``.  Every referenced blob is
    rehashed against its descriptor digest before use - a descriptor pointing
    at tampered or missing bytes fails closed.
    """
    _require(isinstance(descriptor, dict), "index descriptor must be an object")
    media_type = descriptor.get("mediaType")
    _require(isinstance(media_type, str), "descriptor mediaType must be a string")
    digest = descriptor.get("digest")
    _parse_digest(digest, "descriptor digest")
    blob_name = f"blobs/sha256/{digest[len('sha256:'):]}"
    member = members.get(blob_name)
    _require(member is not None, f"descriptor blob missing from archive: {blob_name}")
    _require(_member_sha256(archive, member) == digest,
             f"descriptor blob digest mismatch: {blob_name}")
    body = json.loads(_read_tar_member(archive, member))
    if media_type in _OCI_INDEX_MTS:
        _require(depth < 1, "nested image index exceeds depth 1")
        nested = body.get("manifests") if isinstance(body, dict) else None
        _require(isinstance(nested, list) and len(nested) == 1,
                 "image index must contain exactly one manifest")
        return _resolve_image_descriptor(archive, members, nested[0], depth + 1)
    _require(media_type in _OCI_MANIFEST_MTS,
             f"unsupported descriptor mediaType: {media_type!r}")
    _require(isinstance(body, dict), "manifest blob must be an object")
    config = body.get("config")
    _require(isinstance(config, dict), "manifest config descriptor missing")
    config_hex = _parse_digest(config.get("digest"), "manifest config digest")
    config_blob = f"blobs/sha256/{config_hex}"
    config_member = members.get(config_blob)
    _require(config_member is not None, f"config blob missing from archive: {config_blob}")
    _require(_member_sha256(archive, config_member) == "sha256:" + config_hex,
             f"config blob digest mismatch: {config_blob}")
    return digest, "sha256:" + config_hex


def image_ids(archive_path: str | Path) -> dict:
    """Identity pair of a saved image archive, derived from its bytes only.

    Returns ``{"manifest": "<sha256:...>" | None, "config": "<sha256:...>"}``.

    Why both: docker with the containerd image store (default from 27/29 on
    several platforms) reports the OCI MANIFEST digest as the image Id for
    archives loaded via ``docker load``, while the classic graphdriver store
    reports the CONFIG digest (the historical "image ID").  A save -> load
    roundtrip is only stable at the archive level, so consumers must treat the
    pair as the identity and accept either half.  No extraction to disk: only
    the small index/manifest/config blobs are read, each size-capped.
    """
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            members: dict[str, tarfile.TarInfo] = {}
            for member in archive.getmembers():
                _reject_unsafe_name(member.name)
                _require(not (member.isdev() or member.isfifo()),
                         f"device/FIFO member rejected: {member.name!r}")
                _require(not (member.issym() or member.islnk()),
                         f"archive links are not permitted: {member.name!r}")
                members[member.name] = member
            index_member = members.get("index.json")
            if index_member is not None:
                index = json.loads(_read_tar_member(archive, index_member))
                _require(isinstance(index, dict), "index.json must be an object")
                descriptors = index.get("manifests")
                _require(isinstance(descriptors, list) and len(descriptors) == 1,
                         "index.json must contain exactly one descriptor")
                manifest_digest, config_digest = _resolve_image_descriptor(
                    archive, members, descriptors[0])
                return {"manifest": manifest_digest, "config": config_digest}
            legacy_member = members.get("manifest.json")
            if legacy_member is not None:
                entries = json.loads(_read_tar_member(archive, legacy_member))
                _require(isinstance(entries, list) and len(entries) == 1,
                         "manifest.json must contain exactly one image entry")
                entry = entries[0]
                _require(isinstance(entry, dict), "manifest.json entry must be an object")
                config_path = entry.get("Config")
                _require(isinstance(config_path, str) and bool(config_path),
                         "manifest.json entry missing Config path")
                config_member = members.get(config_path)
                _require(config_member is not None,
                         f"config member missing: {config_path!r}")
                return {"manifest": None, "config": _member_sha256(archive, config_member)}
            raise BundleError("archive is neither an OCI layout nor a docker archive")
    except (tarfile.TarError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BundleError(f"invalid image archive: {exc}") from exc


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cmd_verify_manifest(args: argparse.Namespace) -> int:
    data = verify_manifest(args.bundle_dir)
    print(f"manifest-ok {data['git_sha']}")
    return 0


def _cmd_verify_web(args: argparse.Namespace) -> int:
    verify_web_dist(args.archive)
    print("web-dist-ok")
    return 0


def _cmd_check_manifest_sha(args: argparse.Namespace) -> int:
    data = check_manifest_sha(args.bundle_dir, args.sha)
    print(f"manifest-sha-ok {data['git_sha']}")
    return 0


def _cmd_hash_file(args: argparse.Namespace) -> int:
    path = Path(args.path)
    _require(path.is_file(), f"not a file: {args.path}")
    print(sha256_file(path))
    return 0


def _cmd_image_ids(args: argparse.Namespace) -> int:
    ids = image_ids(args.archive)
    print(f"manifest {ids['manifest'] or '-'}")
    print(f"config {ids['config']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bundle_utils")
    sub = parser.add_subparsers(dest="command", required=True)

    read = sub.add_parser("read-stdin", help="stream stdin to a file with a size cap")
    read.add_argument("out_path")
    read.add_argument("cap_bytes", type=int)

    extract = sub.add_parser("extract-safe", help="safely extract a tar archive")
    extract.add_argument("archive")
    extract.add_argument("dest_dir")
    extract.add_argument("--max-member-b", type=int, default=DEFAULT_MAX_MEMBER_BYTES,
                         help="per-member decompressed size cap in bytes")
    extract.add_argument("--max-total-b", type=int, default=DEFAULT_MAX_TOTAL_BYTES,
                         help="total decompressed size cap in bytes")

    verify = sub.add_parser("verify-manifest", help="rehash members vs manifest")
    verify.add_argument("bundle_dir")

    cms = sub.add_parser("check-manifest-sha",
                         help="bind manifest git_sha to the requested release sha")
    cms.add_argument("bundle_dir")
    cms.add_argument("sha")

    hashf = sub.add_parser("hash-file", help="print the sha256 of a file")
    hashf.add_argument("path")

    imageids = sub.add_parser(
        "image-ids",
        help="print the manifest/config digests of a saved image archive",
    )
    imageids.add_argument("archive")

    web = sub.add_parser("verify-web", help="validate web dist layout")
    web.add_argument("archive")

    build = sub.add_parser("build-manifest", help="compute digests and write manifest")
    build.add_argument("bundle_dir")
    build.add_argument("git_sha")
    build.add_argument("api_image_id")
    build.add_argument("ci_run_id", type=int)
    build.add_argument("ci_run_url")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "read-stdin":
            size = read_stdin_to_file(args.out_path, args.cap_bytes)
            print(f"read-ok {size}")
        elif args.command == "extract-safe":
            extract_safe(args.archive, args.dest_dir,
                         max_member_bytes=args.max_member_b,
                         max_total_bytes=args.max_total_b)
            print("extract-ok")
        elif args.command == "verify-manifest":
            return _cmd_verify_manifest(args)
        elif args.command == "check-manifest-sha":
            return _cmd_check_manifest_sha(args)
        elif args.command == "hash-file":
            return _cmd_hash_file(args)
        elif args.command == "image-ids":
            return _cmd_image_ids(args)
        elif args.command == "verify-web":
            return _cmd_verify_web(args)
        elif args.command == "build-manifest":
            data = build_manifest(args.bundle_dir, args.git_sha, args.api_image_id,
                                  args.ci_run_id, args.ci_run_url)
            print(f"manifest-ok {data['git_sha']}")
        else:  # pragma: no cover - argparse enforces choices
            raise BundleError(f"unknown command: {args.command}")
    except BundleError as exc:
        print(f"BUNDLE-ERR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
