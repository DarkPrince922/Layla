"""ORM models. Importing this package registers every table on ``Base.metadata``
so Alembic autogeneration and metadata.create_all() see them all.
"""
from app.models.agent import AgentConfig, AgentRun, AgentStep, AgentWorker
from app.models.audit import AuditLog
from app.models.chat import Chat, Checkpoint, Message
from app.models.combo import Combo
from app.models.design import Design
from app.models.git import GitCredential
from app.models.grok_login import GrokLogin
from app.models.job import Job
from app.models.knowledge import KnowledgeChunk, KnowledgeDoc
from app.models.mcp import IntelKey, McpServer
from app.models.osint import OsintArtifact, OsintCase, OsintLookup
from app.models.pentest import (
    Engagement,
    Evidence,
    Finding,
    Report,
    Scope,
    Server,
    Venue,
)
from app.models.pentest_authorization import PentestAuthorization
from app.models.pentest_workbench import EngagementRecord, EngagementTask
from app.models.persona import Persona
from app.models.provider import Provider, ProviderKey
from app.models.telegram import TelegramConfig
from app.models.user import AdminBootstrap, Project, User, Workspace

__all__ = [
    "AdminBootstrap",
    "AgentConfig",
    "AgentRun",
    "AgentStep",
    "AgentWorker",
    "AuditLog",
    "Chat",
    "Checkpoint",
    "Combo",
    "Design",
    "Engagement",
    "EngagementRecord",
    "EngagementTask",
    "Evidence",
    "Finding",
    "GitCredential",
    "GrokLogin",
    "IntelKey",
    "Job",
    "KnowledgeChunk",
    "KnowledgeDoc",
    "McpServer",
    "Message",
    "OsintArtifact",
    "OsintCase",
    "OsintLookup",
    "PentestAuthorization",
    "Persona",
    "Project",
    "Provider",
    "ProviderKey",
    "Report",
    "Scope",
    "Server",
    "TelegramConfig",
    "User",
    "Venue",
    "Workspace",
]
