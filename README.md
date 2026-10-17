![mokosh](https://raw.githubusercontent.com/szholobetsky/simrgl/main/images/logo/mokosh.png)

# mokosh

**The dataset a code-search study starts from.** mokosh reads the commits of a git repository,
finds the task each commit belongs to (`PROJ-123 fix login`), fetches those tasks from the
tracker — Jira, GitHub, GitLab or YouTrack — and writes both into one SQLite database:

- `TASK` — `NAME`, `TITLE`, `DESCRIPTION`, `COMMENTS`, in plain text (Jira ADF trees and wiki
  markup are converted);
- `COMMIT` — one row per (commit, file): `SHA`, author, `CMT_DATE`, `MESSAGE`, `PATH`, `DIFF`,
  `TASK_NAME`, `CHANGE_TYPE` — what the commit did to the file: `A` added, `M` modified,
  `D` deleted, `R` renamed (`PATH` is the new name). A database made before `CHANGE_TYPE` gets the
  column on the next `mokosh ingest`, filled from `git log --name-status` of the repository.
  `DIFF` is the patch parent → commit: a line the commit added starts with `+`.

mokosh before 2026-10-10 wrote `DIFF` the other way round (commit → parent: added lines with `-`).
`mokosh ingest` turns such a database the right way before it adds anything; for a database that is
not ingested again, `mokosh fixdiff <db> --repo <repo>` does the same. Either runs once — the
database is marked (`MOKOSH_META`) — and leaves the root commits alone (their patch against the
empty tree was already right).

Everything else in the toolkit reads that database: **simargl** embeds the tasks, **gamayun**
builds an ontology from them, **1bcoder** exports them for a glossary. mokosh is the only one
that writes it. No GPU, no LLM, no embeddings: git, HTTP and SQLite.

## Install

```
pip install mokosh               # gitpython, requests, beautifulsoup4, pyyaml, tqdm, flask, waitress
pip install mokosh[selenium]     # only for Jira instances that need a real browser
```

simargl and gamayun depend on it, so installing either of them installs mokosh too.

## Use

```
cd C:\Projects\Data\sonarqube
mokosh init                      # repository, branch, history since, tracker -> .mokosh/project.yaml
mokosh ingest                    # [1/2] commits   [2/2] task keys from messages + task details
mokosh status                    # tasks, commits, how many are linked to a task, date range
```

`ingest` is incremental. Run it again a month later and it adds only the commits whose SHA is not
in the database yet (new rows get IDs after the existing ones; old rows are never touched), links
only the commits that have no task yet, and asks the tracker only about the tasks that have no
details yet — so each task is fetched once, and a failed one is tried again on the next run. The
same makes an interrupted fetch of ten thousand tasks continue where it stopped. An empty answer of the
tracker (404, no access, a rate limit, a wrong owner/repo) is not stored as a task with no text: the
task stays unfetched and is asked again next time, and when the first 20 tasks all come back empty
the fetch stops and says which settings to check. What each run
added is kept in `.mokosh/ingest_state.yaml`. `--phase git|tasks` runs one phase; `--force`
links every commit to a task key again (after a change of `commit_mask`). History rewritten
upstream (rebase, force push) leaves the old commits in the database and adds the new ones.
Tokens never go into the file. `mokosh init` (and the Setup page of `mokosh ui`) put the token you enter into an
environment variable — `GITHUB_TOKEN`, `JIRA_TOKEN`, `GITLAB_TOKEN`, `YOUTRACK_TOKEN`, or
`MOKOSH_<PROJECT>_GITHUB_TOKEN` when the plain name already holds another token — and project.yaml keeps
only `github_token: ${GITHUB_TOKEN}`. On Windows the variable is saved for your user (`setx`; mokosh also
reads it from there in a terminal opened before); elsewhere init prints the `export` line for your shell
profile.

A monthly refresh is two commands; gamayun rebuilds from scratch in minutes:

```
mokosh ingest
gamayun index --db units.db --repo . --project sonar
```

Then build on it:

```
simargl index units units.db                              # semantic search over the tasks
gamayun index --db units.db --repo . --project sonar      # statistical ontology
```

Other commands:

```
mokosh plaintext units.db [--dry-run]     # clean tracker markup in a database from an older ingest
mokosh export units.db --out DIR          # the rows as text files, for /flow glossary (see below)
```

A folder set up earlier with `simargl init` (`.simargl/project.yaml`) is used as it is — no new
`init`, no ingest from scratch. (simargl no longer gathers anything: it only reads the database.)

### Web UI

```
mokosh ui                        # http://127.0.0.1:8093/   (--port, --host 0.0.0.0, --root FOLDER)
```

Flask + waitress, like the other tools of the toolkit (eight themes, six languages):

- **Setup** — the form `mokosh init` asks for: folder, database, repository and branch, tracker,
  the commit mask with every preset tried on the repository's last 300 commits. A token goes to an
  environment variable, as with `mokosh init`.
- **Ingest** — `mokosh ingest` (both phases, or git / tasks only; `--force`) with its log.
- **Status** — what the database holds, `project.yaml` (tokens masked), `ingest_state.yaml`.

Projects are the folders with `.mokosh/project.yaml` (or an earlier `.simargl/project.yaml`) under
the folder it was started in, and those remembered in `~/.mokosh/projects.json` (`mokosh init`
and the UI remember them).

### Export for a glossary

`/flow glossary` (1bcoder, vyrii) reads folders of text files, never SQLite; `mokosh export` writes
them:

```
mokosh export units.db --out DIR              # --all (default): DIR/tasks/ and DIR/commits/ apart
mokosh export units.db --out DIR --join       # one file per task with its commits (DIR/joined/)
mokosh export units.db --out DIR --task       # tasks only      (--commits: commits only)
mokosh export units.db --out DIR --level title      # TITLE | desc: + DESCRIPTION (default) | comments: + COMMENTS
mokosh export units.db --out DIR --diff-mode full   # the whole stored diff (default changed: only @/+/- lines)
```

`--join` writes each commit as its own `--- commit <sha> ---` block (message + per-file diff); a
task with no commit still gets its file, and commits with no task are counted, not guessed at.
`--all` keeps tasks and commit diffs as separate corpora, to be indexed as separate glossary
projects. `desc` is the default level: title + description beat title alone and the noisier
+comments in the retrieval experiments; `changed` drops the untouched context lines of a diff.

### Trackers

| `source` | Settings in `tasks:` |
|---|---|
| `jira` | `jira_url`, `jira_project`, `jira_connector: api \| html \| selenium`, `jira_token` |
| `github` | `github_owner`, `github_repo`, `github_token` — `mokosh init` asks for the repository URL `https://github.com/<USER>/<REPO>` and writes both names |
| `gitlab` | `gitlab_url`, `gitlab_project`, `gitlab_token` |
| `youtrack` | `youtrack_url`, `youtrack_project`, `youtrack_token` |

How a task key is found in a commit message is `commit_mask` in `tasks:`, for any tracker:

| `commit_mask` | Finds |
|---|---|
| `simple` (default) | `KAFKA-123` at the start of the message |
| `bracketed` | `[KAFKA-123]` anywhere |
| `django` | `Fixed #123` |
| `generic` | `fixes #123`, `closed: #123`, `resolves #123` |
| `broad` | any `#123` |
| `regex-key` | the project's own key anywhere, as a whole word: `Fixed: invoice total (OFBIZ-12345)`. Built from `jira_project` (or `youtrack_project`); a team that uses the keys of several projects writes them all: `jira_project: ABC,ABD,ABK` |
| any regex | your own pattern; the first group is the key, if there is one |

Own regexes, for example:

| regex | finds |
|---|---|
| `(OFBIZ-\d+)` | one project's key anywhere |
| `((?:ABC\|ABD\|ABK)-\d+)` | the keys of several projects anywhere |
| `\b((?:ABC\|ABD\|ABK)-\d+)\b` | the same, not inside a longer word (`XABC-1`) — what `regex-key` builds |

A pattern like `([A-Z]+-\d+)` (any key) also catches `UTF-8`, `SHA-256`, `ISO-8859`; name the keys.

`mokosh init` asks for it with any tracker. It prints this table, then tries every mask on the repository's
last 300 commits and offers the one that links the most:

```
On the last 300 commits of C:/Projects/Data/flink:
  simple     links    0 (0%)   -
  bracketed  links  282 (94%)   FLINK-40293, FLINK-38262, FLINK-40830
  ...
Commit mask [Enter = bracketed]:
```

`mokosh ui` (**Setup**) shows the same table and the same counts.

Without a tracker mokosh stores the commits only (`TASK` stays empty); simargl can still index
them as commit units.

### Table names

New databases have singular `TASK` and `COMMIT`. Older simargl ingests have `TASKS` / `COMMITS`,
and the codeXtract datasets (`sonar.db`, `flink.db`) mix them; `mokosh.schema` resolves whatever a
file has, and simargl and gamayun read through it.

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

## About

(c) 2026 Stanislav Zholobetskyi, Oleh Andriichuk  
Institute for Information Recording, National Academy of Sciences of Ukraine, Kyiv  
PhD research: «Intelligent Technology for Software Development and Maintenance Support»
