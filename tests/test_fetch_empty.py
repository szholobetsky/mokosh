"""An empty answer of the tracker is not a task with no text: it is not written, it is asked again,
and a tracker that answers nothing at all (wrong owner/repo) stops the fetch."""
import sqlite3

from mokosh import task_fetcher
from mokosh.db_manager import DatabaseManager


class _Tracker:
    def __init__(self, answers):
        self.answers = answers

    def fetch_task_details(self, name):
        return self.answers.get(name, ("", "", ""))


def _db(tmp_path, names, title=None):
    db = DatabaseManager(str(tmp_path / "units.db"))
    db.create_tables(has_tasks=True)
    db.insert_tasks_batch(names)
    if title is not None:                                   # what an older mokosh left behind
        con = sqlite3.connect(db.db_file)
        con.execute('UPDATE "TASK" SET TITLE = ?', (title,))
        con.commit()
        con.close()
    return db


def _fetcher(answers):
    f = task_fetcher.TaskFetcher.__new__(task_fetcher.TaskFetcher)
    f.connector = _Tracker(answers)
    import logging
    f.logger = logging.getLogger("test")
    return f


def _titles(db):
    con = sqlite3.connect(db.db_file)
    out = dict(con.execute('SELECT NAME, TITLE FROM "TASK"'))
    con.close()
    return out


def test_an_empty_answer_is_not_written_and_asked_again(tmp_path):
    db = _db(tmp_path, ["1", "2"])
    r = _fetcher({"1": ("Fix the parser", "body", "")}).fetch_all_tasks(db, rate_limit_delay=0)
    assert r["fetched"] == 1 and r["empty"] == 1
    assert _titles(db) == {"1": "Fix the parser", "2": None}
    assert db.get_tasks_without_details() == ["2"]


def test_an_empty_title_from_an_older_mokosh_is_fetched_again(tmp_path):
    db = _db(tmp_path, ["7"], title="")
    assert db.get_tasks_without_details() == ["7"]


def test_a_tracker_that_answers_nothing_stops_the_fetch(tmp_path):
    names = [str(i) for i in range(task_fetcher.STOP_AFTER_EMPTY + 10)]
    db = _db(tmp_path, names)
    r = _fetcher({}).fetch_all_tasks(db, rate_limit_delay=0)
    assert r["stopped"] and r["empty"] == task_fetcher.STOP_AFTER_EMPTY and r["fetched"] == 0
    assert len(db.get_tasks_without_details()) == len(names)
