# Trello PM Business Rules & State Machine

Canonical rules governing how `trello-pm` synchronizes local repositories in `repos/` with a Trello Kanban board.

## 1. Card Naming Convention

- **Strict Match**: `card.name.strip() == repo_name`
- Example:
  - `repos/payments-service` ↔ Trello card titled `"payments-service"`
  - `repos/data-pipeline` ↔ Trello card titled `"data-pipeline"`

## 2. Canonical Board Lists

Default list names (case-insensitive, customizable via `.env`):

| Env Variable | Default | Purpose |
| :--- | :--- | :--- |
| *(optional)* | `Icebox` / `Backlog` | Queued repositories not yet cloned locally |
| `TRELLO_DOING_LIST` | `Doing` | Repositories currently cloned inside `repos/` |
| `TRELLO_TESTING_LIST` | `Testing` | Pre-completion verification (also transitions to `Done` when removed from `repos/`) |
| `TRELLO_DONE_LIST` | `Done` | Completed repositories removed from `repos/` |

## 3. State Transition Matrix

| Local `repos/` State | Trello Card State | `trello-pm` Action | Target List |
| :--- | :--- | :--- | :---: |
| Repo folder **exists** in `repos/` | Card in `Icebox`, `Testing`, or `Done` | Move card to `Doing` + **scan entire card for `#futureimprovements`** | **`Doing`** |
| Repo folder **exists** in `repos/` | Card **does not exist** | Create new card with git summary | **`Doing`** |
| Repo folder **exists** in `repos/` | Card already in `Doing` | Refresh commit summary `desc` + post new commit comment (idempotent) | **`Doing`** |
| Repo folder **removed** from `repos/` | Card in `Doing` or `Testing` | Move card to `Done` | **`Done`** |
| Repo folder **absent** from `repos/` | Card in `Done` or `Icebox` | No-op (idempotent) | Unchanged |

## 4. `#futureimprovements` Full-Card Scan Protocol

Triggered automatically whenever a card transitions into **`Doing`**, or manually via `--scan-improvements`.

### Sources Scanned
1. **Card Description (`desc`)**: Any section or list tagged with `#futureimprovements`.
2. **All Card Comments** (up to 1,000 comments): Detects both `#futureimprovements` and Trello's markdown-escaped `\#futureimprovements` / `\#futureimprovements:`.
3. **All Card Checklists**: All items inside any checklist named `#futureimprovements` / `Future Improvements`, plus any individual checklist item mentioning `#futureimprovements`.

### `DONE` vs `NOT DONE` Classification
An item is classified as **`DONE`** if any of the following apply:
1. Checklist item has `state == "complete"`.
2. Item text contains an inline completion marker: `[x]`, `[X]`, `[DONE]`, `(DONE)`, `STATUS: DONE`, `#done`, `✅`, or `~~strikethrough~~`.
3. A resolution comment exists on the card containing `#futureimprovements-done` and `[ref:<id>]`.

All other `#futureimprovements` items are classified as **`NOT DONE`**.

### Agent Confirmation Behavior
- **`NOT DONE > 0`**: Print both `NOT DONE` and `DONE` lists separately, emit `[ACTION REQUIRED — USER CONFIRMATION]`, and **ask the user whether to work on the open items now or skip them**.
- **`NOT DONE == 0` and `DONE > 0`**: Print a brief informational summary without interrupting for confirmation.

### Non-Destructive Resolution Format
Running `--resolve-improvement <id>` never mutates the user's original comment. Instead:
- For **checklist items**: updates the checkItem state to `complete`.
- For **comment or description items**: posts an idempotent audit-trail comment:

```markdown
✅ **#futureimprovements-done** `[ref:<id>]`
📌 **Item**: <summary of the resolved item>
🔖 **Commit**: `<short_hash>` (`<branch>`) — <commit_subject>
💡 **Resolution Note**: <optional --why note>
```

## 5. Pre-Change Rationale Gate

Before modifying code in any repository under `repos/`, the AI agent must ask:
1. *Why is this feature, fix, or revision needed?*
2. *What problem does it solve or what outcome is expected?*

The agreed rationale is then recorded permanently on the Trello card via `--why "<rationale>"`.

## 6. Security & Secret Hygiene

- Credentials are loaded only from OS environment variables or `.env` (never hardcoded).
- `TRELLO_BOARD_ID` accepts either a short board ID (`AbCdEf12`) or a full Trello board URL (`https://trello.com/b/AbCdEf12/my-board`).
- `key` and `token` query parameters are always redacted (`***`) from HTTP and network error messages.
