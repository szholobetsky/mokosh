"""Tracker text -> plain text.

Jira Cloud (REST API v3) returns descriptions and comment bodies as ADF —
Atlassian Document Format, a JSON tree. Stored with str() it becomes the Python
repr of that tree: node names ('paragraph', 'hardBreak', 'mediaSingle'), attrs
and ids end up in embeddings as if they were words of the task. plain_text()
keeps only what a reader sees.

Accepted input: None, an ADF dict, a list of them (comments), their JSON or
Python repr as a string, or ordinary text (returned unchanged). The repr is read
with json.loads / ast.literal_eval — never eval.
"""
from __future__ import annotations

import ast
import json
import re

_BLOCK = {"doc", "paragraph", "heading", "blockquote", "codeBlock", "bulletList", "orderedList",
          "table", "tableRow", "panel", "rule", "mediaSingle", "mediaGroup", "expand",
          "nestedExpand", "decisionList", "decisionItem", "taskList", "taskItem", "layoutSection",
          "layoutColumn", "blockCard", "embedCard"}
_CELL = {"tableCell", "tableHeader"}
_EMPTY = {"", "None", "null", "[]", "{}"}
_TEXT_LEAF = re.compile(r"""['"]text['"]:\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")


def _inline(node: dict) -> str:
    t, attrs = node.get("type"), node.get("attrs") or {}
    if t == "text":
        return node.get("text") or ""
    if t == "hardBreak":
        return "\n"
    if t in ("mention", "status", "emoji"):
        return attrs.get("text") or attrs.get("shortName") or ""
    if t in ("inlineCard", "blockCard", "embedCard"):
        return attrs.get("url") or ""
    if t == "date":
        return str(attrs.get("timestamp") or "")
    return ""


def adf_to_text(tree) -> str:
    """ADF tree (or a list of trees) -> text: blocks on their own lines, table
    cells joined with ' | '. Iterative — deep documents do not hit the recursion limit."""
    if isinstance(tree, list) and tree and all(isinstance(x, dict) and x.get("type") == "doc" for x in tree):
        return "\n\n".join(t for t in map(adf_to_text, tree) if t)          # comments: one doc each
    out: list[str] = []
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, str):                 # markers pushed below
            out.append(node)
            continue
        if isinstance(node, list):
            stack.extend(reversed(node))
            continue
        if not isinstance(node, dict):
            continue
        t = node.get("type")
        if t in _CELL:                            # a cell is one line, whatever blocks it holds
            out.append(" ".join(adf_to_text({"type": "cell", "content": node.get("content")}).split()) + " | ")
            continue
        if t in _BLOCK:
            stack.append("\n")
        text = _inline(node)
        if text:
            out.append(text)
        if t == "listItem":
            out.append("- ")
        stack.extend(reversed(node.get("content") or []))
    lines = [" ".join(ln.split()).removesuffix(" |") for ln in "".join(out).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def plain_text(value) -> str:
    """Anything a tracker gives back -> plain text."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return adf_to_text(value)
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    s = str(value)
    head = s.lstrip()[:200]
    if s.strip() in _EMPTY:
        return ""
    if head[:1] in "{[" and ("'type'" in head or '"type"' in head):
        for parse in (json.loads, ast.literal_eval):
            try:
                return adf_to_text(parse(s.strip()))
            except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
                continue
        return " ".join(a or b for a, b in _TEXT_LEAF.findall(s))   # broken repr: text leaves only
    return s


def needs_cleaning(value) -> bool:
    if value is None:
        return False
    s = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    head = s.lstrip()[:200]
    return s.strip() in _EMPTY - {""} or (head[:1] in "{[" and ("'type'" in head or '"type"' in head))
