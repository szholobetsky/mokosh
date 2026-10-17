import sqlite3

from mokosh.db_manager import make_plain
from mokosh.text import plain_text

ADF = {"type": "doc", "version": 1, "content": [
    {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Context"}]},
    {"type": "paragraph", "content": [
        {"type": "text", "text": "Leak header says "}, {"type": "text", "text": "Leak", "marks": [{"type": "strong"}]},
        {"type": "hardBreak"}, {"type": "mention", "attrs": {"id": "x1", "text": "@Ann"}},
        {"type": "text", "text": " see "}, {"type": "inlineCard", "attrs": {"url": "https://jira/SONAR-1"}}]},
    {"type": "bulletList", "content": [
        {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "one"}]}]},
        {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "two"}]}]}]},
    {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "abc", "type": "file"}}]},
    {"type": "table", "content": [{"type": "tableRow", "content": [
        {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "a"}]}]},
        {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "b"}]}]}]}]},
]}


def test_adf_tree_and_its_repr_give_the_same_text():
    text = plain_text(ADF)
    assert plain_text(repr(ADF)) == text                         # what str() stored in old ingests
    assert "Context" in text and "Leak header says Leak" in text and "@Ann" in text
    assert "https://jira/SONAR-1" in text and "- one" in text and "a | b" in text
    for junk in ("paragraph", "hardBreak", "mediaSingle", "attrs", "'type'", "abc"):
        assert junk not in text


def test_comments_list_and_empty_markers():
    comments = [{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Looks good"}]}]},
                {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Merged"}]}]}]
    assert plain_text(repr(comments)) == "Looks good\n\nMerged"
    for empty in (None, "None", "[]", "", "{}"):
        assert plain_text(empty) == ""
    assert plain_text("Plain *wiki* text {code}x{code}") == "Plain *wiki* text {code}x{code}"   # text stays


def test_make_plain_rewrites_only_markup(tmp_path):
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE TASK (ID INTEGER PRIMARY KEY, NAME TEXT UNIQUE, TITLE TEXT, DESCRIPTION TEXT, COMMENTS TEXT)")
    con.executemany("INSERT INTO TASK (NAME, TITLE, DESCRIPTION, COMMENTS) VALUES (?,?,?,?)", [
        ("A-1", "Title one", repr(ADF), "[]"),
        ("A-2", "Title two", "already plain", "a comment"),
        ("A-3", "Title three", "None", repr([ADF])),
    ])
    con.commit()
    con.close()
    dry = make_plain(str(db), dry_run=True, log=lambda *a: None)
    assert dry["changed"] == {"TITLE": 0, "DESCRIPTION": 2, "COMMENTS": 2} and dry["rows"] == 2
    con = sqlite3.connect(db)
    assert con.execute("SELECT DESCRIPTION FROM TASK WHERE NAME='A-1'").fetchone()[0] == repr(ADF)   # dry run wrote nothing
    con.close()
    make_plain(str(db), log=lambda *a: None)
    con = sqlite3.connect(db)
    rows = dict(con.execute("SELECT NAME, DESCRIPTION FROM TASK").fetchall())
    comments = dict(con.execute("SELECT NAME, COMMENTS FROM TASK").fetchall())
    assert rows["A-1"].startswith("Context") and rows["A-2"] == "already plain" and rows["A-3"] == ""
    assert comments["A-1"] == "" and comments["A-2"] == "a comment" and "Leak header" in comments["A-3"]
