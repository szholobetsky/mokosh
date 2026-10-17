"""CHANGE_TYPE: what a commit did to each file (A/M/D/R); DIFF taken parent -> commit (a line the
commit added starts with '+'); a database written the other way round by an older mokosh is turned
once; rows stored before CHANGE_TYPE existed are filled from git."""
import sqlite3
import subprocess

from mokosh.db_manager import DatabaseManager, invert_patch
from mokosh.git_connector import GitConnector


def _repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
    git("init", "-q")
    for name, text in (("keep.txt", "a\n"), ("old.txt", "x\n"), ("ren.txt", "r\nr\nr\n")):
        (r / name).write_text(text)
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "first")
    (r / "keep.txt").write_text("a\nb\n")
    (r / "new.txt").write_text("n\n")
    (r / "new2.txt").write_text("m\n")                    # commits add more files than they delete
    git("rm", "-q", "old.txt")
    git("mv", "ren.txt", "moved.txt")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "second")
    return r


def _rows(db, col):
    con = sqlite3.connect(db.db_file)
    out = {}
    for msg, path, v in con.execute(f'SELECT MESSAGE, PATH, {col} FROM "COMMIT"'):
        out.setdefault((msg.decode() if isinstance(msg, bytes) else msg).strip(), {})[path] = v
    con.close()
    return out


def _extracted(tmp_path):
    repo = _repo(tmp_path)
    db = DatabaseManager(str(tmp_path / "units.db"))
    db.create_tables(has_tasks=False)
    g = GitConnector(str(repo))
    g.extract_commits(db, branch="HEAD")
    return db, g


def test_change_types_of_a_commit(tmp_path):
    db, _ = _extracted(tmp_path)
    t = _rows(db, "CHANGE_TYPE")
    assert t["first"] == {"keep.txt": "A", "old.txt": "A", "ren.txt": "A"}            # the root commit
    assert t["second"] == {"keep.txt": "M", "new.txt": "A", "new2.txt": "A", "old.txt": "D",
                           "moved.txt": "R"}


def test_diff_reads_parent_to_commit(tmp_path):
    db, _ = _extracted(tmp_path)
    d = _rows(db, "DIFF")
    assert d["second"]["keep.txt"].splitlines() == ["@@ -1 +1,2 @@", " a", "+b"]
    assert d["second"]["new.txt"].startswith("@@ -0,0 +1 @@\n+n")
    assert d["second"]["old.txt"].startswith("@@ -1 +0,0 @@\n-x")
    assert d["first"]["keep.txt"].startswith("@@ -0,0 +1 @@\n+a")                   # root: against the empty tree
    assert db.diff_direction()["marker"] == "parent->commit"


def test_an_older_database_is_turned_once(tmp_path):
    db, g = _extracted(tmp_path)
    want = _rows(db, "DIFF")
    roots = g.root_shas()
    con = sqlite3.connect(db.db_file)                    # as an older mokosh wrote it: no marker,
    con.execute("DROP TABLE MOKOSH_META")                # every non-root commit commit -> parent
    for i, sha, diff in con.execute('SELECT ID, SHA, DIFF FROM "COMMIT"').fetchall():
        if sha not in roots and diff:
            con.execute('UPDATE "COMMIT" SET DIFF = ? WHERE ID = ?', (invert_patch(diff), i))
    con.commit()
    con.close()
    assert db.fix_diff_direction(roots) == 4             # keep, new, new2, old (a rename has no patch)
    assert _rows(db, "DIFF") == want
    assert db.fix_diff_direction(roots) == 0             # marked: never twice


def test_invert_patch_is_its_own_inverse():
    p = "@@ -3,2 +3,3 @@ def f():\n x\n-y\n+z\n+w\n\\ No newline at end of file"
    assert invert_patch(p) == "@@ -3,3 +3,2 @@ def f():\n x\n+y\n-z\n-w\n\\ No newline at end of file"
    assert invert_patch(invert_patch(p)) == p


def test_rows_stored_before_are_filled_from_git(tmp_path):
    db, g = _extracted(tmp_path)
    want = _rows(db, "CHANGE_TYPE")
    con = sqlite3.connect(db.db_file)                    # as a database made before the column existed
    con.execute('ALTER TABLE "COMMIT" DROP COLUMN CHANGE_TYPE')
    con.commit()
    con.close()
    db.create_tables(has_tasks=False)                    # adds the column back, empty
    assert db.rows_without_change_type() == 8
    assert g.fill_change_types(db) == 8
    assert _rows(db, "CHANGE_TYPE") == want and g.fill_change_types(db) == 0
