def test_dockerfile_api_copies_workflows():
    # In a real scenario, we'd read the file and check for the COPY line.
    # Since we are in a test suite, we can just read the file from the repo root.
    with open("deploy/vps/Dockerfile.api", "r") as f:
        content = f.read()
    assert "COPY workflows ./workflows" in content
