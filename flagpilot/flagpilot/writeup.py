"""Build English solution reports from recorded evidence without model calls."""
from __future__ import annotations

import re
from pathlib import Path

from . import storage
from .trace import read_jsonl


def _code(text, language="text"):
    text = str(text or "")
    size = max([2] + [len(m.group()) for m in re.finditer(r"`+", text)]) + 1
    fence = "`" * size
    return f"{fence}{language}\n{text}\n{fence}\n"


def render(events: list[dict]) -> str:
    meta = next((e for e in events if e.get("type") == "meta"), {})
    summary = next((e for e in reversed(events) if e.get("type") in ("done", "summary")), None)
    if summary is None:
        raise ValueError("A writeup requires a completed run with a summary.")
    start = next((e for e in events if e.get("type") == "start"), {})
    submits = [e for e in events if e.get("type") == "submit"]
    latest = submits[-1] if submits else {}
    replay = summary.get("replay") or next((e for e in reversed(events) if e.get("type") == "replay"), {})
    model_info = meta.get("model") or {}
    model = summary.get("model") or (model_info.get("name", "unknown") if isinstance(model_info, dict) else str(model_info))
    lines = ["# FlagPilot Solution Writeup", "", "## Run", "",
             _code(f"Task: {meta.get('task', 'unknown')}\nCategory: {meta.get('category', 'unknown')}\n"
                   f"Model: {model}\nRun: {meta.get('run_id', 'unknown')}\n"
                   f"Verdict: {summary.get('verdict', 'unknown')}\nReason: {summary.get('reason', '')}"),
             "The verdict below is the recorded outcome. Model reasoning is a self-report; tool and replay outputs are recorded execution evidence.", "",
             "## Challenge", "", _code(start.get("brief", "Not recorded.")),
             "## Recorded Steps", ""]
    for e in events:
        kind = e.get("type")
        if kind == "model":
            lines += [f"### Model call {e.get('turn', '?')}", "", "Model output (self-reported):", "", _code(e.get("text", ""), "json")]
        elif kind == "tool":
            lines += [f"### Tool: {e.get('action', 'unknown')}", "",
                      _code(f"Command: {e.get('cmd', '')}\nExit code: {e.get('exit_code')}\n"
                            f"Timed out: {e.get('timed_out', False)}\nDuration: {e.get('duration_ms', 0)} ms"),
                      "Recorded observation excerpt:", "", _code(e.get("obs_head", ""))]
            if e.get("stderr_head"):
                lines += ["Recorded stderr excerpt:", "", _code(e["stderr_head"])]
    lines += ["## Submission", "", f"Submission attempts: {len(submits)}", "",
              "Submitted flag:", "", _code(latest.get("flag", "No flag submitted.")),
              "Submitted solve.py:", "", _code(latest.get("script", "No script submitted."), "python"),
              "## Replay Verification", "",
              _code(f"Verdict: {replay.get('verdict', 'not run')}\nReason: {replay.get('reason', '')}\n"
                    f"Gold checked: {replay.get('gold_checked', False)}\nExit code: {replay.get('exit_code')}\n"
                    f"Duration: {replay.get('duration_ms', 0)} ms\nDetail: {replay.get('detail', '')}"),
              "Recorded replay stdout:", "", _code(replay.get("stdout", "")),
              "## Usage", "",
              _code(f"Turns: {summary.get('turns', 0)}\nWall time: {summary.get('total_s', summary.get('wall_s', 0))} s\n"
                    f"Input tokens: {summary.get('prompt_tokens', 0)}\nOutput tokens: {summary.get('completion_tokens', 0)}\n"
                    f"Estimated cost: ${summary.get('cost_usd', 0):.6f}"),
              "Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.", ""]
    return "\n".join(lines)


def generate(run_dir: Path, output: Path | None = None) -> Path:
    run_dir = Path(run_dir)
    report = render(read_jsonl(run_dir / "trace.jsonl"))
    return storage.write_artifact(output or run_dir / "writeup.md", report.encode("utf-8"))
