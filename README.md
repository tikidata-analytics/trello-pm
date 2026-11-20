# 🗂️ trello-pm

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero%20(stdlib%20only)-brightgreen.svg)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

**Zero-dependency Trello Kanban sync engine & AI Coding Agent Skill** for multi-repository workspaces.

`trello-pm` automatically keeps your Trello board synchronized with the repositories cloned in your local `repos/` directory, records commit history and change rationale (`--why`) on each card, and scans cards moving into **Doing** for `#futureimprovements` — cleanly separating **`NOT DONE`** vs **`DONE`** items so nothing slips through the cracks.

---

## ✨ Key Features

- **🔄 Automatic Kanban State Sync**:
  - Clone a repo into `repos/<name>` → automatically moves its card to **Doing** (or creates one).
  - Remove a repo from `repos/<name>` → automatically moves its card from **Doing / Testing** to **Done**.
- **🔍 Full-Card `#futureimprovements` Scanner (`DONE` vs `NOT DONE`)**:
  - Whenever a card transitions to **Doing**, `trello-pm` scans the **entire card** — description, all comments (including Trello's markdown-escaped `\#futureimprovements`), and checklists.
  - Separates **`🔴 NOT DONE`** from **`✅ DONE`** items and prompts the AI agent / developer to confirm whether to tackle unfinished improvements now or skip them.
  - Non-destructive resolution via `--resolve-improvement <id>`: posts an immutable `✅ #futureimprovements-done [ref:<id>]` audit comment (or checks off a native Trello checklist item) without altering original user comments.
- **🧠 Pre-Change Rationale Gate (`SKILL.md`)**:
  - Instructs AI coding agents (Claude Code, Codex, Cursor, AI Studio, etc.) to ask *why* a change is needed before editing code, then logs the rationale alongside each commit.
- **📜 Commit History & Idempotent Comment Logs**:
  - Keeps each card's description updated with active branch, commit count, and a 10-commit summary table.
  - Posts each new commit hash once with author, timestamp, and business rationale.
- **🪶 Zero Dependencies & Secure by Default**:
  - 100% Python 3 standard library — no `pip install`, no virtualenvs required.
  - Built-in `.env` loader, automatic Trello board URL normalization, and automatic redaction of `key`/`token` from all error logs.

---

## 📦 Installation

### Option A: Install as an AI Coding Agent Skill (Recommended)

Drop directly into your workspace or global skills directory — `SKILL.md`, `RULES.md`, `agents/openai.yaml`, and `scripts/sync_trello.py` are already in canonical skill layout:

```bash
# Project-local skill (works with Claude Code, Cursor, Codex, AI Studio, etc.)
git clone https://github.com/tikidata-analytics/trello-pm.git .skills/trello-pm

# OR global Claude Code user skill
git clone https://github.com/tikidata-analytics/trello-pm.git ~/.claude/skills/trello-pm
```

### Option B: Standalone CLI Usage

```bash
git clone https://github.com/tikidata-analytics/trello-pm.git
cd trello-pm
cp .env.example .env
```

---

## 🔑 Getting Trello Credentials (2 Minutes)

1. **Get your Trello API Key**:
   - Visit [https://trello.com/power-ups/admin](https://trello.com/power-ups/admin), create a Power-Up (or open an existing one), and copy your **API Key**.
2. **Generate a Personal Token**:
   - Open this URL in your browser (replace `<YOUR_API_KEY>`):
     ```text
     https://trello.com/1/authorize?expiration=never&name=trello-pm&scope=read,write&response_type=token&key=<YOUR_API_KEY>
     ```
   - Click **Allow** and copy the generated token.
3. **Get your Board ID**:
   - Open your Trello board in the browser — e.g. `https://trello.com/b/AbCdEf12/engineering-roadmap`.
   - You can paste **either** the short ID (`AbCdEf12`) **or** the full URL into `TRELLO_BOARD_ID`.
4. **Configure `.env`** (in your workspace root or inside the skill folder):
   ```ini
   TRELLO_API_KEY=your_trello_api_key
   TRELLO_TOKEN=your_trello_token
   TRELLO_BOARD_ID=https://trello.com/b/AbCdEf12/engineering-roadmap

   # Optional: customize list names to match your board (case-insensitive)
   TRELLO_DOING_LIST=Doing
   TRELLO_DONE_LIST=Done
   TRELLO_TESTING_LIST=Testing
   ```

> **Security Tip**: Never commit `.env` to Git. All API credentials are automatically redacted (`***`) if an HTTP or network error occurs.

---

## 🚀 Quick Start & Usage

### 1. Sync Workspace `repos/` with Trello (One-Shot)

```bash
# Simulate first without modifying Trello
python3 .skills/trello-pm/scripts/sync_trello.py --dry-run

# Sync active repos -> Doing, removed repos -> Done
python3 .skills/trello-pm/scripts/sync_trello.py --once

# Sync after a commit with business rationale
python3 .skills/trello-pm/scripts/sync_trello.py --once \
  --why "Switch LLM client to native endpoint so num_ctx is reliably honored"
```

### 2. Scan `#futureimprovements` (`DONE` vs `NOT DONE`)

Scanning happens **automatically** whenever a card moves to `Doing`. You can also trigger a read-only scan anytime:

```bash
# Scan all cards currently in Doing
python3 .skills/trello-pm/scripts/sync_trello.py --scan-improvements

# Scan a specific card (even if it is currently in Done or Icebox)
python3 .skills/trello-pm/scripts/sync_trello.py --scan-improvements --card payments-service
```

Example output:

```text
============================================================
 🔍 TRELLO PM — #futureimprovements SCAN
============================================================
 Board ID      : AbCdEf12
 Target Cards  : payments-service
============================================================
 ------------------------------------------------------------
 🔍 #futureimprovements SCAN — 'payments-service' (Total: 2 | NOT DONE: 1 | DONE: 1)
 ------------------------------------------------------------
 🔴 NOT DONE #futureimprovements (1):
    1. [6affa782] (comment | 2026-01-18) Log exact tenant name instead of 'ALL' when job is skipped
 ✅ DONE #futureimprovements (1):
    1. [1111aaaa] (comment — resolved via comment (2026-01-19)) Split audit log DB credentials from target DB
 ⚠️  [ACTION REQUIRED — USER CONFIRMATION]
     Found 1 unfinished #futureimprovements item(s) on 'payments-service'.
     Remind the user and CONFIRM whether to work on them now or skip them.
     (When finished, mark DONE via: --resolve-improvement <id> --card payments-service)
 ------------------------------------------------------------
```

### 3. Mark a `#futureimprovements` Item as `DONE`

After implementing and committing an improvement (for example `[6affa782]`):

```bash
python3 .skills/trello-pm/scripts/sync_trello.py --once \
  --card payments-service \
  --resolve-improvement 6affa782 \
  --why "Logged per-tenant entries instead of ALL when is_active=false"
```

- If the item came from a **comment** or **card description**, `trello-pm` posts an idempotent audit-trail comment (`✅ #futureimprovements-done [ref:6affa782]`) linking your latest commit hash — leaving the original comment untouched.
- If the item came from a **Trello Checklist**, `trello-pm` marks the checklist item `complete` directly.
- You can also mark items done manually inside Trello using `[x]`, `[DONE]`, `✅`, `#done`, `STATUS: DONE`, or `~~strikethrough~~`.

### 4. Continuous Watcher Mode (Optional)

```bash
python3 .skills/trello-pm/scripts/sync_trello.py --watch --interval 5
```

---

## 🛠️ CLI Reference

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--once` | *(default)* | Run a single sync pass and exit. |
| `--watch` | `false` | Poll `repos/` continuously at `--interval` seconds. |
| `--interval <sec>` | `5` | Polling interval for `--watch` mode. |
| `--dry-run` | `false` | Print planned actions without calling mutating Trello API endpoints. |
| `--repos-dir <path>` | auto-detect `./repos` | Override the directory containing cloned repositories (`TRELLO_REPOS_DIR`). |
| `--board-id <id\|url>` | `TRELLO_BOARD_ID` | Override target Trello board (accepts short ID or full board URL). |
| `--why "<text>"` | `None` | Change rationale appended to commit comments and resolution notes. |
| `--scan-improvements` | `false` | Read-only scan for `#futureimprovements` on `Doing` cards (or `--card`). |
| `--card <name>` | `None` | Target a specific card name for `--scan-improvements` or `--resolve-improvement`. |
| `--resolve-improvement <id>` | `None` | Mark an improvement ID as `DONE` (repeatable for multiple IDs). |

---

## 🧭 How `#futureimprovements` Detection Works

Trello's Markdown editor automatically escapes leading `#` characters in comments (`\#futureimprovements:`). `trello-pm` handles all natural formats seamlessly:

| Where You Write It | Supported Formats | How `DONE` Is Detected |
| :--- | :--- | :--- |
| **Card Comments** | `#futureimprovements: ...` or `\#futureimprovements:\n...` (single note or bulleted/checkbox list) | Audit-trail comment `#futureimprovements-done [ref:<id>]`, or inline `[x]`, `[DONE]`, `✅`, `#done`, `~~...~~` |
| **Card Description** | `#futureimprovements` section with bullets or checkboxes | Same as above (assigned deterministic `desc-<hash>` ID) |
| **Card Checklists** | Checklist titled `#futureimprovements` / `Future Improvements`, or any check item tagged `#futureimprovements` | Native Trello checklist state (`complete` vs `incomplete`) |

---

## 📂 Repository Structure

```text
trello-pm/
├── SKILL.md                  # AI Agent Skill manifest & SOP
├── RULES.md                  # Kanban state machine & #futureimprovements spec
├── README.md                 # Documentation & usage guide
├── CONTRIBUTING.md           # Contribution guidelines
├── LICENSE                   # MIT License
├── .env.example              # Configuration template
├── agents/
│   └── openai.yaml           # Agent metadata manifest
└── scripts/
    ├── sync_trello.py        # Zero-dependency sync & scanner engine
    └── test_sync_trello.py   # Standard-library unit test suite
```

---

## 🧪 Running Tests

```bash
python3 scripts/test_sync_trello.py
```

---

## 📄 License

Released under the [MIT License](./LICENSE).
