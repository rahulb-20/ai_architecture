"""Capacity + token-cost model for a 50k-employee knowledge assistant.
Every number is an ASSUMPTION you must defend in the review. Prices are placeholders:
replace them with your negotiated / current list prices before showing anyone."""

EMPLOYEES = 50_000
DAU_RATIO = 0.30              # 30% use it on a working day
QUERIES_PER_USER = 6
WORK_DAYS = 22
BUSY_HOURS = 8                # traffic concentrated in office hours (multi-timezone flattens this)
PEAK_FACTOR = 3.0             # peak-hour QPS vs. busy-hour average

# tokens per query
SYS_TOK, CTX_CHUNKS, CHUNK_TOK, HIST_TOK, Q_TOK, OUT_TOK = 800, 6, 500, 600, 60, 450
REWRITE_IN, REWRITE_OUT = 700, 60          # small-model query rewrite / router step

# placeholder prices, USD per 1M tokens (EDIT ME)
LARGE_IN, LARGE_OUT, LARGE_CACHED_IN = 2.00, 8.00, 0.50
SMALL_IN, SMALL_OUT = 0.10, 0.40
SEMANTIC_CACHE_HIT = 0.15     # share of queries answered from cache (FAQ-heavy HR/IT traffic)
PROMPT_CACHE_SHARE = SYS_TOK / (SYS_TOK + CTX_CHUNKS * CHUNK_TOK + HIST_TOK + Q_TOK)

# retrieval sizing
DOCS, CHUNKS_PER_DOC, DIM = 2_000_000, 12, 1024


def main() -> None:
    q_day = EMPLOYEES * DAU_RATIO * QUERIES_PER_USER
    q_month = q_day * WORK_DAYS
    avg_qps = q_day / (BUSY_HOURS * 3600)
    peak_qps = avg_qps * PEAK_FACTOR
    llm_q = q_month * (1 - SEMANTIC_CACHE_HIT)

    in_tok = SYS_TOK + CTX_CHUNKS * CHUNK_TOK + HIST_TOK + Q_TOK
    large = llm_q * (in_tok * ((1 - PROMPT_CACHE_SHARE) * LARGE_IN + PROMPT_CACHE_SHARE * LARGE_CACHED_IN)
                     + OUT_TOK * LARGE_OUT) / 1e6
    small = q_month * (REWRITE_IN * SMALL_IN + REWRITE_OUT * SMALL_OUT) / 1e6
    total = large + small

    vectors = DOCS * CHUNKS_PER_DOC
    gb = lambda bytes_per: vectors * bytes_per / 1e9
    print(f"Queries/day {q_day:,.0f} | /month {q_month:,.0f} | avg {avg_qps:.1f} QPS | peak {peak_qps:.1f} QPS")
    print(f"Input tokens/query {in_tok:,} (static prefix share {PROMPT_CACHE_SHARE:.0%})")
    print(f"LLM cost/month  large ${large:,.0f} + small ${small:,.0f} = ${total:,.0f}")
    print(f"Cost per query  ${total / q_month:.4f} | per employee/month ${total / EMPLOYEES:.2f}")
    print(f"Vectors {vectors / 1e6:.0f}M x {DIM}d -> fp32 {gb(DIM * 4):.0f} GB | int8 {gb(DIM):.0f} GB "
          f"| binary {gb(DIM / 8):.1f} GB (+ HNSW graph ~{gb(32 * 4 * 2):.0f} GB at M=32)")
    # sensitivity: the two levers that move the bill most
    for chunks in (4, 6, 10):
        t = SYS_TOK + chunks * CHUNK_TOK + HIST_TOK + Q_TOK
        c = llm_q * (t * LARGE_IN + OUT_TOK * LARGE_OUT) / 1e6
        print(f"  sensitivity: {chunks:>2} chunks/query, no caching -> ${c:,.0f}/month")


if __name__ == "__main__":
    main()
