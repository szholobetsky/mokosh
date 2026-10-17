"""Tracker tokens live in environment variables, never in project.yaml.

project.yaml keeps a reference — `github_token: ${GITHUB_TOKEN}` — so the file can be shared or
committed. `mokosh init` (and simargl's Setup page) put the token entered into the variable:
on Windows it is saved for the user (`setx`, read back from the registry even by a terminal
opened before), elsewhere the wizard prints the `export` line for the shell profile.
"""
from __future__ import annotations

import os
import re
import subprocess

# the variable each tracker's token goes to (the names other tools use too, e.g. gh reads GITHUB_TOKEN)
ENV = {"jira_token": "JIRA_TOKEN", "github_token": "GITHUB_TOKEN",
       "youtrack_token": "YOUTRACK_TOKEN", "gitlab_token": "GITLAB_TOKEN"}

_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def is_token_key(key: str) -> bool:
    return key.endswith("_token")


def ref_name(value) -> str | None:
    """'${VAR}' -> 'VAR', anything else -> None."""
    m = _REF.match(value.strip()) if isinstance(value, str) else None
    return m.group(1) if m else None


def _user_env(name: str) -> str:
    """A user variable saved by setx: a terminal opened before it does not have it in its environment."""
    if os.name != "nt":
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return ""


def lookup(name: str) -> str:
    return os.environ.get(name) or _user_env(name)


def resolve(value) -> str:
    """'${VAR}' -> the variable's value; any other value as it is."""
    name = ref_name(value)
    if name:
        return lookup(name)
    return value or ""


def _var_for(key: str, value: str, project: str) -> str:
    """GITHUB_TOKEN, unless it already holds another token: then one for this project only."""
    var = ENV.get(key) or key.upper()
    have = lookup(var)
    if have and have != value:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", project or "project").strip("_").upper() or "PROJECT"
        var = f"MOKOSH_{slug}_{var}"
    return var


def store(key: str, value: str, project: str = "") -> tuple[str, str]:
    """Put a token into an environment variable -> ('${VAR}' for project.yaml, what was done, for the user).
    A value that is already a reference is kept as it is."""
    value = (value or "").strip()
    if not value or ref_name(value):
        return value, ""
    var = _var_for(key, value, project)
    os.environ[var] = value                       # this process (an ingest started from it) sees it at once
    if os.name == "nt":
        r = subprocess.run(["setx", var, value], capture_output=True, text=True)
        if r.returncode == 0:
            return "${" + var + "}", f"{key}: saved in the user environment variable {var} (not in project.yaml)"
        return "${" + var + "}", (f"{key}: could not save {var} (setx: {(r.stderr or r.stdout).strip()}) — "
                                  f"set it yourself: setx {var} <token>")
    return "${" + var + "}", (f"{key}: project.yaml refers to ${{{var}}}; add to your shell profile: "
                              f"export {var}=<the token>")


def secure(tasks_cfg: dict, project: str = "") -> list[str]:
    """Every token written into tasks_cfg -> an environment variable; tasks_cfg keeps the references."""
    notes = []
    for k, v in list(tasks_cfg.items()):
        if is_token_key(k) and isinstance(v, str) and v.strip() and not ref_name(v):
            tasks_cfg[k], note = store(k, v, project)
            if note:
                notes.append(note)
    return notes
