"""Table names of a task/commit SQLite database.

The canonical names are singular: TASK and COMMIT. Older simargl ingest
databases use TASKS / COMMITS, and codeXtract datasets (sonar.db, flink.db)
mix them (TASK + COMMITS), so readers resolve whatever the file actually has.
New databases are created with the singular names.

COMMIT is an SQL keyword, so the names are returned already quoted —
use them as-is in f-strings: f"SELECT PATH FROM {commit_t}".
"""
import sqlite3

TASK_NAMES = ("TASK", "TASKS")        # preferred first
COMMIT_NAMES = ("COMMIT", "COMMITS")


def _existing(conn: sqlite3.Connection) -> dict:
    """upper-case name -> name as stored, for every table and view."""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
    return {r[0].upper(): r[0] for r in rows}


def _pick(have: dict, candidates) -> str:
    for c in candidates:
        if c in have:
            return f'"{have[c]}"'
    return f'"{candidates[0]}"'          # missing: the canonical (singular) name


def task_table(conn: sqlite3.Connection) -> str:
    return _pick(_existing(conn), TASK_NAMES)


def commit_table(conn: sqlite3.Connection) -> str:
    return _pick(_existing(conn), COMMIT_NAMES)


def tables(conn: sqlite3.Connection) -> tuple[str, str]:
    """-> (task table, commit table), quoted."""
    have = _existing(conn)
    return _pick(have, TASK_NAMES), _pick(have, COMMIT_NAMES)


def has_task_table(conn: sqlite3.Connection) -> bool:
    return any(n in _existing(conn) for n in TASK_NAMES)
