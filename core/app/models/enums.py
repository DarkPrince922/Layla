"""Enumerations used across the data model."""
from __future__ import annotations

import enum


class Domain(str, enum.Enum):
    code = "code"
    pentest = "pentest"
    osint = "osint"
    design = "design"


class ProviderKind(str, enum.Enum):
    openai_compatible = "openai_compatible"
    anthropic = "anthropic"
    custom = "custom"


class KeyStatus(str, enum.Enum):
    active = "active"
    rate_limited = "rate_limited"
    exhausted = "exhausted"
    disabled = "disabled"


class PersonaKind(str, enum.Enum):
    coding = "coding"
    design = "design"
    review = "review"
    bug_hunter = "bug_hunter"
    security = "security"
    pentest = "pentest"
    osint = "osint"
    custom = "custom"


class McpTransport(str, enum.Enum):
    stdio = "stdio"
    http = "http"


class IntelProvider(str, enum.Enum):
    shodan = "shodan"
    virustotal = "virustotal"
    securitytrails = "securitytrails"
    urlscan = "urlscan"
    semrush = "semrush"


class EngagementStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    paused = "paused"
    closed = "closed"


class Severity(str, enum.Enum):
    critical = "CRITICAL"
    high = "HIGH"
    medium = "MEDIUM"
    low = "LOW"
    info = "INFO"


class FindingStatus(str, enum.Enum):
    open = "open"
    triaging = "triaging"
    confirmed = "confirmed"
    false_positive = "false_positive"
    remediated = "remediated"


class FindingSource(str, enum.Enum):
    scanner = "scanner"
    manual = "manual"
    agent = "agent"


class VenueMode(str, enum.Enum):
    """Where active commands / target traffic run (spec §5.4, §7)."""

    analysis_only = "analysis_only"   # default, safe: no shell, no target traffic
    attack_box = "attack_box"         # remote SSH box (recommended for live tests)
    this_machine = "this_machine"     # operator IP — opt-in, off by default


class EgressRoute(str, enum.Enum):
    inherit = "inherit"   # inherit global Privacy Chain default
    direct = "direct"
    tor = "tor"
    proxy = "proxy"


class ServerStatus(str, enum.Enum):
    ready = "ready"
    active = "active"
    error = "error"


class ServerAuth(str, enum.Enum):
    key = "key"            # приватный SSH-ключ (рекомендуется)
    password = "password"  # пароль (осознанно слабее ключа)


class AgentRunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    paused = "paused"       # HITL pause awaiting operator confirmation
    completed = "completed"
    failed = "failed"
    blocked = "blocked"     # blocked by scope/egress guard


class SubjectType(str, enum.Enum):
    person = "person"
    company = "company"
    domain = "domain"


class DesignStack(str, enum.Enum):
    html = "html"
    react = "react"
    vue = "vue"
