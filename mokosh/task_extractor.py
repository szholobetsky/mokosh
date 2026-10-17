"""Extracts task identifiers from commit messages using regex patterns."""

import re
import logging
from typing import Optional


# Built-in pattern presets
PATTERNS = {
    "simple":   r'^[A-Z]+-\d+',               # KAFKA-123 at start
    "bracketed": r'\[([A-Z]+-\d+)\]',          # [KAFKA-123]
    "django":   r'[Ff]ixed\s+#(\d+)',          # Fixed #123
    "generic":  r'(?:fix(?:e[sd])?|clos(?:e[sd]?)|resolv(?:e[sd]?))\s*[:#]?\s*#(\d+)',
    "broad":    r'#(\d+)',                      # any #NNN
}


# what each preset finds — the help shown by `mokosh init` and simargl's Setup page
MASK_HELP = [
    ("simple", "KAFKA-123 at the start of the message", "KAFKA-123 Fix the consumer lag"),
    ("bracketed", "[KAFKA-123] anywhere in the message", "Fix the consumer lag [KAFKA-123]"),
    ("django", "Fixed #123", "Fixed #123 -- Corrected the admin"),
    ("generic", "fixes #123, closed: #123, resolves #123", "Retry on timeout (fixes #123)"),
    ("broad", "any #123", "Merge pull request #123 from user/branch"),
    ("regex-key", "the project's own key anywhere (jira_project: OFBIZ, or several: ABC,ABD,ABK)",
     "Fixed: invoice total (OFBIZ-12345)"),
]

# a regex of your own: the first group is the key — shown next to the presets
REGEX_EXAMPLES = [
    (r"(OFBIZ-\d+)", "one project's key anywhere in the message"),
    (r"((?:ABC|ABD|ABK)-\d+)", "the keys of several projects anywhere"),
    (r"\b((?:ABC|ABD|ABK)-\d+)\b", "the same, not inside a longer word (XABC-1, ABC-12a)"),
]

# the preset that is built from the project's own key(s) — what regex-key stands for
KEY_PRESET = "regex-key"
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def project_keys(tasks_cfg: dict) -> list:
    """The project key(s) of the tracker settings: jira_project or youtrack_project, several split by
    commas or spaces ("ABC,ABD,ABK") -> ["ABC", "ABD", "ABK"]."""
    raw = (tasks_cfg or {}).get("jira_project") or (tasks_cfg or {}).get("youtrack_project") or ""
    return list(dict.fromkeys(k.upper() for k in _KEY.findall(str(raw))))


def key_regex(keys) -> str:
    """["ABC", "ABD"] -> \b((?:ABC|ABD)-\d+)\b  — a key of these projects, anywhere, as a whole word."""
    alt = "|".join(re.escape(k) for k in keys)
    return rf"\b((?:{alt})-\d+)\b" if len(keys) > 1 else rf"\b({alt}-\d+)\b"


def resolve_mask(mask: str, keys=None) -> str:
    """A preset name or a regex -> the regex; regex-key needs the project key(s)."""
    if mask == KEY_PRESET:
        if not keys:
            raise ValueError("regex-key needs the project key: jira_project (or youtrack_project), "
                             "e.g. OFBIZ, or several: ABC,ABD,ABK")
        return key_regex(keys)
    return PATTERNS.get(mask, mask)


def compile_mask(mask: str, keys=None):
    """A preset name or a regex -> compiled pattern; ValueError for a broken regex."""
    pattern = resolve_mask(mask, keys)
    try:
        return re.compile(pattern)
    except re.error as e:
        raise ValueError(f"not a valid commit mask: {mask} ({e})") from e


def preview_masks(messages, masks=None, keys=None) -> list:
    """How many of these commit messages each mask links to a task, with a few keys found:
    [{"mask", "linked", "total", "examples": [key, ...]}], the masks in MASK_HELP order (+ any given).
    regex-key is tried only when the project key(s) are known."""
    masks = masks or [m for m, _, _ in MASK_HELP]
    out = []
    for mask in masks:
        if mask == KEY_PRESET and not keys:
            continue
        ex = TaskExtractor(mask, keys)
        found = [k for k in (ex.extract_task_name(m) for m in messages) if k]
        seen = list(dict.fromkeys(found))
        out.append({"mask": mask, "linked": len(found), "total": len(messages), "examples": seen[:3]})
    return out


def recent_messages(repo: str, n: int = 300, branch: str = "") -> list:
    """The last n commit messages of a git repository (empty if it is not one or git is missing)."""
    import subprocess
    cmd = ["git", "-C", repo, "log", f"-{n}", "--format=%B%x00"] + ([branch] if branch else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0 and branch:                 # the branch may not exist yet: the current one
        return recent_messages(repo, n)
    return [m.strip() for m in r.stdout.split("\x00") if m.strip()] if r.returncode == 0 else []


class TaskExtractor:
    def __init__(self, pattern: str, keys=None):
        """
        Args:
            pattern: regex string OR a preset name (simple/bracketed/django/generic/broad/regex-key)
            keys:    the project key(s) regex-key is built from (project_keys of the tracker settings)
        """
        self.pattern = resolve_mask(pattern, keys)
        self.logger = logging.getLogger(__name__)

    def extract_task_name(self, message: str) -> Optional[str]:
        if not message:
            return None
        match = re.search(self.pattern, message)
        if not match:
            return None
        task_name = match.group(1) if (match.lastindex and match.lastindex >= 1) else match.group(0)
        if task_name.startswith('[') and task_name.endswith(']'):
            inner = re.search(r'\[([A-Z]+-\d+)\]', task_name)
            task_name = inner.group(1) if inner else task_name
        return task_name

    def process_all_commits(self, db_manager) -> dict:
        """Link the commits that have no task yet to the task key in their message, and
        register the keys in the TASK table. Rows linked by an earlier run are not read
        again, so a monthly ingest only looks at the new commits.
        -> {"rows_checked", "rows_linked", "tasks_new"}"""
        commits = db_manager.get_commits_for_extraction(only_unlinked=True)
        pairs = []
        for commit_id, message in commits:
            if isinstance(message, bytes):
                message = message.decode("utf-8", errors="ignore")
            task_name = self.extract_task_name(message)
            if task_name:
                pairs.append((task_name, commit_id))
        db_manager.update_task_names_batch(pairs)
        self.logger.info(f"Extracted {len(pairs)} task names from {len(commits)} unlinked commit rows")
        new_tasks = db_manager.insert_tasks_batch(db_manager.get_distinct_task_names())
        self.logger.info(f"Task population complete: {new_tasks} new task keys")
        return {"rows_checked": len(commits), "rows_linked": len(pairs), "tasks_new": new_tasks}
