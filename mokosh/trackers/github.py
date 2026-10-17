"""GitHub Issues API connector."""

import re
import time
import logging
import requests
from typing import Tuple

URL_HELP = "https://github.com/<USER>/<REPO>"


def parse_repo_url(text: str) -> Tuple[str, str]:
    """https://github.com/<USER>/<REPO> (also with .git, a trailing path, or plain <USER>/<REPO>)
    -> (owner, repo); ValueError when the user or the repo name is missing."""
    s = (text or "").strip().strip('"').strip()
    s = re.sub(r"^(?:https?://)?(?:www\.)?github\.com[/:]", "", s, flags=re.I)
    s = re.sub(r"^git@github\.com:", "", s, flags=re.I)
    parts = [p for p in s.split("/") if p]
    if not parts or "://" in s or "." in parts[0] or ":" in parts[0]:    # another host; a GitHub user has no dots
        raise ValueError(f"not a GitHub repository URL — expected {URL_HELP}")
    if len(parts) < 2:
        raise ValueError(f"missed repo name — expected {URL_HELP}")
    repo = parts[1][:-4] if parts[1].lower().endswith(".git") else parts[1]
    if not repo:
        raise ValueError(f"missed repo name — expected {URL_HELP}")
    return parts[0], repo


class GitHubApiConnector:
    BASE_URL = "https://api.github.com"

    def __init__(self, owner: str, repo: str, token: str = None):
        self.owner = owner
        self.repo = repo
        self.base = f"{self.BASE_URL}/repos/{owner}/{repo}"
        self.logger = logging.getLogger(__name__)
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def fetch_task_details(self, issue_number: str) -> Tuple[str, str, str]:
        url = f"{self.base}/issues/{issue_number}"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            if response.status_code in (404, 410):
                return '', '', ''
            if response.status_code == 403:
                reset_ts = response.headers.get("X-RateLimit-Reset")
                if reset_ts:
                    sleep_secs = max(0, int(reset_ts) - int(time.time())) + 5
                    self.logger.warning(f"GitHub rate limit. Sleeping {sleep_secs}s.")
                    time.sleep(sleep_secs)
                    response = requests.get(url, headers=self.headers, timeout=30)
                    if response.status_code != 200:
                        return '', '', ''
                else:
                    self.logger.warning("GitHub rate limit. Add github_token to project.yaml.")
                    return '', '', ''
            response.raise_for_status()
            data = response.json()
            title = data.get("title", "") or ""
            description = data.get("body", "") or ""
            comments = self._fetch_comments(data)
            return title, description, comments
        except Exception as e:
            self.logger.error(f"Error fetching issue #{issue_number}: {e}")
            return '', '', ''

    def _fetch_comments(self, issue_data: dict) -> str:
        if issue_data.get("comments", 0) == 0:
            return ""
        comments_url = issue_data.get("comments_url", "")
        if not comments_url:
            return ""
        try:
            r = requests.get(comments_url, headers=self.headers, timeout=30)
            r.raise_for_status()
            return " ".join(c.get("body", "") or "" for c in r.json())
        except Exception:
            return ""
