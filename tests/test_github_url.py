"""GitHub is set by one URL: owner and repo are parsed out of it; a URL without the repo is refused."""
import pytest

from mokosh.trackers.github import parse_repo_url


@pytest.mark.parametrize("text", ["https://github.com/sqlfluff/sqlfluff", "https://github.com/sqlfluff/sqlfluff/",
                                  "github.com/sqlfluff/sqlfluff.git", "git@github.com:sqlfluff/sqlfluff.git",
                                  "https://github.com/sqlfluff/sqlfluff/issues/6000", "sqlfluff/sqlfluff"])
def test_a_repository_url_gives_owner_and_repo(text):
    assert parse_repo_url(text) == ("sqlfluff", "sqlfluff")


@pytest.mark.parametrize("text", ["https://github.com/sqlfluff/", "https://github.com/sqlfluff", "sqlfluff", ""])
def test_a_url_without_the_repo_is_refused(text):
    with pytest.raises(ValueError, match="https://github.com/<USER>/<REPO>"):
        parse_repo_url(text)
    if text:
        with pytest.raises(ValueError, match="missed repo name"):
            parse_repo_url(text)


def test_another_host_is_refused():
    with pytest.raises(ValueError, match="not a GitHub"):
        parse_repo_url("https://gitlab.com/group/repo")
