"""Database manager for mokosh ingest.

Tables (singular names, see schema.py):
  COMMIT — one row per (sha, file) pair extracted from git; CHANGE_TYPE is what the commit did
           to the file: A added, M modified, D deleted, R renamed (PATH is the new name)
  TASK   — one row per unique task key, with title/description/comments

A database that already has the older plural tables (COMMITS / TASKS) keeps
using them — names are resolved from the file on every connection.
"""

import re
import sqlite3
import logging
from typing import List, Tuple, Optional

from . import schema
from .text import needs_cleaning, plain_text


META = "MOKOSH_META"
_HUNK = re.compile(r"^@@ -(\S+) \+(\S+) @@")


def invert_patch(text: str) -> str:
    """A unified diff the other way round: '+' <-> '-', and the two ranges of every hunk header."""
    out = []
    for line in text.split("\n"):
        m = _HUNK.match(line)
        if m:
            line = f"@@ -{m.group(2)} +{m.group(1)} @@" + line[m.end():]
        elif line.startswith("+"):
            line = "-" + line[1:]
        elif line.startswith("-"):
            line = "+" + line[1:]
        out.append(line)
    return "\n".join(out)


class DatabaseManager:
    """Manages SQLite database operations for commit and task data."""

    def __init__(self, db_file: str):
        self.db_file = db_file
        self.logger = logging.getLogger(__name__)

    def create_tables(self, has_tasks: bool = True):
        """Create the COMMIT (and optionally TASK) table if missing; an existing
        plural table (COMMITS / TASKS) is reused, not duplicated."""
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {commit_t} (
                    ID          INTEGER PRIMARY KEY,
                    SHA         TEXT,
                    AUTHOR_NAME TEXT,
                    AUTHOR_EMAIL TEXT,
                    CMT_DATE    TEXT,
                    MESSAGE     BLOB,
                    PATH        BLOB,
                    DIFF        BLOB,
                    TASK_NAME   TEXT,
                    CHANGE_TYPE TEXT
                )
            """)
            # a table made before CHANGE_TYPE gets the column; its rows are filled by fill_change_types
            if "CHANGE_TYPE" not in {r[1] for r in conn.execute(f"PRAGMA table_info({commit_t})")}:
                conn.execute(f"ALTER TABLE {commit_t} ADD COLUMN CHANGE_TYPE TEXT")
            conn.execute(f"CREATE TABLE IF NOT EXISTS {META} (KEY TEXT PRIMARY KEY, VALUE TEXT)")
            if not conn.execute(f"SELECT 1 FROM {commit_t} LIMIT 1").fetchone():
                # an empty table: every DIFF it gets is written parent -> commit
                conn.execute(f"INSERT OR IGNORE INTO {META} VALUES ('diff', 'parent->commit')")
            if has_tasks:
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS {task_t} (
                        ID          INTEGER PRIMARY KEY AUTOINCREMENT,
                        NAME        TEXT UNIQUE,
                        TITLE       TEXT,
                        DESCRIPTION TEXT,
                        COMMENTS    TEXT
                    )
                """)
                conn.execute(f"CREATE INDEX IF NOT EXISTS TASK_NAME_INDX ON {task_t} (NAME ASC)")
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ COMMIT

    def insert_commit_data(self, commit_id: int, sha: str, author_name: str,
                           author_email: str, date: str, message: str,
                           path: str, diff: str, change_type: Optional[str] = None):
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            conn.execute(
                f"INSERT OR IGNORE INTO {commit_t}"
                "(ID, SHA, AUTHOR_NAME, AUTHOR_EMAIL, CMT_DATE, MESSAGE, PATH, DIFF, CHANGE_TYPE) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (commit_id, sha, author_name, author_email, date, message, path, diff, change_type),
            )
            conn.commit()
        finally:
            conn.close()

    def insert_commit_data_batch(self, data_list: List[Tuple]):
        """Rows (ID, SHA, AUTHOR_NAME, AUTHOR_EMAIL, CMT_DATE, MESSAGE, PATH, DIFF, CHANGE_TYPE)."""
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            batch_size = 200
            for i in range(0, len(data_list), batch_size):
                conn.executemany(
                    f"INSERT OR IGNORE INTO {commit_t}"
                    "(ID, SHA, AUTHOR_NAME, AUTHOR_EMAIL, CMT_DATE, MESSAGE, PATH, DIFF, CHANGE_TYPE) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    data_list[i:i + batch_size],
                )
                conn.commit()
        finally:
            conn.close()

    def rows_without_change_type(self) -> int:
        conn = sqlite3.connect(self.db_file)
        try:
            _, commit_t = schema.tables(conn)
            return conn.execute(f"SELECT COUNT(*) FROM {commit_t} WHERE CHANGE_TYPE IS NULL").fetchone()[0]
        finally:
            conn.close()

    def set_change_types(self, rows: List[Tuple]) -> int:
        """rows: (change_type, sha, path) — fills only rows that have none; -> rows updated."""
        conn = sqlite3.connect(self.db_file)
        try:
            _, commit_t = schema.tables(conn)
            before = conn.total_changes
            conn.executemany(f"UPDATE {commit_t} SET CHANGE_TYPE = ? WHERE SHA = ? AND PATH = ? "
                             "AND CHANGE_TYPE IS NULL", rows)
            conn.commit()
            return conn.total_changes - before
        finally:
            conn.close()

    def diff_direction(self, root_shas: set = frozenset()) -> dict:
        """How the DIFF column reads: the marker, and the first hunk header of every row outside the
        root commits — a file a commit creates starts '@@ -0,0 +' when the diff is the right way round,
        '... +0,0 @@' when not (a deleted file gives the opposite; commits add far more files than they
        delete). Root commits are left out: their diff against the empty tree is right either way."""
        conn = sqlite3.connect(self.db_file)
        try:
            _, commit_t = schema.tables(conn)
            has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (META,)).fetchone()
            mark = conn.execute(f"SELECT VALUE FROM {META} WHERE KEY = 'diff'").fetchone() if has_meta else None
            roots = sorted(root_shas)
            skip = f" AND SHA NOT IN ({','.join('?' * len(roots))})" if roots else ""
            fwd = conn.execute(f"SELECT COUNT(*) FROM {commit_t} WHERE CAST(DIFF AS TEXT) LIKE '@@ -0,0 +%'{skip}",
                               roots).fetchone()[0]
            back = conn.execute(f"SELECT COUNT(*) FROM {commit_t} WHERE CAST(DIFF AS TEXT) LIKE '@@ -% +0,0 @@%'{skip}",
                                roots).fetchone()[0]
            return {"marker": mark[0] if mark else None, "new_file_hunks": fwd, "deleted_file_hunks": back}
        finally:
            conn.close()

    def fix_diff_direction(self, root_shas: set) -> int:
        """A database written by mokosh before 2026-10-10 has DIFF commit -> parent: turn every row the
        right way, except the root commits' (their diff against the empty tree was already right).
        Marked in MOKOSH_META, so it runs once; -> rows turned (0 when already right)."""
        d = self.diff_direction(root_shas)
        if d["marker"] == "parent->commit":
            return 0
        conn = sqlite3.connect(self.db_file)
        try:
            _, commit_t = schema.tables(conn)
            conn.execute(f"CREATE TABLE IF NOT EXISTS {META} (KEY TEXT PRIMARY KEY, VALUE TEXT)")
            n = 0
            if d["deleted_file_hunks"] > d["new_file_hunks"]:          # written the other way round
                rows = []
                for i, sha, diff in conn.execute(f"SELECT ID, SHA, DIFF FROM {commit_t} WHERE DIFF IS NOT NULL"):
                    if sha in root_shas or not diff:
                        continue
                    text = diff.decode("utf-8", "ignore") if isinstance(diff, bytes) else str(diff)
                    rows.append((invert_patch(text), i))
                    if len(rows) >= 2000:
                        conn.executemany(f"UPDATE {commit_t} SET DIFF = ? WHERE ID = ?", rows)
                        n += len(rows)
                        rows = []
                conn.executemany(f"UPDATE {commit_t} SET DIFF = ? WHERE ID = ?", rows)
                n += len(rows)
            conn.execute(f"INSERT OR REPLACE INTO {META} VALUES ('diff', 'parent->commit')")
            conn.commit()
            return n
        finally:
            conn.close()

    def ensure_indexes(self):
        """SHA and TASK_NAME lookups of an incremental ingest; harmless on an existing db."""
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            conn.execute(f"CREATE INDEX IF NOT EXISTS COMMIT_SHA_INDX ON {commit_t} (SHA)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS COMMIT_TASK_INDX ON {commit_t} (TASK_NAME)")
            conn.commit()
        finally:
            conn.close()

    def known_shas(self) -> set:
        """Commits already in the database: an incremental ingest skips them."""
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            return {r[0] for r in conn.execute(f"SELECT DISTINCT SHA FROM {commit_t}")}
        finally:
            conn.close()

    def max_commit_id(self) -> int:
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            return conn.execute(f"SELECT COALESCE(MAX(ID), 0) FROM {commit_t}").fetchone()[0]
        finally:
            conn.close()

    def commit_count(self) -> int:
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            return conn.execute(f"SELECT COUNT(*) FROM {commit_t}").fetchone()[0]
        finally:
            conn.close()

    def update_task_name_in_commit(self, commit_id: int, task_name: str):
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            conn.execute(
                f"UPDATE {commit_t} SET TASK_NAME = ? WHERE ID = ?",
                (task_name, commit_id),
            )
            conn.commit()
        finally:
            conn.close()

    def update_task_name_by_sha(self, sha: str, task_name: str):
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            conn.execute(
                f"UPDATE {commit_t} SET TASK_NAME = ? WHERE SHA = ? AND TASK_NAME IS NULL",
                (task_name, sha),
            )
            conn.commit()
        finally:
            conn.close()

    def get_commits_for_extraction(self, only_unlinked: bool = True) -> List[Tuple]:
        """(id, message) rows for task-name extraction — by default only the rows that have
        no task yet, so a monthly ingest looks at the new commits, not the whole history."""
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            where = " WHERE TASK_NAME IS NULL" if only_unlinked else ""
            return conn.execute(f"SELECT ID, MESSAGE FROM {commit_t}{where}").fetchall()
        finally:
            conn.close()

    def update_task_names_batch(self, pairs: List[Tuple[str, int]]):
        """[(task_name, commit_id), ...] in one transaction."""
        if not pairs:
            return
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            conn.executemany(f"UPDATE {commit_t} SET TASK_NAME = ? WHERE ID = ?", pairs)
            conn.commit()
        finally:
            conn.close()

    def insert_tasks_batch(self, names: List[str]) -> int:
        """Register task keys (no details yet); -> how many were new."""
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            before = conn.execute(f"SELECT COUNT(*) FROM {task_t}").fetchone()[0]
            conn.executemany(f"INSERT OR IGNORE INTO {task_t} (NAME) VALUES (?)", [(n,) for n in names])
            conn.commit()
            return conn.execute(f"SELECT COUNT(*) FROM {task_t}").fetchone()[0] - before
        finally:
            conn.close()

    def get_distinct_task_names(self) -> List[str]:
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            rows = conn.execute(
                f"SELECT DISTINCT TASK_NAME FROM {commit_t} "
                "WHERE TASK_NAME IS NOT NULL ORDER BY TASK_NAME"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------ TASKS

    def insert_task(self, task_name: str):
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            conn.execute(f"INSERT OR IGNORE INTO {task_t} (NAME) VALUES (?)", (task_name,))
            conn.commit()
        finally:
            conn.close()

    def get_tasks_without_details(self) -> List[str]:
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            rows = conn.execute(
                # '' too: an older mokosh wrote an empty answer of the tracker as an empty title
                f"SELECT NAME FROM {task_t} WHERE (TITLE IS NULL OR TITLE = '') AND NAME IS NOT NULL"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def update_task_details(self, task_name: str, title: str,
                            description: str, comments: str):
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            conn.execute(
                f"UPDATE {task_t} SET TITLE=?, DESCRIPTION=?, COMMENTS=? WHERE NAME=?",
                (plain_text(title), plain_text(description), plain_text(comments), task_name),
            )
            conn.commit()
        finally:
            conn.close()

    def upsert_task_with_details(self, name: str, title: str,
                                  description: str, comments: str):
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            conn.execute(
                f"INSERT INTO {task_t} (NAME, TITLE, DESCRIPTION, COMMENTS) VALUES (?,?,?,?) "
                "ON CONFLICT(NAME) DO UPDATE SET "
                "TITLE=excluded.TITLE, DESCRIPTION=excluded.DESCRIPTION, COMMENTS=excluded.COMMENTS",
                (name, plain_text(title), plain_text(description), plain_text(comments)),
            )
            conn.commit()
        finally:
            conn.close()

    def bulk_upsert_tasks(self, tasks: List[Tuple]):
        conn = sqlite3.connect(self.db_file)
        try:
            task_t, commit_t = schema.tables(conn)
            batch_size = 200
            for i in range(0, len(tasks), batch_size):
                conn.executemany(
                    f"INSERT INTO {task_t} (NAME, TITLE, DESCRIPTION, COMMENTS) VALUES (?,?,?,?) "
                    "ON CONFLICT(NAME) DO UPDATE SET "
                    "TITLE=excluded.TITLE, DESCRIPTION=excluded.DESCRIPTION, COMMENTS=excluded.COMMENTS",
                    [(n, plain_text(t), plain_text(d), plain_text(c))
                     for n, t, d, c in tasks[i:i + batch_size]],
                )
                conn.commit()
        finally:
            conn.close()


def make_plain(db_file: str, dry_run: bool = False, log=print) -> dict:
    """Rewrite TITLE / DESCRIPTION / COMMENTS of an existing database as plain
    text (ADF trees and 'None' / '[]' left by older ingests -> text / empty).
    Rows that are already text stay untouched. -> {"tasks", "changed": {column: n}}"""
    conn = sqlite3.connect(db_file)
    try:
        cols = ("TITLE", "DESCRIPTION", "COMMENTS")
        changed = {c: 0 for c in cols}
        if not schema.has_task_table(conn):                 # a commits-only ingest: no tracker text
            log("no TASK table (commits-only database): nothing to clean")
            return {"tasks": 0, "changed": changed, "rows": 0}
        task_t, _ = schema.tables(conn)
        updates = []
        n = 0
        for row in conn.execute(f"SELECT rowid, {', '.join(cols)} FROM {task_t}"):
            n += 1
            new = {}
            for c, v in zip(cols, row[1:]):
                if needs_cleaning(v):
                    new[c] = plain_text(v)
                    changed[c] += 1
            if new:
                updates.append((row[0], new))
        log(f"{n} tasks; to clean: " + ", ".join(f"{c} {k}" for c, k in changed.items()))
        if not dry_run:
            for rowid, new in updates:
                conn.execute(f"UPDATE {task_t} SET {', '.join(f'{c}=?' for c in new)} WHERE rowid=?",
                             (*new.values(), rowid))
            conn.commit()
            log(f"{len(updates)} tasks rewritten")
        return {"tasks": n, "changed": changed, "rows": len(updates)}
    finally:
        conn.close()
