"""Jira HTML scraping connector — use when REST API is unavailable."""

import requests
from bs4 import BeautifulSoup
import logging
from typing import Tuple


class JiraHtmlConnector:
    def __init__(self, jira_url: str, token: str = None):
        self.jira_url = jira_url.rstrip('/')
        self.logger = logging.getLogger(__name__)

    def fetch_task_details(self, task_key: str) -> Tuple[str, str, str]:
        comment_tab = '?page=com.atlassian.jira.plugin.system.issuetabpanels:comment-tabpanel'
        url = f"{self.jira_url}/browse/{task_key}{comment_tab}"
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            title_el = soup.find('h1', {'id': 'summary-val'})
            title = title_el.text.strip() if title_el else ''
            desc_el = soup.find('div', {'id': 'description-val'})
            description = desc_el.text.strip() if desc_el else ''
            comment_els = soup.find_all(class_='twixi-wrap concise actionContainer')
            comments = ' '.join(c.text.strip() for c in comment_els)
            return title, description, comments
        except Exception as e:
            self.logger.error(f"Error fetching {task_key}: {e}")
            return '', '', ''
