"""mokosh end to end on a throwaway git repository: commits-only ingest, the checkpoint,
the settings of an earlier `simargl init`, status, export; and the task key extraction."""
import os
import subprocess

import pytest

from mokosh import cli, schema
from mokosh.task_extractor import TaskExtractor


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "PROJ-1 add a")
    (r / "b.py").write_text("y = 2\n", encoding="utf-8")
    (r / "a.py").write_text("x = 1\nz = 3\n", encoding="utf-8")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "PROJ-2 add b, change a")
    return r


def _project(folder, store, repo, db):
    d = folder / store
    d.mkdir()
    (d / "project.yaml").write_text(
        f"project: {{name: t, db: {db.as_posix()}}}\n"
        f"git: {{repo: {repo.as_posix()}, branch: main}}\n"
        "ingest: {batch_size: 100, rate_limit_delay: 0}\n", encoding="utf-8")
    return d


def _rows(db):
    import sqlite3
    con = sqlite3.connect(db)
    return sorted(con.execute('SELECT ID, SHA, PATH FROM "COMMIT"').fetchall())


def test_commits_only_ingest_and_rerun_adds_nothing(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "units.db"
    _project(tmp_path, ".mokosh", repo, db)
    assert cli.run_ingest(cli.resolve_store(None)) == db.as_posix()
    s = cli.status(str(db))
    assert s["commits"] == 2 and s["rows"] == 3 and s["files"] == 2 and s["tasks"] == 0
    assert (tmp_path / ".mokosh" / "ingest_state.yaml").is_file()
    before = _rows(db)
    cli.run_ingest(".mokosh")                                    # nothing new: nothing added, nothing doubled
    assert "0 new commits" in capsys.readouterr().out
    assert _rows(db) == before


def test_a_month_later_only_the_new_commits_are_added(tmp_path, repo, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "units.db"
    _project(tmp_path, ".mokosh", repo, db)
    cli.run_ingest(".mokosh")
    before = _rows(db)
    (repo / "c.py").write_text("w = 4\n", encoding="utf-8")
    (repo / "a.py").write_text("x = 1\nz = 3\nq = 5\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "PROJ-3 add c, change a")
    cli.run_ingest(".mokosh")
    after = _rows(db)
    assert after[:len(before)] == before                         # old rows untouched, same IDs
    new = after[len(before):]
    assert len(new) == 2 and {p for _, _, p in new} == {"a.py", "c.py"}
    assert min(i for i, _, _ in new) > max(i for i, _, _ in before)
    assert len({i for i, _, _ in after}) == len(after)           # IDs unique


class _FakeTracker:
    calls = []

    def fetch_task_details(self, key):
        _FakeTracker.calls.append(key)
        return f"title of {key}", "description", ""


def test_tasks_are_fetched_once_each(tmp_path, repo, monkeypatch):
    from mokosh import task_fetcher
    monkeypatch.setattr(task_fetcher, "_build_connector", lambda kind, cfg: _FakeTracker())
    _FakeTracker.calls = []
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "units.db"
    d = _project(tmp_path, ".mokosh", repo, db)
    with open(d / "project.yaml", "a", encoding="utf-8") as f:
        f.write("tasks: {source: jira, jira_url: http://x, jira_project: PROJ}\n")
    cli.run_ingest(".mokosh")
    assert sorted(_FakeTracker.calls) == ["PROJ-1", "PROJ-2"]
    (repo / "d.py").write_text("v = 6\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "PROJ-7 add d")
    cli.run_ingest(".mokosh")
    assert sorted(_FakeTracker.calls) == ["PROJ-1", "PROJ-2", "PROJ-7"]     # only the new one asked
    s = cli.status(str(db))
    assert s["tasks"] == 3 and s["linked_commits"] == 3


def test_an_earlier_simargl_setup_is_used_as_it_is(tmp_path, repo, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _project(tmp_path, ".simargl", repo, tmp_path / "old.db")
    assert cli.resolve_store(None) == ".simargl"
    cli.run_ingest(cli.resolve_store(None))
    assert (tmp_path / ".simargl" / "ingest_state.yaml").is_file()
    assert not (tmp_path / ".mokosh").exists()


def test_new_databases_use_the_singular_tables(tmp_path, repo, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "units.db"
    _project(tmp_path, ".mokosh", repo, db)
    cli.run_ingest(".mokosh")
    import sqlite3
    con = sqlite3.connect(db)
    assert schema.commit_table(con) == '"COMMIT"'


def test_plaintext_on_a_commits_only_database(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "units.db"
    _project(tmp_path, ".mokosh", repo, db)
    cli.run_ingest(".mokosh")
    assert cli.run_plaintext(str(db), dry_run=True)
    assert "nothing to clean" in capsys.readouterr().out


def test_export_commits(tmp_path, repo, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "units.db"
    _project(tmp_path, ".mokosh", repo, db)
    cli.run_ingest(".mokosh")
    out = tmp_path / "exp"
    res = cli.run_export(str(db), str(out), mode="commits")
    assert res["commits_exported"] == 2 and len(os.listdir(out / "commits")) == 2


def test_task_keys_from_commit_messages():
    ex = TaskExtractor("simple")
    assert ex.extract_task_name("PROJ-12 fix the login form") == "PROJ-12"
    assert ex.extract_task_name("refactor without a key") is None


def test_trackers_load_without_selenium():
    from mokosh import trackers
    assert trackers.GitHubApiConnector.__name__ == "GitHubApiConnector"
