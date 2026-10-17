![mokosh](https://raw.githubusercontent.com/szholobetsky/simrgl/main/images/logo/mokosh.png)

# mokosh

Dataset collector for software projects — gathers the commits of a git repository and the tasks of its issue tracker (Jira, GitHub, GitLab, YouTrack) into one SQLite database, linked task by task, without a GPU or an LLM.

## What it does

mokosh reads the commits of a repository, finds the task each commit belongs to from its message
(`PROJ-123 fix login`), fetches those tasks from the tracker and writes both into one database:

- **tasks** — key, title, description and comments, as plain text (tracker markup such as Jira's
  ADF and wiki syntax is converted);
- **commits** — one row per changed file: commit, author, date, message, path, diff, the task it
  belongs to, and what happened to the file (added, modified, deleted, renamed).

Around that it offers:

- **init** — a setup wizard: repository, branch, tracker, and how task keys appear in commit
  messages, with every option tried on the repository's recent commits;
- **ingest** — incremental and resumable: a second run adds only what is new, and an interrupted
  fetch continues where it stopped;
- **status** — what a database holds;
- **export** — the tasks and commits as plain text files, for tools that read folders;
- **ui** — the same in the browser.

---

## Statement of need

Research on mining software repositories and task-to-code retrieval starts from the same dataset:
tasks with their text, and the files each task changed. Building it means walking the git history,
recognising task keys in commit messages, calling the tracker's API page by page, cleaning its
markup and keeping it all consistent when the project moves on. Each study tends to write its own
scripts for this, and their datasets are hard to rebuild or extend.

mokosh does this once, for four trackers, into one documented SQLite layout. It is meant for:

- **researchers** who need a reproducible task–commit dataset of a real project, and want to
  refresh it later without starting over;
- **developers and tool builders** who want their project's history in a form that other tools
  can search: [simargl](https://pypi.org/project/simargl/) (semantic search over past tasks) and
  [gamayun-ontology](https://pypi.org/project/gamayun-ontology/) (a statistical ontology of the
  history) both read mokosh databases.

---

## Install

```bash
pip install mokosh
pip install "mokosh[selenium]"     # only for Jira instances that need a real browser
```

Python 3.10+. Dependencies: gitpython, requests, beautifulsoup4, pyyaml, tqdm, Flask, waitress.

---

## Quick start

```bash
cd my-project                    # a git clone
mokosh init                      # repository, branch, history since, tracker -> .mokosh/project.yaml
mokosh ingest                    # [1/2] commits   [2/2] task keys + task details -> units.db
mokosh status                    # tasks, commits, how many are linked, date range
```

A month later, `mokosh ingest` again adds only the new commits and the tasks not fetched yet.

The database is ready for the other tools:

```bash
simargl index units units.db                              # semantic search over the tasks
gamayun index --db units.db --repo . --project myproj     # statistical ontology
```

---

## Commands

| Command | What it does |
|---|---|
| `mokosh init` | Setup wizard; writes `.mokosh/project.yaml` |
| `mokosh ingest [--phase git\|tasks] [--force]` | Commits, then task keys and task details; only what is new |
| `mokosh status [DB]` | What the database holds |
| `mokosh export DB --out DIR` | Tasks and commits as text files (see [Export](#export)) |
| `mokosh plaintext DB [--dry-run]` | Convert tracker markup to plain text in a database from an older ingest |
| `mokosh fixdiff DB --repo REPO` | Fix the diff direction in a database written by mokosh before 2026-10-10 (`ingest` does it on its own) |
| `mokosh ui [--port 8093] [--host 0.0.0.0] [--root FOLDER]` | The web UI |

`--force` links every commit to a task key again, for example after changing `commit_mask`.

---

## The database

| Table | Columns |
|---|---|
| `TASK` | `ID`, `NAME` (the task key), `TITLE`, `DESCRIPTION`, `COMMENTS` |
| `COMMIT` | `ID`, `SHA`, `AUTHOR_NAME`, `AUTHOR_EMAIL`, `CMT_DATE`, `MESSAGE`, `PATH`, `DIFF`, `TASK_NAME`, `CHANGE_TYPE` |

`COMMIT` has one row per (commit, file). `TASK_NAME` links it to `TASK.NAME`. `CHANGE_TYPE` is
`A` added, `M` modified, `D` deleted or `R` renamed (`PATH` is then the new name). `DIFF` is the
patch from the parent to the commit, so an added line starts with `+`.

Without a tracker mokosh stores the commits only, and `TASK` stays empty.

---

## Trackers

| `source` | Settings in `tasks:` |
|---|---|
| `jira` | `jira_url`, `jira_project`, `jira_connector: api \| html \| selenium`, `jira_token` |
| `github` | `github_owner`, `github_repo`, `github_token` — `mokosh init` asks for the repository URL `https://github.com/<USER>/<REPO>` and writes both names |
| `gitlab` | `gitlab_url`, `gitlab_project`, `gitlab_token` |
| `youtrack` | `youtrack_url`, `youtrack_project`, `youtrack_token` |

**Tokens never go into the file.** `mokosh init` (and the Setup page of the UI) puts the token you
enter into an environment variable (`GITHUB_TOKEN`, `JIRA_TOKEN`, `GITLAB_TOKEN`,
`YOUTRACK_TOKEN`, or `MOKOSH_<PROJECT>_GITHUB_TOKEN` when the plain name already holds another
token), and `project.yaml` keeps only `github_token: ${GITHUB_TOKEN}`. On Windows the variable is
saved for your user (`setx`); elsewhere `init` prints the `export` line for your shell profile.

### Task keys in commit messages

How a task key is found in a commit message is `commit_mask` in `tasks:`, for any tracker:

| `commit_mask` | Finds |
|---|---|
| `simple` (default) | `PROJ-123` at the start of the message |
| `bracketed` | `[PROJ-123]` anywhere |
| `django` | `Fixed #123` |
| `generic` | `fixes #123`, `closed: #123`, `resolves #123` |
| `broad` | any `#123` |
| `regex-key` | the project's own key anywhere, as a whole word: `Fixed: invoice total (SHOP-12345)`. Built from `jira_project` (or `youtrack_project`); for several projects list them all: `jira_project: ABC,ABD,ABK` |
| any regex | your own pattern; the first group is the key, if there is one |

Examples of your own patterns:

| regex | finds |
|---|---|
| `(SHOP-\d+)` | one project's key anywhere |
| `((?:ABC\|ABD\|ABK)-\d+)` | the keys of several projects anywhere |
| `\b((?:ABC\|ABD\|ABK)-\d+)\b` | the same, not inside a longer word (`XABC-1`) — what `regex-key` builds |

A pattern like `([A-Z]+-\d+)` (any key) also catches `UTF-8`, `SHA-256`, `ISO-8859`, so name the
keys instead.

`mokosh init` tries every mask on the repository's last 300 commits and offers the one that links
the most:

```
On the last 300 commits of ./my-project:
  simple     links    0 (0%)   -
  bracketed  links  282 (94%)   PROJ-4029, PROJ-3826, PROJ-4083
  ...
Commit mask [Enter = bracketed]:
```

---

## Updating a dataset

`ingest` is incremental:

- it adds only the commits not in the database yet; existing rows are never changed;
- it links only the commits that have no task yet;
- it asks the tracker only about the tasks with no details yet, so each task is fetched once.

An interrupted fetch therefore continues where it stopped. An empty answer from the tracker (404,
no access, a rate limit, a wrong owner/repo) is not stored: the task is asked again next time, and
if the first 20 tasks all come back empty the fetch stops and says which settings to check. What
each run added is kept in `.mokosh/ingest_state.yaml`. History rewritten upstream (rebase, force
push) keeps the old commits and adds the new ones.

---

## Web UI

```bash
mokosh ui                        # http://127.0.0.1:8093/
```

- **Setup:** the form `mokosh init` asks for, with every commit mask tried on the repository's
  last 300 commits.
- **Ingest:** runs `mokosh ingest` (both phases, or git / tasks only) and shows its log.
- **Status:** what the database holds, `project.yaml` (tokens masked) and `ingest_state.yaml`.

The UI lists the projects under the folder it was started in and those remembered in
`~/.mokosh/projects.json`. It has 8 colour themes and 6 languages (English, Ukrainian, German,
French, Spanish, Portuguese).

---

## Export

Some tools read folders of text files rather than SQLite (for example `/flow glossary` in
[1bcoder](https://pypi.org/project/1bcoder/)). `mokosh export` writes them:

```bash
mokosh export units.db --out DIR                    # DIR/tasks/ and DIR/commits/ (default: --all)
mokosh export units.db --out DIR --join             # one file per task with its commits (DIR/joined/)
mokosh export units.db --out DIR --task             # tasks only   (--commits: commits only)
mokosh export units.db --out DIR --level title      # title only | desc: + description (default) | comments: + comments
mokosh export units.db --out DIR --diff-mode full   # the whole diff (default changed: only the changed lines)
```

With `--join`, each commit is its own `--- commit <sha> ---` block (message and per-file diff). A
task with no commit still gets its file; commits with no task are counted, not guessed at.

---

## Python API

The database is plain SQLite, so it can be read with `sqlite3` directly. `mokosh.schema` gives
the table names as the file has them, already quoted:

```python
import sqlite3
from mokosh import schema

con = sqlite3.connect("units.db")
task_t, commit_t = schema.tables(con)

rows = con.execute(f"""
    SELECT T.NAME, T.TITLE, C.PATH
    FROM {task_t} T JOIN {commit_t} C ON C.TASK_NAME = T.NAME
""").fetchall()
```

Export from Python:

```python
from mokosh.exporter import export_units

export_units("units.db", "out", mode="join", level="desc", diff_mode="changed")
```

---

## Tests

```bash
git clone https://github.com/szholobetsky/mokosh
cd mokosh
pip install -e ".[dev]"
pytest
```

## Support

The project is on GitHub: [github.com/szholobetsky/mokosh](https://github.com/szholobetsky/mokosh).
Questions, bug reports and suggestions go to its
[issues](https://github.com/szholobetsky/mokosh/issues).

---

## Part of the SIMARGL toolkit

mokosh is one of ten tools that together form an **intellectual development support system**:

| Tool | Role |
|---|---|
| **[simargl](https://github.com/szholobetsky/simargl)** | Task-to-code retrieval — given a task description, finds which files and modules are likely affected, using semantic similarity over git history |
| **[svitovyd](https://github.com/szholobetsky/svitovyd)** | Project map — scans any codebase and produces a structural map of definitions and cross-file dependencies; queried from the CLI, a web UI with a layered map, or an MCP server |
| **[1bcoder](https://github.com/szholobetsky/1bcoder)** | AI coding assistant for small local models — surgical context management, agents, parallel inference, proc scripts |
| **[yasna](https://github.com/szholobetsky/yasna)** | Session memory — indexes conversations from all AI agents so you can find what was discussed, when, and where |
| **[radogast](https://github.com/szholobetsky/radogast)** | Context drift monitor — measures how far an AI agent's conversation has drifted from the original task |
| **[vyrii](https://github.com/szholobetsky/vyrii)** | Local AI web UI — chat, translate, web research, RAG and file management in the browser (Flask; Gradio optional); powered by Ollama or any OpenAI-compatible backend |
| **[syryn](https://github.com/szholobetsky/syryn)** | Bluetooth identity beacon for headless devices — the device's IP is its Bluetooth name; hostname, mDNS and network interfaces on request |
| **[alkonost](https://github.com/szholobetsky/alkonost)** | Browser cockpit for 1bcoder's flows — the `deepagent_*` spec pipeline (plan tree → specs → task board) and the glossary (build, browse, search), tuned for small local models |
| **[gamayun](https://github.com/szholobetsky/gamayun)** | Statistical ontology without an LLM — builds triples of terms, identifiers, files and modules from task history or a folder of documents and code; every triple carries its significance test |
| **[mokosh](https://github.com/szholobetsky/mokosh)** | Dataset gathering — commits of a git repository and the tasks of its tracker (Jira, GitHub, GitLab, YouTrack) in one SQLite database that the other tools read |

- **simargl** finds the code related to a task by vector search over the task history (embeddings).
- **svitovyd** answers: *how is the code structured and what depends on what?*
- **1bcoder** answers: *how do I work with local models efficiently?*
- **yasna** answers: *where did I already discuss this?*
- **radogast** answers: *is the AI agent still on track toward the goal?*
- **vyrii** answers: *how do I access all of this through a browser?*
- **syryn** answers: *what is the address of this headless device?*
- **alkonost** answers: *how do I turn one task description into a reviewed, working plan — and see the project's glossary — without a big model or a big context window?*
- **gamayun** finds the code related to a task through an ontology of significant links between words, files and modules (no LLM).
- **mokosh** prepares the initial dataset from version control and the issue tracker.

Together they cover the full development loop: gather the history, understand the codebase, find relevant history, work with AI locally, remember what was decided, and verify the context stays on target.

The name comes from Slavic mythology. Mokosh (Мокош) is the goddess of the harvest and of spinning: she spins the threads of fate and watches over the yield of the land. A fitting name for the tool that spins tasks and commits into one yarn — no good ingest, no harvest.

---

## License

MIT. See [LICENSE](LICENSE).

## About

(c) 2026 Stanislav Zholobetskyi, Oleh Andriichuk  
Institute for Information Recording, National Academy of Sciences of Ukraine, Kyiv  
PhD research: «Intelligent Technology for Software Development and Maintenance Support»
