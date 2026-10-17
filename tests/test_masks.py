"""commit_mask help: what each preset finds, tried on commit messages (mokosh init, simargl Setup)."""
import pytest

from mokosh.task_extractor import (KEY_PRESET, MASK_HELP, PATTERNS, REGEX_EXAMPLES, TaskExtractor, compile_mask,
                                   preview_masks, project_keys)


def test_every_preset_is_explained_and_finds_its_own_example():
    assert [m for m, _, _ in MASK_HELP] == list(PATTERNS) + [KEY_PRESET]
    for mask, _, example in MASK_HELP:
        row = preview_masks([example], [mask], keys=["OFBIZ"])[0]
        assert row["linked"] == 1, mask


def test_regex_key_takes_the_projects_keys_anywhere_as_whole_words():
    keys = project_keys({"source": "jira", "jira_project": "abc, ABD ABK"})
    assert keys == ["ABC", "ABD", "ABK"]
    ex = TaskExtractor(KEY_PRESET, keys)
    assert ex.extract_task_name("Fixed: invoice total (ABD-12)") == "ABD-12"
    assert ex.extract_task_name("ABK-3 at the start") == "ABK-3"
    assert ex.extract_task_name("UTF-8 in XABC-1 and ABC-12a") is None     # not a key, not a whole word
    assert project_keys({"source": "youtrack", "youtrack_project": "KT"}) == ["KT"]
    with pytest.raises(ValueError, match="needs the project key"):
        compile_mask(KEY_PRESET)                       # GitHub / GitLab: no key to build it from
    rows = {r["mask"] for r in preview_masks(["ABC-1 x"])}
    assert KEY_PRESET not in rows                      # not tried without keys
    assert {r["mask"]: r["linked"] for r in preview_masks(["ABC-1 x"], keys=keys)}[KEY_PRESET] == 1


def test_the_regex_examples_are_right():
    msg = "Fixed: invoice total (ABD-12), see OFBIZ-5"
    want = {r"(OFBIZ-\d+)": "OFBIZ-5", r"((?:ABC|ABD|ABK)-\d+)": "ABD-12", r"\b((?:ABC|ABD|ABK)-\d+)\b": "ABD-12"}
    assert {rx: TaskExtractor(rx).extract_task_name(msg) for rx, _ in REGEX_EXAMPLES} == want
    assert TaskExtractor(r"\b((?:ABC|ABD|ABK)-\d+)\b").extract_task_name("XABC-1") is None


def test_preview_counts_and_examples():
    msgs = ["[FLINK-1] a", "[FLINK-2] b", "FLINK-3 c", "no key here"]
    rows = {r["mask"]: r for r in preview_masks(msgs)}
    assert rows["bracketed"]["linked"] == 2 and rows["bracketed"]["examples"] == ["FLINK-1", "FLINK-2"]
    assert rows["simple"]["linked"] == 1 and rows["simple"]["total"] == 4


def test_a_broken_regex_is_refused():
    compile_mask(r"TASK-(\d+)")
    with pytest.raises(ValueError):
        compile_mask("([A-Z+")
