"""YouTrack REST API connector — works for public instances without a token."""

import re
import logging
import requests
from typing import Tuple


class YouTrackApiConnector:
    def __init__(self, base_url: str, token: str = None):
        self.base_url = base_url.rstrip('/')
        self.logger = logging.getLogger(__name__)
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def fetch_task_details(self, issue_id: str) -> Tuple[str, str, str]:
        url = f"{self.base_url}/api/issues/{issue_id}"
        params = {"fields": "summary,description,comments(text)"}
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            if response.status_code == 404:
                return '', '', ''
            if response.status_code == 401:
                self.logger.error(f"YouTrack auth required. Add youtrack_token to project.yaml.")
                return '', '', ''
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                return '', '', ''
            title = data.get("summary", "") or ""
            description = self._clean(data.get("description", "") or "")
            comments = " ".join(
                self._clean(c.get("text", "") or "")
                for c in (data.get("comments", []) or [])
                if c.get("text")
            )
            return title, description, comments
        except Exception as e:
            self.logger.error(f"Error fetching {issue_id}: {e}")
            return '', '', ''

    def _clean(self, text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"<[^>]+>", " ", text)
        text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                    .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
        return re.sub(r"\s+", " ", text).strip()
