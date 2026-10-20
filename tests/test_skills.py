import json
import os
import time
import unittest
from pathlib import Path

from test_walle import Tree

from walle import skills
from walle.collect import frontmatter
from walle.stack import MEMORY_BYTE_LIMIT, claude_stack, memory_folder, memory_slug, memory_text

SKILL = "---\nname: {name}\ndescription: {desc}\n---\nbody\n"


class FrontmatterTest(unittest.TestCase):
    def test_block_scalars(self):
        fm = frontmatter("---\nname: a\ndescription: >\n  first line\n  second line\nx: y\n---\n")
        self.assertEqual(fm["description"], "first line second line")
        self.assertEqual(fm["x"], "y")
        fm = frontmatter("---\ndescription: |-\n  l1\n  l2\nmeta:\n  nested: 1\ndisable-model-invocation: true\n---\n")
        self.assertEqual((fm["description"], fm["disable-model-invocation"]), ("l1\nl2", "true"))

    def test_quoted_and_continued_values(self):
        fm = frontmatter('---\ndescription: "say \\"hi\\" twice\n  and more"\nname: \'it\'\'s\'\nurl: http://a:b\n---\n')
        self.assertEqual(fm["description"], 'say "hi" twice and more')
        self.assertEqual(fm["name"], "it's")
        self.assertEqual(fm["url"], "http://a:b")
        self.assertEqual(frontmatter("---\ndescription: plain\n  continued\n---\n")["description"], "plain continued")


class SkillDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.t = Tree()

    def tearDown(self):
        self.t.close()

    def skill(self, rel, name, desc="d"):
        return self.t.write(f"{rel}/SKILL.md", SKILL.format(name=name, desc=desc))

    def test_sources_prefixes_and_dedupe(self):
        t = self.t
        self.skill(".claude/skills/mine", "mine")
        self.t.write(".claude/skills/off/SKILL.md", "---\nname: off\ndescription: d\ndisable-model-invocation: true\n---\n")
        self.skill(".claude/skills/.trash/1/old", "old")
        self.skill("projects/.claude/skills/proj", "proj")
        # Two sync buckets of one org: the newest one is in use.
        old = self.skill(".claude/skills/synced/org1_acctA/pdf", "pdf", "old pdf").parent.parent
        self.skill(".claude/skills/synced/org1_acctB/pdf", "pdf", "new pdf")
        self.skill(".claude/skills/synced/org1_acctB/xlsx", "xlsx")
        t.write(".claude/skills/synced/org1_acctB/.last-complete-round", "x")
        os.utime(old, (time.time() - 3600, time.time() - 3600))
        # A synced plugin folder with a ~gN suffix, an installed plugin and two host copies.
        self.skill(".claude/plugins/synced/org1_acctB/marketing~g3/skills/seo", "seo")
        inst = t.home / ".claude/plugins/cache/m/typesafe/1.0"
        self.skill(".claude/plugins/cache/m/typesafe/1.0/skills/ts", "ts")
        t.write(".claude/plugins/installed_plugins.json",
                json.dumps({"version": 2, "plugins": {"typesafe@m": [{"installPath": str(inst)}]}}))
        self.skill(".claude/remote/plugins/h1/aa/skills/learn", "learn")
        t.write(".claude/remote/plugins/h1/aa/.claude-plugin/plugin.json", json.dumps({"name": "anthropic-skills"}))
        self.skill(".claude/remote/plugins/h1/bb/skills/forecast", "forecast")
        t.write(".claude/remote/plugins/h1/bb/.claude-plugin/plugin.json", json.dumps({"name": "sales"}))
        found = {s.name: s for s in skills.discover(t.projects, t.home)}
        self.assertEqual(sorted(found), ["anthropic-skills:learn", "anthropic-skills:pdf", "anthropic-skills:xlsx",
                                         "marketing:seo", "mine", "off", "proj", "typesafe:ts"])
        self.assertTrue(found["off"].disabled)
        self.assertEqual(found["anthropic-skills:pdf"].description, "new pdf")
        self.assertEqual(found["anthropic-skills:learn"].source, "remote_plugin")
        self.assertEqual(found["mine"].line, "- mine: d")
        # A disabled plugin is not listed.
        t.write(".claude/settings.json", json.dumps({"enabledPlugins": {"typesafe@m": False}}))
        self.assertNotIn("typesafe:ts", {s.name for s in skills.discover(t.projects, t.home)})

    def test_description_cap_and_when_to_use(self):
        p = self.t.write(".claude/skills/long/SKILL.md",
                         "---\nname: long\ndescription: " + "a" * 2000 + "\nwhen_to_use: later\n---\n")
        s = skills.read_skill(p, "long", "user")
        self.assertEqual(len(s.description), skills.DESCRIPTION_MAX)
        p = self.t.write(".claude/skills/w/SKILL.md", "---\nname: w\ndescription: does w\nwhen_to_use: when w\n---\n")
        self.assertEqual(skills.read_skill(p, "w", "user").description, "does w when w")

    def test_nested_skills_load_on_demand(self):
        self.skill("projects/app/.claude/skills/top", "top")
        self.skill("projects/app/controller/.claude/skills/tick", "tick")
        self.skill("projects/app/node_modules/x/.claude/skills/vendored", "vendored")
        names = [s.name for s in skills.nested(self.t.repo)]
        self.assertEqual(names, ["controller:tick"])
        stack_skills = claude_stack(self.t.repo, self.t.home).layers[-1].note
        self.assertIn("1 skills on disk (project 1)", stack_skills)


class MemoryTest(unittest.TestCase):
    def test_worktree_and_subfolder_use_the_main_repo_slug(self):
        t = Tree()
        try:
            wt = t.worktree("app-feature")
            self.assertEqual(memory_folder(wt / "sub"), t.repo)
            self.assertEqual(memory_folder(t.repo / "sub"), t.repo)
            self.assertEqual(memory_folder(t.projects), t.projects)
            t.write(f".claude/projects/{memory_slug(t.repo)}/memory/MEMORY.md", "- m\n")
            self.assertEqual([l.label for l in claude_stack(wt, t.home).layers], ["memory"])
        finally:
            t.close()

    def test_200_lines_or_25_kb(self):
        text, note = memory_text("\n".join("- short" for _ in range(250)))
        self.assertEqual((text.count("\n") + 1, note), (200, "250 lines, truncated to 200"))
        text, note = memory_text("\n".join("- " + "x" * 300 for _ in range(150)))
        self.assertLessEqual(len(text.encode()), MEMORY_BYTE_LIMIT)
        self.assertTrue(text.endswith("x"))
        self.assertEqual(note, f"150 lines, truncated to {MEMORY_BYTE_LIMIT} bytes")


if __name__ == "__main__":
    unittest.main()
