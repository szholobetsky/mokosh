"""Tracker tokens go to environment variables; project.yaml keeps ${VAR}. setx is replaced here."""
import subprocess

from mokosh import tokens
from mokosh.cli import _resolve_env_token


class _Setx:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "SUCCESS", "")


def _isolate(monkeypatch, env=None):
    setx = _Setx()
    monkeypatch.setattr(tokens.subprocess, "run", setx)
    monkeypatch.setattr(tokens, "_user_env", lambda name: "")
    for v in ("GITHUB_TOKEN", "JIRA_TOKEN", "MOKOSH_SQLFLUFF_GITHUB_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    return setx


def test_a_token_becomes_a_reference(monkeypatch):
    setx = _isolate(monkeypatch)
    cfg = {"source": "github", "github_owner": "sqlfluff", "github_token": "ghp_secret"}
    notes = tokens.secure(cfg, "sqlfluff")
    assert cfg["github_token"] == "${GITHUB_TOKEN}"
    assert _resolve_env_token(cfg["github_token"]) == "ghp_secret"     # this process sees it at once
    assert "ghp_secret" not in " ".join(notes)
    if tokens.os.name == "nt":
        assert setx.calls == [["setx", "GITHUB_TOKEN", "ghp_secret"]]


def test_another_token_in_the_variable_is_not_overwritten(monkeypatch):
    _isolate(monkeypatch, {"GITHUB_TOKEN": "ghp_someone_elses"})
    cfg = {"github_token": "ghp_secret"}
    tokens.secure(cfg, "sqlfluff")
    assert cfg["github_token"] == "${MOKOSH_SQLFLUFF_GITHUB_TOKEN}"
    assert tokens.os.environ["GITHUB_TOKEN"] == "ghp_someone_elses"


def test_a_reference_is_kept(monkeypatch):
    setx = _isolate(monkeypatch)
    cfg = {"jira_token": "${MY_JIRA}", "jira_url": "https://x"}
    assert tokens.secure(cfg) == []
    assert cfg["jira_token"] == "${MY_JIRA}" and setx.calls == []
