from fastapi.testclient import TestClient

from dylive.server import create_app, _safe_media


def test_health_and_index(app_cfg):
    app = create_app(app_cfg)
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    page = client.get("/")
    assert page.status_code == 200
    body = page.text
    assert "抖音直播切片工作室" in body
    css = client.get("/static/styles.css")
    assert css.status_code == 200
    assert "#0d0d0f" in css.text
    js = client.get("/static/app.js")
    assert js.status_code == 200


def test_effects_endpoint(app_cfg):
    client = TestClient(create_app(app_cfg))
    r = client.get("/api/effects")
    assert r.status_code == 200
    data = r.json()
    names = {row["name"] for row in data["ffmpeg"]}
    for n in ("vignette", "grain", "glitch", "rgb_split", "contrast", "punch_zoom"):
        assert n in names
    assert "fadeblack" in data["xfade_types"]
    assert any(m["ffmpeg"] == "fadeblack" for m in data["map"])


def test_jobs_empty(app_cfg):
    client = TestClient(create_app(app_cfg))
    r = client.get("/api/jobs")
    assert r.status_code == 200
    assert r.json()["jobs"] == []


def test_media_rejects_traversal(app_cfg, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("nope", encoding="utf-8")
    assert _safe_media(app_cfg, "clips", "../secret.txt") is None
    assert _safe_media(app_cfg, "clips", "/etc/passwd") is None
    client = TestClient(create_app(app_cfg))
    r = client.get("/media/clips/../../secret.txt")
    assert r.status_code in {404, 400, 422}
