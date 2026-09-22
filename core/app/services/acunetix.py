"""Парсер HTML-отчёта Acunetix → нормализованные находки (спец. §5.4).

HTML-экспорты сканеров бывают разной вёрстки, поэтому парсер устойчивый:
1) основной путь — таблица с колонками (severity/type/url/parameter/method);
2) запасной — секции с метками важности.
Дедупликация по (type, url, param); для источника считается SHA-256.
Маппинг колонок при необходимости настраивается под конкретный шаблон Acunetix.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from app.models.enums import Severity

_SEV_MAP = {
    "critical": Severity.critical,
    "high": Severity.high,
    "medium": Severity.medium,
    "low": Severity.low,
    "informational": Severity.info,
    "info": Severity.info,
}


def sha256_of(content: str | bytes) -> str:
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def normalize_severity(text: str) -> Severity:
    return _SEV_MAP.get((text or "").strip().lower(), Severity.info)


@dataclass
class ParsedReport:
    findings: list[dict] = field(default_factory=list)
    declared: int = 0


class _TableExtractor(HTMLParser):
    """Собирает все таблицы как списки строк (каждая строка — список ячеек)."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._cur_table: list[list[str]] | None = None
        self._cur_row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._cur_table = []
        elif tag == "tr" and self._cur_table is not None:
            self._cur_row = []
        elif tag in ("td", "th") and self._cur_row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._cur_row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._cur_row is not None:
            self._cur_table.append(self._cur_row)
            self._cur_row = None
        elif tag == "table" and self._cur_table is not None:
            self.tables.append(self._cur_table)
            self._cur_table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _column_index(headers: list[str], *names: str) -> int | None:
    for i, h in enumerate(headers):
        low = h.lower()
        if any(n in low for n in names):
            return i
    return None


def _parse_tables(content: str) -> list[dict]:
    ext = _TableExtractor()
    ext.feed(content)
    findings: list[dict] = []
    for table in ext.tables:
        if len(table) < 2:
            continue
        headers = [c.lower() for c in table[0]]
        sev_i = _column_index(headers, "severity", "risk")
        url_i = _column_index(headers, "url", "affected", "target", "location")
        if sev_i is None or url_i is None:
            continue
        type_i = _column_index(headers, "type", "alert", "name", "vulnerabilit")
        param_i = _column_index(headers, "parameter", "param")
        method_i = _column_index(headers, "method")
        for row in table[1:]:
            if len(row) <= max(sev_i, url_i):
                continue

            def cell(i):
                return row[i] if i is not None and i < len(row) else ""

            findings.append(
                {
                    "severity": normalize_severity(cell(sev_i)),
                    "type": cell(type_i) or "Unknown",
                    "url": cell(url_i) or None,
                    "param": cell(param_i) or None,
                    "method": cell(method_i) or None,
                }
            )
    return findings


_SECTION_RE = re.compile(
    r"(critical|high|medium|low|informational|info)\b.*?(https?://[^\s<\"']+)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_sections(content: str) -> list[dict]:
    text = re.sub(r"<[^>]+>", " ", content)
    out: list[dict] = []
    for m in _SECTION_RE.finditer(text):
        out.append(
            {
                "severity": normalize_severity(m.group(1)),
                "type": "Unknown",
                "url": m.group(2),
                "param": None,
                "method": None,
            }
        )
    return out


def parse_html(content: str) -> ParsedReport:
    findings = _parse_tables(content)
    if not findings:
        findings = _parse_sections(content)
    return ParsedReport(findings=findings, declared=len(findings))


def dedup_key(f: dict) -> str:
    return "|".join(
        [
            str(f.get("type", "")).strip().lower(),
            str(f.get("url", "") or "").strip().lower(),
            str(f.get("param", "") or "").strip().lower(),
        ]
    )
