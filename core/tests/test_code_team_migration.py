"""Upgrade the actual pre-team worker shape without losing jobs or progress."""
import json
import sqlite3

from tests.test_m3_migration import migrate


def test_populated_pre_team_worker_upgrade(tmp_path):
    path = tmp_path / "pre-team.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY);
            INSERT INTO alembic_version VALUES ('0025_shell_checkpoints');
            CREATE TABLE users (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE chats (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE jobs (id VARCHAR(36) PRIMARY KEY);
            INSERT INTO users VALUES ('owner');
            INSERT INTO chats VALUES ('chat');
            INSERT INTO projects VALUES ('project');
            CREATE TABLE code_workers (
                id VARCHAR(36) PRIMARY KEY,
                owner_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                chat_id VARCHAR(36) NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                job_id VARCHAR(36) REFERENCES jobs(id) ON DELETE SET NULL,
                slot INTEGER, kind VARCHAR(16) NOT NULL, role VARCHAR(16) NOT NULL,
                task TEXT NOT NULL, status VARCHAR(20) NOT NULL,
                output TEXT NOT NULL, report TEXT NOT NULL, error TEXT,
                events JSON NOT NULL, payload JSON NOT NULL, changes JSON NOT NULL,
                applied JSON NOT NULL, summary JSON NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT uq_code_worker_slot UNIQUE (chat_id, slot)
            );
            INSERT INTO code_workers (id,owner_id,chat_id,project_id,kind,role,task,status,
                output,report,events,payload,changes,applied,summary)
            VALUES ('worker','owner','chat','project','agent','implement','Keep progress',
                'interrupted','saved output','saved report','[]','{"initialized":true}',
                '[]','{}','{}');
        """)
    migrate(path)
    migrate(path)
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT output, payload, team_id, member_name FROM code_workers").fetchone()
        assert row[0] == "saved output" and json.loads(row[1]) == {"initialized": True}
        assert row[2:] == (None, None)
        assert "code_teams" in {row[2] for row in db.execute("PRAGMA foreign_key_list(code_workers)")}
        assert {"code_teams", "code_team_messages", "code_transcripts"} <= {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
