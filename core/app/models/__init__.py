"""ORM models. Importing this package registers every table on ``Base.metadata``
so Alembic autogeneration and metadata.create_all() see them all.
"""
from app.models.agent import AgentConfig, AgentRun, AgentStep
from app.models.audit import AuditLog
from app.models.chat import Chat, Message
from app.models.design import Design
from app.models.knowledge import KnowledgeChunk, KnowledgeDoc
from app.models.mcp import IntelKey, McpServer
from app.models.telegram import TelegramConfig
from app.models.osint import OsintArtifact, OsintCase, OsintLookup
from app.models.persona import Persona
from app.models.pentest import (
    Engagement,
    Evidence,
    Finding,
    Report,
    Scope,
    Server,
    Venue,
)
from app.models.provider import Provider, ProviderKey
from app.models.user import Project, User, Workspace

__all__ = [
    "AgentRun",
    "AgentStep",
    "AgentConfig",
    "AuditLog",
    "Chat",
    "Message",
    "Design",
    "KnowledgeChunk",
    "KnowledgeDoc",
    "IntelKey",
    "McpServer",
    "TelegramConfig",
    "OsintCase",
    "OsintArtifact",
    "OsintLookup",
    "Persona",
    "Engagement",
    "Evidence",
    "Finding",
    "Report",
    "Scope",
    "Server",
    "Venue",
    "Provider",
    "ProviderKey",
    "Project",
    "User",
    "Workspace",
]
