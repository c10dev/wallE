import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

# The fixture module sits next to this file: import it also when this file runs by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from real0817 import STACKS, Tree as RealTree  # noqa: E402

from walle import cli, judge, runs, triage  # noqa: E402
from walle.scan import scan  # noqa: E402
from walle.stack import memory_slug  # noqa: E402

SCHEMA_1 = {"generated", "roots", "artifact_count", "copy_count", "stacks", "files", "flags", "pairs", "copies"}


def words(seed: str, n: int = 9) -> str:
    """Words that match no rule and look like no other seed's words."""
    import random
    rng = random.Random(seed)
    return " ".join("".join(rng.choice("bdfklprstvz") + rng.choice("aeiu") for _ in range(3)) for _ in range(n))


class Home:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        # The CLI resolves roots and --out; a temp folder behind a symlink (macOS /var) must compare equal.
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / "home"
        self.projects = self.home / "projects"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".codex").mkdir(parents=True)

    def write(self, rel: str, text: str) -> Path:
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def repo(self, name: str) -> Path:
        (self.projects / name / ".git").mkdir(parents=True, exist_ok=True)
        return self.projects / name

    def scan(self, *roots, budget=triage.BUDGET) -> dict:
        return scan(list(roots) or [self.projects], self.home, budget=budget)

    def close(self):
        self.tmp.cleanup()


def by_id(data):
    return {it["id"]: it for it in data["items"]}


def at(data, path, line):
    """Items whose first location is path:line."""
    return [it for it in data["items"] if it["locations"][0]["file"] == str(path) and it["locations"][0]["line"] == line]


class RealShapeTest(unittest.TestCase):
    """The hand check of design brief 2.2 and 10.1, on a tree with the shape of the 08:17 run."""

    @classmethod
    def setUpClass(cls):
        cls.t = RealTree()
        cls.data = scan([cls.t.projects], cls.t.home)
        cls.items = by_id(cls.data)
        cls.p = cls.t.projects

    @classmethod
    def tearDownClass(cls):
        cls.t.close()

    def stack(self, name):
        return self.data["triage"]["stacks"][f"{self.p}/{name}"]

    def start(self, name):
        return [self.items[i] for i in self.stack(name)["start_here"]]

    def where(self, it):
        l = it["locations"][0]
        return (Path(l["file"]).relative_to(self.t.home).as_posix(), l["line"])

    def test_stack_tokens_match_the_run(self):
        for name, tokens in STACKS.items():
            self.assertAlmostEqual(self.stack(name)["tokens_est"], tokens, delta=tokens * 0.01, msg=name)

    def test_runup_is_lean_with_two_every_task_steps(self):
        e = self.stack("runup@claude")
        self.assertEqual(e["verdict"], "LEAN")
        self.assertEqual([self.where(it) for it in self.start("runup@claude")],
                         [("projects/runup/CLAUDE.md", 8), ("projects/runup/CLAUDE.md", 3)])
        self.assertEqual({(it["verb"], it["label"], it["certainty"]) for it in self.start("runup@claude")},
                         {("CHECK", "every_task_step", "candidate")})
        self.assertEqual(e["to_read"], 2)
        for part, tokens in (("your_files", 689), ("shared", 1297), ("skills", 491)):
            self.assertAlmostEqual(e["split"][part], tokens, delta=3)

    def test_tennis_web_is_lean_with_the_memory_group_last(self):
        self.assertEqual(self.stack("tennis-web@claude")["verdict"], "LEAN")
        first, memory = self.start("tennis-web@claude")
        self.assertEqual(self.where(first), ("projects/tennis-web/CLAUDE.md", 57))
        self.assertEqual(first["label"], "every_task_step")
        self.assertEqual((memory["label"], memory["group_size"], memory["ownership"]), ("memory_lines", 7, "memory"))
        self.assertEqual(memory["group"]["rules"], {"dated": 6, "always_step": 1})
        self.assertEqual([l["line"] for l in memory["locations"]], [2, 4, 7, 8, 9, 10, 11])

    def test_designlab_is_trim_with_three_items_in_its_files(self):
        e = self.stack("DesignLAB@claude")
        self.assertEqual((e["verdict"], e["reasons"]), ("TRIM", ["candidates"]))
        step, move, dated = self.start("DesignLAB@claude")
        self.assertEqual((step["label"], step["locations"][0]["line"], step["also"]), ("every_task_step", 161, ["dated"]))
        self.assertEqual((move["verb"], move["move"]["heading"]), ("MOVE", "Release and copy rules"))
        self.assertEqual((move["locations"][0]["line"], move["shares"][f"{self.p}/DesignLAB@claude"]), (22, 74))
        # Line 161 is the every-task step item, so the dated group holds the other 10 lines.
        self.assertEqual((dated["label"], dated["group_size"]), ("dated_lines", 10))
        self.assertNotIn(161, [l["line"] for l in dated["locations"]])

    def test_atc_controller_is_over_budget_and_moves_first(self):
        e = self.stack("atc/controller@claude")
        self.assertEqual(e["verdict"], "OVER BUDGET")
        first, second, third = self.start("atc/controller@claude")
        self.assertEqual((first["verb"], self.where(first)), ("MOVE", ("projects/atc/controller/CLAUDE.md", 45)))
        self.assertEqual(first["shares"][f"{self.p}/atc/controller@claude"], 51)
        # atc/CLAUDE.md is in the git checkout of atc/controller: it is the user's file there.
        self.assertEqual((second["label"], self.where(second)), ("every_task_step", ("projects/atc/CLAUDE.md", 18)))
        self.assertEqual(third["label"], "model_name")
        self.assertEqual(self.stack("atc@claude")["verdict"], "LEAN")

    def test_vocado_rn_repeat_at_one_is_delete_and_below_is_check(self):
        dele = [it for it in self.start("vocado_RN@codex") if it["verb"] == "DELETE"]
        self.assertEqual(len(dele), 1)
        it = dele[0]
        self.assertEqual((it["label"], it["certainty"], it["score"], it["relation"]), ("repeated", "certain", 1.0, "same_file"))
        self.assertEqual([(l["role"], l["line"]) for l in it["locations"]], [("delete", 74), ("keep", 53)])
        check = self.start("vocado_RN@claude")
        self.assertEqual([(i["verb"], i["label"], self.where(i)) for i in check],
                         [("CHECK", "repeated_maybe", ("projects/vocado_RN/CLAUDE.md", 129))])
        self.assertLess(check[0]["score"], triage.SAFE_DELETE)

    def test_claude_config_global_is_one_file_copy_note(self):
        notes = [it for it in self.data["items"] if it["relation"] == "file_copy"]
        self.assertEqual(len(notes), 1)
        n = notes[0]
        self.assertEqual((n["label"], n["group_size"], n["verb"], n["certainty"]), ("source_of", 32, None, "info"))
        self.assertEqual(n["of"], str(self.t.global_md))
        e = self.stack("claude-config/global@claude")
        self.assertEqual(e["groups"]["everything_else"][0], n["id"])
        self.assertNotIn(n["id"], e["start_here"])
        # The 32 pairs are inside the note, not items of their own.
        self.assertFalse([it for it in self.data["items"] if it["label"] in ("loaded_twice", "similar_twice")])

    def test_schema_2_adds_fields_and_keeps_schema_1(self):
        self.assertEqual(self.data["schema"], 2)
        self.assertLessEqual(SCHEMA_1, set(self.data))
        f = next(f for f in self.data["files"] if f["path"].endswith("DesignLAB/CLAUDE.md"))
        self.assertEqual(len(f["sha"]), 40)
        self.assertEqual(f["ownership"], "owned")
        self.assertEqual([s["heading"] for s in f["sections"]],
                         ["Release and copy rules", "Sessions", "Reports", "Landing"])
        self.assertEqual(self.data["triage"]["verdicts"]["OVER BUDGET"], 1)
        self.assertEqual(self.data["triage"]["budget"], 8000)
        ids = [it["id"] for it in self.data["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{6}", i) for i in ids))

    def test_several_folders_rank_by_reach(self):
        top = [self.items[i] for i in self.data["triage"]["start_here_by_reach"]]
        self.assertEqual(top[0]["verb"], "MOVE")
        self.assertEqual(self.where(top[0]), ("projects/atc/controller/CLAUDE.md", 45))
        # The global every-task step loads in every claude stack, so its reach puts it next.
        self.assertEqual(self.where(top[1])[0], ".claude/CLAUDE.md")

    def test_judge_reads_schema_2(self):
        items = judge.select_items(self.data, self.t.home)
        self.assertIn(f"cost:{self.p}/runup/CLAUDE.md:8", {it["id"] for it in items})


class ItemTest(unittest.TestCase):
    def setUp(self):
        self.h = Home()

    def tearDown(self):
        self.h.close()

    def test_id_stays_when_the_line_moves_and_differs_between_sibling_copies(self):
        r = self.h.repo("atc")
        line = "- Run the full check suite for every change."
        self.h.write("projects/atc/occ/CLAUDE.md", f"# Occ\n\n{line}\n")
        self.h.write("projects/atc/duty/CLAUDE.md", f"# Duty\n\n{line}\n")
        before = self.h.scan()
        a = at(before, r / "occ/CLAUDE.md", 3)[0]
        b = at(before, r / "duty/CLAUDE.md", 3)[0]
        self.assertNotEqual(a["id"], b["id"])
        self.h.write("projects/atc/occ/CLAUDE.md", f"# Occ\n\n{words('x')}.\n\n{line}\n")
        moved = at(self.h.scan(), r / "occ/CLAUDE.md", 5)[0]
        self.assertEqual(moved["id"], a["id"])
        self.assertEqual(moved["locations"][0]["hash"], a["locations"][0]["hash"])

    def test_text_is_whole_with_end_line_hash_and_columns(self):
        r = self.h.repo("app")
        long = "- Read the design notes before you start any task. " + words("long", 70)
        self.h.write("projects/app/CLAUDE.md", f"# App\n\n{long}\n  {words('cont')}.\n")
        data = self.h.scan()
        it = at(data, r / "CLAUDE.md", 3)[0]
        loc = it["locations"][0]
        self.assertEqual((loc["line"], loc["end_line"]), (3, 4))
        self.assertEqual(loc["text"], f"{long}\n  {words('cont')}.")
        self.assertGreater(len(loc["text"]), 400)
        self.assertEqual(loc["text"][loc["col_start"] - 1:loc["col_end"] - 1], it["match"])
        self.assertEqual(len(loc["hash"]), 12)
        # Schema 1 fields keep their old cut.
        self.assertEqual(len(next(f for f in data["flags"] if f["line"] == 3)["text"]), 240)

    def test_sections(self):
        text = "---\nname: x\n---\n# T\n## A\none\n\n## B\n```\n## not a heading\n```\ntwo\n\n\n### B1\nthree\n# U\n"
        self.assertEqual([(s["heading"], s["line_start"], s["line_end"]) for s in triage.sections(text)],
                         [("A", 5, 6), ("B", 8, 16)])

    def test_safe_keep_deletes_the_narrower_copy_and_never_a_shared_file(self):
        same = f"- Keep commit messages short: {words('same')}."
        mirror = f"- Write the report in plain words: {words('mirror')}."
        self.h.write(".claude/CLAUDE.md", f"# Global\n\n{same}\n\n{mirror}\n")
        r = self.h.repo("app")
        self.h.write("projects/app/CLAUDE.md", f"# App\n\n@AGENTS.md\n\n{same}\n\n{words('a1')}.\n")
        self.h.write("projects/app/AGENTS.md", f"# Agents\n\n{mirror}\n\n{words('a2')}.\n")
        data = self.h.scan(r)
        e = data["triage"]["stacks"][f"{r}@claude"]
        dele = at(data, r / "CLAUDE.md", 5)[0]
        self.assertEqual((dele["verb"], dele["label"]), ("DELETE", "loaded_twice"))
        self.assertEqual([l["role"] for l in dele["locations"]], ["delete", "keep"])
        self.assertEqual(dele["locations"][1]["file"], str(self.h.home / ".claude/CLAUDE.md"))
        self.assertIn(dele["id"], e["start_here"])
        # Codex loads AGENTS.md but not ~/.claude/CLAUDE.md: no side is safe to delete.
        m = at(data, r / "AGENTS.md", 3)[0]
        self.assertEqual((m["verb"], m["label"], m["certainty"]), ("CHECK", "different_agents", "candidate"))
        self.assertNotIn(m["id"], e["start_here"])
        self.assertIn(m["id"], e["groups"]["everything_else"])

    def test_consecutive_copies_are_one_item(self):
        rules = [f"- Rule {n}: {words(f'r{n}')}." for n in range(6)]
        self.h.write(".claude/CLAUDE.md", "# Global\n\n" + "\n".join(rules) + "\n")
        r = self.h.repo("app")
        own = "\n\n".join(f"{words(f'own{n}')}." for n in range(10))
        self.h.write("projects/app/CLAUDE.md", "# App\n\n" + "\n".join(rules) + "\n\n" + own + "\n")
        data = self.h.scan(r)
        dele = [it for it in data["items"] if it["verb"] == "DELETE"]
        self.assertEqual(len(dele), 1)
        it = dele[0]
        self.assertEqual((it["group_size"], it["group"]["kind"], it["group"]["loose"]), (6, "consecutive", False))
        # One location for each copied line: six to delete in the repo file, six to keep in the global file.
        delete = [l for l in it["locations"] if l["side"] == "a"]
        self.assertEqual([(l["line"], l["end_line"], l["role"]) for l in delete], [(n, n, "delete") for n in range(3, 9)])
        self.assertEqual([l["text"] for l in delete], rules)
        self.assertEqual({l["role"] for l in it["locations"] if l["side"] == "b"}, {"keep"})
        self.assertFalse([i for i in data["items"] if i["relation"] == "file_copy"])

    def test_one_rename_between_sibling_folders_is_one_item(self):
        r = self.h.repo("atc")
        self.h.write("projects/atc/CLAUDE.md", "# ATC\n\n" + words("root") + ".\n")
        rules = [f"- Send the result to {{}} with the board note {words(f's{n}', 4)}." for n in range(6)]
        self.h.write("projects/atc/occ/CLAUDE.md", "# Occ\n\n" + "\n".join(x.format("OCC") for x in rules) + "\n")
        self.h.write("projects/atc/duty/CLAUDE.md", "# Duty\n\n" + "\n".join(x.format("DUTY") for x in rules) + "\n")
        data = self.h.scan()
        copies = [it for it in data["items"] if it["scope"] == "not_together"]
        self.assertEqual(len(copies), 1)
        it = copies[0]
        self.assertEqual((it["relation"], it["group_size"]), ("sibling_copy", 6))
        self.assertIn(it["label"], ("copied_between", "drifted_copy"))
        row = data["triage"]["stacks"][f"{r}/duty@claude"]["groups"]["copies"][0]
        self.assertEqual((row["items"], row["parent"]), ([it["id"]], str(r / "CLAUDE.md")))

    def test_dated_lines_group_from_three(self):
        r = self.h.repo("app")
        dated = [f"- Decision 2026-0{n}-01: {words(f'd{n}')}." for n in range(1, 4)]
        self.h.write("projects/app/CLAUDE.md", "# App\n\n" + "\n\n".join(dated) + "\n")
        self.h.write("projects/app/sub/CLAUDE.md",
                     "# Sub\n\n" + "\n\n".join(f"- Decision 2026-0{n}-02: {words(f's{n}')}." for n in (1, 2)) + "\n")
        data = self.h.scan()
        group = at(data, r / "CLAUDE.md", 3)[0]
        self.assertEqual((group["label"], group["group_size"]), ("dated_lines", 3))
        self.assertEqual([it["label"] for it in data["items"] if it["locations"][0]["file"] == str(r / "sub/CLAUDE.md")],
                         ["dated", "dated"])

    def test_scopes_and_hidden_findings(self):
        r = self.h.repo("app")
        self.h.write("projects/app/CLAUDE.md", "# App\n\n" + words("app") + ".\n")
        self.h.write("projects/app/.claude/skills/x/SKILL.md", "---\nname: x\ndescription: d\n---\n- Always run the linter first.\n")
        self.h.write("projects/app/.cursorrules", "- Always run the formatter first.\n")
        (self.h.projects / "notes").mkdir(parents=True)
        self.h.write(f".claude/projects/{memory_slug(self.h.projects / 'notes')}/memory/MEMORY.md",
                     "- [Old](old.md) - done 2026-01-01.\n")
        data = self.h.scan()
        scopes = {Path(it["locations"][0]["file"]).name: it["scope"] for it in data["items"]}
        self.assertEqual(scopes, {"SKILL.md": "on_demand", ".cursorrules": "other_agent", "MEMORY.md": "elsewhere"})
        self.assertEqual(data["triage"]["hidden"], {"elsewhere": 1})
        e = data["triage"]["stacks"][f"{r}@claude"]
        self.assertEqual((e["verdict"], e["start_here"], e["counts"]["on_demand"], e["counts"]["other_agents"]),
                         ("LEAN", [], 1, 1))

    def test_verdict_thresholds(self):
        r = self.h.repo("app")
        steps = [f"- Check the logs for every task: {words(f'c{n}')}." for n in range(3)]
        self.h.write("projects/app/CLAUDE.md", "# App\n\n" + "\n\n".join(steps[:2]) + "\n")
        key = f"{r}@claude"
        self.assertEqual(self.h.scan(r)["triage"]["stacks"][key]["verdict"], "LEAN")
        self.assertEqual(self.h.scan(r, budget=10)["triage"]["stacks"][key]["verdict"], "OVER BUDGET")
        self.h.write("projects/app/CLAUDE.md", "# App\n\n" + "\n\n".join(steps) + "\n")
        e = self.h.scan(r)["triage"]["stacks"][key]
        self.assertEqual((e["verdict"], e["reasons"]), ("TRIM", ["candidates"]))
        # Certain DELETE lines of 100 or more estimated tokens.
        same = f"- {words('big', 60)}."
        self.h.write("projects/app/CLAUDE.md", f"# App\n\n{same}\n\n{words('gap')}.\n\n{same}\n")
        e = self.h.scan(r)["triage"]["stacks"][key]
        self.assertEqual((e["verdict"], e["reasons"]), ("TRIM", ["delete_tokens"]))


class ReviewFixTest(unittest.TestCase):
    """One test for each defect that the review of 32f6f7e found (numbered as in the review)."""

    def setUp(self):
        self.h = Home()

    def tearDown(self):
        self.h.close()

    def stack(self, data, rel, agent="claude"):
        return data["triage"]["stacks"][f"{self.h.projects / rel}@{agent}"]

    def test_1_same_named_checkouts_keep_their_own_items(self):
        line = "- Run the full check suite for every change."
        big = "## Big\n\n" + "\n\n".join(f"{words(f'big{n}', 40)}." for n in range(60)) + "\n"
        for client in ("clientA", "clientB"):
            self.h.repo(f"{client}/web")
            dated = "\n\n".join(f"- Decision 2026-0{n}-0{n}: {words(f'{client}{n}')}." for n in range(1, 4))
            self.h.write(f"projects/{client}/web/CLAUDE.md", f"# Web\n\n{line}\n\n{dated}\n\n{big}")
        data = self.h.scan()
        items = by_id(data)
        for client in ("clientA", "clientB"):
            key = f"{self.h.projects / client / 'web'}@claude"
            mine = [items[i] for i in data["triage"]["stacks"][key]["start_here"]]
            self.assertEqual({it["label"] for it in mine}, {"every_task_step", "large_layer", "dated_lines"}, client)
            for it in mine:
                self.assertEqual(it["stacks"], [key])
                self.assertEqual({l["file"] for l in it["locations"]}, {str(self.h.projects / client / "web/CLAUDE.md")})
        # The ids of a folder do not depend on the roots of the scan.
        a = str(self.h.projects / "clientA")
        alone = self.h.scan(self.h.projects / "clientA" / "web")
        self.assertEqual({it["id"] for it in alone["items"] if it["locations"][0]["file"].startswith(a)},
                         {it["id"] for it in data["items"] if it["locations"][0]["file"].startswith(a)})

    def test_2_a_delete_run_names_only_the_copied_lines(self):
        rules = [f"- Rule {n}: {words(f'r{n}')}." for n in range(5)]
        self.h.write(".claude/CLAUDE.md", "# Global\n\n" + "\n".join(rules) + "\n")
        r = self.h.repo("app")
        filler = "\n\n" + "\n\n".join(f"{words(f'own{n}')}." for n in range(10)) + "\n"
        own = f"- {words('mine')}."
        self.h.write("projects/app/CLAUDE.md", "# App\n\n" + "\n".join(rules[:2] + [own] + rules[2:]) + filler)
        dele = [it for it in self.h.scan(r)["items"] if it["verb"] == "DELETE"]
        self.assertEqual(len(dele), 1)
        self.assertEqual([l["line"] for l in dele[0]["locations"] if l["side"] == "a"], [3, 4, 6, 7, 8])
        # A rule edited in the copy is a conflict, and the DELETE does not name it.
        words2 = rules[2].split()
        edited = " ".join(words2[:-2] + ["deploy", "never on fridays."])
        self.h.write("projects/app/CLAUDE.md", "# App\n\n" + "\n".join(rules[:2] + [edited] + rules[3:]) + filler)
        data = self.h.scan(r)
        dele = [it for it in data["items"] if it["verb"] == "DELETE"]
        self.assertEqual([l["line"] for it in dele for l in it["locations"] if l["side"] == "a"], [3, 4, 6, 7])
        conflict = at(data, r / "CLAUDE.md", 5)
        self.assertEqual([(it["label"], it["verb"]) for it in conflict], [("conflict", "PICK ONE")])

    def test_3_a_conflict_between_shared_files_makes_the_stack_trim(self):
        base = f"- Rule: {words('g', 9)}."
        self.h.write(".claude/CLAUDE.md", "# Global\n\n" + base + "\n")
        self.h.write("projects/CLAUDE.md", "# Projects\n\n" + base.rsplit(" ", 2)[0] + " deploy never.\n")
        self.h.repo("app")
        self.h.write("projects/app/CLAUDE.md", "# App\n\n" + words("app") + ".\n")
        data = self.h.scan()
        e = self.stack(data, "app")
        self.assertEqual((e["verdict"], e["reasons"], e["start_here"]), ("TRIM", ["conflict"], []))
        conflict = [it for it in data["items"] if it["label"] == "conflict"]
        self.assertEqual(len(conflict), 1)
        self.assertIn(conflict[0]["id"], e["groups"]["shared"][0]["items"] + e["groups"]["shared"][1]["items"])

    def test_4_a_parent_file_outside_every_checkout_is_shared(self):
        self.h.write("projects/CLAUDE.md", "# Projects\n\n- Run the linter for every change.\n")
        for name in ("app", "api"):
            self.h.repo(name)
            self.h.write(f"projects/{name}/CLAUDE.md", f"# {name}\n\n{words(name)}.\n")
        data = self.h.scan()
        own = {f["path"]: f["ownership"] for f in data["files"]}
        self.assertEqual(own[str(self.h.projects / "CLAUDE.md")], "shared")
        self.assertEqual(own[str(self.h.projects / "app/CLAUDE.md")], "owned")
        it = at(data, self.h.projects / "CLAUDE.md", 3)[0]
        self.assertEqual(it["ownership"], "shared")
        for name in ("app", "api"):
            row = self.stack(data, name)["groups"]["shared"][0]
            self.assertEqual((row["file"], row["items"]), (str(self.h.projects / "CLAUDE.md"), [it["id"]]))

    def test_5_mirrors_count_toward_trim(self):
        lines = [f"- Write the report in plain words {n}: {words(f'm{n}')}." for n in range(3)]
        def body(seed):  # each line, then four filler paragraphs of its own
            return "\n\n".join(line + "\n\n" + "\n\n".join(f"{words(f'{seed}{i}-{n}')}." for n in range(4))
                               for i, line in enumerate(lines)) + "\n"
        self.h.write(".claude/CLAUDE.md", "# Global\n\n" + body("gap"))
        r = self.h.repo("app")
        self.h.write("projects/app/CLAUDE.md", "# App\n\n@AGENTS.md\n")
        self.h.write("projects/app/AGENTS.md", "# Agents\n\n" + body("own"))
        data = self.h.scan(r)
        e = self.stack(data, "app")
        mirrors = [it["id"] for it in data["items"] if it["label"] == "different_agents"]
        self.assertEqual(len(mirrors), 3)
        self.assertEqual((e["verdict"], e["reasons"], e["checks"]["candidates"], e["to_read"]),
                         ("TRIM", ["candidates"], 3, 3))
        self.assertEqual((e["start_here"], set(e["groups"]["everything_else"])), ([], set(mirrors)))

    def test_6_symlinked_files_keep_their_stacks(self):
        self.h.repo("dotfiles")
        self.h.write("projects/dotfiles/CLAUDE.md", "# Dotfiles\n\n- Run the full test suite for every change.\n")
        (self.h.home / ".claude" / "CLAUDE.md").symlink_to(self.h.projects / "dotfiles" / "CLAUDE.md")
        self.h.repo("app")
        self.h.write("projects/app/CLAUDE.md", "# App\n\n" + words("app") + ".\n")
        data = self.h.scan()
        it = at(data, self.h.projects / "dotfiles/CLAUDE.md", 3)[0]
        self.assertEqual((it["scope"], it["ownership"]), ("session_start", "shared"))
        app = f"{self.h.projects / 'app'}@claude"
        self.assertIn(app, it["stacks"])
        self.assertIn(it["id"], data["triage"]["stacks"][app]["groups"]["shared"][0]["items"])
        # A CLAUDE.md that links to AGENTS.md: both agents load the line.
        r = self.h.repo("both")
        self.h.write("projects/both/AGENTS.md", "# Both\n\n- Check the logs for every task.\n")
        (r / "CLAUDE.md").symlink_to("AGENTS.md")
        data = self.h.scan(r)
        files = [f["path"] for f in data["files"] if f["path"].startswith(str(r))]
        self.assertEqual(len(files), 1)
        it = at(data, Path(files[0]), 3)[0]
        self.assertEqual(it["stacks"], [f"{r}@claude", f"{r}@codex"])
        for agent in ("claude", "codex"):
            self.assertEqual(self.stack(data, "both", agent)["start_here"], [it["id"]])

    def test_7_a_home_behind_a_symlink_gives_the_same_items(self):
        mirror = f"- Write the report in plain words: {words('mirror')}."
        self.h.write(".claude/CLAUDE.md", f"# Global\n\n{mirror}\n")
        self.h.repo("app")
        self.h.write("projects/app/CLAUDE.md", "# App\n\n@AGENTS.md\n")
        self.h.write("projects/app/AGENTS.md", f"# Agents\n\n{mirror}\n\n{words('a2')}.\n")
        link = self.h.root / "link"
        link.symlink_to(self.h.home)
        # The @import target resolves to the real path; the walked files keep the link path.
        data = scan([link / "projects" / "app"], link)
        m = at(data, link / "projects/app/AGENTS.md", 3)[0]
        self.assertEqual((m["label"], m["relation"], m["scope"]), ("different_agents", "same_stack", "session_start"))
        self.assertIn(m["id"], data["triage"]["stacks"][f"{link / 'projects/app'}@claude"]["groups"]["everything_else"])

    def test_8_the_test_file_imports_when_run_by_path(self):
        root = Path(__file__).resolve().parent.parent
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        done = subprocess.run([sys.executable, "-c", "import runpy; runpy.run_path('tests/test_triage.py', run_name='x')"],
                              cwd=root, env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_9_topic_pairs_wait_for_a_judge(self):
        r = self.h.repo("app")
        self.h.write("projects/app/CLAUDE.md",
                     "# App\n\n- Send the result to `OCC` with SendMessage when the run ends.\n\n"
                     f"{words('x')}.\n\n- Do not send messages to `OCC` during a review {words('y', 4)}.\n")
        data = self.h.scan(r)
        clash = [it for it in data["items"] if it["rule"] == "topic"]
        self.assertEqual(len(clash), 1)
        it = clash[0]
        self.assertEqual((it["label"], it["verb"], it["certainty"], it["relation"]),
                         ("term_clash", "CHECK", "candidate", "same_file"))
        e = self.stack(data, "app")
        self.assertEqual((e["verdict"], e["checks"]["conflicts"], e["checks"]["candidates"], e["start_here"]),
                         ("LEAN", 0, 0, []))
        self.assertEqual(e["groups"]["everything_else"], [it["id"]])

    def test_10_a_far_off_match_does_not_break_a_run(self):
        rows = [(2, 11), (3, 12), (4, 13), (2, 26)]
        self.assertEqual(triage.chain_runs(rows, lambda r: r[0], lambda r: r[1]),
                         [[(2, 11), (3, 12), (4, 13)], [(2, 26)]])


class RunRecordTest(unittest.TestCase):
    def setUp(self):
        self.h = Home()
        self.h.repo("app")
        self.h.write("projects/app/CLAUDE.md", "# App\n\n- Run the tests for every change.\n")
        self.out = self.h.root / "runs"

    def tearDown(self):
        self.h.close()

    def run_cli(self, *args):
        with redirect_stdout(io.StringIO()), mock.patch("walle.cli.scan", lambda roots: scan(roots, self.h.home)):
            self.assertEqual(cli.main(["--out", str(self.out), "scan", *args]), 0)

    def test_index_lines_link_each_run_to_the_previous_scan_of_its_roots(self):
        app = str(self.h.projects / "app")
        self.run_cli(app, "--label", "before")
        self.run_cli(app)
        self.run_cli(str(self.h.projects))
        rows = runs.read_index(self.out)
        self.assertEqual(len(rows), 3)
        self.assertEqual((rows[0]["parent_id"], rows[0]["label"]), (None, "before"))
        self.assertEqual(rows[1]["parent_id"], rows[0]["id"])
        self.assertIsNone(rows[2]["parent_id"])  # other roots
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])
        data = json.loads(Path(rows[1]["path"]).read_text())
        run = data["run"]
        self.assertEqual((run["id"], run["kind"], run["parent_id"], run["estimator"]),
                         (rows[1]["id"], "scan", rows[0]["id"], "bytes-v1"))
        self.assertEqual(run["rules_hash"], triage.rules_hash())
        self.assertEqual(run["roots"], [app])
        self.assertEqual(rows[1]["per_folder"], {f"{app}@claude": data["stacks"][0]["tokens_est"],
                                                 f"{app}@codex": data["stacks"][1]["tokens_est"]})
        self.assertEqual((rows[1]["open"], rows[1]["kept"], rows[1]["verdicts"][f"{app}@claude"]), (1, 0, "LEAN"))
        self.assertEqual(Path(rows[1]["path"]).parent.name, rows[1]["id"])

    def test_rules_hash_follows_the_thresholds(self):
        h = triage.rules_hash()
        self.assertRegex(h, r"^[0-9a-f]{40}$")
        with mock.patch.object(triage, "SAFE_DELETE", 0.9):
            self.assertNotEqual(triage.rules_hash(), h)
        self.assertEqual(triage.rules_hash(), h)

    def test_git_head(self):
        git = self.h.projects / "app" / ".git"
        (git / "refs" / "heads").mkdir(parents=True)
        (git / "HEAD").write_text("ref: refs/heads/main\n")
        (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n")
        self.assertEqual(runs.git_head(self.h.projects / "app"), "a" * 40)
        (git / "refs" / "heads" / "main").unlink()
        (git / "packed-refs").write_text(f"# pack-refs\n{'b' * 40} refs/heads/main\n")
        self.assertEqual(runs.git_head(self.h.projects / "app"), "b" * 40)
        self.assertIsNone(runs.git_head(self.h.root))


if __name__ == "__main__":
    unittest.main()
