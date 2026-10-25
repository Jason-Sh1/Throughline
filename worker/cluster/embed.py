"""Local sentence embeddings, stored in articles.embedding (pgvector).

We embed "title. summary" with a small sentence-transformers model on CPU:
free, private, and fast enough for a few thousand articles a day. Vectors are
L2-normalized, so cosine similarity is just a dot product.

Install the model runtime once with `make embed-deps` (CPU-only PyTorch).
"""

from __future__ import annotations

import os
from typing import Protocol

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # 384 dims, matches the schema
DIM = 384


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str | None = None):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise RuntimeError("sentence-transformers missing; run `make embed-deps`") from exc
        self.model = SentenceTransformer(model_name or os.environ.get("EMBED_MODEL", DEFAULT_MODEL))

    def encode(self, texts: list[str]) -> np.ndarray:
        return self.model.encode(texts, batch_size=32, normalize_embeddings=True)


def to_array(v) -> np.ndarray:
    """pgvector returns `Vector` objects (or numpy arrays, depending on version)."""
    if hasattr(v, "to_numpy"):
        v = v.to_numpy()
    return np.asarray(v, dtype=np.float32)


def article_text(title: str, summary: str | None) -> str:
    return f"{title}. {summary}" if summary else title


def embed_pending(
    conn: psycopg.Connection, embedder: Embedder, batch_size: int = 128, limit: int = 20000
) -> int:
    """Embed articles that don't have a vector yet. Duplicates are skipped:
    they inherit their original's cluster instead."""
    register_vector(conn)
    done = 0
    while done < limit:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, title, summary from articles
                where embedding is null and duplicate_of is null
                order by id limit %s
                """,
                (min(batch_size, limit - done),),
            )
            rows = cur.fetchall()
            if not rows:
                break
            vectors = embedder.encode([article_text(t, s) for _, t, s in rows])
            cur.executemany(
                "update articles set embedding = %s where id = %s",
                [
                    (np.asarray(v, dtype=np.float32), i)
                    for (i, _, _), v in zip(rows, vectors, strict=True)
                ],
            )
        conn.commit()
        done += len(rows)
    return done
