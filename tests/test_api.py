def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert isinstance(body["ffmpeg"], bool)


def test_project_list_starts_empty(client):
    assert client.get("/api/projects").json() == []


def test_create_and_get_project(client, tmp_path):
    created = client.post("/api/projects", json={"name": "  测试项目  "})
    assert created.status_code == 201
    project = created.json()
    assert project["name"] == "测试项目"
    assert project["status"] == "created"

    assert client.get(f"/api/projects/{project['id']}").json()["id"] == project["id"]
    assert [p["id"] for p in client.get("/api/projects").json()] == [project["id"]]
    assert (tmp_path / "data" / "projects" / project["id"] / "clips").is_dir()


def test_create_project_rejects_blank_name(client):
    assert client.post("/api/projects", json={"name": ""}).status_code == 422
    assert client.post("/api/projects", json={"name": "   "}).status_code == 422


def test_missing_project_is_404(client):
    assert client.get("/api/projects/does-not-exist").status_code == 404


def test_capabilities_providers_presets(client):
    capabilities = client.get("/api/capabilities").json()
    assert len(capabilities) == 8
    assert {c["id"] for c in capabilities if c["optional"]} == {"tts", "lipsync", "asr"}

    providers = {p["id"]: p for p in client.get("/api/providers").json()}
    assert providers["mock"]["implemented"] is True
    assert providers["dashscope"]["implemented"] is True
    assert providers["volcengine"]["implemented"] is False

    presets = {p["id"] for p in client.get("/api/presets").json()}
    assert presets == {"cn", "global", "demo"}


def test_setup_status_unconfigured(client):
    assert client.get("/api/setup/status").json() == {"configured": False}


def test_jobs_list_empty_and_404(client):
    assert client.get("/api/jobs").json() == []
    assert client.get("/api/jobs/missing").status_code == 404
