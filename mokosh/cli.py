"""mokosh command line: gather the dataset.

  mokosh init                                    # wizard: repository, branch, tracker -> .mokosh/project.yaml
  mokosh ingest [--phase git|tasks] [--force]    # commits, then task references and task details;
                                                 # run it again (e.g. monthly) to add only what is new
  mokosh plaintext <db> [--dry-run]              # tracker markup (Jira ADF, wiki) in TASK -> plain text
  mokosh export <db> [--join|--all|--task|--commits] [--level title|desc|comments] [--out DIR]
  mokosh status [<db>]                           # what the database holds
  mokosh fixdiff <db> --repo <repo>              # DIFF of an older mokosh (commit -> parent) the right way
  mokosh ui [--port 8093] [--host 127.0.0.1]     # web UI: Setup form, ingest with its log, status

The project settings and the resume checkpoint live in .mokosh/ (project.yaml, ingest_state.yaml).
A folder set up earlier with `simargl init` (.simargl/project.yaml) is used as it is: no new init,
no ingest from scratch. Then: `simargl index units <db>`, `gamayun index --db <db>`.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sqlite3
import sys

STORE = ".mokosh"
LEGACY_STORE = ".simargl"


def _yaml():
    import yaml
    return yaml


def resolve_store(store_dir: str | None) -> str:
    """The folder with project.yaml: the one given, else .mokosh, else an earlier .simargl."""
    if store_dir:
        return store_dir
    if not os.path.isfile(os.path.join(STORE, "project.yaml")) and \
            os.path.isfile(os.path.join(LEGACY_STORE, "project.yaml")):
        return LEGACY_STORE
    return STORE


def _resolve_env_token(value: str) -> str:
    """${VAR} -> the environment variable's value (tokens stay out of project.yaml)."""
    from .tokens import resolve
    return resolve(value)


def load_project(store_dir: str) -> dict | None:
    path = os.path.join(store_dir, "project.yaml")
    if not os.path.exists(path):
        print(f"project.yaml not found at {path}")
        print("Run: mokosh init")
        return None
    with open(path, encoding="utf-8") as f:
        return _yaml().safe_load(f)


def _load_state(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return _yaml().safe_load(f) or {}
    return {}


def _save_state(path: str, state: dict):
    with open(path, "w", encoding="utf-8") as f:
        _yaml().dump(state, f, default_flow_style=False, allow_unicode=True)


def _fresh_state() -> dict:
    return {"git": {"status": "pending"},
            "tasks": {"status": "pending", "fetched": 0, "last_key": None}}


# ── init ─────────────────────────────────────────────────────────────────────

def _ask_mask(repo: str, branch: str, default: str, keys=None) -> str:
    """Explain what each commit mask finds, try them all on the repository's last commits and
    ask, the mask that links the most commits offered as the default."""
    from .task_extractor import MASK_HELP, REGEX_EXAMPLES, compile_mask, preview_masks, recent_messages
    print("\nHow is a task key written in a commit message? (commit_mask)")
    for name, finds, example in MASK_HELP:
        print(f"  {name:<10} {finds:<42} e.g. {example}")
    print("  or a regex of your own: the first group is the key, if there is one, e.g.")
    for rx, what in REGEX_EXAMPLES:
        print(f"    {rx:<30} {what}")
    if keys:
        print(f"  regex-key here: the key{'s' if len(keys) > 1 else ''} {', '.join(keys)}")
    messages = recent_messages(repo, 300, branch)
    if messages:
        rows = preview_masks(messages, keys=keys)
        print(f"\nOn the last {len(messages)} commits of {repo}:")
        for r in rows:
            ex = ", ".join(r["examples"]) if r["examples"] else "-"
            print(f"  {r['mask']:<10} links {r['linked']:>4} ({100 * r['linked'] / r['total']:.0f}%)   {ex}")
        best = max(rows, key=lambda r: r["linked"])
        if best["linked"]:
            default = best["mask"]
    else:
        print(f"\n(no git history read from {repo} — cannot try the masks on it)")
    while True:
        mask = input(f"Commit mask [Enter = {default}]: ").strip() or default
        try:
            compile_mask(mask, keys)
            return mask
        except ValueError as e:
            print(f"  {e}")


def run_init(store_dir: str = STORE, next_hint: str = "mokosh ingest"):
    yaml_path = os.path.join(store_dir, "project.yaml")
    if os.path.exists(yaml_path):
        ans = input(f"{yaml_path} already exists. Overwrite? [y/N]: ").strip().lower()
        if ans != "y":
            print("[cancelled]")
            return

    print("\nmokosh init\n")
    default_name = os.path.basename(os.path.abspath("."))
    name = input(f"Project name [{default_name}]: ").strip() or default_name
    repo = input("Git repo path [. = current folder]: ").strip() or "."
    branch = input("Branch [main]: ").strip() or "main"
    since = input("Fetch history since (YYYY-MM-DD, Enter = full history): ").strip()

    print()
    tracker = input("Task tracker? [jira/github/youtrack/gitlab, Enter = none]: ").strip().lower() or "none"

    cfg: dict = {
        "project": {"name": name, "db": "units.db"},
        "git": {"repo": repo, "branch": branch},
        "ingest": {"batch_size": 100, "rate_limit_delay": 1.0},
    }
    if since:
        cfg["git"]["since"] = since

    if tracker == "jira":
        jira_url = input("Jira URL: ").strip()
        jira_proj = input("Jira project key (e.g. KAFKA): ").strip()
        jira_conn = input("Connector [api/html/selenium, Enter = api]: ").strip() or "api"
        jira_token = input("Token (Enter to skip for public instances): ").strip()
        tasks_cfg: dict = {"source": "jira", "jira_url": jira_url,
                           "jira_project": jira_proj, "jira_connector": jira_conn}
        if jira_token:
            tasks_cfg["jira_token"] = jira_token
        cfg["tasks"] = tasks_cfg
    elif tracker == "github":
        from .trackers.github import URL_HELP, parse_repo_url
        while True:                                  # one URL, owner and repo parsed out of it
            try:
                gh_owner, gh_repo = parse_repo_url(input(f"GitHub repo URL ({URL_HELP}): "))
                break
            except ValueError as e:
                print(f"  {e}")
        gh_token = input("Token (Enter to skip, but only 60 req/h without): ").strip()
        tasks_cfg = {"source": "github", "github_owner": gh_owner, "github_repo": gh_repo}
        if gh_token:
            tasks_cfg["github_token"] = gh_token
        cfg["tasks"] = tasks_cfg
    elif tracker == "youtrack":
        yt_url = input("YouTrack URL [https://youtrack.jetbrains.com]: ").strip() or "https://youtrack.jetbrains.com"
        yt_proj = input("YouTrack project key (e.g. KT): ").strip()
        yt_token = input("Token (Enter to skip for public instances): ").strip()
        tasks_cfg = {"source": "youtrack", "youtrack_url": yt_url, "youtrack_project": yt_proj}
        if yt_token:
            tasks_cfg["youtrack_token"] = yt_token
        cfg["tasks"] = tasks_cfg
    elif tracker == "gitlab":
        gl_url = input("GitLab URL [https://gitlab.com]: ").strip() or "https://gitlab.com"
        gl_proj = input("GitLab project (org/repo): ").strip()
        gl_token = input("Token (required for comments, Enter to skip): ").strip()
        tasks_cfg = {"source": "gitlab", "gitlab_url": gl_url, "gitlab_project": gl_proj}
        if gl_token:
            tasks_cfg["gitlab_token"] = gl_token
        cfg["tasks"] = tasks_cfg

    if "tasks" in cfg:                    # how commits are linked to tasks — for every tracker
        from .task_extractor import project_keys
        keys = project_keys(cfg["tasks"])
        cfg["tasks"]["commit_mask"] = _ask_mask(repo, branch,
                                                "generic" if tracker in ("github", "gitlab") else
                                                "regex-key" if keys else "simple", keys)
        # a token goes to an environment variable; project.yaml keeps ${VAR}
        from .tokens import secure
        for note in secure(cfg["tasks"], name):
            print(f"  {note}")

    os.makedirs(store_dir, exist_ok=True)
    with open(yaml_path, "w", encoding="utf-8") as f:
        _yaml().dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"\nCreated {yaml_path}")
    from .ui.server import remember                # mokosh ui lists the project from any folder
    remember(os.path.dirname(os.path.abspath(store_dir)))
    print(f"Mode: {'full (git + tasks)' if 'tasks' in cfg else 'commits-only'}")
    print(f"Next: {next_hint}")


# ── ingest ───────────────────────────────────────────────────────────────────

def run_ingest(store_dir: str, phase: str | None = None, force: bool = False) -> str | None:
    """Commits (phase git), then task keys from commit messages and the tasks' details
    (phase tasks). Always incremental: run it again a month later and it adds the new
    commits and fetches only the tasks that have no details yet — the tracker is the slow
    part, and it is asked about each task once. An interrupted fetch resumes the same way.
    force: link every commit to a task key again, not only the new ones (after a change of
    commit_mask). <store>/ingest_state.yaml keeps what each run added. -> the db path."""
    cfg = load_project(store_dir)
    if cfg is None:
        return None

    db_path = cfg["project"]["db"]
    git_cfg = cfg.get("git", {})
    tasks_cfg = cfg.get("tasks")
    ingest_cfg = cfg.get("ingest", {})
    has_tasks = tasks_cfg is not None

    state_path = os.path.join(store_dir, "ingest_state.yaml")
    state = _load_state(state_path) or _fresh_state()

    def save_state(updated_tasks_state=None):
        if updated_tasks_state is not None:
            state["tasks"] = updated_tasks_state
        _save_state(state_path, state)

    from .db_manager import DatabaseManager
    from .git_connector import GitConnector

    db = DatabaseManager(db_path)
    db.create_tables(has_tasks=has_tasks)
    db.ensure_indexes()
    now = lambda: _dt.datetime.now().isoformat(timespec="seconds")

    run_git = phase is None or phase == "git"
    if run_git:
        # before any new row: an older mokosh wrote DIFF commit -> parent; new rows are parent -> commit
        turned = db.fix_diff_direction(GitConnector(git_cfg.get("repo", ".")).root_shas())
        if turned:
            print(f"\n      DIFF turned the right way (parent -> commit) in {turned} rows written by an older mokosh")
        print("\n[1/2] Extracting new commits from the Git repository...")
        g = GitConnector(git_cfg.get("repo", ".")).extract_commits(
            db, branch=git_cfg.get("branch", "main"), since=git_cfg.get("since"))
        state["git"] = {"status": "done", "completed_at": now(), "head": g["head"],
                        "last_run": {k: g[k] for k in ("commits_new", "rows_new", "skipped_known")}}
        save_state()
        print(f"      {g['commits_new']} new commits ({g['rows_new']} commit-file rows), "
              f"{g['skipped_known']} already in the database. Total rows: {db.commit_count()}")
        filled = GitConnector(git_cfg.get("repo", ".")).fill_change_types(db)
        if filled:
            print(f"      CHANGE_TYPE (A/M/D/R) filled for {filled} rows stored before it existed")
    else:
        print("[1/2] Git phase skipped (--phase tasks).")

    run_tasks = phase is None or phase == "tasks"
    if run_tasks and has_tasks:
        print("\n[2/2] Linking commits to task keys...")
        from .task_extractor import TaskExtractor
        from .task_fetcher import TaskFetcher

        from .task_extractor import project_keys
        extractor = TaskExtractor(tasks_cfg.get("commit_mask", "simple"), project_keys(tasks_cfg))
        if force:
            rows = db.get_commits_for_extraction(only_unlinked=False)
            pairs = [(extractor.extract_task_name(m.decode("utf-8", "ignore") if isinstance(m, bytes) else m), i)
                     for i, m in rows]
            db.update_task_names_batch([(n, i) for n, i in pairs if n])
            db.insert_tasks_batch(db.get_distinct_task_names())
            print(f"      --force: re-linked all {len(rows)} commit rows")
        x = extractor.process_all_commits(db)
        print(f"      {x['rows_linked']} of {x['rows_checked']} unlinked rows linked, {x['tasks_new']} new task keys")

        print("      Fetching details of the tasks that have none yet...")
        tracker_cfg = {k: _resolve_env_token(v) if isinstance(v, str) else v for k, v in tasks_cfg.items()}
        fetcher = TaskFetcher(tracker_type=tasks_cfg["source"], tracker_config=tracker_cfg)
        tasks_state = state.get("tasks") or {"fetched": 0, "last_key": None}
        f = fetcher.fetch_all_tasks(
            db,
            rate_limit_delay=float(ingest_cfg.get("rate_limit_delay", 1.0)),
            batch_size=int(ingest_cfg.get("batch_size", 100)),
            state=tasks_state,
            state_saver=lambda s: save_state(s),
        ) or {}
        state["tasks"] = {**tasks_state, "status": "stopped" if f.get("stopped") else "done", "completed_at": now(),
                          "last_run": {"tasks_new": x["tasks_new"], **f}}
        save_state()
        print(f"      {f.get('fetched', 0)} fetched, {f.get('failed', 0)} failed, "
              f"{f.get('empty', 0)} empty answers (all tried again next time)")
        if f.get("stopped"):
            src = tasks_cfg["source"]
            where = {"github": "github_owner / github_repo (the <USER> / <REPO> of https://github.com/<USER>/<REPO>)",
                     "gitlab": "gitlab_url / gitlab_project (org/repo)", "jira": "jira_url",
                     "youtrack": "youtrack_url"}.get(src, "the tracker settings")
            print(f"      STOPPED: the tracker answered nothing for the first {f['empty']} tasks. "
                  f"Check {where} and the token in {os.path.join(store_dir, 'project.yaml')}, "
                  f"then run `mokosh ingest --phase tasks` again.")
    elif run_tasks and not has_tasks:
        print("[2/2] No task tracker configured — commits-only mode.")
    else:
        print("[2/2] Tasks phase skipped (--phase git).")
    return db_path


# ── plaintext / export / status ──────────────────────────────────────────────

def run_plaintext(db_path: str, dry_run: bool = False) -> bool:
    from .db_manager import make_plain
    if not os.path.isfile(db_path):
        print(f"no such database: {db_path}")
        return False
    make_plain(db_path, dry_run=dry_run)
    return True


def run_export(db_path, out_dir, mode="all", level="desc", diff_mode="changed"):
    from .exporter import export_units
    result = export_units(db_path, out_dir, mode=mode, level=level, diff_mode=diff_mode)
    if mode == "join":
        print(f"Done. Joined: {result['joined_exported']}  "
              f"Commits included: {result['commits_included']}  "
              f"Commits orphaned (no TASK_NAME): {result['commits_orphaned']}  -> {out_dir}")
    else:
        print(f"Done. Tasks exported: {result['tasks_exported']}  "
              f"Commits exported: {result['commits_exported']}  -> {out_dir}")
    return result


def status(db_path: str) -> dict:
    """What a dataset holds: tasks, commits, (commit, file) rows, task-linked commits, files."""
    from . import schema
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    task_t, commit_t = schema.tables(con)
    have = schema.has_task_table(con)
    out = {"db": db_path,
           "tasks": con.execute(f"SELECT COUNT(*) FROM {task_t}").fetchone()[0] if have else 0,
           "rows": con.execute(f"SELECT COUNT(*) FROM {commit_t}").fetchone()[0],
           "commits": con.execute(f"SELECT COUNT(DISTINCT SHA) FROM {commit_t}").fetchone()[0],
           "linked_commits": con.execute(f"SELECT COUNT(DISTINCT SHA) FROM {commit_t} "
                                         "WHERE TASK_NAME IS NOT NULL AND TASK_NAME != ''").fetchone()[0],
           "files": con.execute(f"SELECT COUNT(DISTINCT PATH) FROM {commit_t}").fetchone()[0],
           "first": con.execute(f"SELECT MIN(CMT_DATE) FROM {commit_t}").fetchone()[0],
           "last": con.execute(f"SELECT MAX(CMT_DATE) FROM {commit_t}").fetchone()[0]}
    con.close()
    return out


def _next_steps(db_path):
    print("\nNext:")
    print(f"  simargl index units {db_path}                 # semantic search over the tasks")
    print(f"  gamayun index --db {db_path} --repo . --project <name>   # statistical ontology")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="mokosh", description="Gather a task + commit dataset into SQLite")
    sub = p.add_subparsers(dest="cmd", required=True)
    ini = sub.add_parser("init", help="wizard: repository, branch, tracker -> .mokosh/project.yaml")
    ini.add_argument("--store-dir", default=STORE)
    ing = sub.add_parser("ingest", help="commits, then task references and details; run it again "
                                        "to add only what is new (incremental, resumable)")
    ing.add_argument("--phase", choices=["git", "tasks"], default=None, help="run only one phase (default: both)")
    ing.add_argument("--force", action="store_true",
                     help="link every commit to a task key again, not only new ones (after changing commit_mask)")
    ing.add_argument("--store-dir", default=None, help="default: .mokosh, or an earlier .simargl")
    pt = sub.add_parser("plaintext", help="rewrite TASK title/description/comments as plain text")
    pt.add_argument("db_path", nargs="?", default="units.db")
    pt.add_argument("--dry-run", action="store_true", help="only count what would change")
    exp = sub.add_parser("export", help="TASK/COMMIT rows as flat text files (for a glossary or a folder index)")
    exp.add_argument("db_path")
    exp.add_argument("--out", required=True, help="output directory")
    mode = exp.add_mutually_exclusive_group()
    mode.add_argument("--task", action="store_true", help="tasks only")
    mode.add_argument("--commits", action="store_true", help="commits only")
    mode.add_argument("--all", action="store_true", help="tasks/ and commits/ separately (default)")
    mode.add_argument("--join", action="store_true", help="one file per task with its commits (joined/)")
    exp.add_argument("--level", default="desc", choices=["title", "desc", "comments"])
    exp.add_argument("--diff-mode", default="changed", choices=["changed", "full"])
    fx = sub.add_parser("fixdiff", help="turn DIFF the right way (parent -> commit) in a database an older "
                                        "mokosh wrote; `ingest` does it on its own")
    fx.add_argument("db_path")
    fx.add_argument("--repo", required=True, help="the git repository (its root commits are left as they are)")
    ui = sub.add_parser("ui", help="web UI (Flask): the Setup form, ingest with its log, status")
    ui.add_argument("--port", type=int, default=8093)
    ui.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to open it to the network")
    ui.add_argument("--root", default=None, help="the folder to look for projects under (default: here)")
    st = sub.add_parser("status", help="what a dataset holds")
    st.add_argument("db_path", nargs="?", default=None, help="default: the db of the project in this folder")
    st.add_argument("--store-dir", default=None)
    a = p.parse_args(argv)

    if a.cmd == "init":
        run_init(a.store_dir)
    elif a.cmd == "ingest":
        db_path = run_ingest(resolve_store(a.store_dir), a.phase, a.force)
        if db_path is None:
            sys.exit(1)
        print("\nIngest complete.")
        _next_steps(db_path)
    elif a.cmd == "ui":
        from .ui.server import serve
        serve(host=a.host, port=a.port, start=a.root)
    elif a.cmd == "fixdiff":
        from .db_manager import DatabaseManager
        from .git_connector import GitConnector
        db = DatabaseManager(a.db_path)
        roots = GitConnector(a.repo).root_shas()
        before = db.diff_direction(roots)
        if not roots:
            print(f"no git history at {a.repo}")
            sys.exit(1)
        n = db.fix_diff_direction(roots)
        print(f"{a.db_path}: first hunks of new files '@@ -0,0 +' {before['new_file_hunks']}, "
              f"'+0,0 @@' {before['deleted_file_hunks']}, marker {before['marker']}")
        print(f"  {n} rows turned" if n else "  already the right way — marked, nothing changed")
    elif a.cmd == "plaintext":
        if not run_plaintext(a.db_path, a.dry_run):
            sys.exit(1)
    elif a.cmd == "export":
        m = "task" if a.task else "commits" if a.commits else "join" if a.join else "all"
        run_export(a.db_path, a.out, mode=m, level=a.level, diff_mode=a.diff_mode)
    elif a.cmd == "status":
        db_path = a.db_path
        if not db_path:
            cfg = load_project(resolve_store(a.store_dir))
            if cfg is None:
                sys.exit(1)
            db_path = cfg["project"]["db"]
        if not os.path.isfile(db_path):
            print(f"no such database: {db_path}")
            sys.exit(1)
        s = status(db_path)
        print(f"{s['db']}\n  tasks {s['tasks']}  commits {s['commits']} ({s['linked_commits']} linked to a task)"
              f"  (commit, file) rows {s['rows']}  files {s['files']}\n  from {s['first']} to {s['last']}")


if __name__ == "__main__":
    main()
