"""Call the secured server with different tokens and show what the server does."""
import asyncio
import sys
from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

URL = "http://127.0.0.1:8000/mcp"


async def try_token(token: str | None) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with create_mcp_http_client(headers=headers) as http:
            async with Client(streamable_http_client(URL, http_client=http)) as client:
                r = await client.call_tool("search_policies", {"query": "hotel metro"})
                print(f"{token!s:14} -> OK   {r.structured_content['result'][0]['id']}")
    except Exception:  # 401/403 surface as transport errors inside an ExceptionGroup
        print(f"{token!s:14} -> DENIED (see 401 in the server log)")


async def main() -> None:
    for t in [None, "wrong-token", "dev-other-aud", "dev-reader"]:
        await try_token(t)

asyncio.run(main())
