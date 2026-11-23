"""agent_host.py - a minimal MCP *host*: local LLM (Ollama) + MCP client + approval gate.
Run: python agent_host.py "How many leave days can I carry forward? Raise an HR ticket to confirm."
"""
import asyncio
import json
import os
import sys
from ollama import AsyncClient
from mcp import Client, StdioServerParameters

MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
MAX_STEPS = 6  # hard stop: agents loop, budgets don't


def to_ollama_tools(mcp_tools) -> list[dict]:
    """MCP tool schema -> OpenAI-style function schema (what Ollama expects)."""
    return [{"type": "function", "function": {
        "name": t.name, "description": t.description or "", "parameters": t.input_schema}}
        for t in mcp_tools]


def needs_approval(tool) -> bool:
    """Anything not explicitly read-only goes through a human. Annotations are HINTS from the
    server - only trust them for servers you operate."""
    return not (tool.annotations and tool.annotations.read_only_hint)


async def run(question: str) -> None:
    params = StdioServerParameters(command=sys.executable, args=["policy_server.py"],
                                   env={"POLICY_KB_USER": os.environ.get("USERNAME", "rahul")})
    async with Client(params) as mcp_client:
        tools = (await mcp_client.list_tools()).tools
        by_name = {t.name: t for t in tools}
        llm = AsyncClient()
        messages = [
            {"role": "system", "content": "You are an HR assistant. Use tools to look up policy and "
             "cite policy ids in [brackets]. Only raise tickets if the user asks."},
            {"role": "user", "content": question},
        ]
        for step in range(MAX_STEPS):
            resp = await llm.chat(model=MODEL, messages=messages, tools=to_ollama_tools(tools),
                                  options={"temperature": 0})
            msg = resp.message
            messages.append(msg)
            if not msg.tool_calls:
                print(f"\nANSWER:\n{msg.content}")
                return
            for call in msg.tool_calls:
                name, args = call.function.name, dict(call.function.arguments)
                print(f"[step {step}] model wants {name}({json.dumps(args)})")
                tool = by_name.get(name)
                if tool is None:
                    output = f"error: unknown tool {name}"
                elif needs_approval(tool) and input(f"  approve {name}? [y/N] ").lower() != "y":
                    output = "error: the user declined this action"
                else:
                    result = await mcp_client.call_tool(name, args)
                    output = (json.dumps(result.structured_content) if result.structured_content
                              else "\n".join(c.text for c in result.content if hasattr(c, "text")))
                    if result.is_error:
                        output = f"error: {output}"
                print(f"  -> {output[:160]}")
                messages.append({"role": "tool", "tool_name": name, "content": output})
        print("Stopped: step budget exhausted (this is a feature, not a bug).")


if __name__ == "__main__":
    asyncio.run(run(" ".join(sys.argv[1:]) or "How many leave days can I carry forward?"))
