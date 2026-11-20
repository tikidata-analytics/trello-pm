#!/usr/bin/env python3
"""
Unit test suite for Trello PM Skill
Covers: .env loading, URL credential redaction, board ID normalization,
commit summaries, idempotent commit comments, full-card #futureimprovements
scanning (DONE vs NOT DONE separation), move-to-Doing confirmation gate,
and audit-trail / checklist resolution.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_trello


class TestTrelloPM(unittest.TestCase):
    def setUp(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.skill_dir = os.path.abspath(os.path.join(self.base_dir, ".."))

    def test_canonical_skill_artifacts_exist(self):
        """Verify canonical skill files exist and contain required directives."""
        skill_md = os.path.join(self.skill_dir, "SKILL.md")
        rules_md = os.path.join(self.skill_dir, "RULES.md")
        openai_yaml = os.path.join(self.skill_dir, "agents", "openai.yaml")
        sync_py = os.path.join(self.skill_dir, "scripts", "sync_trello.py")

        for path in (skill_md, rules_md, openai_yaml, sync_py):
            self.assertTrue(os.path.exists(path), f"Missing required file: {path}")

        with open(skill_md, "r", encoding="utf-8") as fh:
            content = fh.read()
            self.assertIn("name: trello-pm", content)
            self.assertIn("Pre-Change Rationale Gate", content)
            self.assertIn("#futureimprovements", content)
            self.assertIn("NOT DONE", content)
            self.assertIn("DONE", content)

        with open(openai_yaml, "r", encoding="utf-8") as fh:
            self.assertIn('display_name: "Trello PM"', fh.read())

    def test_sanitize_url_and_normalize_board_id(self):
        """Verify credentials are redacted from URLs and board URLs normalize cleanly."""
        raw = "https://api.trello.com/1/boards/AbCd1234/cards?filter=all&key=SECRET_KEY&token=SECRET_TOK"
        safe = sync_trello.sanitize_url(raw)
        self.assertNotIn("SECRET_KEY", safe)
        self.assertNotIn("SECRET_TOK", safe)
        self.assertIn("key=%2A%2A%2A", safe)
        self.assertIn("token=%2A%2A%2A", safe)

        self.assertIsNone(sync_trello.normalize_board_id(""))
        self.assertIsNone(sync_trello.normalize_board_id(None))
        self.assertEqual(sync_trello.normalize_board_id("AbCd1234"), "AbCd1234")
        self.assertEqual(
            sync_trello.normalize_board_id("https://trello.com/b/AbCd1234/engineering-roadmap"),
            "AbCd1234",
        )

    def test_load_dotenv_file_does_not_override_existing_env(self):
        """Verify stdlib .env loader parses quoted values without clobbering OS env."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = os.path.join(tmpdir, ".env")
            with open(env_file, "w", encoding="utf-8") as fh:
                fh.write(
                    '# comment\n'
                    'TRELLO_API_KEY="file_key"\n'
                    "TRELLO_TOKEN='file_token'\n"
                    "export TRELLO_BOARD_ID=https://trello.com/b/XyZ98765/my-board\n"
                )
            with patch.dict(os.environ, {"TRELLO_API_KEY": "os_key"}, clear=True):
                sync_trello.load_dotenv_file(env_file)
                self.assertEqual(os.environ.get("TRELLO_API_KEY"), "os_key")
                self.assertEqual(os.environ.get("TRELLO_TOKEN"), "file_token")
                self.assertEqual(
                    sync_trello.normalize_board_id(os.environ.get("TRELLO_BOARD_ID")),
                    "XyZ98765",
                )

    def test_format_card_description(self):
        """Verify markdown table generation for commit summaries."""
        mock_meta = {
            "branch": "main",
            "short_hash": "a1b2c3d",
            "subject": "feat: add webhook retries",
            "author": "Alice",
            "iso_date": "2026-01-15T10:30:00Z",
            "total_commits": "18",
            "recent_commits": [
                {"hash": "a1b2c3d", "subject": "feat: add webhook retries", "date": "2026-01-15"},
                {"hash": "e4f5g6h", "subject": "fix: handle timeout | fallback", "date": "2026-01-14"},
            ],
        }
        desc = sync_trello.format_card_description("payments-service", mock_meta)
        self.assertIn("### 📦 Repository Status: `payments-service`", desc)
        self.assertIn("**Active Branch**: `main`", desc)
        self.assertIn("**Total Commits**: `18`", desc)
        self.assertIn("| `a1b2c3d` | feat: add webhook retries | 2026-01-15 |", desc)
        self.assertIn("| `e4f5g6h` | fix: handle timeout / fallback | 2026-01-14 |", desc)

    def test_parse_future_improvements_separates_done_and_not_done_across_entire_card(self):
        """Verify full-card scanning across description, escaped Trello comments, and checklists."""
        desc = (
            "### Repository Overview\n"
            "General details.\n\n"
            "#futureimprovements\n"
            "- [ ] Add exponential backoff for database reconnects\n"
            "- [x] Standardize ISO-8601 log timestamps\n\n"
            "### Notes\n"
            "Unrelated section."
        )

        # Trello returns comments newest -> oldest
        comments = [
            {
                "id": "9999ffff00001111",
                "date": "2026-01-19T10:00:00Z",
                "data": {"text": "✅ **#futureimprovements-done** `[ref:1111aaaa]`\n🔖 Commit: `abc1234`"},
            },
            {
                "id": "2222bbbb00002222",
                "date": "2026-01-18T08:00:00Z",
                "data": {
                    "text": r"\#futureimprovements:"
                    + "\nLog the exact tenant name instead of 'ALL' when job is skipped"
                },
            },
            {
                "id": "3333cccc00003333",
                "date": "2026-01-17T08:00:00Z",
                "data": {"text": "#futureimprovements: [DONE] Add unit tests for CSV escaping"},
            },
            {
                "id": "1111aaaa00001111",
                "date": "2026-01-16T08:00:00Z",
                "data": {"text": "#futureimprovements\nSplit audit log DB credentials from target DB"},
            },
        ]

        checklists = [
            {
                "id": "cl_01",
                "name": "#futureimprovements",
                "checkItems": [
                    {"id": "chkitem01abcdef", "name": "Expose Prometheus metrics endpoint", "state": "incomplete"},
                    {"id": "chkitem02abcdef", "name": "Redact credentials in CLI logs", "state": "complete"},
                ],
            }
        ]

        report = sync_trello.parse_future_improvements(
            card_id="card_1",
            card_name="payments-service",
            desc=desc,
            comments=comments,
            checklists=checklists,
        )

        self.assertEqual(report["total"], 7)
        self.assertEqual(len(report["not_done"]), 3)
        self.assertEqual(len(report["done"]), 4)

        not_done_texts = " | ".join(i["text"] for i in report["not_done"])
        done_texts = " | ".join(i["text"] for i in report["done"])

        self.assertIn("exponential backoff", not_done_texts)
        self.assertIn("exact tenant name", not_done_texts)
        self.assertIn("Prometheus metrics", not_done_texts)
        self.assertNotIn("futureimprovements", not_done_texts.lower())

        self.assertIn("ISO-8601", done_texts)
        self.assertIn("CSV escaping", done_texts)
        self.assertIn("Split audit log DB credentials", done_texts)
        self.assertIn("Redact credentials", done_texts)

        for item in report["not_done"]:
            self.assertNotIn("9999ffff", item["id"])

    def test_move_to_doing_triggers_scan_and_confirmation_gate(self):
        """Verify moving a card to Doing triggers full-card scan and only prompts if NOT DONE > 0."""
        mock_lists = [
            {"id": "id_doing", "name": "Doing"},
            {"id": "id_done", "name": "Done"},
        ]
        mock_cards = [
            {"id": "card_open", "name": "service-a", "idList": "id_done", "desc": "old"},
            {"id": "card_all_done", "name": "service-b", "idList": "id_done", "desc": "old"},
        ]

        def mock_request(url, method="GET", data=None):
            if "lists" in url:
                return mock_lists
            if "/boards/" in url and "/cards" in url:
                return mock_cards
            if "card_open/actions" in url and method == "GET":
                return [
                    {
                        "id": "aa11bb22cc33",
                        "date": "2026-01-18T00:00:00Z",
                        "data": {"text": r"\#futureimprovements: Optimize batch query"},
                    }
                ]
            if "card_all_done/actions" in url and method == "GET":
                return [
                    {
                        "id": "ff99ee88dd77",
                        "date": "2026-01-19T00:00:00Z",
                        "data": {"text": "✅ #futureimprovements-done [ref:dd44ee55]"},
                    },
                    {
                        "id": "dd44ee55ff66",
                        "date": "2026-01-18T00:00:00Z",
                        "data": {"text": "#futureimprovements: Already resolved task"},
                    },
                ]
            if "checklists" in url and method == "GET":
                return []
            return {}

        mock_cfg = {
            "api_key": "key",
            "token": "tok",
            "board_id": "board123",
            "doing_list": "Doing",
            "done_list": "Done",
            "testing_list": "Testing",
        }

        buf = io.StringIO()
        with patch("sync_trello.get_env_config", return_value=mock_cfg), \
             patch("sync_trello.trello_api_request", side_effect=mock_request), \
             patch("sync_trello.get_local_repos", return_value=["service-a", "service-b"]), \
             patch("sync_trello.get_git_repo_metadata", return_value=None), \
             redirect_stdout(buf):
            ok = sync_trello.sync_trello_state("/dummy/repos", dry_run=True)

        self.assertTrue(ok)
        out = buf.getvalue()
        self.assertIn("NOT DONE #futureimprovements (1)", out)
        self.assertIn("[aa11bb22]", out)
        self.assertIn("ACTION REQUIRED — USER CONFIRMATION", out)
        self.assertIn("All 1 #futureimprovements item(s) on 'service-b' are already DONE", out)

    def test_resolve_future_improvement_posts_comment_and_completes_checklist(self):
        """Verify resolving comment and checklist #futureimprovements items."""
        card = {"id": "card_x", "name": "payments-service", "desc": ""}
        comments = [
            {
                "id": "abc12345ffff",
                "date": "2026-01-18T00:00:00Z",
                "data": {"text": r"\#futureimprovements: Fix tenant logging"},
            }
        ]
        checklists = [
            {
                "id": "cl1",
                "name": "#futureimprovements",
                "checkItems": [
                    {"id": "chk9876543210", "name": "Add readiness probe", "state": "incomplete"}
                ],
            }
        ]

        calls = []

        def mock_request(url, method="GET", data=None):
            calls.append({"url": url, "method": method, "data": data})
            if "actions" in url and method == "GET":
                return comments
            if "checklists" in url and method == "GET":
                return checklists
            return {}

        mock_meta = {"short_hash": "19847f8", "branch": "main", "subject": "fix: tenant logging"}
        with patch("sync_trello.trello_api_request", side_effect=mock_request):
            count = sync_trello.resolve_future_improvement(
                card,
                ["abc12345", "chk-chk98765"],
                mock_meta,
                "Verified with unit tests",
                "key",
                "tok",
                dry_run=False,
            )

        self.assertEqual(count, 2)
        posted = [c for c in calls if c["method"] == "POST" and "comments" in c["url"]]
        self.assertEqual(len(posted), 1)
        self.assertIn("#futureimprovements-done", posted[0]["data"]["text"])
        self.assertIn("[ref:abc12345]", posted[0]["data"]["text"])
        self.assertIn("`19847f8`", posted[0]["data"]["text"])

        chk_put = [c for c in calls if c["method"] == "PUT" and "checkItem/chk9876543210" in c["url"]]
        self.assertEqual(len(chk_put), 1)
        self.assertEqual(chk_put[0]["data"], {"state": "complete"})

    def test_post_commit_comment_is_idempotent(self):
        """Verify commit comments are posted once per short hash."""
        mock_meta = {
            "branch": "main",
            "short_hash": "c0ffee1",
            "subject": "perf: reduce latency",
            "author": "Bob",
            "iso_date": "2026-01-15T12:00:00Z",
        }
        posted = []

        def mock_request(url, method="GET", data=None):
            if method == "GET" and "actions" in url:
                return [{"data": {"text": "Previous commit `deadbeef`"}}]
            if method == "POST" and "comments" in url:
                posted.append(data.get("text", ""))
                return {"id": "act_1"}
            return {}

        with patch("sync_trello.trello_api_request", side_effect=mock_request):
            self.assertTrue(
                sync_trello.post_commit_comment_if_new(
                    "card_1", mock_meta, "Cuts p95 latency by 40%", "key", "tok"
                )
            )
            self.assertEqual(len(posted), 1)
            self.assertIn("`c0ffee1`", posted[0])
            self.assertIn("Cuts p95 latency by 40%", posted[0])

        with patch(
            "sync_trello.trello_api_request",
            return_value=[{"data": {"text": "Already logged `c0ffee1`"}}],
        ):
            self.assertFalse(
                sync_trello.post_commit_comment_if_new(
                    "card_1", mock_meta, "Duplicate rationale", "key", "tok"
                )
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
