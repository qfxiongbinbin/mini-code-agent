import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

DEFAULT_WORKSPACE = "/Users/xiongbin/codespace/mini-code-agent-v0"
PROVIDER = os.environ.get("PROVIDER", "zhipu").strip().lower()
WORKSPACE = Path(os.environ.get("AGENT_WORKSPACE", DEFAULT_WORKSPACE)).resolve()
MODEL = os.environ.get("MODEL", "glm-5")
MAX_STEPS = int(os.environ.get("MAX_STEPS", "8"))
THINKING_TYPE = os.environ.get("THINKING_TYPE", "enabled").strip().lower()
BASE_URL = os.environ.get("BASE_URL") or {
    "zhipu": "https://open.bigmodel.cn/api/paas/v4/",
    "minimax": "https://api.minimaxi.com/v1",
}.get(PROVIDER, "https://open.bigmodel.cn/api/paas/v4/")
API_KEY = os.environ.get("API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("ZAI_API_KEY")

SYSTEM_PROMPT = f"""
You are a minimal code agent.

Your job:
- solve the user's task using tools
- stay inside the workspace: {WORKSPACE}
- prefer searching before reading
- keep file writes small and intentional
- stop when the task is complete

Rules:
- never access files outside the workspace
- never run shell metacharacters or chained commands
- if a tool fails, inspect the error and choose the next best step
""".strip()

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search text in the workspace with ripgrep.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text or regex to search for."}
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative or absolute."}
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a UTF-8 text file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative or absolute."},
                    "content": {"type": "string", "description": "Full file content to write."},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a safe command in the workspace and capture output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "A single shell command without pipes or chaining.",
                    }
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
]


class LocalTools:
    SAFE_COMMANDS = {"pwd", "ls", "cat", "sed", "python", "python3", "pytest", "rg"}
    BLOCKED_TOKENS = {"|", "&&", "||", ";", ">", ">>", "<", "$(", "`"}

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "search_code":
                return self.search_code(args["query"])
            if name == "read_file":
                return self.read_file(args["path"])
            if name == "write_file":
                return self.write_file(args["path"], args["content"])
            if name == "run_command":
                return self.run_command(args["command"])
            return {"ok": False, "error": f"unknown tool: {name}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def resolve_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if not path.is_absolute():
            path = self.workspace / path
        path = path.resolve()
        if self.workspace not in path.parents and path != self.workspace:
            raise ValueError(f"path escapes workspace: {raw_path}")
        return path

    def search_code(self, query: str) -> dict[str, Any]:
        result = subprocess.run(
            ["rg", "-n", "--hidden", "--glob", "!.git", query, str(self.workspace)],
            capture_output=True,
            text=True,
            cwd=self.workspace,
        )
        output = (result.stdout or result.stderr).strip()
        return {
            "ok": result.returncode in (0, 1),
            "matches": output[:12000],
            "returncode": result.returncode,
        }

    def read_file(self, raw_path: str) -> dict[str, Any]:
        path = self.resolve_path(raw_path)
        if not path.is_file():
            return {"ok": False, "error": f"file not found: {path}"}
        return {"ok": True, "path": str(path), "content": path.read_text(encoding="utf-8")}

    def write_file(self, raw_path: str, content: str) -> dict[str, Any]:
        path = self.resolve_path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(path), "bytes": len(content.encode("utf-8"))}

    def run_command(self, command: str) -> dict[str, Any]:
        if any(token in command for token in self.BLOCKED_TOKENS):
            return {"ok": False, "error": "shell metacharacters are not allowed"}

        parts = shlex.split(command)
        if not parts:
            return {"ok": False, "error": "empty command"}
        if parts[0] not in self.SAFE_COMMANDS:
            return {"ok": False, "error": f"command not allowed: {parts[0]}"}

        for part in parts[1:]:
            if part.startswith("/"):
                resolved = self.resolve_path(part)
                if self.workspace not in resolved.parents and resolved != self.workspace:
                    return {"ok": False, "error": f"path escapes workspace: {part}"}

        result = subprocess.run(
            parts,
            cwd=self.workspace,
            capture_output=True,
            text=True,
        )
        return {
            "ok": result.returncode == 0,
            "command": command,
            "stdout": result.stdout[:12000],
            "stderr": result.stderr[:12000],
            "returncode": result.returncode,
        }


class Agent:
    def __init__(self, client: OpenAI, model: str, provider: str, tools: LocalTools) -> None:
        self.client = client
        self.model = model
        self.provider = provider
        self.tools = tools

    def _request_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "model": self.model,
            "tools": TOOL_SCHEMAS,
            "tool_choice": "auto",
        }
        if self.provider == "zhipu" and THINKING_TYPE in {"enabled", "disabled"}:
            options["extra_body"] = {"thinking": {"type": THINKING_TYPE}}
        if self.provider == "minimax":
            options["extra_body"] = {"reasoning_split": True}
        return options

    def run(self, task: str, max_steps: int = MAX_STEPS) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]

        for step in range(1, max_steps + 1):
            response = self.client.chat.completions.create(
                messages=messages,
                **self._request_options(),
            )
            message = response.choices[0].message
            assistant_message = message.model_dump(exclude_none=True)
            messages.append(assistant_message)

            tool_calls = message.tool_calls or []
            if not tool_calls:
                return message.content or "(no final text returned)"

            print(f"\n[step {step}]")
            for call in tool_calls:
                args = json.loads(call.function.arguments)
                print(f"- tool: {call.function.name} {args}")
                result = self.tools.call(call.function.name, args)
                print(f"  result ok={result.get('ok')}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        return f"Stopped after {max_steps} steps without a final answer."


def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: python3 main.py "your task"')
        return 1

    if not API_KEY:
        print("API_KEY is required")
        return 1

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    task = sys.argv[1]
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    agent = Agent(client=client, model=MODEL, provider=PROVIDER, tools=LocalTools(WORKSPACE))

    print(f"provider: {PROVIDER}")
    print(f"base_url: {BASE_URL}")
    print(f"workspace: {WORKSPACE}")
    print(f"model: {MODEL}")
    print(f"task: {task}")

    final_answer = agent.run(task)
    print("\n[final answer]")
    print(final_answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
