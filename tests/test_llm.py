import json

import pytest
from pydantic import BaseModel

from jobsearch import llm


class Answer(BaseModel):
    summary: str
    terms: list[str]


# Stands in for `claude -p`: records how it was called, then replies like the real CLI's
# stream-json output, with the JSON answer streamed as structured-output tool input.
FAKE_CLI = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
system = open(args[args.index("--system-prompt-file") + 1]).read()
with open({record!r}, "w") as f:
    json.dump({{"args": args, "stdin": sys.stdin.read(), "system": system, "cwd": os.getcwd(),
               "api_key": os.environ.get("ANTHROPIC_API_KEY"),
               "max_tokens": os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")}}, f)
mode = {mode!r}

def emit(message):
    print(json.dumps(message), flush=True)

def delta(d):
    emit({{"type": "stream_event", "event": {{"type": "content_block_delta", "index": 0, "delta": d}}}})

emit({{"type": "system", "subtype": "init"}})
if mode == "error":
    emit({{"type": "result", "subtype": "success", "is_error": True, "result": "You've hit your usage limit"}})
elif mode == "crash":
    print("boom", file=sys.stderr)
    sys.exit(1)
elif mode == "text":
    delta({{"type": "text_delta", "text": "Hello "}})
    delta({{"type": "text_delta", "text": "there"}})
    emit({{"type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn", "result": "Hello there"}})
else:
    delta({{"type": "thinking_delta", "thinking": "hmm"}})
    delta({{"type": "input_json_delta", "partial_json": '{{"summary": "ok", '}})
    delta({{"type": "input_json_delta", "partial_json": '"terms": ["PLC"]}}'}})
    emit({{"type": "result", "subtype": "success", "is_error": False, "stop_reason": "tool_use",
          "structured_output": {{"summary": "ok", "terms": ["PLC"]}}}})
'''


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """Install a fake claude command; returns a function that sets its mode and reads its record."""
    record = tmp_path / "record.json"
    script = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CLI", str(script))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
    monkeypatch.setenv("CLAUDE_BACKEND", "cli")

    def use(mode: str = "json"):
        script.write_text(FAKE_CLI.format(record=str(record), mode=mode))
        script.chmod(0o755)
        return lambda: json.loads(record.read_text())

    return use


def test_parse_uses_cli_without_api_key(fake_cli):
    recorded = fake_cli()
    events = []
    answer = llm.parse(
        "Be brief.", "Define PLC.", Answer,
        model="claude-sonnet-5-5", effort="low", on_event=lambda kind, text: events.append((kind, text)),
        max_tokens=32000,
    )
    assert answer == Answer(summary="ok", terms=["PLC"])
    assert events == [("thinking", "hmm"), ("text", '{"summary": "ok", '), ("text", '"terms": ["PLC"]}')]

    call = recorded()
    assert call["api_key"] is None and call["max_tokens"] == "32000"
    assert call["stdin"] == "Define PLC." and call["system"] == "Be brief."
    args = call["args"]
    assert args[args.index("--model") + 1] == "claude-sonnet-5-5" and args[args.index("--effort") + 1] == "low"
    assert args[args.index("--tools") + 1] == "" and "--safe-mode" in args
    assert json.loads(args[args.index("--json-schema") + 1])["required"] == ["summary", "terms"]


def test_chat_sends_earlier_turns_as_transcript(fake_cli):
    recorded = fake_cli("text")
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "Which posting fits best?"},
    ]
    chunks = list(llm.stream_text(["System.", "<materials/>"], messages, model="claude-sonnet-5", effort="medium"))
    assert chunks == ["Hello ", "there"]
    call = recorded()
    assert call["system"] == "System.\n\n<materials/>"
    assert "<user>\nHi\n</user>" in call["stdin"] and "<assistant>\nHello!\n</assistant>" in call["stdin"]
    assert call["stdin"].endswith("Which posting fits best?")


def test_cli_errors_become_readable(fake_cli):
    fake_cli("error")
    with pytest.raises(RuntimeError, match="usage limit"):
        llm.parse("s", "c", Answer, model="m", effort="low")
    fake_cli("crash")
    with pytest.raises(RuntimeError, match="boom"):
        llm.parse("s", "c", Answer, model="m", effort="low")


def test_missing_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CLI", str(tmp_path / "missing"))
    monkeypatch.setenv("CLAUDE_BACKEND", "cli")
    with pytest.raises(RuntimeError, match="isn't installed"):
        llm.parse("s", "c", Answer, model="m", effort="low")


def test_backend_setting(monkeypatch):
    monkeypatch.delenv("CLAUDE_BACKEND", raising=False)
    assert llm.backend() == "api"
    monkeypatch.setenv("CLAUDE_BACKEND", " CLI ")
    assert llm.backend() == "cli"
