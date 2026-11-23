"""policy_server.py - an MCP server exposing an HR-policy knowledge base.
Tools (model-controlled), Resources (app-controlled), Prompts (user-controlled)."""
import logging
import os
import re
import sys
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

POLICIES = {
    "leave-001": ("Annual leave", "Employees accrue 1.5 days of paid leave per month. Max carry-forward is 30 days. Encashment only at exit."),
    "travel-002": ("Domestic travel", "Economy class for flights under 4 hours. Hotel cap is INR 6,000/night in metro cities. Book via the travel portal."),
    "wfh-003": ("Remote work", "Up to 2 remote days per week with manager approval. Client-site roles follow the client's policy."),
    "sec-004": ("Data handling", "Customer PII must never leave approved systems. Use the DLP-approved file share for transfers."),
}
TICKETS: list[dict] = []
ALLOWED_TEAMS = {"hr", "it", "finance"}
# stdio servers: stdout is the protocol channel - log to stderr ONLY.
# (MCP protocol-level logging is deprecated in spec 2026-07-28, SEP-2577.)
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
log = logging.getLogger("policy-kb")

mcp = MCPServer(
    "policy-kb",
    instructions="HR policy lookup. Always cite policy ids. Never invent policy text.",
)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def search_policies(query: str, top_k: int = 2) -> list[dict]:
    """Keyword search over HR policies. Returns id, title and text for the best matches."""
    if not 1 <= top_k <= 5:
        raise ToolError("top_k must be between 1 and 5")
    words = set(re.findall(r"\w+", query.lower()))
    scored = []
    for pid, (title, text) in POLICIES.items():
        score = len(words & set(re.findall(r"\w+", f"{title} {text}".lower())))
        if score:
            scored.append((score, {"id": pid, "title": title, "text": text}))
    scored.sort(key=lambda s: -s[0])
    return [d for _, d in scored[:top_k]]


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False, openWorldHint=True))
def raise_ticket(team: str, summary: str) -> dict:
    """Raise a helpdesk ticket with hr, it or finance. Use only when the user asks for it."""
    team = team.lower().strip()
    if team not in ALLOWED_TEAMS:  # server-side allow-list: never trust the model's arguments
        raise ToolError(f"team must be one of {sorted(ALLOWED_TEAMS)}")
    if len(summary) > 300:
        raise ToolError("summary too long (max 300 chars)")
    log.info("creating ticket for %s", team)  # audit trail goes to stderr / your log pipeline
    ticket = {"id": f"TCK-{1000 + len(TICKETS)}", "team": team, "summary": summary,
              "requested_by": os.environ.get("POLICY_KB_USER", "unknown")}
    TICKETS.append(ticket)
    return ticket


@mcp.resource("policy://{policy_id}")
def get_policy(policy_id: str) -> str:
    """Full text of a single policy."""
    if policy_id not in POLICIES:
        raise ValueError(f"unknown policy {policy_id}")
    title, text = POLICIES[policy_id]
    return f"# {title} ({policy_id})\n\n{text}"


@mcp.prompt()
def policy_answer(question: str) -> str:
    """Grounded-answer template the host can offer as a slash command."""
    return (f"Answer using ONLY policies returned by search_policies. Cite ids like [leave-001]. "
            f"If nothing matches, say you don't know.\n\nQuestion: {question}")


if __name__ == "__main__":
    mcp.run()  # stdio by default; use mcp.run("streamable-http") to serve over HTTP
