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
