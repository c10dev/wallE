"""Format checks on every real session fixture: Claude Code transcripts and Codex rollouts.

walle reads formats that the vendors do not document. When a new Claude Code or Codex version changes them,
the parsers do not fail; they return fewer sources or zero tokens. These tests make that loud.

To check a new version, add an anonymized session from it: a transcript to tests/fixtures/*.jsonl or a
rollout to tests/fixtures/codex/*.jsonl. Every test here runs on it. A record kind that no fixture had
before fails test_*_kinds_are_known: read what it holds, then add it to the KNOWN set it belongs in.
"""

import json
import unittest
from pathlib import Path

from walle import rollout, transcript

FIXTURES = Path(__file__).parent / "fixtures"
CLAUDE = sorted(FIXTURES.glob("*.jsonl"))
CODEX = sorted((FIXTURES / "codex").glob("*.jsonl"))

# Claude Code: line types and attachment types seen in the fixtures and reviewed. walle reads some and
# counts the rest under other_attachments.
KNOWN_CLAUDE_TYPES = {"user", "assistant", "attachment"}
KNOWN_ATTACHMENTS = {
    "prompt_snapshot", "credential_org", "deferred_tools_delta", "mcp_instructions_delta", "skill_listing",
    "instructions", "agent_listing_delta", "environment", "model", "auto_mode", "total_tokens_reminder",
    "session_context", "date", "advisor_tool",
}
# The usage keys that make the measured first call.
CLAUDE_USAGE = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

# Codex: record types, event_msg types and response_item types seen in the fixtures and reviewed.
KNOWN_CODEX_TYPES = {"session_meta", "turn_context", "response_item", "event_msg",
                     "inter_agent_communication_metadata", "token_usage_record"}
KNOWN_EVENTS = {"task_started", "task_complete", "thread_settings_applied", "item_completed", "token_count"}
KNOWN_ITEMS = {"message", "agent_message", "reasoning", "custom_tool_call", "custom_tool_call_output"}
# The session_meta keys walle reads.
CODEX_META = ("id", "timestamp", "cli_version", "originator")


def lines(path: Path) -> list[dict]:
    return [json.loads(raw) for raw in path.read_text(encoding="utf-8").splitlines() if raw.strip()]


class ClaudeTranscriptFormat(unittest.TestCase):
    def test_there_are_fixtures(self):
        self.assertTrue(CLAUDE)

    def test_kinds_are_known(self):
        for path in CLAUDE:
            with self.subTest(fixture=path.name):
                ds = lines(path)
                self.assertLessEqual({d.get("type") for d in ds}, KNOWN_CLAUDE_TYPES)
                atts = {(d.get("attachment") or {}).get("type") for d in ds if d.get("type") == "attachment"}
                self.assertLessEqual(atts, KNOWN_ATTACHMENTS)

    def test_the_first_call_has_usage(self):
        for path in CLAUDE:
            with self.subTest(fixture=path.name):
                usage = next((d["message"]["usage"] for d in lines(path)
                              if d.get("type") == "assistant" and (d.get("message") or {}).get("usage")), None)
                self.assertIsNotNone(usage)
                for key in CLAUDE_USAGE:
                    self.assertIsInstance(usage.get(key), int, key)

    def test_the_parser_finds_the_first_call(self):
        for path in CLAUDE:
            with self.subTest(fixture=path.name):
                fc = transcript.first_call(path)
                self.assertIsNotNone(fc)
                self.assertTrue(fc.version and fc.cwd and fc.start and fc.model)
                self.assertGreater(fc.measured["total"], 0)
                self.assertEqual(fc.measured["total"],
                                 fc.measured["input"] + fc.measured["cache_creation"] + fc.measured["cache_read"])
                by = {s.kind: s for s in fc.sources}
                self.assertEqual(set(by), set(transcript.SOURCES))
                # Every session has a system prompt, tools and a first prompt. Zero bytes means a renamed key.
                for kind in ("system_prompt", "builtin_tools", "first_prompt"):
                    self.assertGreater(by[kind].bytes, 0, kind)
                    self.assertNotEqual(by[kind].recorded, "not_recorded", kind)


class CodexRolloutFormat(unittest.TestCase):
    def test_there_are_fixtures(self):
        self.assertTrue(CODEX)

    def test_kinds_are_known(self):
        for path in CODEX:
            with self.subTest(fixture=path.name):
                ds = lines(path)
                self.assertLessEqual({d.get("type") for d in ds}, KNOWN_CODEX_TYPES)
                payloads = [(d.get("type"), d.get("payload") or {}) for d in ds]
                self.assertLessEqual({p.get("type") for t, p in payloads if t == "event_msg"}, KNOWN_EVENTS)
                self.assertLessEqual({p.get("type") for t, p in payloads if t == "response_item"}, KNOWN_ITEMS)

    def test_session_meta_has_the_keys_walle_reads(self):
        for path in CODEX:
            with self.subTest(fixture=path.name):
                meta = next(d["payload"] for d in lines(path) if d.get("type") == "session_meta")
                for key in CODEX_META:
                    self.assertTrue(meta.get(key), key)

    def test_the_parser_finds_the_first_call(self):
        for path in CODEX:
            with self.subTest(fixture=path.name):
                r = rollout.load(path)
                self.assertIsNotNone(r)
                fc = rollout.first_call(r)
                self.assertIsNotNone(fc)
                self.assertTrue(fc.version and fc.cwd and fc.start and fc.model)
                self.assertGreater(fc.measured["total"], 0)
                self.assertLessEqual(fc.measured["cached"], fc.measured["total"])
                by = {s.kind: s.bytes for s in fc.sources}
                # Every rollout starts with the base instructions and a first prompt.
                for kind in ("base_instructions", "first_prompt"):
                    self.assertGreater(by.get(kind, 0), 0, kind)


if __name__ == "__main__":
    unittest.main()
