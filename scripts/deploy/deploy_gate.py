"""Pure-python CI-run gate for the STROY production deploy (issue #130).

No repo dependencies (stdlib only): the GitHub Actions ``gate`` job fetches
JSON with ``curl`` and pipes it here.  Every check is fail-closed.

Trusted CI run definition (frozen design):
  * workflow ``ci.yml``
  * ``event == "push"``, ``head_branch == "main"``
  * ``status == "completed"`` and ``conclusion == "success"``
  * the LATEST eligible run is selected (by ``created_at`` then ``id``)
  * ALL five ci.yml jobs must exist and be ``success`` (skipped/cancelled/
    missing/malformed, or an incomplete paginated payload, = FAIL).
"""

from __future__ import annotations

import argparse
import json
import re
import sys

REQUIRED_JOBS = (
    "blender-smoke",
    "contracts-and-python",
    "deployment-config",
    "web",
    "security-gates",
)

TRUSTED_EVENT = "push"
TRUSTED_BRANCH = "main"
TRUSTED_STATUS = "completed"
TRUSTED_CONCLUSION = "success"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def validate_sha(sha: object) -> bool:
    """True only for an exact full lowercase 40-hex commit SHA."""
    return isinstance(sha, str) and bool(_SHA_RE.fullmatch(sha))


def _loads(payload):
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def parse_runs(payload, sha: str) -> list[dict]:
    """Parse a workflow-runs API payload and keep runs for *sha*.

    Raises ``ValueError`` on a malformed payload or invalid sha.
    """
    if not validate_sha(sha):
        raise ValueError("sha must be 40 lowercase hex")
    data = _loads(payload)
    if not isinstance(data, dict):
        raise ValueError("runs payload must be a JSON object")
    runs = data.get("workflow_runs")
    if not isinstance(runs, list):
        raise ValueError("runs payload missing workflow_runs list")
    total = data.get("total_count")
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        raise ValueError("runs payload total_count must be a positive integer")
    return [run for run in runs if isinstance(run, dict) and run.get("head_sha") == sha]


def select_run(runs: list[dict]) -> dict | None:
    """Return the latest trusted run, or ``None`` if none is eligible."""
    if not isinstance(runs, list):
        raise ValueError("runs must be a list")
    eligible = [
        run
        for run in runs
        if isinstance(run, dict)
        and run.get("event") == TRUSTED_EVENT
        and run.get("head_branch") == TRUSTED_BRANCH
        and run.get("status") == TRUSTED_STATUS
        and run.get("conclusion") == TRUSTED_CONCLUSION
    ]
    if not eligible:
        return None

    def _key(run: dict):
        run_id = run.get("id")
        return (str(run.get("created_at") or ""), int(run_id) if isinstance(run_id, int) else 0)

    return max(eligible, key=_key)


def verify_jobs(payload, required: tuple[str, ...] = REQUIRED_JOBS) -> tuple[bool, str]:
    """Verify every required job exists and succeeded. Fail-closed."""
    try:
        data = _loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return False, f"invalid jobs payload: {exc}"
    if not isinstance(data, dict):
        return False, "invalid jobs payload: not an object"
    if data.get("incomplete") is True:
        return False, "jobs payload marked incomplete (pagination)"
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        return False, "no jobs returned"
    total = data.get("total_count")
    if isinstance(total, int) and not isinstance(total, bool) and total > len(jobs):
        return False, f"pagination incomplete: {len(jobs)} of {total} jobs fetched"

    by_name: dict[str, list[dict]] = {}
    for job in jobs:
        if not isinstance(job, dict):
            return False, "malformed job entry"
        name = job.get("name")
        if not isinstance(name, str) or not name:
            return False, "malformed job entry: missing name"
        by_name.setdefault(name, []).append(job)

    for name in required:
        entries = by_name.get(name)
        if not entries:
            return False, f"required job missing: {name}"
        for entry in entries:
            conclusion = entry.get("conclusion")
            if conclusion != TRUSTED_CONCLUSION:
                return False, f"job {name} conclusion={conclusion!r} (want success)"
    return True, "all required jobs succeeded"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _read_payload(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deploy_gate")
    sub = parser.add_subparsers(dest="command", required=True)

    sha_cmd = sub.add_parser("validate-sha")
    sha_cmd.add_argument("sha")

    select = sub.add_parser("select-run")
    select.add_argument("runs_json")
    select.add_argument("sha")

    jobs = sub.add_parser("verify-jobs")
    jobs.add_argument("jobs_json")

    args = parser.parse_args(argv)

    if args.command == "validate-sha":
        if not validate_sha(args.sha):
            print(f"GATE-ERR: sha is not 40 lowercase hex: {args.sha!r}", file=sys.stderr)
            return 1
        print("sha-ok")
        return 0

    if args.command == "select-run":
        try:
            runs = parse_runs(_read_payload(args.runs_json), args.sha)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"GATE-ERR: {exc}", file=sys.stderr)
            return 1
        run = select_run(runs)
        if run is None:
            print("GATE-ERR: no eligible trusted CI run for sha", file=sys.stderr)
            return 1
        summary = {
            "id": run.get("id"),
            "url": run.get("html_url"),
            "run_number": run.get("run_number"),
            "created_at": run.get("created_at"),
            "head_sha": run.get("head_sha"),
        }
        print(json.dumps(summary))
        return 0

    if args.command == "verify-jobs":
        ok, reason = verify_jobs(_read_payload(args.jobs_json))
        if not ok:
            print(f"GATE-ERR: {reason}", file=sys.stderr)
            return 1
        print(f"jobs-ok: {reason}")
        return 0

    parser.error(f"unknown command: {args.command}")  # pragma: no cover
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
