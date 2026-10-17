"""Fetches task details from a tracker and writes them to the TASK table.

Accepts a tracker_config dict (from project.yaml) — no dependency on config.py.
Supports resume: skips tasks that already have a TITLE in the DB. An empty answer from the tracker
is not written: the task stays unfetched and the next ingest asks again.
"""

import time
import logging
import datetime
from tqdm import tqdm

# this many empty answers before the first real one: the tracker settings are wrong, not the tasks
STOP_AFTER_EMPTY = 20


def _build_connector(tracker_type: str, cfg: dict):
    if tracker_type == 'jira':
        connector_type = cfg.get('jira_connector', 'api')
        url = cfg['jira_url']
        token = cfg.get('jira_token') or None
        if connector_type == 'html':
            from .trackers.jira_html import JiraHtmlConnector
            return JiraHtmlConnector(url, token=token)
        if connector_type == 'selenium':
            from .trackers.jira_selenium import JiraSeleniumConnector
            return JiraSeleniumConnector(url, token=token)
        from .trackers.jira_api import JiraApiConnector
        return JiraApiConnector(url, token=token)

    if tracker_type == 'github':
        from .trackers.github import GitHubApiConnector
        return GitHubApiConnector(
            owner=cfg['github_owner'],
            repo=cfg['github_repo'],
            token=cfg.get('github_token') or None,
        )

    if tracker_type == 'youtrack':
        from .trackers.youtrack import YouTrackApiConnector
        return YouTrackApiConnector(
            base_url=cfg['youtrack_url'],
            token=cfg.get('youtrack_token') or None,
        )

    if tracker_type == 'gitlab':
        from .trackers.gitlab import GitLabApiConnector
        return GitLabApiConnector(
            base_url=cfg['gitlab_url'],
            project=cfg['gitlab_project'],
            token=cfg.get('gitlab_token') or None,
        )

    raise ValueError(f"Unknown tracker type: {tracker_type}")


class TaskFetcher:
    def __init__(self, tracker_type: str, tracker_config: dict):
        self.connector = _build_connector(tracker_type, tracker_config)
        self.logger = logging.getLogger(__name__)

    def fetch_all_tasks(self, db_manager, rate_limit_delay: float = 1.0,
                        batch_size: int = 100,
                        state: dict = None, state_saver=None):
        """Fetch details for all tasks in TASK table that have no TITLE yet (NULL or empty).

        Args:
            db_manager:        DatabaseManager instance
            rate_limit_delay:  Seconds to sleep between requests
            batch_size:        Commit to DB every N fetches
            state:             Checkpoint dict (tasks sub-dict from ingest_state.yaml)
            state_saver:       Callable(state) — called after each batch to persist checkpoint
        """
        task_names = db_manager.get_tasks_without_details()
        if not task_names:
            self.logger.info("No tasks to fetch (all have details or none registered)")
            return {"to_fetch": 0, "fetched": 0, "failed": 0}

        self.logger.info(f"Fetching details for {len(task_names)} tasks")
        fetched = 0
        failed = 0
        empty = 0

        for task_name in tqdm(task_names, desc="Fetching tasks", unit="task"):
            try:
                title, description, comments = self.connector.fetch_task_details(task_name)
                if not (title or description):
                    # every connector answers ('', '', '') when it got nothing (404, auth, rate limit,
                    # a wrong owner/repo/URL): not a task with no text — left unfetched, tried next time
                    empty += 1
                    if fetched == 0 and empty >= STOP_AFTER_EMPTY:
                        self.logger.error(f"The first {empty} tasks came back empty — stopping")
                        return {"to_fetch": len(task_names), "fetched": 0, "failed": failed,
                                "empty": empty, "stopped": True}
                    if rate_limit_delay > 0:
                        time.sleep(rate_limit_delay)
                    continue
                db_manager.update_task_details(task_name, title, description, comments)
                fetched += 1

                if state is not None:
                    state['fetched'] = fetched
                    state['last_key'] = task_name
                    state['last_updated'] = datetime.datetime.now().isoformat()

                if fetched % batch_size == 0 and state_saver and state is not None:
                    state_saver(state)

                if rate_limit_delay > 0:
                    time.sleep(rate_limit_delay)

            except Exception as e:
                self.logger.error(f"Error processing {task_name}: {e}")
                failed += 1                       # stays without a TITLE: the next ingest tries again
                continue

        if state_saver and state is not None:
            state_saver(state)

        self.logger.info(f"Fetched details for {fetched} tasks")
        return {"to_fetch": len(task_names), "fetched": fetched, "failed": failed, "empty": empty}
