"""Stretch 1: same MCP server, consumed by a LangChain v1 agent via langchain[mcp]."""
import asyncio
import warnings
from pathlib import Path
from langchain.agents import create_agent
from langchain_ollama import ChatOllama
from langchain_core._api import LangChainBetaWarning

warnings.filterwarnings("ignore", category=LangChainBetaWarning)
from langchain.mcp import MCPAdapter  # noqa: E402


async def main() -> None:
    async with MCPAdapter(Path("policy_server.py")) as adapter:  # Path => explicit stdio launch
        tools = await adapter.list_tools()
        print("tools:", [t.name for t in tools])
        agent = create_agent(ChatOllama(model="qwen2.5:3b", temperature=0), tools,
                             system_prompt="Cite policy ids in [brackets].")
        out = await agent.ainvoke({"messages": [{"role": "user",
                                   "content": "What is the hotel cap in metro cities?"}]})
        print(out["messages"][-1].content)

asyncio.run(main())
