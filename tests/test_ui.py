"""mokosh ui: projects, the Setup form (masks, tokens to the environment), ingest as a job, status."""
import os
import subprocess
import time

import pytest

from mokosh import tokens
from mokosh.ui import server


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "HOME", tmp_path / "home")
    monkeypatch.setattr(server, "REGISTRY", tmp_path / "home" / "projects.json")
    monkeypatch.setattr(server, "_start", str(tmp_path))
    real_run = subprocess.run                                      # never the real setx in a test; git runs

    def run(cmd, **kw):
        if cmd and cmd[0] == "setx":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kw)
    monkeypatch.setattr(tokens.subprocess, "run", run)
    monkeypatch.setattr(tokens, "_user_env", lambda name: "")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return server.create_app().test_client()


def _repo(path):
    path.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    for i, msg in enumerate(["[FLINK-1] one", "Fixed: two (FLINK-2)", "no key"]):
        (path / f"f{i}.txt").write_text(msg, encoding="utf-8")
        git("add", ".")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)
    return path


def test_the_page_and_its_files(app):
    assert b"Mokosh" in app.get("/").data
    for f in ("app.js", "i18n.js", "style.css", "lang/uk.js", "themes/ocean.css"):
        assert app.get(f"/ui/{f}").status_code == 200, f


def test_setup_writes_mokosh_project_yaml(app, tmp_path):
    repo = _repo(tmp_path / "proj")
    m = app.post("/api/mask_preview", json={"folder": str(repo), "repo": ".", "keys": "FLINK"}).get_json()
    rows = {r["mask"]: r for r in m["rows"]}
    assert m["total"] == 3 and rows["regex-key"]["linked"] == 2 and rows["simple"]["linked"] == 0
    bad = app.post("/api/init", json={"folder": str(repo), "form": {"tracker": "github", "gh_url": "https://github.com/o/"}})
    assert bad.status_code == 400 and "missed repo name" in bad.get_json()["error"]
    r = app.post("/api/init", json={"folder": str(repo), "form": {"tracker": "github", "gh_url": "https://github.com/o/r",
                                                                   "gh_token": "SECRET123", "mask": "generic"}}).get_json()
    raw = (repo / ".mokosh" / "project.yaml").read_text(encoding="utf-8")
    assert r["store"] == os.path.join(str(repo), ".mokosh")
    assert "SECRET123" not in raw and "${GITHUB_TOKEN}" in raw and "github_owner: o" in raw and "commit_mask: generic" in raw
    assert app.post("/api/init", json={"folder": str(repo), "form": {}}).status_code == 400      # no silent overwrite
    j = app.post("/api/init", json={"folder": str(repo), "overwrite": True, "form": {
        "tracker": "jira", "jira_url": "https://x", "jira_project": "FLINK", "mask": "regex-key"}}).get_json()
    assert "commit_mask: regex-key" in j["yaml"] and "jira_connector: api" in j["yaml"]
    listed = app.get("/api/projects").get_json()["projects"]
    assert [p["folder"] for p in listed] == [str(repo)] and listed[0]["tracker"] == "jira"


def test_ingest_runs_as_a_job_and_status_reads_the_db(app, tmp_path):
    repo = _repo(tmp_path / "proj")
    app.post("/api/init", json={"folder": str(repo), "form": {"tracker": "none"}})
    jid = app.post("/api/ingest", json={"folder": str(repo), "phase": "git"}).get_json()["job"]
    for _ in range(120):
        j = app.get(f"/api/jobs/{jid}").get_json()
        if j["status"] != "running":
            break
        time.sleep(0.5)
    assert j["status"] == "done", j
    s = app.post("/api/status", json={"folder": str(repo)}).get_json()
    assert s["db_exists"] and s["stats"]["commits"] == 3 and "git:" in s["state"]
    assert app.post("/api/ingest", json={"folder": str(tmp_path)}).status_code == 400            # no project.yaml there
