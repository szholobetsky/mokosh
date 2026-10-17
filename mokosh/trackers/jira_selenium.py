"""Jira Selenium connector — use for JS-rendered Jira pages."""

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import logging
from typing import Tuple


class JiraSeleniumConnector:
    def __init__(self, jira_url: str, token: str = None):
        self.jira_url = jira_url.rstrip('/')
        self.logger = logging.getLogger(__name__)

    def fetch_task_details(self, task_key: str) -> Tuple[str, str, str]:
        comment_tab = '?page=com.atlassian.jira.plugin.system.issuetabpanels:comment-tabpanel'
        url = f"{self.jira_url}/browse/{task_key}{comment_tab}"
        browser = None
        try:
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--blink-settings=imagesEnabled=false')
            options.add_argument('--disable-gpu')
            options.add_argument('--no-sandbox')
            browser = webdriver.Chrome(options=options)
            browser.get(url)
            browser.implicitly_wait(10)
            soup = BeautifulSoup(browser.page_source, 'html.parser')
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
        finally:
            if browser:
                browser.quit()
