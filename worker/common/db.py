"""Postgres connection helper. The worker connects with the service-role
DATABASE_URL, which bypasses row-level security."""

from __future__ import annotations

import os

import psycopg
from dotenv import load_dotenv


def connect(url: str | None = None) -> psycopg.Connection:
    load_dotenv()
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    return psycopg.connect(url)
