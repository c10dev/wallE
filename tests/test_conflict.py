import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from walle import cli, conflict, judge, report, report_html
from walle.conflict import info, key_terms, negated, opposite, variants
from walle.scan import scan
from walle.segment import Unit

# The three shapes of the manual audit, in small form:
# 1. one file: "do not send messages" and "send it to DISPATCH(OCC)".
# 2. parent and child that load together: "rules do not apply here" and "root rules apply as-is".
# 3. two repositories: `READBACK D-xxxx @xxxxxx` and `READBACK D-xxxx`.
ATC = """# atc 작업 규칙

**관제 세션은 제외.** `controller/`(TOWER), `duty/`(DUTY) 폴더에서 연 세션은 그 폴더의 `CLAUDE.md`를 따르고, 코드를 고치지 않으므로 아래 작업 규칙은 적용되지 않는다.

## 교신

- atc OCC에서 `[DISPATCH D-xxxx]`로 시작하는 FLIGHT PLAN을 받으면 `READBACK D-xxxx @xxxxxx`(끝줄 그대로)로 답하고, 못 하면 `UNABLE D-xxxx — <사유>`로 답한다.
"""
DUTY = """# DUTY

- **다른 세션에 메시지를 보내지 않는다.** `SendMessage`는 도구 목록에 없다. 팀에 일을 보내는 것은 DISPATCH·OCC와 사용자 몫이다.

## 설계

루트 `CLAUDE.md`의 규칙이 그대로 적용된다. 이 절은 DUTY가 그 규칙을 어떻게 쓰는지다.

## 막힐 때

- SUPERVISOR 몫이 아닌 부탁(다시 보내기, STAND 정리)은 카드가 아니라 DUTY나 DISPATCH(OCC)에 보낸다.
"""
APP = """# atc-app

- atc OCC의 `[DISPATCH D-xxxx]` FLIGHT PLAN에는 `READBACK D-xxxx`(못 하면 `UNABLE D-xxxx — <이유>`)로 답한다.
"""


def u(text, line=1, file="f.md"):
    return info(Unit(file, line, "", text))


class HelpersTest(unittest.TestCase):
    def test_key_terms(self):
        self.assertEqual(key_terms("DUTY가 `SendMessage`를 쓰고 `duty charter`를 남긴다. NEVER 한다."),
                         {"DUTY", "SendMessage", "`duty charter`"})

    def test_korean_opposite_matches_conjugated_verb(self):
        self.assertEqual(opposite(u("다른 세션에 메시지를 보내지 않는다."), u("부탁은 DISPATCH에 보낸다.")), ["보내"])
        self.assertEqual(opposite(u("아래 규칙은 적용되지 않는다."), u("루트 규칙이 그대로 적용된다.")), ["적용되"])
        # Both sides say "do not": no conflict in sign.
        self.assertEqual(opposite(u("메시지를 보내지 않는다."), u("DISPATCH에도 보내지 말 것.")), [])
        # One-syllable stems match too many words and are left out.
        self.assertEqual(negated("파일을 쓰지 않는다."), set())

    def test_english_opposite(self):
        self.assertEqual(opposite(u("Do not send messages to other sessions."), u("Send the report to OCC.")), ["send"])
        self.assertEqual(opposite(u("Never merge a PR."), u("The SUPERVISOR merges it.")), ["merg"])

    def test_message_forms(self):
        a = u("Answer `READBACK D-xxxx @xxxxxx` or `STANDBY R-7 — <why>` or `TEAM_X → OCC ARRIVED …`.")
        b = u("Answer `READBACK D-xxxx` or `STANDBY R-7 — <progress>` or `TEAM_X → OCC ARRIVED ATC-n`.")
        # A placeholder name and a trailing "…" are not a different form.
        self.assertEqual(variants(a, b), [{"message": "READBACK D-xxxx", "a": ["READBACK D-xxxx @xxxxxx"],
                                           "b": ["READBACK D-xxxx"]}])
        # A side that only writes fewer forms has no form of its own.
        self.assertEqual(variants(u("Use `READBACK D-xxxx RECALL`."), u("Use `READBACK D-xxxx` or `READBACK D-xxxx RECALL`.")), [])


class ScanConflictTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.projects = self.home / "projects"
        for repo in ("atc", "atc-app"):
            (self.projects / repo / ".git").mkdir(parents=True)
        (self.home / ".claude").mkdir(parents=True)
        self.atc = self.write("atc/CLAUDE.md", ATC)
        self.duty = self.write("atc/duty/CLAUDE.md", DUTY)
        self.app = self.write("atc-app/CLAUDE.md", APP)
        self.data = scan([self.projects], self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.projects / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return str(p)

    def pairs(self, kind):
        return {(p["a"]["file"], p["a"]["line"], p["b"]["file"], p["b"]["line"]): p
                for p in self.data["pairs"] if p["kind"] == kind}

    def test_all_three_shapes(self):
        topic = self.pairs("topic")
        same_file = topic[(self.duty, 3, self.duty, 11)]
        self.assertEqual(same_file["opposite"], ["보내"])
        self.assertEqual(same_file["shared"], ["DISPATCH", "OCC"])
        parent_child = topic[(self.atc, 3, self.duty, 7)]
        self.assertEqual(parent_child["opposite"], ["적용되"])
        self.assertIn("`CLAUDE.md`", parent_child["shared"])
        self.assertEqual(len(topic), 2)
        proto = self.pairs("protocol")
        self.assertEqual(list(proto), [(self.app, 3, self.atc, 7)])
        self.assertEqual(proto[(self.app, 3, self.atc, 7)]["variants"],
                         [{"message": "READBACK D-xxxx", "a": ["READBACK D-xxxx"], "b": ["READBACK D-xxxx @xxxxxx"]}])

    def test_report_and_page_show_them(self):
        md = report.render_scan(self.data)
        self.assertIn("## 4. 중복, drift, 충돌 후보", md)
        self.assertIn("### topic", md)
        self.assertIn("부정이 갈린 말: 보내", md)
        self.assertIn("형식: READBACK D-xxxx ↔ READBACK D-xxxx @xxxxxx", md)
        self.assertIn('<option value="protocol">', report_html.render(self.data))

    def test_judge_reads_them_as_conflicts(self):
        items = {tuple(it["refs"]): it for it in judge.select_items(self.data)}
        same_file = items[(f"{self.duty}:3", f"{self.duty}:11")]
        self.assertEqual((same_file["type"], same_file["pair"]), ("conflict", "topic"))
        self.assertEqual(same_file["ask"]["loaded"], "session_start")
        self.assertEqual(same_file["ask"]["shared"], ["DISPATCH", "OCC"])
        self.assertEqual(items[(f"{self.atc}:3", f"{self.duty}:7")]["ask"]["loaded"], "session_start")
        cross = items[(f"{self.app}:3", f"{self.atc}:7")]
        self.assertEqual((cross["type"], cross["pair"]), ("conflict", "protocol"))
        self.assertEqual(cross["ask"]["loaded"], "never")
        self.assertIn("@xxxxxx", cross["ask"]["b"]["text"])

    def test_dry_run_lists_items_by_file_and_line(self):
        runs = Path(self.tmp.name) / "runs"
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("walle.cli.scan", lambda roots: self.data), \
                mock.patch.object(judge, "CACHE_FILE", Path(self.tmp.name) / "cache.json"):
            self.assertEqual(cli.main(["--out", str(runs), "scan", str(self.projects)]), 0)
            self.assertEqual(cli.main(["--out", str(runs), "judge", "--dry-run"]), 0)
        text = out.getvalue()
        self.assertIn("conflict candidates: 3 (drift 0, topic 2, protocol 1)", text)
        self.assertIn(f"topic    {self.duty}:3 <> {self.duty}:11  [DISPATCH, OCC]", text)
        self.assertIn(f"topic    {self.atc}:3 <> {self.duty}:7", text)
        self.assertIn(f"protocol {self.app}:3 <> {self.atc}:7  [READBACK D-xxxx]", text)
        self.assertIn("Nothing was written.", text)

    def test_shared_term_alone_is_not_a_candidate(self):
        self.write("atc/duty/CLAUDE.md", DUTY + "\n- `SendMessage`로 OCC에 결과를 보고한다. DISPATCH 줄도 읽는다.\n")
        data = scan([self.projects], self.home)
        lines = {(p["a"]["line"], p["b"]["line"]) for p in data["pairs"]
                 if p["kind"] == "topic" and p["a"]["file"] == p["b"]["file"] == self.duty}
        self.assertEqual(lines, {(3, 11)})

    def test_cap_per_stack(self):
        with mock.patch.object(conflict, "PER_STACK", 1):
            data = scan([self.projects], self.home)
        self.assertEqual(sum(1 for p in data["pairs"] if p["kind"] == "topic"), 1)


if __name__ == "__main__":
    unittest.main()
