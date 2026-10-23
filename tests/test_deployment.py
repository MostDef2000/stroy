import shlex
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "deploy/vps/deploy-wrapper"
COMPOSE = REPO / "deploy/vps/docker-compose.yml"
ENV_EXAMPLE = REPO / "deploy/vps/env.example"


def test_dockerfile_api_copies_workflows():
    # In a real scenario, we'd read the file and check for the COPY line.
    # Since we are in a test suite, we can just read the file from the repo root.
    with open("deploy/vps/Dockerfile.api", "r") as f:
        content = f.read()
    assert "COPY workflows ./workflows" in content


def test_vps_worker_does_not_advertise_blender_render():
    # #64: fake mode has no real Blender executor, so the VPS worker must not
    # advertise blender_render or it can silently lease render jobs.
    with open("deploy/vps/docker-compose.yml", "r") as f:
        content = f.read()
    capability_lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip().startswith("STROY_WORKER_CAPABILITIES:")
    ]
    assert capability_lines, "worker capabilities line not found"
    assert "blender_render" not in capability_lines[0]


def _wrapper_config_block() -> str:
    """Extract the deploy-wrapper configuration block (assignments + mode
    validation) so it can be sourced under bash without executing main()."""
    text = WRAPPER.read_text()
    start = text.index("# ---- configuration")
    end = text.index("on_err() {")
    return text[start:end]


def _eval_config(env: dict[str, str], probe_vars: tuple[str, ...] = ("EDGE_MODE", "WEB_MODE", "HEALTH_URL", "PUBLIC_URL", "PUBLIC_URL_SET")) -> tuple[int, str, str, dict[str, str]]:
    script = "".join(f"export {k}={shlex.quote(v)}\n" for k, v in env.items())
    script += _wrapper_config_block() + "\n"
    script += " ".join(f'printf "%s\\n" "{v}=${v}"' for v in probe_vars) + "\n"
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    parsed = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            parsed[key] = value
    return proc.returncode, proc.stdout, proc.stderr, parsed


def test_wrapper_defaults_match_legacy_single_host_topology():
    # Default modes must reproduce the pre-migration behavior exactly:
    # nginx edge on the API host, web published locally.
    rc, _, err, cfg = _eval_config({})
    assert rc == 0, err
    assert cfg["EDGE_MODE"] == "nginx"
    assert cfg["WEB_MODE"] == "local"
    assert cfg["HEALTH_URL"] == "http://127.0.0.1:8000/health"
    assert cfg["PUBLIC_URL_SET"] == "0"


def test_wrapper_health_url_follows_api_host_port():
    rc, _, err, cfg = _eval_config({"STROY_API_HOST_PORT": "8002"})
    assert rc == 0, err
    assert cfg["HEALTH_URL"] == "http://127.0.0.1:8002/health"


def test_wrapper_explicit_health_url_wins_over_port_derivation():
    rc, _, err, cfg = _eval_config(
        {"STROY_API_HOST_PORT": "8002", "STROY_DEPLOY_HEALTH_URL": "http://127.0.0.1:9999/health"}
    )
    assert rc == 0, err
    assert cfg["HEALTH_URL"] == "http://127.0.0.1:9999/health"


def test_wrapper_external_edge_mode_selected():
    rc, _, err, cfg = _eval_config({"STROY_DEPLOY_EDGE": "external"})
    assert rc == 0, err
    assert cfg["EDGE_MODE"] == "external"


def test_wrapper_rejects_unknown_edge_mode():
    rc, _, err, _ = _eval_config({"STROY_DEPLOY_EDGE": "haproxy"})
    assert rc != 0
    assert "invalid STROY_DEPLOY_EDGE" in err


def test_wrapper_web_external_mode_selected():
    rc, _, err, cfg = _eval_config({"STROY_DEPLOY_WEB": "external"})
    assert rc == 0, err
    assert cfg["WEB_MODE"] == "external"


def test_wrapper_rejects_unknown_web_mode():
    rc, _, err, _ = _eval_config({"STROY_DEPLOY_WEB": "rsync"})
    assert rc != 0
    assert "invalid STROY_DEPLOY_WEB" in err


def test_wrapper_public_url_set_only_when_explicitly_configured():
    # Without explicit config the ingress probe is disabled in external-edge
    # mode (127.0.0.1 no longer terminates the edge); with it, enabled.
    rc, _, err, cfg = _eval_config({"STROY_DEPLOY_EDGE": "external"})
    assert rc == 0, err
    assert cfg["PUBLIC_URL_SET"] == "0"
    rc, _, err, cfg = _eval_config(
        {"STROY_DEPLOY_EDGE": "external", "STROY_DEPLOY_PUBLIC_URL": "https://stroy.mostdef.ru/"}
    )
    assert rc == 0, err
    assert cfg["PUBLIC_URL_SET"] == "1"


def test_wrapper_rollback_skips_nginx_and_web_in_external_modes():
    # Regression pin: rollback_previous must guard its nginx restore, the
    # nginx -t/reload pair, and the web symlink repoint behind the mode
    # checks — an external-edge rollback must never touch nginx or a web
    # symlink nobody serves.
    text = WRAPPER.read_text()
    rollback = text.split("rollback_previous() {", 1)[1]
    assert 'if [ "$EDGE_MODE" = "nginx" ]; then' in rollback
    assert 'if [ "$WEB_MODE" = "local" ]; then' in rollback


def test_compose_api_port_is_loopback_and_parametrized():
    # #211: same compose must serve the legacy 8000 topology by default and
    # the re-targeted 8002 API host via STROY_API_HOST_PORT; container port
    # stays 8000 either way. Bind address is parametrized too: default
    # 127.0.0.1 (loopback-only, single-host edge), tailnet IP for the
    # external-edge topology.
    content = COMPOSE.read_text()
    assert '"${STROY_API_BIND:-127.0.0.1}:${STROY_API_HOST_PORT:-8000}:8000"' in content


def test_env_example_documents_api_host_port_default():
    content = ENV_EXAMPLE.read_text()
    assert "STROY_API_BIND=127.0.0.1" in content
    assert "STROY_API_HOST_PORT=8000" in content
    assert "STROY_DEPLOY_EDGE" in content  # pointer to wrapper-side config
