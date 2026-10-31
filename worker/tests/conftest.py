import os

import pytest

DB_URL = os.environ.get("TEST_DATABASE_URL")

# Tables the pipeline writes to; truncated before each DB test.
PIPELINE_TABLES = (
    "llm_batches, extractions, edition_cards, editions, cards, cluster_articles, clusters, "
    "article_entities, entities, articles, feeds, sources, pipeline_runs, jobs"
)


@pytest.fixture
def db():
    """A connection to the throwaway test database, emptied first.

    Skips when TEST_DATABASE_URL isn't set. Never point it at a real database.
    """
    if not DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    import psycopg

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(f"truncate {PIPELINE_TABLES} restart identity cascade")
        conn.commit()
        yield conn
