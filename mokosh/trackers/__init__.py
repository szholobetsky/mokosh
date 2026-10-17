"""Task tracker connectors. Imported lazily: each one pulls its own client library
(requests, beautifulsoup4, selenium), and a missing optional one — selenium — must not
break the others."""
import importlib

_WHERE = {
    "JiraApiConnector": ".jira_api",
    "JiraHtmlConnector": ".jira_html",
    "JiraSeleniumConnector": ".jira_selenium",
    "GitHubApiConnector": ".github",
    "YouTrackApiConnector": ".youtrack",
    "GitLabApiConnector": ".gitlab",
}
__all__ = list(_WHERE)


def __getattr__(name):
    if name in _WHERE:
        return getattr(importlib.import_module(_WHERE[name], __name__), name)
    raise AttributeError(name)
