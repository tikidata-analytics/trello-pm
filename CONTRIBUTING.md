# Contributing to `trello-pm`

Contributions, bug reports, and improvements are welcome!

## Design Principles

- **Zero external dependencies**: Keep `scripts/sync_trello.py` 100% Python 3 standard library (`urllib`, `json`, `re`, `subprocess`, `argparse`).
- **No hardcoded secrets or org-specific defaults**: All board IDs, tokens, and list names must come from environment variables, `.env`, or CLI flags.
- **Idempotency & audit safety**: Repeated runs must never create duplicate cards, duplicate commit comments, or overwrite user-written comments.
- **Tests for every behavior change**: Add or update unit tests in `scripts/test_sync_trello.py` whenever modifying parsing, transitions, or CLI flags.

## Running Tests Locally

```bash
python3 -m py_compile scripts/sync_trello.py scripts/test_sync_trello.py
python3 scripts/test_sync_trello.py
```

## Submitting a Pull Request

1. Fork the repository and create a descriptive feature branch (`feat/...` or `fix/...`).
2. Keep commits focused and well-scoped.
3. Ensure all unit tests pass (`python3 scripts/test_sync_trello.py`).
4. Open a pull request explaining **why** the change is needed and how it was verified.
