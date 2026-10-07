"""Send requests to Claude through the Claude Code CLI or the Claude API.

CLAUDE_BACKEND (in .env) picks which:
- "api" (the default): calls the Claude API with ANTHROPIC_API_KEY.
- "cli": runs the local `claude` command, signed in to your claude.ai account, so requests count
  against your Claude plan's usage limits instead of being billed to an API key.
  ANTHROPIC_API_KEY is removed from its environment, since the CLI would otherwise bill the key.

Using your subscription this way is for your own local copy of the app. If other people use the
app, switch to "api" so each person's requests use an API key.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Called with ("thinking", text) for each reasoning-summary chunk and ("text", text)
# for each chunk of the JSON answer, as they stream in.
OnEvent = Callable[[Literal["thinking", "text"], str], None]

# Long enough for the biggest analyses; a stuck CLI is killed after this.
CLI_TIMEOUT_SECONDS = 20 * 60
# Variables that would make the CLI bill an API key instead of using the claude.ai login.
API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def cli_path() -> str:
    """The claude command. Apps started from an IDE may not have the installer's folder on PATH."""
    if path := os.environ.get("CLAUDE_CLI") or shutil.which("claude"):
        return path
    return str(Path.home() / ".local" / "bin" / "claude")


def backend() -> Literal["cli", "api"]:
    # Read on every call, since .env is loaded after this module is imported.
    return "cli" if os.environ.get("CLAUDE_BACKEND", "").strip().lower() == "cli" else "api"


def parse(
    system: str,
    content: str,
    output_format: type[BaseModel],
    *,
    model: str,
    effort: Effort,
    on_event: OnEvent | None = None,
    max_tokens: int = 16000,
    client: anthropic.Anthropic | None = None,
):
    """Send one request and return Claude's answer as an instance of output_format."""
    if backend() == "api":
        return _parse_api(system, content, output_format, model, effort, on_event, max_tokens, client)
    return _parse_cli(system, content, output_format, model, effort, on_event, max_tokens)


def stream_text(
    system: list[str],
    messages: list[dict],
    *,
    model: str,
    effort: Effort,
    max_tokens: int = 16000,
    client: anthropic.Anthropic | None = None,
) -> Iterator[str]:
    """Stream Claude's reply to a conversation as text chunks. system is joined in order."""
    if backend() == "api":
        return _stream_api(system, messages, model, effort, max_tokens, client)
    return _stream_cli(system, messages, model, effort, max_tokens)


# ---------- Claude API ----------

def _parse_api(system, content, output_format, model, effort, on_event, max_tokens, client):
    client = client or anthropic.Anthropic()
    with client.beta.messages.stream(
        model=model,
        max_tokens=max_tokens,
        output_config={"effort": effort},
        # Summarized thinking lets the UI show what Claude is reasoning about.
        thinking={"type": "adaptive", "display": "summarized"},
        # On a safety decline, the API retries the request on a fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": content}],
        output_format=output_format,
    ) as stream:
        for event in stream:
            if on_event and event.type == "content_block_delta":
                if event.delta.type == "thinking_delta":
                    on_event("thinking", event.delta.thinking)
                elif event.delta.type == "text_delta":
                    on_event("text", event.delta.text)
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to process this request.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Response was cut off. Try shorter inputs or raise max_tokens.")
    if response.parsed_output is None:
        raise RuntimeError("Claude did not return a valid result.")
    return response.parsed_output


def _stream_api(system, messages, model, effort, max_tokens, client) -> Iterator[str]:
    client = client or anthropic.Anthropic()
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        system=[{"type": "text", "text": text} for text in system],
        # Caches the system prompt and conversation so far, so each new message only pays full
        # price for what's new.
        cache_control={"type": "ephemeral"},
        messages=messages,
    ) as stream:
        yield from stream.text_stream
        final = stream.get_final_message()

    if final.stop_reason == "refusal":
        raise RuntimeError("Claude declined to answer that.")
    if final.stop_reason == "max_tokens":
        yield "\n\n*(This reply hit the length limit and was cut off.)*"


# ---------- Claude Code CLI ----------

def _parse_cli(system, content, output_format, model, effort, on_event, max_tokens):
    schema = json.dumps(output_format.model_json_schema())
    result = None
    for message in _cli(system, content, model, effort, max_tokens, ("--json-schema", schema)):
        if message["type"] == "result":
            result = message
        elif on_event and message["type"] == "stream_event":
            delta = message["event"].get("delta", {})
            if delta.get("type") == "thinking_delta":
                on_event("thinking", delta["thinking"])
            # The CLI returns structured output as a tool call, so the JSON streams as tool input.
            elif delta.get("type") in ("text_delta", "input_json_delta"):
                on_event("text", delta.get("text") or delta.get("partial_json", ""))

    if result.get("stop_reason") == "refusal":
        raise RuntimeError("Claude declined to process this request.")
    if result.get("structured_output") is None:
        raise RuntimeError("Claude did not return a valid result.")
    return output_format.model_validate(result["structured_output"])


def _stream_cli(system, messages, model, effort, max_tokens) -> Iterator[str]:
    # The CLI takes one prompt, so earlier turns are passed along as a transcript.
    *earlier, latest = messages
    prompt = latest["content"]
    if earlier:
        transcript = "\n\n".join(f"<{m['role']}>\n{m['content']}\n</{m['role']}>" for m in earlier)
        prompt = (
            f"<conversation_so_far>\n{transcript}\n</conversation_so_far>\n\n"
            f"Reply to the user's latest message:\n\n{prompt}"
        )

    result = None
    for message in _cli("\n\n".join(system), prompt, model, effort, max_tokens):
        if message["type"] == "result":
            result = message
        elif message["type"] == "stream_event":
            delta = message["event"].get("delta", {})
            if delta.get("type") == "text_delta":
                yield delta["text"]

    if result.get("stop_reason") == "refusal":
        raise RuntimeError("Claude declined to answer that.")
    if result.get("stop_reason") == "max_tokens":
        yield "\n\n*(This reply hit the length limit and was cut off.)*"


def _cli(
    system: str, prompt: str, model: str, effort: Effort, max_tokens: int, extra_args: tuple[str, ...] = ()
) -> Iterator[dict]:
    """Run `claude -p` and yield each stream-json message. Raises RuntimeError if it fails."""
    env = {k: v for k, v in os.environ.items() if k not in API_KEY_VARS}
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_tokens)

    # An empty working directory, so no project files, CLAUDE.md, or memory get pulled in.
    with tempfile.TemporaryDirectory(prefix="jobsearch-claude-") as tmp:
        system_file = Path(tmp) / "system.txt"
        system_file.write_text(system)
        command = [
            cli_path(), "-p",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--model", model,
            "--effort", effort,
            "--system-prompt-file", str(system_file),
            # No tools (it only answers), no saved session, and no user customizations like hooks
            # or plugins.
            "--tools", "",
            "--no-session-persistence",
            "--safe-mode",
            *extra_args,
        ]
        stderr = (Path(tmp) / "stderr.txt").open("w+")
        try:
            process = subprocess.Popen(
                command, cwd=tmp, env=env, text=True, encoding="utf-8",
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
            )
        except FileNotFoundError:
            stderr.close()
            raise RuntimeError(
                "Claude Code isn't installed or isn't on your PATH. Install it and run `claude` once to "
                "sign in, or set CLAUDE_BACKEND=api in .env to use an API key."
            ) from None

        timed_out = threading.Event()

        def kill() -> None:
            timed_out.set()
            process.kill()

        timer = threading.Timer(CLI_TIMEOUT_SECONDS, kill)
        timer.start()
        result = None
        try:
            try:
                process.stdin.write(prompt)
                process.stdin.close()
            except BrokenPipeError:
                pass  # it exited early; the error is reported below
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if message.get("type") == "result":
                    result = message
                yield message
        finally:
            # Also runs if the caller stops reading early, so the process never outlives it.
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            stderr.seek(0)
            errors = stderr.read().strip()
            stderr.close()

    if result is None:
        if timed_out.is_set():
            raise RuntimeError(f"Claude Code didn't finish within {CLI_TIMEOUT_SECONDS // 60} minutes.")
        raise RuntimeError(f"Claude Code stopped without an answer. {errors[-500:]}".strip())
    if result.get("is_error"):
        detail = result.get("result") or ", ".join(result.get("errors", [])) or result.get("subtype", "")
        raise RuntimeError(f"Claude Code: {detail}")
