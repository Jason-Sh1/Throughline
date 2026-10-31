"""Record each pipeline step in `pipeline_runs` for the admin dashboard."""

from __future__ import annotations

import json
import traceback
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg


@contextmanager
def pipeline_run(conn: psycopg.Connection, kind: str) -> Iterator[dict]:
    """Yields a stats dict; on exit the run is marked succeeded (with the stats)
    or failed (with the traceback), and the exception is re-raised."""
    with conn.cursor() as cur:
        cur.execute("insert into pipeline_runs (kind) values (%s) returning id", (kind,))
        run_id = cur.fetchone()[0]
    conn.commit()
    stats: dict = {}
    try:
        yield stats
    except BaseException:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                """update pipeline_runs set status = 'failed', finished_at = now(),
                   stats = %s, error = %s where id = %s""",
                (json.dumps(stats, default=str), traceback.format_exc()[-4000:], run_id),
            )
        conn.commit()
        raise
    with conn.cursor() as cur:
        # LLM steps put their spend in the stats; it also gets its own columns so
        # the admin dashboard can sum it.
        cur.execute(
            """update pipeline_runs set status = 'succeeded', finished_at = now(), stats = %s,
                   input_tokens = %s, output_tokens = %s, cost_usd = %s
               where id = %s""",
            (json.dumps(stats, default=str), stats.get("input_tokens", 0),
             stats.get("output_tokens", 0), stats.get("cost_usd", 0), run_id),
        )  # fmt: skip
    conn.commit()
