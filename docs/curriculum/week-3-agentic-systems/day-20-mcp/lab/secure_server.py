"""Stretch 2: the same tools over Streamable HTTP, protected as an OAuth 2.1 resource server.
DEV ONLY: DevTokenVerifier stands in for real JWT validation against your IdP
(Entra ID / Keycloak / IBM Security Verify): check signature, iss, exp, aud == this server."""
import time
from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
import policy_server as base

RESOURCE = "http://127.0.0.1:8000/mcp"
DEV_TOKENS = {  # token -> (subject, scopes, audience)
    "dev-reader": ("rahul", ["policy:read"], RESOURCE),
    "dev-other-aud": ("rahul", ["policy:read"], "https://some-other-api.example"),
}


class DevTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token not in DEV_TOKENS:
            return None
        sub, scopes, aud = DEV_TOKENS[token]
        return AccessToken(token=token, client_id="day20-lab", scopes=scopes, subject=sub,
                           resource=aud, expires_at=int(time.time()) + 3600)


mcp = MCPServer(
    "policy-kb-secure",
    token_verifier=DevTokenVerifier(),
    auth=AuthSettings(
        issuer_url="https://idp.example.com",          # your authorization server
        resource_server_url=RESOURCE,                   # advertised via RFC 9728 metadata
        required_scopes=["policy:read"],
        validate_token_resource=True,                   # reject tokens minted for other audiences
    ),
)
mcp.add_tool(base.search_policies)   # reuse the tool functions from policy_server.py

if __name__ == "__main__":
    mcp.run("streamable-http")
