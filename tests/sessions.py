"""Synthetic, anonymized session transcripts for the measure and compare tests.

A Claude Code session is a main transcript ~/.claude/projects/<slug>/<id>.jsonl with the attachments that
walle context reads (prompt_snapshot, skill_listing, deferred_tools_delta, mcp_instructions_delta) and one
API call. A Codex thread is a rollout under ~/.codex/sessions. Names and text are filler.
"""

import json
from pathlib import Path


def slug(folder: Path) -> str:
    return str(folder).replace("/", "-").replace(".", "-")


def claude_session(projects: Path, sid: str, cwd: Path, start: str, *, model: str = "claude-opus-5-5",
                   total: int = 90000, entrypoint: str = "claude-desktop", skills: tuple = (),
                   servers: dict | None = None, instructions: dict | None = None, prompt: str = "do the task",
                   calls: tuple = (), resumed_from: str | None = None, system: str = "s" * 4000) -> Path:
    """One main transcript. servers: {name: [tool, ...]} offered as deferred names; instructions: {name: text};
    calls: tool names the first answer calls (an Edit counts as the first edit; mcp__x__y uses server x)."""
    base = {"cwd": str(cwd), "sessionId": sid, "entrypoint": entrypoint, "version": "2.1.300", "isSidechain": False}
    lines = []
    if resumed_from:  # a resumed session starts with lines copied from the session it resumes
        lines.append({**base, "sessionId": resumed_from, "type": "user", "timestamp": start,
                      "message": {"role": "user", "content": "earlier prompt"}})
    att = [{"type": "prompt_snapshot", "systemPrompt": [system]}]
    if skills:
        att.append({"type": "skill_listing", "names": list(skills),
                    "content": "\n".join(f"- {n}: Filler description of {n} for the listing." for n in skills)})
    names = [f"mcp__{s}__{tool}" for s, tools in (servers or {}).items() for tool in tools]
    if names:
        att.append({"type": "deferred_tools_delta", "addedNames": names, "addedLines": names})
    if instructions:
        att.append({"type": "mcp_instructions_delta", "addedNames": list(instructions),
                    "addedBlocks": list(instructions.values())})
    for a in att:
        lines.append({**base, "type": "attachment", "timestamp": start, "attachment": a})
    lines.append({**base, "type": "user", "timestamp": start, "message": {"role": "user", "content": prompt}})
    content = [{"type": "tool_use", "id": f"{sid}-t{i}", "name": name,
                "input": {"skill": "x"} if name == "Skill" else {}} for i, name in enumerate(calls)]
    lines.append({**base, "type": "assistant", "timestamp": start, "message": {
        "id": f"{sid}-m0", "model": model, "content": content,
        "usage": {"input_tokens": 2, "cache_creation_input_tokens": total - 2, "cache_read_input_tokens": 0,
                  "output_tokens": 5}}})
    d = projects / slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return path


def codex_rollout(codex: Path, thread: str, cwd: Path, start: str, *, model: str = "gpt-5.1-codex",
                  total: int = 30000, originator: str = "Codex Desktop", agents_md: str = "", resumed: bool = False,
                  forked: bool = False, subagent: bool = False) -> Path:
    """One rollout file with a session_meta, a turn_context, one user message and one token_count."""
    meta = {"id": thread, "timestamp": start, "cwd": str(cwd), "originator": originator, "cli_version": "0.50.0"}
    if resumed:
        meta["history_base"] = {"thread_id": "00000000-0000-7000-8000-00000000ffff"}
    if forked:
        meta["forked_from_id"] = "00000000-0000-7000-8000-00000000fffe"
    if subagent:
        meta["source"] = {"subagent": {"other": "x"}}
    items = [{"type": "session_meta", "payload": meta},
             {"type": "turn_context", "payload": {"model": model, "cwd": str(cwd)}}]
    if agents_md:
        items.append({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": f"# AGENTS.md instructions for {cwd}\n\n<INSTRUCTIONS>\n{agents_md}\n</INSTRUCTIONS>"}]}})
    items.append({"type": "response_item", "payload": {"type": "message", "role": "user",
                                                       "content": [{"type": "input_text", "text": "do the task"}]}})
    items.append({"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {
        "input_tokens": total, "cached_input_tokens": 0, "output_tokens": 10}}}})
    stamp = start.replace(":", "-")[:19]
    d = codex / "sessions" / start[:4] / start[5:7] / start[8:10]
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"rollout-{stamp}-{thread}.jsonl"
    path.write_text("\n".join(json.dumps({"timestamp": start, **x}) for x in items) + "\n")
    return path
