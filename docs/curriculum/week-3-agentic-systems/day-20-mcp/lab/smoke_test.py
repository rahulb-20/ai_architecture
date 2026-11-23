"""smoke_test.py - talk to the server over stdio with the v2 Client, no LLM involved."""
import asyncio
import sys
from mcp import Client, StdioServerParameters


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["policy_server.py"],
                                   env={"POLICY_KB_USER": "rahul"})
    async with Client(params) as client:
        print("protocol:", client.protocol_version)
        tools = await client.list_tools()
        for t in tools.tools:
            print(f"tool: {t.name:16} readOnly={t.annotations.read_only_hint if t.annotations else None}")
        res = await client.call_tool("search_policies", {"query": "how many leave days carry forward"})
        print("search:", res.structured_content)
        bad = await client.call_tool("raise_ticket", {"team": "payroll-admin", "summary": "x"})
        print("bad team -> is_error:", bad.is_error, "|", bad.content[0].text)
        r = await client.read_resource("policy://wfh-003")
        print("resource:", r.contents[0].text.splitlines()[0])
        p = await client.get_prompt("policy_answer", {"question": "Hotel cap in Mumbai?"})
        print("prompt:", p.messages[0].content.text[:60], "...")

asyncio.run(main())
