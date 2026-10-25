"""Embed new articles and assign them to clusters.

Usage: python -m cluster.run
"""

from __future__ import annotations

import sys
from dataclasses import asdict

from cluster.embed import SentenceTransformerEmbedder, embed_pending
from cluster.online import ClusterConfig, run_online
from common.db import connect
from common.runs import pipeline_run


def main() -> int:
    cfg = ClusterConfig.from_env()
    with connect() as conn, pipeline_run(conn, "cluster") as stats:
        stats["embedded"] = embed_pending(conn, SentenceTransformerEmbedder())
        stats.update(run_online(conn, cfg))
        stats["config"] = asdict(cfg)
        print(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
