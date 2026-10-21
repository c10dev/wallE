"""A synthetic tree with the shape of the author's 2026-10-20 08:17 scan.

The design brief (2.2, 10.1) checks the triage rules by hand against that run. This tree
rebuilds what decided each result: layer sizes in estimated tokens, the line numbers and
rules of the flagged lines, and the pair scores. The text is made up, except two lines
of runup/CLAUDE.md, a public repository. Filler is random syllables that match no rule
and pair with nothing.
"""

from __future__ import annotations

import random
import tempfile
from pathlib import Path

from walle.stack import estimate_tokens, memory_slug

CONS, VOW = "bdfklprstvz", "aeiu"

# Estimated tokens of each layer in the 08:17 run.
TOKENS = {
    "global": 1297, "codex_global": 531, "skills": 491, "codex_skills": 244,
    "runup": 689, "tennis-web": 1044, "tennis-web memory": 392, "DesignLAB": 5187,
    "atc": 2314, "atc memory": 3431, "atc skills": 418, "atc/controller": 8148, "atc/controller skills": 64,
    "vocado_RN claude": 1650, "vocado_RN agents": 1900,
}
# Stack totals of the 08:17 run. atc/controller was 12732 then: walle looked for MEMORY.md under the
# subfolder's slug. A session there loads the atc repository's MEMORY.md, so the total now adds "atc memory".
STACKS = {"runup@claude": 2477, "tennis-web@claude": 3224, "DesignLAB@claude": 6975, "atc@claude": 7951,
          "atc/controller@claude": 16134, "vocado_RN@codex": 2675}


def words(seed: str, chars: int) -> str:
    rng = random.Random(seed)
    out: list[str] = []
    while sum(len(w) + 1 for w in out) < chars:
        out.append("".join(rng.choice(CONS) + rng.choice(VOW) for _ in range(rng.randint(2, 4))))
    return " ".join(out)


def fill(lines: list, seed: str, tokens: int) -> str:
    """Put filler in the None slots of lines ((prefix, None) for a prefix) until the text is near `tokens`."""
    slots = [i for i, l in enumerate(lines) if l is None or (isinstance(l, tuple) and l[1] is None)]

    def render(sizes: dict[int, int]) -> str:
        out = []
        for i, l in enumerate(lines):
            if i in sizes:
                prefix = l[0] if isinstance(l, tuple) else ""
                text = words(f"{seed}:{i}", sizes[i])
                out.append(prefix + text[:1].upper() + text[1:] + ".")
            else:
                out.append(l)
        return "\n".join(out) + "\n"

    base = estimate_tokens(render({i: 0 for i in slots}))
    each = max(30, (tokens - base) * 4 // max(1, len(slots)))
    sizes = {i: each for i in slots}
    text = render(sizes)
    last = slots[-1]
    while abs(estimate_tokens(text) - tokens) > 2 and sizes[last] > 10:
        sizes[last] += (tokens - estimate_tokens(text)) * 4
        sizes[last] = max(10, sizes[last])
        text = render(sizes)
    return text


def layout(n_lines: int, at: dict[int, str]) -> list:
    """Lines 1..n_lines: the given lines at their numbers, filler units between, blank lines around both."""
    lines: list = []
    prev = False
    for n in range(1, n_lines + 1):
        if n in at:
            lines.append(at[n])
            prev = True
        elif prev or (n + 1) in at:
            lines.append("")
            prev = False
        else:
            lines.append(None)
            prev = True
    return lines


def rule(seed: str, head: str) -> str:
    return f"- {head} {words(seed, 60)}."


class Tree:
    """home/ with projects/ under it, as on the author's machine."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve() / "home"  # the CLI resolves paths
        self.projects = self.home / "projects"
        self.global_md = self.write(".claude/CLAUDE.md", self.global_text())
        self.write(".codex/AGENTS.md", fill(layout(30, {1: "# Codex"}), "codex", TOKENS["codex_global"]))
        self.skills(".claude/skills", "skills", 3, TOKENS["skills"])
        self.skills(".codex/skills", "codex-skills", 1, TOKENS["codex_skills"])
        self.runup()
        self.tennis_web()
        self.designlab()
        self.atc()
        self.claude_config()
        self.vocado_rn()

    def close(self):
        self.tmp.cleanup()

    def write(self, rel: str, text: str) -> Path:
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def repo(self, name: str) -> Path:
        (self.projects / name / ".git").mkdir(parents=True, exist_ok=True)
        return self.projects / name

    def skills(self, rel: str, seed: str, n: int, tokens: int) -> None:
        # One catalog line per skill: "- name: description", and for Codex " (file: r0/<name>/SKILL.md)" after it.
        # Split the tokens over the skills.
        for i in range(n):
            name = words(f"{seed}-name-{i}", 6).replace(" ", "")
            tail = len(f" (file: r0/{name}/SKILL.md)") if rel.startswith(".codex/") else 0
            desc = words(f"{seed}-desc-{i}", max(10, tokens * 4 // n - len(name) - 6 - tail))
            body = fill(layout(12, {1: f"# {name}"}), f"{seed}-body-{i}", 120)
            self.write(f"{rel}/{name}/SKILL.md", f"---\nname: {name}\ndescription: {desc}\n---\n{body}")

    def global_text(self) -> str:
        at = {1: "# Global rules",
              4: rule("g4", "Every session starts from these defaults:"),
              12: rule("g12", "Open each reply with the result, as on every turn."),
              37: "## Output format ladder (2026-10-02)"}
        self.global_lines = layout(75, at)
        return fill(self.global_lines, "global", TOKENS["global"])

    def runup(self):
        r = self.repo("runup")
        at = {1: "# RUN-UP",
              3: "RUN-UP is a public MIT repository. Read this before changing anything.",
              8: "- Open a PR for every change. structure reviews it; SUPERVISOR merges. Do not merge your own PR."}
        (r / "CLAUDE.md").write_text(fill(layout(40, at), "runup", TOKENS["runup"]))

    def tennis_web(self):
        r = self.repo("tennis-web")
        at = {1: "# Tennis web", 57: "## Done criteria (every task)"}
        (r / "CLAUDE.md").write_text(fill(layout(70, at), "tennis", TOKENS["tennis-web"]))
        mem = [("- ", None)] * 11
        for n, date in ((2, "2026-10-13"), (4, "2026-10-13"), (7, "2026-10-16"), (8, "2026-10-16"),
                        (9, "2026-10-16"), (10, "2026-10-16")):
            mem[n - 1] = f"- [Note {n}](note-{n}.md) - done {date}; {words(f'mem{n}', 50)}."
        mem[10] = f"- [Deploy](deploy.md) - the agent builds before each deploy; {words('mem11', 50)}."
        self.memory = self.write(f".claude/projects/{memory_slug(r)}/memory/MEMORY.md",
                                 fill(mem, "tennis-mem", TOKENS["tennis-web memory"]))

    def designlab(self):
        r = self.repo("DesignLAB")
        at = {1: "# DesignLAB", 22: "## Release and copy rules", 120: "## Sessions", 170: "## Reports",
              220: "## Landing"}
        for n, date in ((28, "2026-07-27"), (97, "2026-07-27"), (124, "2026-07-27"), (139, "2026-07-27"),
                        (147, "2026-07-27"), (155, "2026-07-31"), (158, "2026-07-31"), (173, "2026-07-24"),
                        (224, "2026-07-30"), (229, "2026-07-27")):
            at[n] = rule(f"d{n}", f"**Decision ({date}).**")
        at[161] = rule("d161", "**Drafts (2026-08-05).** Always keep the draft in the advisor session.")
        (r / "CLAUDE.md").write_text(fill(layout(240, at), "designlab", TOKENS["DesignLAB"]))

    def atc(self):
        r = self.repo("atc")
        at = {1: "# ATC", 18: "- `npm test` and `npx tsc` must all pass before a PR."}
        (r / "CLAUDE.md").write_text(fill(layout(75, at), "atc", TOKENS["atc"]))
        self.write(f".claude/projects/{memory_slug(r)}/memory/MEMORY.md",
                   fill([("- ", None)] * 40, "atc-mem", TOKENS["atc memory"]))
        at = {1: "# Controller", 10: "## Role", 45: "## Landing queue", 120: "## Ticks", 150: "## Handoff",
              59: "- Review grade: read `landingQueue[].codexFindings` first.",
              60: "- When the codex-review quota runs out, wait six hours."}
        (r / "controller" / "CLAUDE.md").parent.mkdir(parents=True, exist_ok=True)
        (r / "controller" / "CLAUDE.md").write_text(fill(layout(180, at), "controller", TOKENS["atc/controller"]))
        self.skills("projects/atc/.claude/skills", "atc-skills", 1, TOKENS["atc skills"])
        self.skills("projects/atc/controller/.claude/skills", "ctl-skills", 1, TOKENS["atc/controller skills"])

    def claude_config(self):
        """claude-config/global/CLAUDE.md is the source of ~/.claude/CLAUDE.md: 32 of its rules are the same."""
        r = self.repo("claude-config")
        lines = list(self.global_lines)
        rules = [i for i, l in enumerate(lines) if l is None or (isinstance(l, str) and l.startswith("- "))]
        self.global_rules = len(rules)
        text = fill(self.global_lines, "global", TOKENS["global"]).splitlines()
        for k, i in enumerate(rules[32:]):
            text[i] = f"Local note {words(f'cc{k}', 120)}."
        (r / "global").mkdir(parents=True, exist_ok=True)
        (r / "global" / "CLAUDE.md").write_text("\n".join(text) + "\n")

    def vocado_rn(self):
        r = self.repo("vocado_RN")
        same = "- Preserve cross-platform behavior unless the task is explicitly platform-specific."
        (r / "AGENTS.md").write_text(fill(layout(150, {1: "# Agents", 53: same, 74: same}), "vrn-agents",
                                          TOKENS["vocado_RN agents"]))
        at = {1: "# App",
              21: "- `docs/agent-output-contract.md` defines the report format",  # 0.62 like :21 and :129 at 08:17
              129: "- `docs/agent-output-contract.md` defines it:"}
        (r / "CLAUDE.md").write_text(fill(layout(150, at), "vrn-claude", TOKENS["vocado_RN claude"]))
