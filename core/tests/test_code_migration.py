"""Upgrade a populated pre-project-chat schema, including SQLite FK/index DDL."""

import sqlite3

from tests.test_m3_migration import migrate


def test_upgrade_legacy_chat_keeps_history(tmp_path):
    path = tmp_path / "legacy-code.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY);
            INSERT INTO alembic_version VALUES ('0006_provmodels');
            CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE chats (id VARCHAR(36) PRIMARY KEY, title VARCHAR(300));
            INSERT INTO chats VALUES ('existing', 'Keep this chat');
        """)
    migrate(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT title, project_id FROM chats").fetchone() == (
            "Keep this chat",
            None,
        )
        assert any(row[2] == "projects" for row in db.execute("PRAGMA foreign_key_list(chats)"))
        assert any(
            row[1] == "ix_chats_project_id" for row in db.execute("PRAGMA index_list(chats)")
        )
