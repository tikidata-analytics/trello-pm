---
name: trello-pm
description: Automatically manage and synchronize Trello Kanban cards based on active repositories in your workspace `repos/` folder. Enforces a Pre-Change Rationale Gate, scans cards moved to Doing for `#futureimprovements` (separating DONE vs NOT DONE and asking for confirmation), and logs commit summaries and reasoning on cards.
disable-model-invocation: true
---

# Trello PM Skill

An Agent Skill and zero-dependency Python CLI that keeps a Trello Kanban board automatically synchronized with local repositories inside your workspace's `repos/` directory.

## Core Rules

1. **Pre-Change Rationale Gate ("Always Ask Why")**
   - Before editing code in any active repository under `repos/`, the agent **MUST** confirm with the user: *why is this feature, fix, or revision needed, and what problem does it solve?*
   - Do not modify code until the rationale is clear.

2. **Clone Repo into `repos/` + `#futureimprovements` Scan Gate**
   - Move the matching Trello card to **`Doing`** (or create a new card in `Doing` if none exists).
   - **Immediately upon moving a card to `Doing`**, `sync_trello.py` scans the **entire card** — description, all comments (including Trello's escaped `\#futureimprovements` syntax), and all checklists — and cleanly separates **`NOT DONE`** vs **`DONE`** `#futureimprovements`.
   - **If `NOT DONE >= 1`**: The agent **MUST** remind the user of every unfinished `#futureimprovements` item (including its short `[id]`) and ask for explicit confirmation on whether to work on them now or skip them.
   - **If `NOT DONE == 0` and `DONE >= 1`**: Report briefly as informational context without blocking the workflow for confirmation.

3. **Non-Destructive `DONE` vs `NOT DONE` Resolution**
   - An item is classified as **`DONE`** if:
     - It is a completed Trello checklist item (`state == "complete"`), OR
     - It contains an inline completion marker (`[x]`, `[DONE]`, `✅`, `#done`, `STATUS: DONE`, or `~~strikethrough~~`), OR
     - An audit-trail resolution comment exists containing `#futureimprovements-done` and `[ref:<id>]`.
   - When an agent finishes working on a `#futureimprovements` item, mark it done with `--resolve-improvement <id>` so the original user comment remains intact as audit history.

4. **Remove Repo from `repos/`**
   - When a repository folder is removed from `repos/`, move its card from `Doing` (or `Testing`) to **`Done`**.

5. **Exact Card Name Matching**
   - `card.name.strip() == repo_name` (case-sensitive after whitespace trim).

6. **Commit Tracking on Cards**
   - **Card Description (`desc`)**: Macro summary of active branch, latest commit, total commits, and a Markdown table of the 10 most recent commits.
   - **Card Comments**: Idempotent per-commit comment with commit hash, author, timestamp, and change rationale (`--why`).

7. **Configuration & Secret Hygiene**
   - Reads `TRELLO_API_KEY`, `TRELLO_TOKEN`, `TRELLO_BOARD_ID`, and optional custom list names (`TRELLO_DOING_LIST`, `TRELLO_DONE_LIST`, `TRELLO_TESTING_LIST`) from environment variables or `.env`.
   - Never hardcodes credentials or board IDs; redacts `key` and `token` from all HTTP/network error output.

## Standard Operating Procedure (SOP)

1. **Clone & Move Card to `Doing`**:
   ```bash
   python3 .skills/trello-pm/scripts/sync_trello.py --once
   ```
   If `[ACTION REQUIRED — USER CONFIRMATION]` appears for unfinished `#futureimprovements`, stop and confirm with the user before proceeding.
2. **Pre-Change Rationale Gate**: Ensure the change rationale (`--why`) is clearly established.
3. **Implement & Test**: Make changes and run tests locally.
4. **Commit & Sync to Trello**:
   ```bash
   python3 .skills/trello-pm/scripts/sync_trello.py --once --why "Why this change was needed"
   ```
   If the commit also finishes a `#futureimprovements` item `<id>`:
   ```bash
   python3 .skills/trello-pm/scripts/sync_trello.py --once --why "Resolved improvement" --resolve-improvement <id>
   ```

## Supporting Files

- [RULES.md](./RULES.md) — State transition matrix, `#futureimprovements` parsing rules, and comment templates.
- [agents/openai.yaml](./agents/openai.yaml) — Agent metadata manifest.
- [scripts/sync_trello.py](./scripts/sync_trello.py) — Standalone Python 3 standard-library engine.
- [scripts/test_sync_trello.py](./scripts/test_sync_trello.py) — Unit test suite.
