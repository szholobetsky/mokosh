"""Git connector — extracts commits from a local repository into the COMMIT table.

Incremental: commits whose SHA is already in the database are skipped, and new rows get
IDs after the largest one there. A first run on an empty database numbers the rows from 1
in git's order (newest first), exactly as before. History rewritten upstream (rebase, force
push) leaves the old commits in the database and adds the new ones; nothing is deleted.

The diff of a commit is taken parent -> commit: a line the commit added starts with '+'. (mokosh
before 2026-10-10 took it commit -> parent, the other way round; `mokosh fixdiff` turns such a
database's DIFF the right way.)
"""

import logging
from datetime import datetime, timezone
from tqdm import tqdm

BATCH_ROWS = 500


def change_type(diff) -> str:
    """What the commit did to the file: A / M / D / R (diff taken parent -> commit)."""
    if diff.renamed_file:
        return "R"
    if diff.new_file:
        return "A"
    if diff.deleted_file:
        return "D"
    return "M"


class GitConnector:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.logger = logging.getLogger(__name__)

    def extract_commits(self, db_manager, branch: str = 'main',
                        since: str = None, print_content: bool = False) -> dict:
        """Extract the commits (with per-file diffs) not yet in the database.

        Args:
            db_manager: DatabaseManager instance
            branch:     Branch to walk (default: main)
            since:      ISO date string 'YYYY-MM-DD' — skip commits older than this
            print_content: Print each row to console (debug)
        Returns:
            {"head", "commits_seen", "commits_new", "rows_new", "skipped_known", "no_diff"}
        """
        try:
            import git
        except ImportError:
            raise ImportError("gitpython is required: pip install mokosh")

        since_dt = None
        if since:
            since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)

        try:
            repo = git.Repo(self.repo_path)
        except Exception as e:
            raise RuntimeError(f"Cannot open git repo at '{self.repo_path}': {e}")

        # Try given branch, fall back to HEAD
        try:
            commits = list(repo.iter_commits(branch))
            head = repo.commit(branch).hexsha
        except git.exc.GitCommandError:
            self.logger.warning(f"Branch '{branch}' not found, using HEAD")
            commits = list(repo.iter_commits('HEAD'))
            head = repo.head.commit.hexsha

        if since_dt:
            commits = [c for c in commits
                       if c.committed_datetime.astimezone(timezone.utc) >= since_dt]
            self.logger.info(f"After since={since}: {len(commits)} commits remain")

        known = db_manager.known_shas()
        todo = [c for c in commits if c.hexsha not in known]
        stats = {"head": head, "commits_seen": len(commits), "commits_new": 0, "rows_new": 0,
                 "skipped_known": len(commits) - len(todo), "no_diff": 0}
        self.logger.info(f"'{branch}': {len(commits)} commits, {len(todo)} not in the database yet")
        counter = db_manager.max_commit_id()
        rows = []

        for commit in tqdm(todo, desc="Extracting commits", unit="commit"):
            sha = commit.hexsha
            author_name = commit.author.name or ""
            author_email = commit.author.email or ""
            date = commit.committed_datetime.isoformat()
            message = commit.message or ""

            try:
                # parent -> commit; a root commit against the empty tree, which GitPython already
                # gives that way round (every file new, every line '+')
                diffs = (commit.parents[0].diff(commit, create_patch=True) if commit.parents
                         else commit.diff(git.NULL_TREE, create_patch=True))
            except Exception as e:
                self.logger.warning(f"Could not diff {sha[:8]}: {e}")
                continue
            if not diffs:
                stats["no_diff"] += 1
                continue

            for diff in diffs:
                counter += 1
                path = diff.b_path or diff.a_path or ""          # b = the commit's side: a rename's new name
                try:
                    diff_content = diff.diff.decode('utf-8', errors='ignore') if diff.diff else ""
                except Exception:
                    diff_content = ""
                rows.append((counter, sha, author_name, author_email, date, message, path, diff_content,
                             change_type(diff)))
                if print_content:
                    print(f"[{counter}] {sha[:8]} {path}")
            stats["commits_new"] += 1
            if len(rows) >= BATCH_ROWS:                       # one transaction per batch, not per row
                db_manager.insert_commit_data_batch(rows)
                stats["rows_new"] += len(rows)
                rows = []

        if rows:
            db_manager.insert_commit_data_batch(rows)
            stats["rows_new"] += len(rows)
        self.logger.info(f"Extracted {stats['rows_new']} commit-file rows from {stats['commits_new']} new commits")
        return stats

    def fill_change_types(self, db_manager) -> int:
        """Rows stored before CHANGE_TYPE existed: one `git log --name-status` (first parent, renames
        found, as GitPython does) gives every commit's A/M/D/R; -> rows filled. Commits no longer in
        the repository stay NULL."""
        import subprocess
        if not db_manager.rows_without_change_type():
            return 0
        out = subprocess.run(["git", "-C", self.repo_path, "log", "--all", "--diff-merges=first-parent", "-M",
                              "--name-status", "--format=@@%H"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
        rows, sha = [], None
        for line in out.splitlines():
            if line.startswith("@@"):
                sha = line[2:].strip()
            elif line.strip() and sha:
                parts = line.split("\t")
                kind = parts[0][:1]
                if kind in "AMDR" and len(parts) >= 2:
                    rows.append((kind, sha, parts[-1].strip()))     # a rename: the new name, as PATH
        return db_manager.set_change_types(rows)

    def root_shas(self) -> set:
        import subprocess
        out = subprocess.run(["git", "-C", self.repo_path, "rev-list", "--max-parents=0", "--all"],
                             capture_output=True, text=True).stdout
        return {x.strip() for x in out.splitlines() if x.strip()}
