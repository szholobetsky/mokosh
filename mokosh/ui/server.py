"""mokosh web UI — Flask API + static pages (mokosh/ui/webui, no build step). mokosh gathers the
dataset; simargl, gamayun and svitovyd only read it, so the Setup form and the ingest live here.

Start:  mokosh ui [--port 8093] [--host 127.0.0.1] [--root FOLDER]

A project is a folder with .mokosh/project.yaml (or an earlier .simargl/project.yaml): the folders
under the one the UI was started in, and those remembered in ~/.mokosh/projects.json once opened or set
up here (`mokosh init` remembers them too).

  GET  /api/projects                         known project folders
  POST /api/projects/open   {path}           remember a folder (the project or its .mokosh)
  POST /api/mask_preview    {folder, repo, branch, mask, keys}   every commit mask tried on the last commits
  POST /api/init            {folder, overwrite, form}            write .mokosh/project.yaml
  POST /api/status          {folder}         project.yaml (tokens masked), ingest_state.yaml, what the db holds
  POST /api/ingest          {folder, phase, force}               mokosh ingest as a job -> {job}
  GET  /api/jobs/<id>                        {status, log, error}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import traceback
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

UI_DIR = Path(__file__).parent / "webui"
HOME = Path(os.environ.get("MOKOSH_HOME") or Path.home() / ".mokosh")     # MOKOSH_HOME: for the tests
REGISTRY = HOME / "projects.json"
STORE = ".mokosh"
LEGACY_STORE = ".simargl"
SKIP = {".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist", "target", ".idea"}
SECRET = ("token", "password", "secret")

_start = None
_jobs = {}


# ── projects ─────────────────────────────────────────────────────────────────

def _registry() -> list:
    try:
        return [p for p in json.loads(REGISTRY.read_text(encoding="utf-8")) if isinstance(p, str)]
    except (OSError, ValueError):
        return []


def remember(folder: str):
    """Put a project folder first in ~/.mokosh/projects.json (mokosh init calls it too)."""
    p = os.path.abspath(folder)
    rest = [x for x in _registry() if _same(x) != _same(p)]
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        REGISTRY.write_text(json.dumps([p] + rest[:49], indent=1), encoding="utf-8")
    except OSError:
        pass


def _same(path: str) -> str:
    """One key per folder: c:/x and C:/X are the same folder on Windows."""
    return os.path.normcase(os.path.abspath(path))


def _folder_of(path: str) -> str:
    """A project folder from what was typed: the folder itself, or its .mokosh / .simargl."""
    p = os.path.normpath((path or "").strip().strip('"'))
    if os.path.basename(p) in (STORE, LEGACY_STORE):
        p = os.path.dirname(p)
    return os.path.abspath(p)


def _store_of(folder: str) -> str | None:
    """The folder's settings folder: .mokosh, else an earlier .simargl (as `mokosh ingest` picks)."""
    for name in (STORE, LEGACY_STORE):
        if os.path.isfile(os.path.join(folder, name, "project.yaml")):
            return os.path.join(folder, name)
    return None


def _discover(start, max_depth=4) -> list:
    out = []
    if not start or not os.path.isdir(start):
        return out
    base = len(Path(start).parts)
    for dirpath, dirnames, _ in os.walk(start):
        if len(Path(dirpath).parts) - base >= max_depth:
            dirnames[:] = []
        if _store_of(dirpath):
            out.append(dirpath)
        dirnames[:] = [d for d in dirnames if d not in SKIP and not d.startswith(".")]
    return out


def _yaml(path):
    import yaml
    try:
        return yaml.safe_load(open(path, encoding="utf-8").read()) or {}
    except (OSError, ValueError):
        return {}


def _yaml_text(path, mask=True):
    import yaml
    if not os.path.isfile(path):
        return ""

    def hide(x):
        if isinstance(x, dict):
            return {k: ("***" if mask and any(s in str(k).lower() for s in SECRET) and v
                        and not str(v).startswith("${") else hide(v)) for k, v in x.items()}
        if isinstance(x, list):
            return [hide(v) for v in x]
        return x
    return yaml.dump(hide(_yaml(path)), default_flow_style=False, allow_unicode=True, sort_keys=False)


def _describe(folder):
    store = _store_of(folder)
    cfg = _yaml(os.path.join(store, "project.yaml")) if store else {}
    db = ((cfg.get("project") or {}).get("db")) or ""
    db_abs = db if not db or os.path.isabs(db) else os.path.join(folder, db)
    return {"folder": folder, "name": (cfg.get("project") or {}).get("name") or os.path.basename(folder),
            "store": store or "", "db": db_abs, "db_exists": bool(db_abs) and os.path.isfile(db_abs),
            "tracker": (cfg.get("tasks") or {}).get("source", "")}


def _folder_arg(b) -> str:
    f = _folder_of(b.get("folder") or "")
    if not os.path.isdir(f):
        raise ValueError(f"not a folder: {f}")
    return f


# ── jobs ─────────────────────────────────────────────────────────────────────

def _cli_job(args, cwd):
    """Run the mokosh CLI as a child process; its output streams into the job's log."""
    jid = uuid.uuid4().hex[:12]
    job = {"status": "running", "log": ["$ mokosh " + " ".join(args)], "error": ""}
    _jobs[jid] = job

    def run():
        try:
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
            proc = subprocess.Popen([sys.executable, "-m", "mokosh.cli", *args], cwd=cwd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
            for line in proc.stdout:
                line = line.rstrip("\n").split("\r")[-1]          # tqdm redraws with \r: the last state only
                if line.strip():
                    job["log"].append(line)
                    del job["log"][:-400]
            proc.wait()
            job["status"] = "done" if proc.returncode == 0 else "error"
            if proc.returncode:
                job["error"] = f"exit code {proc.returncode}"
        except Exception as e:
            job["status"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"
            job["log"].append(traceback.format_exc())

    threading.Thread(target=run, daemon=True).start()
    return jid


# ── app ──────────────────────────────────────────────────────────────────────

def create_app():
    app = Flask(__name__, static_folder=None)

    @app.errorhandler(ValueError)
    @app.errorhandler(FileNotFoundError)
    def _bad(e):
        return jsonify({"error": str(e)}), 400

    @app.get("/")
    def index():
        return send_from_directory(UI_DIR, "index.html")

    @app.get("/ui/<path:name>")
    def ui(name):
        return send_from_directory(UI_DIR, name)

    @app.get("/api/projects")
    def projects():
        seen, out = set(), []
        for f in _registry() + _discover(_start):
            a = os.path.abspath(f)
            if _same(a) not in seen and os.path.isdir(a) and _store_of(a):
                seen.add(_same(a))
                out.append(_describe(a))
        return jsonify({"projects": out, "start": _start or ""})

    @app.post("/api/projects/open")
    def open_project():
        f = _folder_arg({"folder": (request.get_json(silent=True) or {}).get("path", "")})
        if not _store_of(f):
            raise ValueError(f"no .mokosh/project.yaml in {f} — fill in the Setup page for it")
        remember(f)
        return jsonify(_describe(f))

    @app.post("/api/mask_preview")
    def mask_preview():
        from ..task_extractor import MASK_HELP, compile_mask, preview_masks, project_keys, recent_messages
        b = request.get_json(silent=True) or {}
        folder = _folder_of(b.get("folder") or "") if (b.get("folder") or "").strip() else (_start or ".")
        repo = (b.get("repo") or ".").strip().strip('"')
        repo = repo if os.path.isabs(repo) else os.path.normpath(os.path.join(folder, repo))
        masks = [m for m, _, _ in MASK_HELP]
        keys = project_keys({"jira_project": b.get("keys") or ""})     # the project key(s) of the form, for regex-key
        own = (b.get("mask") or "").strip()
        if own and own not in masks:
            compile_mask(own, keys)
            masks.append(own)
        msgs = recent_messages(repo, 300, (b.get("branch") or "").strip())
        return jsonify({"repo": repo, "total": len(msgs), "keys": keys,
                        "rows": preview_masks(msgs, masks, keys) if msgs else [],
                        "help": [{"mask": m, "example": e} for m, _, e in MASK_HELP]})

    @app.post("/api/init")
    def init():
        import yaml
        from ..task_extractor import compile_mask, project_keys
        from ..tokens import secure
        from ..trackers.github import parse_repo_url
        b = request.get_json(silent=True) or {}
        folder = _folder_arg(b)
        store = os.path.join(folder, STORE)
        path = os.path.join(store, "project.yaml")
        if os.path.isfile(path) and not b.get("overwrite"):
            raise ValueError("project.yaml already exists here — tick 'overwrite' to replace it")
        f = {k: (str(v).strip() if v is not None else "") for k, v in (b.get("form") or {}).items()}
        cfg = {"project": {"name": f.get("name") or os.path.basename(folder), "db": f.get("db") or "units.db"},
               "git": {"repo": f.get("repo") or ".", "branch": f.get("branch") or "main"},
               "ingest": {"batch_size": 100, "rate_limit_delay": 1.0}}
        if f.get("since"):
            cfg["git"]["since"] = f["since"]
        tracker = f.get("tracker") or "none"
        fields = {"jira": [("jira_url", "jira_url"), ("jira_project", "jira_project"),
                           ("jira_connector", "jira_connector"), ("jira_token", "jira_token")],
                  "github": [("github_token", "gh_token")],
                  "youtrack": [("youtrack_url", "yt_url"), ("youtrack_project", "yt_project"), ("youtrack_token", "yt_token")],
                  "gitlab": [("gitlab_url", "gl_url"), ("gitlab_project", "gl_project"), ("gitlab_token", "gl_token")]}
        notes = []
        if tracker in fields:
            tc = {"source": tracker}
            for key, name in fields[tracker]:
                if f.get(name):
                    tc[key] = f[name]
            if tracker == "jira":
                tc.setdefault("jira_connector", "api")
            if tracker == "github":                       # one URL: the owner and the repository out of it
                tc["github_owner"], tc["github_repo"] = parse_repo_url(f.get("gh_url", ""))
            mask = f.get("mask") or ("generic" if tracker in ("github", "gitlab") else "simple")
            compile_mask(mask, project_keys(tc))          # regex-key: built from jira_project / youtrack_project
            tc["commit_mask"] = mask
            cfg["tasks"] = tc
            notes = secure(tc, cfg["project"]["name"])   # a token goes to an environment variable
        os.makedirs(store, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(cfg, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
        remember(folder)
        return jsonify({"folder": folder, "store": store, "yaml": _yaml_text(path), "notes": notes,
                        "mode": "tasks" if "tasks" in cfg else "commits"})

    @app.post("/api/status")
    def status():
        b = request.get_json(silent=True) or {}
        folder = _folder_arg(b)
        d = _describe(folder)
        if not d["store"]:
            raise ValueError(f"no .mokosh/project.yaml in {folder}")
        out = {**d, "yaml": _yaml_text(os.path.join(d["store"], "project.yaml")),
               "state": _yaml_text(os.path.join(d["store"], "ingest_state.yaml"), mask=False)}
        if d["db_exists"]:
            from ..cli import status as db_status
            out["stats"] = db_status(d["db"])
        return jsonify(out)

    @app.post("/api/ingest")
    def ingest():
        b = request.get_json(silent=True) or {}
        folder = _folder_arg(b)
        store = _store_of(folder)
        if not store:
            raise ValueError(f"no .mokosh/project.yaml in {folder} — fill in the Setup page first")
        args = ["ingest", "--store-dir", store]
        if b.get("phase") in ("git", "tasks"):
            args += ["--phase", b["phase"]]
        if b.get("force"):
            args.append("--force")
        remember(folder)
        return jsonify({"job": _cli_job(args, folder)})

    @app.get("/api/jobs/<jid>")
    def job(jid):
        j = _jobs.get(jid)
        if not j:
            raise ValueError("unknown job")
        return jsonify(j)

    return app


def serve(host="127.0.0.1", port=8093, start=None):
    global _start
    _start = os.path.abspath(start or os.getcwd())
    if _store_of(_start):
        remember(_start)
    from waitress import serve as waitress_serve
    print(f"mokosh UI: http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}/")
    waitress_serve(create_app(), host=host, port=port, threads=8)
