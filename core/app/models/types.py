"""Portable JSON column type usable on both Postgres and SQLite."""
from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator

# Using generic JSON keeps list/dict columns portable for tests (SQLite) while
# still mapping to jsonb-capable storage on Postgres.
JSONList = JSON
