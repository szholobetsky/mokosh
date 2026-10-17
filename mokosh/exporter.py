"""exporter — dump TASK/COMMIT rows from a mokosh ingest SQLite db to flat
text files, one file per unit, for `/flow glossary index <out_dir>` to consume.

glossary.py (1bcoder/vyrii) stays schema-agnostic by design (see
concepts/GLOSSARY.md — self-contained, portable /flow files) — it only ever
reads folders of text files. This module is where TASK/COMMIT schema
knowledge lives, reusing the same tables and query patterns as index_units()
in indexer.py, so the schema is understood in exactly one place.

Tasks and commits are exported into two separate subfolders (out_dir/tasks,
out_dir/commits), not merged into one document per task — a docs-glossary,
a tasks-glossary, and a diffs-glossary carry knowledge at different levels
and are meant to be /flow glossary index'd as separate --project namespaces,
then cross-referenced later via their [file: ...] source tags, not
pre-combined.
"""
from __future__ import annotations

import os
import re
import sqlite3

from . import schema

# Cumulative task detail levels — same names as exp3's source variants
# (title / desc / comments) so vocabulary stays consistent across the
# research codebase and this production export path.
_TASK_LEVELS = {
    "title":    ["TITLE"],
    "desc":     ["TITLE", "DESCRIPTION"],
    "comments": ["TITLE", "DESCRIPTION", "COMMENTS"],
}

# Unified-diff line prefixes that mark an actual change: '+'/'-' added/removed
# lines, '@' hunk headers (@@ ... @@). Plain context lines start with a space
# and are dropped in "changed" mode — they were never touched, so keeping
# them only pads the glossary chunk with lines the LLM will "discover" facts
# about even though nothing happened there.
_DIFF_LINE_RE = re.compile(r'^[+@-]')


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def _safe_filename(name: str) -> str:
    safe = re.sub(r'[^\w.\-]', '_', (name or "").strip())
    return safe or "unnamed"


def _filter_diff_lines(diff_text: str, diff_mode: str) -> str:
    if diff_mode == "full" or not diff_text:
        return diff_text
    kept = [l for l in diff_text.splitlines() if _DIFF_LINE_RE.match(l.strip())]
    return "\n".join(kept)


def _format_commit_sections(sha_rows: list, diff_mode: str, include_task: bool = True) -> list:
    """Build the MESSAGE:/TASK:/FILE: section strings for one commit (all
    (sha, path) rows sharing a SHA). Returns [] if there is nothing worth
    writing (no message and every file's diff was filtered to empty).
    include_task=False when embedding under a task's own file in --join mode
    — repeating "TASK: X" inside that same task's document is redundant."""
    first = sha_rows[0]
    message = _as_text(first["MESSAGE"]).strip()
    task_name = _as_text(first["TASK_NAME"]).strip()

    sections = [f"MESSAGE:\n{message}\n"] if message else []
    if include_task and task_name:
        sections.append(f"TASK: {task_name}\n")

    file_sections = []
    for row in sha_rows:
        diff = _filter_diff_lines(_as_text(row["DIFF"]), diff_mode)
        if not diff.strip():
            continue
        path = _as_text(row["PATH"]).strip() or "(unknown path)"
        file_sections.append(f"FILE: {path}\n{diff}\n")

    if not file_sections and not message:
        return []
    return sections + file_sections


def _export_tasks(conn: sqlite3.Connection, out_dir: str, level: str) -> int:
    fields = _TASK_LEVELS[level]
    os.makedirs(out_dir, exist_ok=True)
    rows = conn.execute(f"SELECT NAME, {', '.join(fields)} FROM {schema.task_table(conn)}").fetchall()
    count = 0
    for row in rows:
        name = row["NAME"]
        if not name:
            continue
        sections = []
        for f in fields:
            val = _as_text(row[f]).strip()
            if val:
                sections.append(f"{f}:\n{val}\n")
        if not sections:
            continue
        with open(os.path.join(out_dir, _safe_filename(name) + ".txt"),
                   "w", encoding="utf-8") as f:
            f.write("\n".join(sections))
        count += 1
    return count


def _export_commits(conn: sqlite3.Connection, out_dir: str, diff_mode: str) -> int:
    os.makedirs(out_dir, exist_ok=True)
    rows = conn.execute(
        f"SELECT SHA, MESSAGE, PATH, DIFF, TASK_NAME FROM {schema.commit_table(conn)} WHERE SHA IS NOT NULL"
    ).fetchall()

    by_sha: dict = {}
    for row in rows:
        by_sha.setdefault(row["SHA"], []).append(row)

    count = 0
    for sha, sha_rows in by_sha.items():
        sections = _format_commit_sections(sha_rows, diff_mode, include_task=True)
        if not sections:
            continue
        task_name = _as_text(sha_rows[0]["TASK_NAME"]).strip()
        # Named by TASK_NAME (+ short sha to disambiguate multiple commits under
        # the same task) so the filename itself is the join key back to a task,
        # same as _export_tasks/_export_joined. Commits with no TASK_NAME fall
        # back to sha-only naming — this is exactly the "orphaned" case
        # _export_joined reports, so it stays visually distinguishable here too.
        stem = f"{task_name}_{sha[:12]}" if task_name else sha[:12]
        with open(os.path.join(out_dir, _safe_filename(stem) + ".txt"),
                   "w", encoding="utf-8") as f:
            f.write("\n".join(sections))
        count += 1
    return count


def _export_joined(conn: sqlite3.Connection, out_dir: str, level: str, diff_mode: str) -> dict:
    """One file per task (out_dir/<task>.txt), combining the task's own
    TITLE/DESCRIPTION/COMMENTS with every commit linked to it via TASK_NAME.
    Commits with no TASK_NAME can't be joined to any task and are counted as
    orphaned/skipped rather than silently guessed at."""
    fields = _TASK_LEVELS[level]
    os.makedirs(out_dir, exist_ok=True)

    commit_rows = conn.execute(
        f"SELECT SHA, MESSAGE, PATH, DIFF, TASK_NAME FROM {schema.commit_table(conn)} WHERE SHA IS NOT NULL"
    ).fetchall()
    by_task: dict = {}
    orphaned_shas: set = set()
    for row in commit_rows:
        task_name = _as_text(row["TASK_NAME"]).strip()
        if task_name:
            by_task.setdefault(task_name, {}).setdefault(row["SHA"], []).append(row)
        else:
            orphaned_shas.add(row["SHA"])

    tasks = conn.execute(f"SELECT NAME, {', '.join(fields)} FROM {schema.task_table(conn)}").fetchall()
    joined, commits_included = 0, 0
    for task in tasks:
        name = task["NAME"]
        if not name:
            continue
        sections = []
        for f in fields:
            val = _as_text(task[f]).strip()
            if val:
                sections.append(f"{f}:\n{val}\n")
        if not sections:
            continue

        for sha, sha_rows in by_task.get(name, {}).items():
            commit_sections = _format_commit_sections(sha_rows, diff_mode, include_task=False)
            if commit_sections:
                sections.append(f"--- commit {sha[:12]} ---\n")
                sections.extend(commit_sections)
                commits_included += 1

        with open(os.path.join(out_dir, _safe_filename(name) + ".txt"),
                   "w", encoding="utf-8") as f:
            f.write("\n".join(sections))
        joined += 1

    return {
        "joined_exported": joined,
        "commits_included": commits_included,
        "commits_orphaned": len(orphaned_shas),
    }


def export_units(
    db_path: str,
    out_dir: str,
    mode: str = "all",
    level: str = "desc",
    diff_mode: str = "changed",
) -> dict:
    """Export TASK and/or COMMIT rows from a mokosh ingest SQLite db as
    flat text files, for `/flow glossary index <out_dir>/...` to consume.

    mode      "all" (default) | "join" | "task" | "commits":
              - "all"     out_dir/tasks/ + out_dir/commits/ — both, as two
                          separate corpora, not merged — a tasks-glossary and
                          a diffs-glossary carry knowledge at a different
                          level, meant to be indexed as separate --project
                          namespaces and cross-referenced later. This is what
                          /flow history consumes.
              - "join"    out_dir/joined/<task>.txt — one file per task,
                          combining its TITLE/DESCRIPTION/COMMENTS with every
                          commit linked to it via TASK_NAME (each commit as
                          its own block). Commits with no TASK_NAME can't be
                          attached to any task and are reported as
                          "commits_orphaned" rather than silently dropped.
              - "task"    out_dir/tasks/<task>.txt — tasks only.
              - "commits" out_dir/commits/<sha>.txt — commits only.
    level     task detail level, cumulative: "title" | "desc"
              (title+description) | "comments" (+comments). Default "desc"
              matches the exp3 finding that title+description outperforms
              both title-only and the noisier +comments variant.
    diff_mode "changed" (default) keeps only @/+/- diff lines — dropping
              untouched context lines before the text ever reaches glossary
              indexing is the first line of defense against fact-explosion;
              --unique at index time is the second. "full" keeps the diff
              exactly as stored.

    Returns {"tasks_exported": int, "commits_exported": int} for
    task/commits/all, or
    {"joined_exported": int, "commits_included": int, "commits_orphaned": int}
    for "join".
    """
    if mode not in ("task", "commits", "all", "join"):
        raise ValueError(f"unknown mode: {mode!r} (expected task/commits/all/join)")
    if level not in _TASK_LEVELS:
        raise ValueError(f"unknown level: {level!r} (expected title/desc/comments)")
    if diff_mode not in ("changed", "full"):
        raise ValueError(f"unknown diff_mode: {diff_mode!r} (expected changed/full)")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if mode == "join":
            return _export_joined(conn, os.path.join(out_dir, "joined"), level, diff_mode)

        result = {"tasks_exported": 0, "commits_exported": 0}
        if mode in ("task", "all"):
            result["tasks_exported"] = _export_tasks(
                conn, os.path.join(out_dir, "tasks"), level)
        if mode in ("commits", "all"):
            result["commits_exported"] = _export_commits(
                conn, os.path.join(out_dir, "commits"), diff_mode)
        return result
    finally:
        conn.close()
