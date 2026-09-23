"""Migrations on both a fresh database and a populated pre-M3 schema."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine

from app.db import Base
from app.security import crypto

ROOT = Path(__file__).parents[1]


def migrate(path: Path, revision="head"):
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path}"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_fresh_migration(tmp_path):
    path = tmp_path / "fresh.db"
    migrate(path)
    migrate(path)  # already at head
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0014_git_credentials"
        assert {"osint_artifacts", "osint_lookups"} <= {
            r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def test_upgrade_preserves_case_and_encrypts_legacy_mcp_env(tmp_path):
    path = tmp_path / "upgrade.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    # Reconstruct the actual M2 table shape; do not use today's live baseline.
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE osint_artifacts")
        db.execute("DROP TABLE osint_lookups")
        db.execute("ALTER TABLE mcp_servers DROP COLUMN env_secret_ref")
        db.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        db.execute("INSERT INTO alembic_version VALUES ('0002_m2')")
        db.execute(
            "INSERT INTO osint_cases (id, owner_id, subject_type, subject, artifacts, sources) VALUES ('case', 'user', 'domain', 'example.com', '[]', '[]')"
        )
        db.execute(
            "INSERT INTO mcp_servers (id, owner_id, name, transport, enabled, personas, env) VALUES ('server', 'user', 'legacy', 'stdio', 1, '[]', ?)",
            (json.dumps({"TOKEN": "old-secret"}),),
        )
    migrate(path)
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT subject FROM osint_cases WHERE id='case'").fetchone()[0]
            == "example.com"
        )
        env, ciphertext = db.execute("SELECT env, env_secret_ref FROM mcp_servers").fetchone()
        assert json.loads(env) == {} and "old-secret" not in ciphertext
        assert json.loads(crypto.decrypt(ciphertext)) == {"TOKEN": "old-secret"}
