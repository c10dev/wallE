"""The plugin's subagents: agents/eve.md must name only walle commands and flags that exist, and stay read-only."""

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVE = ROOT / "agents" / "eve.md"
WRITERS = {"apply", "undo", "keep", "unkeep", "pin", "trim", "judge"}


def frontmatter(text: str) -> dict:
    head = text.split("---\n")[1]
    return dict(line.split(": ", 1) for line in head.strip().splitlines())


def help_of(sub: str) -> str:
    return subprocess.run([sys.executable, str(ROOT / "bin" / "walle"), sub, "--help"],
                          capture_output=True, text=True, check=True).stdout


class EveAgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = EVE.read_text()
        cls.meta = frontmatter(cls.text)
        cls.body = cls.text.split("---\n", 2)[2]

    def test_frontmatter(self):
        self.assertEqual(self.meta["name"], "eve")
        # The description goes into the agent list of every session: keep it short.
        self.assertLessEqual(len(self.meta["description"].encode()), 250)
        tools = {t.strip() for t in self.meta["tools"].split(",")}
        self.assertFalse(tools & {"Edit", "Write", "MultiEdit", "NotebookEdit"})
        self.assertIn(self.meta["model"], {"haiku", "sonnet", "opus", "inherit"})

    def test_commands_and_flags_exist(self):
        commands = re.findall(r"`walle ([a-z]+)([^`]*)`", self.body)
        self.assertTrue(commands)
        helps: dict[str, str] = {}
        for sub, rest in commands:
            if sub not in helps:
                helps[sub] = help_of(sub)
            for flag in re.findall(r"--[a-z][a-z-]*", rest):
                self.assertIn(flag, helps[sub], f"walle {sub} has no {flag}")

    def test_writing_commands_appear_only_as_forbidden(self):
        for line in self.body.splitlines():
            for sub in re.findall(r"`walle ([a-z]+)", line):
                if sub in WRITERS:
                    self.assertTrue(line.lstrip("- ").startswith("Never run"), line)


if __name__ == "__main__":
    unittest.main()
