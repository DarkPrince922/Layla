import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_scheduler_and_mcp_upgrade_downgrade_preserve_existing_data(tmp_path):
    path = tmp_path / "scheduled-code.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path}"}
    def migrate(*args):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=Path(__file__).parents[1],
            env=env, capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 0, result.stderr
    migrate("upgrade", "0031_code_session_archive")
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users (id, email, pw_hash, is_active, is_admin, must_change_password, created_at, updated_at) VALUES ('existing-owner', 'keep@example.com', 'hash', 1, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
    migrate("upgrade", "head")
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"code_schedules", "code_mcp_auth", "code_mcp_artifacts"} <= tables
        assert db.execute("SELECT email FROM users WHERE id='existing-owner'").fetchone() == ("keep@example.com",)
        assert {row[2] for row in db.execute("PRAGMA foreign_key_list(code_schedules)")} >= {"users", "chats", "projects", "jobs"}
    migrate("check")
    migrate("downgrade", "0031_code_session_archive")
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"code_schedules", "code_mcp_auth", "code_mcp_artifacts"} & tables
        assert db.execute("SELECT email FROM users WHERE id='existing-owner'").fetchone()
