"""Day 50 lab, part 1: measure what a LangGraph thread costs you in a DR review.

Offline. No model, no API key, no GPU. Fake nodes sleep to stand in for LLM and tool latency.

    python state_drill.py            # runs parts A to D and writes measurements.json
    python state_drill.py --part C   # one part only

A. Parallel fan-out without a reducer fails; with a reducer it merges.
B. Streaming: time to first event versus time to the final answer.
C. Kill the process mid-run under each durability mode, then resume.
   Counts the steps that run again and the duplicate tickets filed.
D. Checkpoint growth per conversation turn: plain reducer, DeltaChannel, and pruning.
"""
from __future__ import annotations

import argparse
import json
import operator
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.channels import DeltaChannel
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.errors import InvalidUpdateError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

SOURCES = ["policy_wiki", "hr_tickets", "sharepoint"]
STEP_SECONDS = 0.15  # stand-in for one retrieval or model call


# ---------------------------------------------------------------- part A
class BadState(TypedDict):
    question: str
    evidence: list[str]  # no reducer: two writers in one step is an error


class GoodState(TypedDict):
    question: str
    evidence: Annotated[list[str], operator.add]  # reducer: concatenate parallel writes
    answer: str


def _fan_out(state: dict) -> list[Send]:
    return [Send("retrieve", {"question": state["question"], "source": s}) for s in SOURCES]


def _retrieve(payload: dict) -> dict:
    time.sleep(STEP_SECONDS)
    return {"evidence": [f"{payload['source']}: passage about {payload['question']}"]}


def part_a() -> dict:
    results = {}
    for name, schema in (("no reducer", BadState), ("operator.add reducer", GoodState)):
        g = StateGraph(schema)
        g.add_node("retrieve", _retrieve)
        g.add_conditional_edges(START, _fan_out, ["retrieve"])
        g.add_edge("retrieve", END)
        try:
            out = g.compile().invoke({"question": "parental leave", "evidence": []})
            results[name] = f"ok, {len(out['evidence'])} passages merged"
        except InvalidUpdateError as exc:
            results[name] = f"InvalidUpdateError: {str(exc).splitlines()[0][:80]}"
    print("A. Parallel writers to one state key")
    for k, v in results.items():
        print(f"   {k:<22} {v}")
    return results


# ---------------------------------------------------------------- part B
def _draft(state: GoodState) -> dict:
    writer = get_stream_writer()
    for pct in (25, 50, 75, 100):
        time.sleep(STEP_SECONDS)
        writer({"progress": pct})  # shows up in stream_mode="custom"
    return {"answer": f"Answer from {len(state['evidence'])} passages"}


def build_research_graph(checkpointer=None):
    g = StateGraph(GoodState)
    g.add_node("retrieve", _retrieve)
    g.add_node("draft", _draft)
    g.add_conditional_edges(START, _fan_out, ["retrieve"])
    g.add_edge("retrieve", "draft")
    g.add_edge("draft", END)
    return g.compile(checkpointer=checkpointer)


def part_b() -> dict:
    graph = build_research_graph()
    start = time.perf_counter()
    first = None
    events = 0
    for part in graph.stream(
        {"question": "parental leave", "evidence": []},
        stream_mode=["updates", "custom"],
        version="v2",  # every chunk is {"type": ..., "ns": ..., "data": ...}
    ):
        events += 1
        if first is None:
            first = time.perf_counter() - start
    total = time.perf_counter() - start
    print("B. Streaming the same run")
    print(f"   first event after {first:.2f}s, final answer after {total:.2f}s, {events} events")
    return {"first_event_s": round(first, 2), "total_s": round(total, 2), "events": events}


# ---------------------------------------------------------------- part C
class TicketState(TypedDict):
    request_id: str
    log: Annotated[list[str], operator.add]


def _ledger(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY, idem_key TEXT UNIQUE, body TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS runs (node TEXT)")
    con.commit()
    return con


def build_ticket_graph(checkpointer, ledger_path: str, use_key: bool, crash_in: str | None):
    def step(name: str):
        def node(state: TicketState) -> dict:
            con = _ledger(ledger_path)
            con.execute("INSERT INTO runs VALUES (?)", (name,))
            con.commit()
            time.sleep(0.02)
            if name == "file_ticket":
                key = f"{state['request_id']}:file_ticket" if use_key else None
                con.execute("INSERT OR IGNORE INTO tickets (idem_key, body) VALUES (?, ?)", (key, "laptop request"))
                con.commit()
            con.close()
            if crash_in == name:
                os._exit(137)  # the pod is killed: no cleanup, no finally blocks
            return {"log": [name]}

        return node

    g = StateGraph(TicketState)
    names = ["classify", "lookup_user", "check_policy", "file_ticket", "notify"]
    for n in names:
        g.add_node(n, step(n))
    g.add_edge(START, names[0])
    for a, b in zip(names, names[1:]):
        g.add_edge(a, b)
    g.add_edge(names[-1], END)
    return g.compile(checkpointer=checkpointer)


def worker(db: str, ledger: str, durability: str, use_key: bool) -> None:
    """Child process: runs the graph and dies inside file_ticket, after the write."""
    with SqliteSaver.from_conn_string(db) as saver:
        graph = build_ticket_graph(saver, ledger, use_key, crash_in="file_ticket")
        graph.invoke({"request_id": "REQ-1", "log": []}, {"configurable": {"thread_id": "t1"}}, durability=durability)


def part_c() -> dict:
    print("C. Kill the worker inside file_ticket (after its write), then resume")
    print(f"   {'durability':<11}{'idempotency key':<17}{'resumed from':<16}{'steps re-run':<14}{'tickets filed'}")
    rows = []
    for durability in ("exit", "async", "sync"):
        for use_key in (False, True):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                db, ledger = str(Path(tmp, "ckpt.sqlite")), str(Path(tmp, "ledger.sqlite"))
                subprocess.run(
                    [sys.executable, __file__, "--worker", db, ledger, durability, str(int(use_key))],
                    check=False,
                )
                con = _ledger(ledger)
                before = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
                with SqliteSaver.from_conn_string(db) as saver:
                    graph = build_ticket_graph(saver, ledger, use_key, crash_in=None)
                    cfg = {"configurable": {"thread_id": "t1"}}
                    snapshot = graph.get_state(cfg)
                    if snapshot.next:  # a checkpoint survived: continue the thread
                        resumed_from = snapshot.next[0]
                        graph.invoke(None, cfg, durability="sync")
                    else:  # nothing survived: the client has to send the request again
                        resumed_from = "(start)"
                        graph.invoke({"request_id": "REQ-1", "log": []}, cfg, durability="sync")
                after = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
                tickets = con.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
                con.close()
                # steps that had already run before the crash and ran again afterwards
                rerun = (after - before) - (5 - before)
                rows.append(
                    {"durability": durability, "idempotency_key": use_key, "resumed_from": resumed_from,
                     "steps_rerun": rerun, "tickets": tickets}
                )
                print(f"   {durability:<11}{('yes' if use_key else 'no'):<17}{resumed_from:<16}{rerun:<14}{tickets}")
    # write cost of each mode on a healthy run
    timing = {}
    for durability in ("exit", "async", "sync"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with SqliteSaver.from_conn_string(str(Path(tmp, "c.sqlite"))) as saver:
                graph = build_ticket_graph(saver, str(Path(tmp, "l.sqlite")), True, None)
                t0 = time.perf_counter()
                for i in range(20):
                    graph.invoke({"request_id": f"R{i}", "log": []}, {"configurable": {"thread_id": f"h{i}"}}, durability=durability)
                timing[durability] = round((time.perf_counter() - t0) / 20 * 1000, 1)
    print(f"   healthy-run time per 5-step request (ms): {timing}")
    return {"crash": rows, "healthy_ms_per_run": timing}


# ---------------------------------------------------------------- part D
class ChatState(TypedDict):
    messages: Annotated[list[str], operator.add]  # full list stored in every checkpoint


def _extend(state: list[str] | None, writes: list[list[str]]) -> list[str]:
    out = list(state or [])
    for w in writes:
        out.extend(w)
    return out


class DeltaChatState(TypedDict):
    # beta in langgraph 1.2: checkpoints hold a marker, the saver replays the writes
    messages: Annotated[list[str], DeltaChannel(_extend)]


def _db_bytes(con: sqlite3.Connection) -> int:
    a = con.execute("SELECT COALESCE(SUM(LENGTH(checkpoint) + LENGTH(metadata)), 0) FROM checkpoints").fetchone()[0]
    b = con.execute("SELECT COALESCE(SUM(LENGTH(value)), 0) FROM writes").fetchone()[0]
    return a + b


def _keep_latest(con: sqlite3.Connection, thread_id: str) -> None:
    """SqliteSaver 3.1 has no prune(), so this is the retention job you would schedule.
    Safe for plain reducers only. Do not run it on a thread that uses DeltaChannel."""
    latest = con.execute(
        "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ? ORDER BY checkpoint_id DESC LIMIT 1", (thread_id,)
    ).fetchone()[0]
    for table in ("checkpoints", "writes"):
        con.execute(f"DELETE FROM {table} WHERE thread_id = ? AND checkpoint_id <> ?", (thread_id, latest))
    con.commit()


def _chat_run(schema, turns: int):
    def retrieve(state) -> dict:
        return {"messages": ["[context] " + "policy text " * 120]}  # about 1.4 KB of retrieved text

    def answer(state) -> dict:
        return {"messages": ["[assistant] " + "answer text " * 30]}

    g = StateGraph(schema)
    g.add_node("retrieve", retrieve)
    g.add_node("answer", answer)
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "answer")
    g.add_edge("answer", END)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    graph = g.compile(checkpointer=SqliteSaver(con))
    cfg = {"configurable": {"thread_id": "chat-1"}}
    sizes = []
    for turn in range(1, turns + 1):
        graph.invoke({"messages": [f"[user] question {turn}"]}, cfg, durability="sync")
        sizes.append(_db_bytes(con))
    return con, graph, cfg, sizes


def part_d(turns: int = 20) -> dict:
    con, graph, cfg, sizes = _chat_run(ChatState, turns)
    state = graph.get_state(cfg).values
    state_bytes = len(json.dumps(state))
    checkpoints = con.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    _keep_latest(con, "chat-1")
    pruned = _db_bytes(con)
    assert graph.get_state(cfg).values == state, "pruning must not change the live state"
    _, dgraph, dcfg, dsizes = _chat_run(DeltaChatState, turns)
    assert dgraph.get_state(dcfg).values == state, "DeltaChannel must rebuild the same state"

    print("D. Checkpoint storage for one chat thread (SqliteSaver, every step kept)")
    print(f"   {'after turn':<12}{'operator.add':>14}{'DeltaChannel (beta)':>22}")
    for t in sorted({1, 5, 10, turns}):
        print(f"   {t:<12}{sizes[t - 1] / 1024:>11.1f} KB{dsizes[t - 1] / 1024:>19.1f} KB")
    print(f"   live state is {state_bytes / 1024:.1f} KB; operator.add history is {sizes[-1] / state_bytes:.0f}x that across {checkpoints} checkpoints")
    print(f"   operator.add after keep-latest pruning: {pruned / 1024:.1f} KB")
    return {
        "turns": turns,
        "kb_by_turn": [round(s / 1024, 1) for s in sizes],
        "delta_kb_by_turn": [round(s / 1024, 1) for s in dsizes],
        "live_state_kb": round(state_bytes / 1024, 1),
        "checkpoints": checkpoints,
        "pruned_kb": round(pruned / 1024, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=list("ABCD"))
    ap.add_argument("--worker", nargs=4, metavar=("DB", "LEDGER", "DURABILITY", "USE_KEY"))
    args = ap.parse_args()
    if args.worker:
        db, ledger, durability, use_key = args.worker
        worker(db, ledger, durability, use_key == "1")
        return
    parts = {"A": part_a, "B": part_b, "C": part_c, "D": part_d}
    out = {}
    for key, fn in parts.items():
        if args.part in (None, key):
            out[key] = fn()
            print()
    if args.part is None:
        Path(__file__).with_name("measurements.json").write_text(json.dumps(out, indent=2))
        print("wrote measurements.json (dr_nfr_model.py reads it)")


if __name__ == "__main__":
    main()
