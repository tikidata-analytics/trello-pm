#!/usr/bin/env python3
"""
Trello PM Synchronization Engine
Zero-dependency Python 3 standard library engine that synchronizes local
repositories in a workspace `repos/` directory with a Trello Kanban board:

- Repo present in `repos/`  -> Move card to Doing (or create new card in Doing)
- Repo removed from `repos/` -> Move card from Doing/Testing to Done
- Exact card name matching  -> `card.name.strip() == repo_name`
- Card description (`desc`) -> Macro summary of active branch & recent commits
- Card comments             -> Idempotent per-commit log with change rationale (`--why`)
- `#futureimprovements`     -> Full-card scan (description, all comments including
                               Trello-escaped `\\#futureimprovements`, and checklists)
                               when a card moves to Doing, cleanly separating
                               NOT DONE vs DONE items and prompting for confirmation.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_DOING_LIST = "Doing"
DEFAULT_DONE_LIST = "Done"
DEFAULT_TESTING_LIST = "Testing"

# Matches #futureimprovements and Trello's markdown-escaped \#futureimprovements
# Negative lookahead prevents matching #futureimprovements-done as an open item.
FUTURE_IMPROVEMENT_TAG_RE = re.compile(
    r"(?:\\)?#futureimprovements(?![-\w])\s*:?",
    re.IGNORECASE,
)

# Matches audit-trail resolution comments: #futureimprovements-done
FUTURE_IMPROVEMENT_DONE_TAG_RE = re.compile(
    r"(?:\\)?#futureimprovements-done(?![-\w])",
    re.IGNORECASE,
)

# Matches reference IDs inside resolution comments, e.g. [ref:6affa782] or ref:desc-12ab34cd
REF_ID_RE = re.compile(r"\[?ref\s*:\s*([a-zA-Z0-9_-]+)\]?", re.IGNORECASE)

# Inline completion markers inside description or comment items
INLINE_DONE_MARKERS_RE = re.compile(
    r"(?:\[\s*x\s*\]|\[done\]|\(done\)|status\s*:\s*done|(?:\\)?#done(?![-\w])|✅)",
    re.IGNORECASE,
)

BULLET_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")
CHECKBOX_LINE_RE = re.compile(r"^\s*(?:[-*•]\s+)?\[([ xX])\]\s+(.*)$")


def load_dotenv_file(dotenv_path):
    """Lightweight stdlib .env loader (never overrides already-set OS env vars)."""
    if not dotenv_path or not os.path.isfile(dotenv_path):
        return
    try:
        with open(dotenv_path, "r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
    except OSError as exc:
        print(f"[WARN] Could not read .env file {dotenv_path}: {exc}", file=sys.stderr)


def sanitize_url(url):
    """Redact `key` and `token` query parameters from URLs before logging errors."""
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted_pairs = [
        (k, "***" if k.lower() in ("key", "token") else v)
        for k, v in query_pairs
    ]
    new_query = urllib.parse.urlencode(redacted_pairs)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment)
    )


def normalize_board_id(raw_board_id):
    """Extract a Trello short board ID from either a raw ID or full board URL."""
    val = (raw_board_id or "").strip()
    if not val:
        return None
    url_match = re.search(r"trello\.com/b/([a-zA-Z0-9]+)", val)
    if url_match:
        return url_match.group(1)
    return val.strip("/") or None


def get_env_config(workspace_root=None):
    """Load Trello credentials and list names from environment variables or .env."""
    candidate_envs = [os.path.join(os.getcwd(), ".env")]
    if workspace_root:
        candidate_envs.append(os.path.join(workspace_root, ".env"))
    script_skill_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    candidate_envs.append(os.path.join(script_skill_root, ".env"))

    seen = set()
    for env_path in candidate_envs:
        norm = os.path.abspath(env_path)
        if norm not in seen:
            seen.add(norm)
            load_dotenv_file(norm)

    api_key = (os.environ.get("TRELLO_API_KEY") or "").strip() or None
    token = (os.environ.get("TRELLO_TOKEN") or "").strip() or None
    board_id = normalize_board_id(os.environ.get("TRELLO_BOARD_ID"))
    doing_list = (os.environ.get("TRELLO_DOING_LIST") or DEFAULT_DOING_LIST).strip()
    done_list = (os.environ.get("TRELLO_DONE_LIST") or DEFAULT_DONE_LIST).strip()
    testing_list = (os.environ.get("TRELLO_TESTING_LIST") or DEFAULT_TESTING_LIST).strip()

    return {
        "api_key": api_key,
        "token": token,
        "board_id": board_id,
        "doing_list": doing_list,
        "done_list": done_list,
        "testing_list": testing_list,
    }


def trello_api_request(url, method="GET", data=None, timeout=15):
    """Execute an HTTP request against the Trello REST API with redacted error logs."""
    encoded_data = None
    headers = {"Accept": "application/json", "User-Agent": "trello-pm/1.0"}
    if data is not None:
        encoded_data = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    safe_url = sanitize_url(url)
    req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as err:
        err_msg = err.read().decode("utf-8", "ignore").strip()
        raise RuntimeError(f"Trello API HTTP {err.code} on {method} {safe_url}: {err_msg}") from err
    except Exception as err:
        raise RuntimeError(f"Network error on Trello API {method} {safe_url}: {err}") from err


def resolve_board_lists(
    board_id,
    api_key,
    token,
    doing_name=DEFAULT_DOING_LIST,
    done_name=DEFAULT_DONE_LIST,
    testing_name=DEFAULT_TESTING_LIST,
):
    """Fetch board lists and resolve Doing, Done, and optional Testing list IDs."""
    url = f"https://api.trello.com/1/boards/{board_id}/lists?key={api_key}&token={token}"
    lists = trello_api_request(url, method="GET")
    list_map = {}
    id_to_name = {}

    for item in lists:
        name_lower = item["name"].strip().lower()
        list_map[name_lower] = item["id"]
        id_to_name[item["id"]] = item["name"]

    doing_id = list_map.get(doing_name.strip().lower())
    done_id = list_map.get(done_name.strip().lower())
    testing_id = list_map.get(testing_name.strip().lower()) if testing_name else None

    if not doing_id:
        available = ", ".join(id_to_name.values()) or "(none)"
        raise RuntimeError(
            f"List '{doing_name}' not found on board '{board_id}'. Available lists: {available}"
        )
    if not done_id:
        available = ", ".join(id_to_name.values()) or "(none)"
        raise RuntimeError(
            f"List '{done_name}' not found on board '{board_id}'. Available lists: {available}"
        )

    return {
        "doing_id": doing_id,
        "done_id": done_id,
        "testing_id": testing_id,
        "doing_name": id_to_name[doing_id],
        "done_name": id_to_name[done_id],
        "list_map": list_map,
        "id_to_name": id_to_name,
    }


def get_local_repos(repos_dir):
    """Scan `repos_dir` and return sorted non-hidden subdirectory names."""
    if not os.path.exists(repos_dir):
        return []
    items = []
    for entry in os.listdir(repos_dir):
        full_path = os.path.join(repos_dir, entry)
        if os.path.isdir(full_path) and not entry.startswith("."):
            items.append(entry.strip())
    return sorted(items)


def get_git_repo_metadata(repo_path):
    """Extract branch, latest commit, total commit count, and 10 recent commits."""
    if not os.path.exists(os.path.join(repo_path, ".git")):
        return None

    try:
        branch = subprocess.check_output(
            ["git", "-C", repo_path, "branch", "--show-current"],
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
        ).strip() or "main"

        latest_raw = subprocess.check_output(
            ["git", "-C", repo_path, "log", "-n", "1", "--format=%h|%s|%an|%cI"],
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
        ).strip()
        parts = latest_raw.split("|", 3)
        short_hash = parts[0] if len(parts) > 0 else "-"
        subject = parts[1] if len(parts) > 1 else "-"
        author = parts[2] if len(parts) > 2 else "-"
        iso_date = parts[3] if len(parts) > 3 else "-"

        total_commits = subprocess.check_output(
            ["git", "-C", repo_path, "rev-list", "--count", "HEAD"],
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
        ).strip()

        history_raw = subprocess.check_output(
            ["git", "-C", repo_path, "log", "-n", "10", "--format=%h|%s|%as"],
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
        ).strip().splitlines()

        recent_commits = []
        for line in history_raw:
            if "|" in line:
                h, s, d = line.split("|", 2)
                recent_commits.append({"hash": h, "subject": s, "date": d})

        return {
            "branch": branch,
            "short_hash": short_hash,
            "subject": subject,
            "author": author,
            "iso_date": iso_date,
            "total_commits": total_commits,
            "recent_commits": recent_commits,
        }
    except Exception as err:
        print(f"[WARN] Failed to read git metadata from {repo_path}: {err}", file=sys.stderr)
        return None


def format_card_description(repo_name, git_meta):
    """Build the Trello card description summarizing active branch and commit history."""
    if not git_meta:
        return f"### 📦 Repository: `{repo_name}`\n\nActive repository tracked in `repos/`."

    lines = [
        f"### 📦 Repository Status: `{repo_name}`",
        f"- **Active Branch**: `{git_meta['branch']}`",
        f"- **Latest Commit**: `{git_meta['short_hash']}` — {git_meta['subject']}",
        f"- **Author / Date**: {git_meta['author']} ({git_meta['iso_date'][:10]})",
        f"- **Total Commits**: `{git_meta['total_commits']}`",
        "",
        "#### 📜 Recent Commit History",
        "| Hash | Commit Message | Date |",
        "| :--- | :--- | :--- |",
    ]

    for commit in git_meta["recent_commits"]:
        clean_subj = commit["subject"].replace("|", "/")
        lines.append(f"| `{commit['hash']}` | {clean_subj} | {commit['date']} |")

    lines.append("")
    lines.append("*(Description updated automatically by `trello-pm`)*")
    return "\n".join(lines)


def _has_inline_done_marker(text):
    """Return True if an item contains an inline completion marker."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if INLINE_DONE_MARKERS_RE.search(stripped):
        return True
    if stripped.startswith("~~") and stripped.endswith("~~") and len(stripped) > 4:
        return True
    return False


def _clean_improvement_text(text):
    """Remove leading #futureimprovements / \\#futureimprovements tag for display."""
    cleaned = FUTURE_IMPROVEMENT_TAG_RE.sub("", text or "", count=1).strip()
    cleaned = re.sub(r"^[:\-–—\s]+", "", cleaned).strip()
    return cleaned


def _stable_desc_id(text):
    """Generate a deterministic short ID for items found in card descriptions."""
    norm = re.sub(r"\s+", " ", (text or "").strip().lower())
    return f"desc-{hashlib.sha1(norm.encode('utf-8')).hexdigest()[:8]}"


def _extract_resolved_refs(comments):
    """Collect all reference IDs marked complete via `#futureimprovements-done` comments."""
    resolved = {}
    for act in comments:
        text = (act.get("data") or {}).get("text", "")
        if not FUTURE_IMPROVEMENT_DONE_TAG_RE.search(text):
            continue
        act_date = (act.get("date") or "")[:10]
        for match in REF_ID_RE.finditer(text):
            ref_id = match.group(1).strip().lower()
            if ref_id:
                resolved[ref_id] = {
                    "date": act_date,
                    "comment_id": (act.get("id") or "")[:8],
                }
    return resolved


def _extract_items_from_tagged_block(raw_text, base_id, source, item_date, resolved_refs):
    """Extract one or more `#futureimprovements` entries from a comment or description block."""
    items = []
    lines = [ln.rstrip() for ln in (raw_text or "").splitlines()]

    body_lines = []
    tag_found = False
    for ln in lines:
        if not tag_found:
            if FUTURE_IMPROVEMENT_TAG_RE.search(ln):
                tag_found = True
                remainder = _clean_improvement_text(ln)
                if remainder:
                    body_lines.append(remainder)
        else:
            body_lines.append(ln)

    if not tag_found:
        return items

    non_empty_body = [ln for ln in body_lines if ln.strip()]
    list_entries = []
    for ln in non_empty_body:
        cb_match = CHECKBOX_LINE_RE.match(ln)
        if cb_match:
            list_entries.append((cb_match.group(1).lower() == "x", cb_match.group(2).strip(), ln))
            continue
        bl_match = BULLET_LINE_RE.match(ln)
        if bl_match:
            list_entries.append((False, bl_match.group(1).strip(), ln))

    has_checkbox = any(CHECKBOX_LINE_RE.match(ln) for ln in non_empty_body)
    if len(list_entries) >= 2 or (len(list_entries) == 1 and has_checkbox and len(non_empty_body) == 1):
        for idx, (is_checked, item_text, raw_line) in enumerate(list_entries, 1):
            item_id = f"{base_id}-{idx}" if base_id else _stable_desc_id(item_text)
            lookup_keys = [item_id.lower()]
            if base_id:
                lookup_keys.append(base_id.lower())

            done_reason = None
            if is_checked:
                done_reason = "inline checkbox [x]"
            elif _has_inline_done_marker(raw_line):
                done_reason = "inline DONE marker"
            else:
                for key in lookup_keys:
                    if key in resolved_refs:
                        ref_info = resolved_refs[key]
                        done_reason = f"resolved via comment ({ref_info['date'] or 'recorded'})"
                        break

            items.append({
                "id": item_id,
                "source": source,
                "text": item_text,
                "date": item_date,
                "status": "DONE" if done_reason else "NOT DONE",
                "done_reason": done_reason,
            })
        return items

    combined = " ".join(ln.strip() for ln in non_empty_body).strip() or _clean_improvement_text(raw_text)
    if not combined:
        return items

    item_id = base_id if base_id else _stable_desc_id(combined)
    done_reason = None
    if _has_inline_done_marker(raw_text):
        done_reason = "inline DONE marker"
    elif item_id.lower() in resolved_refs:
        ref_info = resolved_refs[item_id.lower()]
        done_reason = f"resolved via comment ({ref_info['date'] or 'recorded'})"

    items.append({
        "id": item_id,
        "source": source,
        "text": combined,
        "date": item_date,
        "status": "DONE" if done_reason else "NOT DONE",
        "done_reason": done_reason,
    })
    return items


def parse_future_improvements(card_id, card_name, desc="", comments=None, checklists=None):
    """Scan card description, all comments, and all checklists for `#futureimprovements`.

    Returns a structured report separating NOT DONE and DONE items.
    """
    comments = comments or []
    checklists = checklists or []
    resolved_refs = _extract_resolved_refs(comments)

    not_done = []
    done = []

    def _classify(entry):
        if entry["status"] == "DONE":
            done.append(entry)
        else:
            not_done.append(entry)

    # 1. Card description
    if desc and FUTURE_IMPROVEMENT_TAG_RE.search(desc):
        desc_lines = desc.splitlines()
        capturing = False
        block_lines = []
        blocks = []
        for ln in desc_lines:
            if FUTURE_IMPROVEMENT_TAG_RE.search(ln) and not FUTURE_IMPROVEMENT_DONE_TAG_RE.search(ln):
                if block_lines:
                    blocks.append("\n".join(block_lines))
                    block_lines = []
                capturing = True
                block_lines.append(ln)
            elif capturing:
                if re.match(r"^\s*#{1,6}\s+", ln) and not FUTURE_IMPROVEMENT_TAG_RE.search(ln):
                    blocks.append("\n".join(block_lines))
                    block_lines = []
                    capturing = False
                else:
                    block_lines.append(ln)
        if block_lines:
            blocks.append("\n".join(block_lines))

        for blk in blocks:
            for item in _extract_items_from_tagged_block(blk, None, "description", "", resolved_refs):
                _classify(item)

    # 2. All card comments (oldest -> newest for chronological readability)
    for act in reversed(comments):
        text = (act.get("data") or {}).get("text", "")
        if not text:
            continue
        if FUTURE_IMPROVEMENT_DONE_TAG_RE.search(text) and not FUTURE_IMPROVEMENT_TAG_RE.search(text):
            continue
        if FUTURE_IMPROVEMENT_TAG_RE.search(text):
            act_id = (act.get("id") or "")[:8]
            act_date = (act.get("date") or "")[:10]
            for item in _extract_items_from_tagged_block(text, act_id, "comment", act_date, resolved_refs):
                _classify(item)

    # 3. All card checklists
    for checklist in checklists:
        cl_name = checklist.get("name", "")
        cl_is_fi = bool(
            FUTURE_IMPROVEMENT_TAG_RE.search(cl_name)
            or "futureimprovements" in cl_name.lower().replace(" ", "")
        )
        for check_item in checklist.get("checkItems", []):
            ci_name = check_item.get("name", "").strip()
            if not cl_is_fi and not FUTURE_IMPROVEMENT_TAG_RE.search(ci_name):
                continue
            ci_full_id = check_item.get("id", "")
            ci_short_id = f"chk-{ci_full_id[:8]}" if ci_full_id else _stable_desc_id(ci_name)
            is_complete = check_item.get("state", "").lower() == "complete"
            done_reason = None
            if is_complete:
                done_reason = "checklist complete"
            elif _has_inline_done_marker(ci_name):
                done_reason = "inline DONE marker"
            elif ci_short_id.lower() in resolved_refs:
                done_reason = (
                    f"resolved via comment ({resolved_refs[ci_short_id.lower()]['date'] or 'recorded'})"
                )

            _classify({
                "id": ci_short_id,
                "check_item_id": ci_full_id,
                "checklist_name": cl_name,
                "source": "checklist",
                "text": _clean_improvement_text(ci_name) if FUTURE_IMPROVEMENT_TAG_RE.search(ci_name) else ci_name,
                "date": "",
                "status": "DONE" if done_reason else "NOT DONE",
                "done_reason": done_reason,
            })

    return {
        "card_id": card_id,
        "card_name": card_name,
        "total": len(not_done) + len(done),
        "not_done": not_done,
        "done": done,
    }


def scan_card_future_improvements(card, api_key, token, existing_comments=None):
    """Fetch comments & checklists for a card and parse `#futureimprovements`."""
    card_id = card["id"]
    card_name = card["name"].strip()
    desc = card.get("desc", "")

    if existing_comments is None:
        actions_url = (
            f"https://api.trello.com/1/cards/{card_id}/actions"
            f"?filter=commentCard&limit=1000&key={api_key}&token={token}"
        )
        comments = trello_api_request(actions_url, method="GET")
    else:
        comments = existing_comments

    checklists_url = f"https://api.trello.com/1/cards/{card_id}/checklists?key={api_key}&token={token}"
    checklists = trello_api_request(checklists_url, method="GET")

    return parse_future_improvements(
        card_id=card_id,
        card_name=card_name,
        desc=desc,
        comments=comments,
        checklists=checklists,
    )


def print_future_improvements_report(report):
    """Print separated NOT DONE vs DONE `#futureimprovements` and confirmation prompt."""
    if report["total"] == 0:
        return

    card_name = report["card_name"]
    not_done = report["not_done"]
    done = report["done"]

    print(" ------------------------------------------------------------")
    print(
        f" 🔍 #futureimprovements SCAN — '{card_name}' "
        f"(Total: {report['total']} | NOT DONE: {len(not_done)} | DONE: {len(done)})"
    )
    print(" ------------------------------------------------------------")

    if not_done:
        print(f" 🔴 NOT DONE #futureimprovements ({len(not_done)}):")
        for idx, item in enumerate(not_done, 1):
            date_part = f" | {item['date']}" if item.get("date") else ""
            print(f"    {idx}. [{item['id']}] ({item['source']}{date_part}) {item['text']}")
    else:
        print(" 🔴 NOT DONE #futureimprovements (0): (None)")

    if done:
        print(f" ✅ DONE #futureimprovements ({len(done)}):")
        for idx, item in enumerate(done, 1):
            reason_part = f" — {item['done_reason']}" if item.get("done_reason") else ""
            print(f"    {idx}. [{item['id']}] ({item['source']}{reason_part}) {item['text']}")

    if not_done:
        print(" ⚠️  [ACTION REQUIRED — USER CONFIRMATION]")
        print(f"     Found {len(not_done)} unfinished #futureimprovements item(s) on '{card_name}'.")
        print("     Remind the user and CONFIRM whether to work on them now or skip them.")
        print(
            f"     (When finished, mark DONE via: --resolve-improvement <id> --card {card_name})"
        )
    else:
        print(
            f" ℹ️  All {len(done)} #futureimprovements item(s) on '{card_name}' are already DONE "
            "(no confirmation needed)."
        )
    print(" ------------------------------------------------------------")


def resolve_future_improvement(card, improvement_ids, git_meta, why_reasoning, api_key, token, dry_run=False):
    """Mark one or more `#futureimprovements` items DONE (checklist complete or audit-trail comment)."""
    report = scan_card_future_improvements(card, api_key, token)
    card_id = card["id"]
    card_name = card["name"].strip()

    all_items_by_id = {item["id"].lower(): item for item in (report["not_done"] + report["done"])}
    done_ids = {item["id"].lower() for item in report["done"]}

    resolved_count = 0
    for raw_id in improvement_ids:
        target_id = raw_id.strip()
        if not target_id:
            continue
        key = target_id.lower()

        if key in done_ids:
            print(f" [FI-RESOLVE] Item '{target_id}' on '{card_name}' is already DONE. Skipping.")
            continue

        matched = all_items_by_id.get(key)
        if matched and matched.get("source") == "checklist" and matched.get("check_item_id"):
            print(f" [FI-RESOLVE] Completing checklist item '{matched['id']}' on '{card_name}'...")
            if not dry_run:
                chk_url = (
                    f"https://api.trello.com/1/cards/{card_id}/checkItem/{matched['check_item_id']}"
                    f"?key={api_key}&token={token}"
                )
                trello_api_request(chk_url, method="PUT", data={"state": "complete"})
            resolved_count += 1
            continue

        note_lines = [f"✅ **#futureimprovements-done** `[ref:{target_id}]`"]
        if matched:
            snippet = matched["text"][:200] + ("..." if len(matched["text"]) > 200 else "")
            note_lines.append(f"📌 **Item**: {snippet}")
        if git_meta:
            note_lines.append(
                f"🔖 **Commit**: `{git_meta['short_hash']}` (`{git_meta['branch']}`) — {git_meta['subject']}"
            )
        if why_reasoning and why_reasoning.strip():
            note_lines.append(f"💡 **Resolution Note**: {why_reasoning.strip()}")

        comment_body = "\n".join(note_lines)
        print(f" [FI-RESOLVE] Marking #futureimprovements '[ref:{target_id}]' DONE on '{card_name}'...")
        if not dry_run:
            comment_url = f"https://api.trello.com/1/cards/{card_id}/actions/comments?key={api_key}&token={token}"
            trello_api_request(comment_url, method="POST", data={"text": comment_body})
        resolved_count += 1

    return resolved_count


def post_commit_comment_if_new(
    card_id,
    git_meta,
    why_reasoning,
    api_key,
    token,
    dry_run=False,
    existing_comments=None,
):
    """Post a commit activity comment with rationale if the commit hash is not yet recorded."""
    if not git_meta:
        return False

    short_hash = git_meta["short_hash"]

    if existing_comments is None:
        actions_url = (
            f"https://api.trello.com/1/cards/{card_id}/actions"
            f"?filter=commentCard&limit=1000&key={api_key}&token={token}"
        )
        existing_actions = trello_api_request(actions_url, method="GET")
    else:
        existing_actions = existing_comments

    for act in existing_actions:
        comment_text = (act.get("data") or {}).get("text", "")
        if f"`{short_hash}`" in comment_text or short_hash in comment_text:
            print(f" [COMMENT] Commit '{short_hash}' already logged on card. Skipping.")
            return False

    reasoning_text = why_reasoning.strip() if why_reasoning else "No specific rationale provided."
    comment_lines = [
        f"🚀 **New Commit Recorded**: `{short_hash}` (`{git_meta['branch']}`)",
        f"📝 **Message**: {git_meta['subject']}",
        f"👤 **Author**: {git_meta['author']} | 🕒 {git_meta['iso_date']}",
        "",
        "💡 **Why This Change Was Needed (Reasoning)**:",
        f"> {reasoning_text}",
    ]
    full_comment = "\n".join(comment_lines)

    print(f" [COMMENT -> CARD] Posting commit '{short_hash}' and reasoning to card...")
    if not dry_run:
        comment_url = f"https://api.trello.com/1/cards/{card_id}/actions/comments?key={api_key}&token={token}"
        trello_api_request(comment_url, method="POST", data={"text": full_comment})
    return True


def sync_trello_state(
    repos_dir,
    board_id=None,
    why_reasoning=None,
    dry_run=False,
    scan_improvements=False,
    target_card_name=None,
    resolve_improvements=None,
    workspace_root=None,
):
    """Synchronize local `repos/` directory state with the target Trello board."""
    cfg = get_env_config(workspace_root=workspace_root)
    api_key = cfg["api_key"]
    token = cfg["token"]
    target_board = normalize_board_id(board_id) if board_id else cfg["board_id"]

    if not api_key or not token:
        print(
            "[ERROR] Missing TRELLO_API_KEY or TRELLO_TOKEN. "
            "Set them in environment variables or a .env file (see .env.example).",
            file=sys.stderr,
        )
        return False

    if not target_board:
        print(
            "[ERROR] Missing TRELLO_BOARD_ID. "
            "Set TRELLO_BOARD_ID in your environment/.env or pass --board-id <BOARD_ID_OR_URL>.",
            file=sys.stderr,
        )
        return False

    board_info = resolve_board_lists(
        target_board,
        api_key,
        token,
        doing_name=cfg["doing_list"],
        done_name=cfg["done_list"],
        testing_name=cfg["testing_list"],
    )
    doing_id = board_info["doing_id"]
    done_id = board_info["done_id"]
    testing_id = board_info["testing_id"]
    doing_label = board_info["doing_name"]
    done_label = board_info["done_name"]
    id_to_name = board_info["id_to_name"]

    cards_url = f"https://api.trello.com/1/boards/{target_board}/cards?key={api_key}&token={token}"
    cards = trello_api_request(cards_url, method="GET")

    card_by_name = {c["name"].strip(): c for c in cards}

    # Read-only scan mode
    if scan_improvements and not resolve_improvements:
        if target_card_name:
            if target_card_name not in card_by_name:
                print(
                    f"[ERROR] Card '{target_card_name}' not found on board '{target_board}'.",
                    file=sys.stderr,
                )
                return False
            selected = [card_by_name[target_card_name]]
        else:
            selected = [c for c in cards if c["idList"] == doing_id]

        print("============================================================")
        print(" 🔍 TRELLO PM — #futureimprovements SCAN")
        print("============================================================")
        print(f" Board ID      : {target_board}")
        print(f" Target Cards  : {', '.join(c['name'].strip() for c in selected) if selected else '(None)'}")
        print("============================================================")
        for card in selected:
            report = scan_card_future_improvements(card, api_key, token)
            if report["total"] > 0:
                print_future_improvements_report(report)
            else:
                print(f" [OK] No #futureimprovements found on card '{card['name'].strip()}'.")
        if not selected:
            print(f" [INFO] No active cards in '{doing_label}' to scan.")
        print("============================================================\n")
        return True

    local_repos = get_local_repos(repos_dir)
    local_repos_set = set(local_repos)
    actions_taken = 0

    print("============================================================")
    print(" 🚀 TRELLO PM SYNCHRONIZATION")
    print("============================================================")
    print(f" Board ID      : {target_board}")
    print(f" Repos Folder  : {repos_dir}")
    print(f" Active Repos  : {', '.join(local_repos) if local_repos else '(Empty)'}")
    if why_reasoning:
        print(f" Reasoning     : {why_reasoning}")
    print("============================================================")

    if resolve_improvements:
        resolve_targets = [target_card_name] if target_card_name else local_repos
        for rname in resolve_targets:
            if rname not in card_by_name:
                print(
                    f" [WARN] Card '{rname}' not found for --resolve-improvement.",
                    file=sys.stderr,
                )
                continue
            rpath = os.path.join(repos_dir, rname)
            gmeta = get_git_repo_metadata(rpath) if os.path.isdir(rpath) else None
            actions_taken += resolve_future_improvement(
                card_by_name[rname],
                resolve_improvements,
                gmeta,
                why_reasoning,
                api_key,
                token,
                dry_run=dry_run,
            )

    # RULE 1: Repo exists in repos/ -> Ensure card is in Doing, scan #futureimprovements on move,
    #         update description & post commit comment if new
    for repo_name in local_repos:
        repo_path = os.path.join(repos_dir, repo_name)
        git_meta = get_git_repo_metadata(repo_path)
        expected_desc = format_card_description(repo_name, git_meta)

        if repo_name in card_by_name:
            card = card_by_name[repo_name]
            card_id = card["id"]
            current_list_id = card["idList"]
            current_list_name = id_to_name.get(current_list_id, current_list_id)
            moved_to_doing = current_list_id != doing_id

            actions_url = (
                f"https://api.trello.com/1/cards/{card_id}/actions"
                f"?filter=commentCard&limit=1000&key={api_key}&token={token}"
            )
            card_comments = trello_api_request(actions_url, method="GET")

            if moved_to_doing:
                print(
                    f" [MOVE -> {doing_label.upper()}] Repo '{repo_name}' is active. "
                    f"Moving card from '{current_list_name}' to '{doing_label}'..."
                )
                if not dry_run:
                    put_url = f"https://api.trello.com/1/cards/{card_id}?key={api_key}&token={token}"
                    trello_api_request(put_url, method="PUT", data={"idList": doing_id})
                actions_taken += 1

                # Always scan entire card when moving into Doing
                fi_report = scan_card_future_improvements(
                    card, api_key, token, existing_comments=card_comments
                )
                print_future_improvements_report(fi_report)
            else:
                print(f" [OK] Repo '{repo_name}' is already in '{doing_label}'.")

            if card.get("desc", "").strip() != expected_desc.strip():
                print(f" [UPDATE DESC] Updating card description for '{repo_name}' with commit summary...")
                if not dry_run:
                    put_desc_url = f"https://api.trello.com/1/cards/{card_id}?key={api_key}&token={token}"
                    trello_api_request(put_desc_url, method="PUT", data={"desc": expected_desc})
                actions_taken += 1

            if git_meta:
                comment_posted = post_commit_comment_if_new(
                    card_id,
                    git_meta,
                    why_reasoning,
                    api_key,
                    token,
                    dry_run=dry_run,
                    existing_comments=card_comments,
                )
                if comment_posted:
                    actions_taken += 1
        else:
            print(
                f" [CREATE -> {doing_label.upper()}] No Trello card for '{repo_name}'. "
                f"Creating new card in '{doing_label}'..."
            )
            if not dry_run:
                post_url = f"https://api.trello.com/1/cards?key={api_key}&token={token}"
                new_card = trello_api_request(
                    post_url,
                    method="POST",
                    data={
                        "idList": doing_id,
                        "name": repo_name,
                        "desc": expected_desc,
                    },
                )
                card_by_name[repo_name] = new_card
                if git_meta:
                    post_commit_comment_if_new(
                        new_card["id"],
                        git_meta,
                        why_reasoning,
                        api_key,
                        token,
                        dry_run=False,
                        existing_comments=[],
                    )
            actions_taken += 1

    # RULE 2: Card is in Doing/Testing but repo directory no longer exists -> Move to Done
    for card_name, card in card_by_name.items():
        current_list_id = card["idList"]
        if current_list_id == doing_id or (testing_id and current_list_id == testing_id):
            if card_name not in local_repos_set:
                print(
                    f" [MOVE -> {done_label.upper()}] Folder '{card_name}' removed from repos/. "
                    f"Moving card to '{done_label}'..."
                )
                if not dry_run:
                    put_url = f"https://api.trello.com/1/cards/{card['id']}?key={api_key}&token={token}"
                    trello_api_request(put_url, method="PUT", data={"idList": done_id})
                actions_taken += 1

    print("------------------------------------------------------------")
    if dry_run:
        print(f" [DRY RUN COMPLETE] {actions_taken} action(s) simulated.")
    else:
        print(f" [SYNC COMPLETE] {actions_taken} action(s) applied to Trello.")
    print("============================================================\n")

    return True


def detect_default_repos_dir():
    """Auto-detect workspace `repos/` directory from env, cwd, or skill install location."""
    env_dir = (os.environ.get("TRELLO_REPOS_DIR") or "").strip()
    if env_dir:
        return os.path.abspath(env_dir), os.path.dirname(os.path.abspath(env_dir))

    cwd_repos = os.path.join(os.getcwd(), "repos")
    if os.path.isdir(cwd_repos):
        return os.path.abspath(cwd_repos), os.getcwd()

    # If installed at <workspace>/.skills/trello-pm/scripts/sync_trello.py or <workspace>/skills/...
    script_dir = os.path.dirname(os.path.abspath(__file__))
    inferred_root = os.path.abspath(os.path.join(script_dir, "..", "..", ".."))
    inferred_repos = os.path.join(inferred_root, "repos")
    if os.path.isdir(inferred_repos):
        return inferred_repos, inferred_root

    return os.path.abspath(cwd_repos), os.getcwd()


def main():
    parser = argparse.ArgumentParser(
        description="Trello PM — Zero-dependency Kanban sync & #futureimprovements scanner"
    )
    parser.add_argument(
        "--repos-dir",
        default=None,
        help="Path to the repos/ directory (default: TRELLO_REPOS_DIR or auto-detected ./repos)",
    )
    parser.add_argument(
        "--board-id",
        default=None,
        help="Trello Board ID or full Trello board URL (overrides TRELLO_BOARD_ID)",
    )
    parser.add_argument(
        "--why",
        default=None,
        help="Rationale explaining why the commit/change was needed",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single synchronization pass and exit (default)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Run continuously as a polling watcher daemon",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Polling interval in seconds for --watch mode (default: 5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate synchronization without modifying Trello",
    )
    parser.add_argument(
        "--scan-improvements",
        action="store_true",
        help="Scan Doing cards (or --card) for #futureimprovements (NOT DONE vs DONE)",
    )
    parser.add_argument(
        "--card",
        default=None,
        help="Specific Trello card name for --scan-improvements or --resolve-improvement",
    )
    parser.add_argument(
        "--resolve-improvement",
        action="append",
        default=None,
        help="Mark a #futureimprovements item ID as DONE (can be repeated)",
    )

    args = parser.parse_args()

    default_repos_dir, workspace_root = detect_default_repos_dir()
    repos_dir = os.path.abspath(args.repos_dir) if args.repos_dir else default_repos_dir

    if args.watch:
        print(f"[*] Starting Trello PM watcher every {args.interval}s on: {repos_dir}")
        try:
            while True:
                sync_trello_state(
                    repos_dir,
                    board_id=args.board_id,
                    why_reasoning=args.why,
                    dry_run=args.dry_run,
                    workspace_root=workspace_root,
                )
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[*] Trello PM watcher stopped by user.")
            sys.exit(0)
    else:
        success = sync_trello_state(
            repos_dir,
            board_id=args.board_id,
            why_reasoning=args.why,
            dry_run=args.dry_run,
            scan_improvements=args.scan_improvements,
            target_card_name=args.card,
            resolve_improvements=args.resolve_improvement,
            workspace_root=workspace_root,
        )
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
