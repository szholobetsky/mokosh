"""GitLab REST API v4 connector.

Note: commit-to-issue link rates on GitLab are typically very low (~1-6%).
For better coverage use fetch_mr_issue_links() instead of commit-message regex.
"""

import logging
import requests
from typing import Tuple, List, Dict


class GitLabApiConnector:
    def __init__(self, base_url: str, project: str, token: str = None):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.logger = logging.getLogger(__name__)
        project_id = project.replace('/', '%2F') if '/' in project else project
        self.api_base = f"{self.base_url}/api/v4/projects/{project_id}"
        self.headers = {"Accept": "application/json"}
        if token:
            self.headers["PRIVATE-TOKEN"] = token

    def fetch_task_details(self, issue_iid: str) -> Tuple[str, str, str]:
        url = f"{self.api_base}/issues/{issue_iid}"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            if response.status_code in (404, 401):
                return '', '', ''
            response.raise_for_status()
            data = response.json()
            title = data.get("title", "") or ""
            description = data.get("description", "") or ""
            comments = self._fetch_comments(issue_iid)
            return title, description, comments
        except Exception as e:
            self.logger.error(f"Error fetching issue #{issue_iid}: {e}")
            return '', '', ''

    def _fetch_comments(self, issue_iid: str) -> str:
        if not self.token:
            return ""
        url = f"{self.api_base}/issues/{issue_iid}/notes"
        try:
            r = requests.get(url, headers=self.headers,
                             params={"per_page": 100, "sort": "asc"}, timeout=30)
            if r.status_code in (401, 403):
                return ""
            r.raise_for_status()
            return " ".join(
                n.get("body", "") or "" for n in r.json()
                if not n.get("system", False) and n.get("body")
            )
        except Exception:
            return ""
