import re
import string
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from real0817 import Tree as RealTree  # noqa: E402

from walle import strings, triage  # noqa: E402
from walle.scan import scan  # noqa: E402
from walle.strings import STRINGS, UnknownString, approx, label, resolve_lang, round_tokens, t, tn  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FILM = re.compile(r"WALL-E|Wall-E|WALL·E|\bEVE\b")


def fields(text: str) -> set[str]:
    return {f for _lit, f, _spec, _conv in string.Formatter().parse(text) if f is not None}


class TableTest(unittest.TestCase):
    def test_every_key_has_both_languages_with_the_same_fields(self):
        for key, (en, ko) in STRINGS.items():
            self.assertTrue(en.strip() and ko.strip(), key)
            self.assertEqual(fields(en), fields(ko), key)

    def test_unknown_key_missing_field_and_unknown_language_raise(self):
        with self.assertRaises(UnknownString):
            t("verdict.FINE")
        with self.assertRaises(KeyError):
            t("label.dated_lines", "en")
        with self.assertRaises(ValueError):
            t("verdict.LEAN", "fr")

    def test_every_engine_value_has_a_string(self):
        for lang in strings.LANGS:
            for v in (triage.OVER, triage.TRIM, triage.LEAN):
                t(f"verdict.{v}", lang)
            for v in triage.VERBS:
                t(f"verb.{v}", lang)
            for c in ("certain", "candidate", "info"):
                t(f"certainty.{c}", lang)
            for name in triage.LABELS:
                self.assertIn(f"label.{name}", STRINGS)
        self.assertEqual(set(triage.SEVERITY) - set(triage.LABELS), set())
        self.assertEqual({v for _l, v in triage.FLAG_LABEL.values()} - set(triage.VERBS), set())

    def test_every_item_of_a_real_shaped_scan_has_a_label(self):
        tree = RealTree()
        try:
            data = scan([tree.projects], tree.home)
        finally:
            tree.close()
        for it in data["items"]:
            for lang in strings.LANGS:
                text = label(it, lang)
                self.assertNotIn("{", text)
                if it["verb"]:
                    t(f"verb.{it['verb']}", lang)
        memory = next(it for it in data["items"] if it["label"] == "memory_lines")
        self.assertEqual(label(memory), "7 memory lines: still true?")
        self.assertEqual(label(memory, "ko"), "메모리 줄 7개: 아직 맞나?")
        note = next(it for it in data["items"] if it["label"] == "source_of")
        self.assertEqual(label(note, short=lambda p: "~/.claude/CLAUDE.md"), "source of ~/.claude/CLAUDE.md")

    def test_counts(self):
        self.assertEqual(tn("sentence.to_read", 1), "1 item to read in your files.")
        self.assertEqual(tn("sentence.to_read", 2), "2 items to read in your files.")
        self.assertEqual(tn("sentence.to_read", 2, "ko"), "내 파일에서 읽어 볼 항목이 2개다.")

    def test_tone(self):
        for key, (en, ko) in STRINGS.items():
            for text in (en, ko):
                self.assertNotIn("!", text, key)
            self.assertNotRegex(en, r"(?i)\bplease\b|\bgreat job\b|\bI (?:found|think|see)\b", key)
            # One fact per sentence, 20 words or fewer.
            for sentence in re.split(r"(?<=[.?])\s+(?=[A-Z\"])", en):
                self.assertLessEqual(len(sentence.split()), 20, f"{key}: {sentence}")


class NameTest(unittest.TestCase):
    def test_no_film_names_in_display_text(self):
        files = [*ROOT.glob("walle/*.py"), *ROOT.glob("README*.md"), ROOT / "ROADMAP.md",
                 *ROOT.glob("skills/**/*.md"), *ROOT.glob(".claude-plugin/*.json"), *ROOT.glob("hooks/*")]
        hits = [f"{p.relative_to(ROOT)}:{n}" for p in files if p.is_file()
                for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1) if FILM.search(line)]
        self.assertEqual(hits, [])
        self.assertEqual([k for k, pair in STRINGS.items() if any(FILM.search(s) for s in pair)], [])


class LangTest(unittest.TestCase):
    def test_flag_then_environment(self):
        self.assertEqual(resolve_lang("ko", {}), "ko")
        self.assertEqual(resolve_lang(None, {"LANG": "ko_KR.UTF-8"}), "ko")
        self.assertEqual(resolve_lang(None, {"LC_ALL": "C.UTF-8", "LANG": "ko_KR.UTF-8"}), "en")
        self.assertEqual(resolve_lang(None, {"LC_ALL": "", "LANG": "ko_KR.UTF-8"}), "ko")
        self.assertEqual(resolve_lang(None, {}), "en")
        with self.assertRaises(ValueError):
            resolve_lang("de", {})


class NumberTest(unittest.TestCase):
    def test_rounding(self):
        # Values of the 08:17 run as the design brief writes them.
        cases = [(2477, "2,500"), (689, "690"), (1297, "1,300"), (491, "490"), (12732, "12,700"), (0, "0"), (4, "10")]
        for n, text in cases:
            self.assertEqual(round_tokens(n, 8000), text)
        self.assertEqual(approx(2477), "~2,500")

    def test_near_the_budget(self):
        self.assertEqual(round_tokens(7951, 8000), "7,950")
        self.assertEqual(round_tokens(8100, 8000), "8,100")
        # A count under the budget is never written as the budget.
        self.assertEqual(round_tokens(7996, 8000), "7,990")
        self.assertEqual(round_tokens(8004, 8000), "8,010")
        self.assertEqual(round_tokens(8000, 8000), "8,000")
        self.assertEqual(round_tokens(7996), "8,000")


if __name__ == "__main__":
    unittest.main()
