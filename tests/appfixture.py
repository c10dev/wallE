"""A fake home for the walle app tests and screenshots: a scan, context runs, a measure run and a trim plan
made by the real walle commands, plus a judge.json and a keep.json written by hand.

The tree is real0817.Tree (home/ with projects/). One more project has markup in its folder name and one
scan has markup in its label, so the tests can check that the app escapes paths and labels. Sessions are the
synthetic transcripts of sessions.py. Nothing outside the temporary folder is read or written: every command
runs with HOME set to the fake home.

    python3 tests/appfixture.py <folder>   builds the fixture there and prints its home and runs folder.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent))
from real0817 import Tree, fill, layout  # noqa: E402
from sessions import claude_session  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MARKUP = '<img src=x onerror="alert(1)">'
LABEL = '<script>alert("label")</script> & baseline'
CHILD = "import sys; sys.path.insert(0, sys.argv.pop(1)); from walle.cli import main; sys.exit(main())"


def child(home: Path, out: Path, *args) -> tuple[list[str], dict]:
    """The argv and env that run the walle CLI of this checkout with the fake home."""
    env = {**os.environ, "HOME": str(home), "NO_COLOR": "1", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    return [sys.executable, "-I", "-X", "utf8", "-c", CHILD, str(ROOT), "--out", str(out), *map(str, args)], env


def walle(home: Path, out: Path, *args, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the walle CLI of this checkout with the fake home."""
    argv, env = child(home, out, *args)
    return subprocess.run(argv, cwd=str(cwd or home), env=env, capture_output=True, text=True, timeout=300)


def _ok(p: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
    if p.returncode != 0:
        raise RuntimeError(f"walle {p.args[7:]} exited {p.returncode}: {p.stderr[-2000:]}")
    return p


class Fixture:
    def __init__(self, base: Path | None = None):
        self.tree = Tree()
        self.home = self.tree.home
        self.out = self.home / ".walle" / "runs"
        self.projects = self.tree.projects
        self.app = self.projects / "atc"
        self.markup = self.projects / MARKUP
        self.markup.mkdir(parents=True, exist_ok=True)
        (self.markup / "CLAUDE.md").write_text(fill(layout(20, {1: "# Markup folder"}), "markup", 300))
        self._sessions()
        self.scan = self._scan()
        self.context = _ok(walle(self.home, self.out, "context", "--session", "atc-23", "--json", cwd=self.app))
        _ok(walle(self.home, self.out, "context", "--usage", "--json", cwd=self.app))
        _ok(walle(self.home, self.out, "eve", self.app, "--around", self.split, "--label", "after the trim"))
        self.trim = walle(self.home, self.out, "trim", "--plan", cwd=self.app)
        self._judge()
        self._keep()

    def close(self):
        self.tree.close()

    def _sessions(self) -> None:
        """24 sessions in atc, 12 before the split date and 12 after it, and 6 in runup."""
        projects = self.home / ".claude" / "projects"
        start = datetime(2026, 10, 1, 9, 0)
        servers = {"linear": ["list_issues", "get_issue", "save_issue"], "figma": ["get_screenshot", "get_design_context"],
                   "rea": [f"tool_{i}" for i in range(12)]}
        instructions = {"linear": "Use Linear for issues. " * 20, "rea": "Reverse engineering tools. " * 40}
        skills = ("glossary", "report", "deploy-check", "release-notes")
        for i in range(24):
            when = start + timedelta(days=i, hours=i % 5)
            before = i < 12
            calls = ("Read", "Grep", "Edit", "mcp__linear__list_issues") if i % 3 == 0 else ("Read", "Edit", "Bash")
            claude_session(projects, f"atc-{i:02d}", self.app, when.isoformat() + "Z",
                           total=(52000 if before else 44000) + (i % 7) * 900, entrypoint="cli" if i % 2 else "claude-desktop",
                           skills=skills, servers=servers if before else {"linear": servers["linear"]},
                           instructions=instructions if before else {"linear": instructions["linear"]}, calls=calls)
        for i in range(6):
            claude_session(projects, f"runup-{i}", self.projects / "runup", (start + timedelta(days=i)).isoformat() + "Z",
                           total=38000 + i * 500, skills=skills[:2], calls=("Read",))
        self.split = (start + timedelta(days=12)).date().isoformat()

    def _scan(self) -> dict:
        _ok(walle(self.home, self.out, "scan", self.projects, "--label", LABEL, cwd=self.projects))
        rows = [json.loads(l) for l in (self.out / "index.jsonl").read_text().splitlines()]
        row = [r for r in rows if r["kind"] == "scan"][-1]
        return json.loads(Path(row["path"]).read_text())

    def _judge(self) -> None:
        """judge.json next to the scan: one real conflict, one not-a-problem conflict, one cost item not judged."""
        rows = [json.loads(l) for l in (self.out / "index.jsonl").read_text().splitlines()]
        scan_path = Path([r for r in rows if r["kind"] == "scan"][-1]["path"])
        locs = [(l["file"], l["line"], l.get("text") or "") for it in self.scan["items"] for l in it["locations"]
                if l.get("line") and l["file"].startswith(str(self.app))][:6]
        while len(locs) < 6:
            locs.append((str(self.app / "CLAUDE.md"), len(locs) + 1, "Filler rule."))
        long = ("Run `npm test` before every commit and keep the output in the PR body. " * 4).strip()
        items = [
            {"id": f"drift:{locs[0][0]}:{locs[0][1]}|{locs[1][0]}:{locs[1][1]}", "type": "conflict", "pair": "drift",
             "refs": [f"{locs[0][0]}:{locs[0][1]}", f"{locs[1][0]}:{locs[1][1]}"], "texts": [long, locs[1][2]],
             "verdict": {"real": True, "keep": "a", "fix": "Keep the first rule and delete the `second` one.",
                         "why": "The two lines give different test commands."}, "judge": "claude-opus-5-5", "from_cache": False},
            {"id": f"topic:{locs[2][0]}:{locs[2][1]}|{locs[3][0]}:{locs[3][1]}", "type": "conflict", "pair": "topic",
             "refs": [f"{locs[2][0]}:{locs[2][1]}", f"{locs[3][0]}:{locs[3][1]}"], "texts": [locs[2][2], locs[3][2]],
             "verdict": {"real": False, "keep": "both", "fix": "-", "why": "They cover different folders."},
             "judge": "claude-opus-5-5", "from_cache": True},
            {"id": f"cost:{locs[4][0]}:{locs[4][1]}", "type": "cost", "refs": [f"{locs[4][0]}:{locs[4][1]}"],
             "texts": [MARKUP + " " + locs[4][2]], "verdict": None},
        ]
        data = {"generated": "2026-10-21T06:00:00", "scan": str(scan_path), "scan_generated": self.scan["generated"],
                "plan": {}, "files": [], "errors": [], "items": items}
        (scan_path.parent / "judge.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))

    def _keep(self) -> None:
        keep = {"version": 1, "entries": [{"id": "86ac3d", "kind": "source", "source": "claude.ai connector",
                                           "key": "claude.ai:vercel", "name": "claude.ai Vercel",
                                           "note": "used for deploy checks; owner decision 2026-10-21", "date": "2026-10-21"}]}
        (self.out.parent / "keep.json").write_text(json.dumps(keep, indent=1))


if __name__ == "__main__":
    import tempfile
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
    base.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(base)
    f = Fixture()
    f.tree.tmp._finalizer.detach()  # keep the folder after this script ends (screenshots read it)
    print(json.dumps({"home": str(f.home), "out": str(f.out), "app": str(f.app), "markup": str(f.markup),
                      "trim_exit": f.trim.returncode, "trim_err": f.trim.stderr[-500:]}))
