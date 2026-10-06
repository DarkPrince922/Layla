"""Парсер отчётов Acunetix → нормализованные находки (спец. §5.4).

Acunetix умеет выгружать отчёт в нескольких форматах. Основной машинный формат —
**XML** (`<ScanGroup>/<Scan>/<ReportItems>/<ReportItem>`), именно его стоит грузить.
Раньше парсер понимал только HTML, поэтому XML-выгрузка «ломалась»: HTML-парсер
растаскивал XML-теги как текст, подмешивал ссылки-референсы (owasp/cwe/…) в цели и
путал важность/тип. Теперь формат определяется автоматически:

1) **XML Acunetix** (`parse_report` → `_parse_acunetix_xml`): берём из каждого
   `<ReportItem>` имя (тип), важность, затронутый путь (`Affects`) и склеиваем его
   со `StartURL` сканера в полный URL цели; параметр/метод достаём из `Parameter`,
   `TechnicalDetails/Request` и описаний; `IsFalsePositive` пропускаем. Ссылки из
   `References` НЕ считаются целями.
2) **HTML-отчёт** (fallback): а) таблица с колонками
   (severity/type/url/parameter/method/status); б) DOM-секции: заголовок уязвимости
   (тип) + метка важности + затронутые URL. Ссылки на документацию/референсы
   (github, owasp, mozilla и т.п.) отбрасываются.

Дедуп по (type, url, param); для источника считается SHA-256.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from app.models.enums import Severity

_SEV_MAP = {
    "critical": Severity.critical,
    "high": Severity.high,
    "medium": Severity.medium,
    "low": Severity.low,
    "informational": Severity.info,
    "info": Severity.info,
}

# Классический Acunetix WVS кодирует важность числом: 0=info … 3=high (4=critical).
_SEV_NUM = {
    "0": Severity.info,
    "1": Severity.low,
    "2": Severity.medium,
    "3": Severity.high,
    "4": Severity.critical,
}

# Домены-референсы (документация/шаблон отчёта) — не цели теста.
_REFERENCE_HOSTS = {
    "github.com", "www.github.com", "acunetix.com", "www.acunetix.com",
    "invicti.com", "www.invicti.com", "w3.org", "www.w3.org",
    "developer.mozilla.org", "mozilla.org", "owasp.org", "www.owasp.org",
    "cheatsheetseries.owasp.org", "portswigger.net", "cwe.mitre.org",
    "cve.mitre.org", "nvd.nist.gov", "en.wikipedia.org", "wikipedia.org",
    "tools.ietf.org", "ietf.org", "capec.mitre.org", "wapiti.sourceforge.io",
}

_SEV_WORD_RE = re.compile(r"^\s*(critical|high|medium|low|informational|info)\s*$", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_PARAM_RE = re.compile(r"(?:parameter|param|variable|input)\s*[:=]\s*([A-Za-z0-9_\-\[\]]+)", re.I)
_METHOD_RE = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH|HEAD)\b")
_CONFIRMED_RE = re.compile(r"\b(confirmed|verified|подтвержд)", re.I)


def sha256_of(content: str | bytes) -> str:
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def normalize_severity(text: str) -> Severity:
    t = (text or "").strip().lower()
    if t in _SEV_NUM:
        return _SEV_NUM[t]
    for word, sev in _SEV_MAP.items():
        if word in t:
            return sev
    return Severity.info


def is_reference_host(url: str | None) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in _REFERENCE_HOSTS


@dataclass
class ParsedReport:
    findings: list[dict] = field(default_factory=list)
    declared: int = 0


# --------------------------------------------------------------------------- #
# Стратегия 1: таблицы
# --------------------------------------------------------------------------- #
class _TableExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _col(headers: list[str], *names: str) -> int | None:
    for i, h in enumerate(headers):
        low = h.lower()
        if any(n in low for n in names):
            return i
    return None


def _status_from(text: str) -> str | None:
    text = (text or "").strip()
    if re.search(r"unconfirmed|unverified|not (?:confirmed|verified)|не подтвержд", text, re.I):
        return None
    if re.fullmatch(r"confirmed|verified|подтверждено|подтвержден", text, re.I) or re.search(
            r"^(?:confirmed|verified) via\b|(?:status|state|статус)\s*:\s*(?:confirmed|verified|подтвержден)", text, re.I):
        return "confirmed"
    return None


def _parse_tables(content: str) -> list[dict]:
    ext = _TableExtractor()
    ext.feed(content)
    findings: list[dict] = []
    for table in ext.tables:
        if len(table) < 2:
            continue
        headers = [c.lower() for c in table[0]]
        sev_i = _col(headers, "severity", "risk", "criticality")
        url_i = _col(headers, "url", "affected", "target", "location", "web page", "address")
        if sev_i is None or url_i is None:
            continue
        type_i = _col(headers, "type", "alert", "name", "vulnerabilit", "threat", "title")
        if type_i is None:
            continue  # severity/affected-count summary tables are not findings
        param_i = _col(headers, "parameter", "param", "variable", "input")
        method_i = _col(headers, "method")
        status_i = _col(headers, "status", "state", "confidence", "confirm")
        for row in table[1:]:
            if len(row) <= max(sev_i, url_i):
                continue

            def cell(i):
                return row[i] if i is not None and i < len(row) else ""

            url = (cell(url_i) or "").strip() or None
            name = cell(type_i).strip()
            if not _is_finding_title(name) or is_reference_host(url) or (url and not _URL_RE.fullmatch(url)):
                continue
            findings.append(
                {
                    "severity": normalize_severity(cell(sev_i)),
                    "type": (cell(type_i) or "Unknown").strip() or "Unknown",
                    "url": url,
                    "param": (cell(param_i) or "").strip() or None,
                    "method": (cell(method_i) or "").strip() or None,
                    "status": _status_from(cell(status_i)),
                }
            )
    return findings


# --------------------------------------------------------------------------- #
# Стратегия 2: DOM-секции (заголовок уязвимости + важность + затронутые URL)
# --------------------------------------------------------------------------- #
class _DomExtractor(HTMLParser):
    """Ordered semantic headings/text; nested markup must not end a heading early."""
    _HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    _VOID = {"br", "hr", "img", "input", "meta", "link", "wbr", "source", "area", "base", "embed", "param", "track", "col"}

    def __init__(self):
        super().__init__()
        self.events = []
        self.stack = []
        self.captures = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        if tag in ("script", "style", "head"):
            self.ignored += 1
        if tag not in self._VOID:
            self.stack.append(tag)
        if self.ignored:
            return
        cls = ad.get("class") or ""
        heading = tag in self._HEADING_TAGS or bool(re.search(
            r"(?:^|[ _-])(?:title|(?:mso)?heading[1-6]?|(?:vuln|alert|issue)[_-]?name)(?:$|[ _-])", cls, re.I))
        if heading and not self.captures:
            index = len(self.events)
            self.events.append(("heading", ""))
            self.captures.append((len(self.stack), index, []))
        if tag == "a" and ad.get("href", "").startswith(("http://", "https://")):
            self.events.append(("url", ad["href"]))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.stack and tag in self.stack:
            depth = len(self.stack) - self.stack[::-1].index(tag)
            for cap in list(self.captures):
                if cap[0] >= depth:
                    value = " ".join("".join(cap[2]).split())
                    match = re.fullmatch(r"severity\s*:?\s*(critical|high|medium|low|informational|info)", value, re.I)
                    kind = "sev" if match else ("groupsev" if _SEV_WORD_RE.fullmatch(value) else "heading")
                    if match:
                        value = match.group(1)
                    self.events[cap[1]] = (kind, value)
                    self.captures.remove(cap)
            self.stack = self.stack[:depth - 1]
        if tag in ("script", "style", "head") and self.ignored:
            self.ignored -= 1

    def handle_data(self, data):
        if self.ignored:
            return
        for depth, index, buf in self.captures:
            buf.append(data)
        if self.captures:
            return
        value = " ".join(data.split())
        if not value:
            return
        match = re.fullmatch(r"(?:severity\s*:?\s*)?(critical|high|medium|low|informational|info)", value, re.I)
        if match:
            self.events.append(("sev", match.group(1)))
        else:
            self.events.append(("text", value))
            self.events.extend(("url", url) for url in _URL_RE.findall(value))


_STRUCTURAL = {
    "affected items", "affected item", "affected urls", "affected url", "affected pages",
    "total alerts found", "alert group", "alert details", "vulnerability details", "vulnerabilities",
    "severity", "risk", "confidence", "status", "description", "impact", "recommendation",
    "recommendations", "remediation", "references", "web references", "details", "attack details",
    "technical details", "request", "request headers", "response", "response headers",
    "summary", "executive summary", "scan summary", "scan details", "scan information", "scan results",
    "target", "target url", "start url", "scan url", "url", "location", "affects", "parameter", "method", "cvss", "cvss score", "cwe", "cve",
    "high", "medium", "low", "critical", "informational", "info",
}
_AFFECTED = {"affected items", "affected item", "affected urls", "affected url", "affected pages"}
_REFERENCE_LABELS = {"references", "web references"}


def _label(value):
    return re.sub(r"\s+", " ", value).strip().rstrip(":").lower()


def _is_finding_title(value):
    label = _label(value)
    return bool(value and len(value) <= 500 and label not in _STRUCTURAL
                and not re.match(r"^(?:acunetix|scan (?:report|summary|details)|total alerts|affected items|executive summary)\b", label)
                and not value.isdigit() and not _URL_RE.fullmatch(value))


def _parse_sections(content):
    ext = _DomExtractor()
    ext.feed(content)
    findings, current = [], None
    group_severity, pending, base_url = None, None, None
    affected, references = False, False

    def finish():
        if not current or current["severity"] is None:
            return
        # Summary counts are not instances; real detail sections can lack a URL.
        if not current["urls"] and not current["details"]:
            return
        blob = "\n".join(current["text"])
        pm, mm = _PARAM_RE.search(blob), _METHOD_RE.search(blob)
        for item in current["urls"] or [{"url": None}]:
            findings.append({"type": current["type"], "severity": current["severity"], "url": item["url"],
                "param": item.get("param") or (pm.group(1) if pm else None),
                "method": item.get("method") or (mm.group(1) if mm else None),
                "status": _status_from(blob), "description": blob[:20000] or None})

    def title(value):
        nonlocal current, affected, references
        if not _is_finding_title(value):
            return
        finish()
        current = {"type": value, "severity": group_severity, "urls": [], "text": [], "details": False}
        affected, references = False, False

    def target(value):
        nonlocal base_url
        value = value.strip().rstrip(".,;")
        if not current:
            if not base_url and not is_reference_host(value):
                base_url = value
            return
        if references or is_reference_host(value):
            return
        # Explicit details/description links are evidence, not affected URLs.
        if not affected and current["details"]:
            return
        context = current["text"][-1] if current["text"] else ""
        pm, mm = _PARAM_RE.search(context), _METHOD_RE.search(context)
        param, method = pm.group(1) if pm else None, mm.group(1) if mm else None
        match = next((item for item in current["urls"] if item["url"] == value
            and (not param or not item.get("param") or item["param"] == param)), None)
        if match is None:
            current["urls"].append({"url": value, "param": param, "method": method})
        else:
            match["param"] = param or match.get("param")
            match["method"] = method or match.get("method")

    for kind, value in ext.events:
        label = _label(value)
        if kind == "groupsev":
            if current and not current["urls"] and not current["details"]:
                current["severity"] = normalize_severity(value)
            else:
                finish()
                current = None
            group_severity = normalize_severity(value)
            pending = None
        elif kind == "sev":
            if current:
                current["severity"] = normalize_severity(value)
        elif kind in ("heading", "text"):
            if label in _STRUCTURAL:
                if label == "alert group":
                    pending = "title"
                elif label in _AFFECTED:
                    affected, references = True, False
                elif label in _REFERENCE_LABELS:
                    affected, references = False, True
                elif label in ("description", "impact", "recommendation", "recommendations", "remediation", "details", "attack details", "technical details", "request", "request headers", "response", "response headers"):
                    affected, references = False, False
                    if current:
                        current["details"] = True
                elif label in ("summary", "executive summary", "scan summary", "scan information", "scan results", "total alerts found"):
                    finish()
                    current = None
                    group_severity = None
                continue
            if pending == "title":
                title(value)
                pending = None
            elif kind == "heading":
                title(value)
            elif current:
                current["text"].append(value)
                if affected and current["urls"] and not _URL_RE.search(value):
                    pm, mm = _PARAM_RE.search(value), _METHOD_RE.search(value)
                    if pm:
                        current["urls"][-1]["param"] = pm.group(1)
                    if mm:
                        current["urls"][-1]["method"] = mm.group(1)
                if re.match(r"^(?:affected|affects|url|location)\s*:", value, re.I):
                    affected, references = True, False
                if affected and base_url and value.startswith("/") and not value.startswith("//"):
                    target(urljoin(base_url, value))
        elif kind == "url":
            target(value)
    finish()
    return findings


# --------------------------------------------------------------------------- #
# Стратегия 0 (основная): нативный XML Acunetix
# /ScanGroup/Scan/ReportItems/ReportItem
# --------------------------------------------------------------------------- #
_XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)
_DOCTYPE_RE = re.compile(r"<!(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TRUE_WORDS = {"true", "1", "yes", "да"}


def _looks_like_xml(content: str) -> bool:
    head = content.lstrip().lstrip("﻿")[:4096].lower()
    return head.startswith("<?xml") or "<scangroup" in head or "<reportitem" in head


def _xml_root(content: str) -> ElementTree.Element | None:
    """Безопасный разбор XML: без DTD/внешних сущностей (защита от XXE/биллион-смеха)."""
    if _DOCTYPE_RE.search(content):
        # Acunetix не использует DTD; наличие DOCTYPE/ENTITY — красный флаг.
        raise ValueError("XML с DOCTYPE/ENTITY отклонён (защита от XXE)")
    # str с объявлением encoding ElementTree не принимает — срезаем пролог.
    text = _XML_DECL_RE.sub("", content.lstrip().lstrip("﻿"), count=1)
    return ElementTree.fromstring(text)


def _local(tag: object) -> str:
    """Имя тега без namespace (Acunetix их не использует, но на всякий случай)."""
    return str(tag).rsplit("}", 1)[-1]


def _strip_tags(text: str | None) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()


def _findtext(item: ElementTree.Element, *paths: str) -> str:
    """Первый непустой текст по одному из относительных путей (учёт namespace)."""
    for path in paths:
        val = item.findtext(path)
        if val and val.strip():
            return val.strip()
        # fallback по локальному имени — на случай namespace/иной вёрстки.
        want = [p for p in path.split("/") if p]
        node = item
        for name in want:
            node = next((c for c in list(node) if _local(c.tag) == name), None)
            if node is None:
                break
        if node is not None and node.text and node.text.strip():
            return node.text.strip()
    return ""


def _affected_url(start_url: str, affects: str) -> str | None:
    """Полный URL цели: Affects (путь) склеивается со StartURL сканера."""
    affects = (affects or "").strip()
    start = (start_url or "").strip()
    if affects.startswith(("http://", "https://")):
        return affects
    if start and "://" not in start:
        start = "http://" + start
    if not start:
        return affects or None
    if not affects:
        return start
    try:
        return urljoin(start, affects)
    except ValueError:
        return start


def _iter_report_items(root: ElementTree.Element):
    """Пары (scan_start_url, report_item) по всему дереву, устойчиво к вёрстке."""
    scans = [e for e in root.iter() if _local(e.tag) == "Scan"]
    if not scans:
        scans = [root]
    for scan in scans:
        start_url = _findtext(scan, "StartURL") or ""
        for item in scan.iter():
            if _local(item.tag) == "ReportItem":
                yield start_url, item


def _parse_acunetix_xml(content: str) -> ParsedReport | None:
    """Разобрать нативный XML Acunetix; None — если это не он (→ HTML-ветка)."""
    if not _looks_like_xml(content):
        return None
    try:
        root = _xml_root(content)
    except ElementTree.ParseError:
        return None  # битый/не-XML — пусть попробует HTML-ветка
    except ValueError:
        # DOCTYPE/ENTITY (защита от XXE) — не парсим и НЕ отдаём в HTML-ветку.
        return ParsedReport(findings=[], declared=0)
    if root is None:
        return None
    if _local(root.tag) not in ("ScanGroup", "Scan") and not any(
        _local(e.tag) == "ReportItem" for e in root.iter()
    ):
        return None  # XML, но не похож на Acunetix

    findings: list[dict] = []
    for start_url, item in _iter_report_items(root):
        if _findtext(item, "IsFalsePositive").lower() in _TRUE_WORDS:
            continue  # ложные срабатывания не импортируем

        name = _findtext(item, "Name") or "Unknown"
        severity = _findtext(item, "Severity")
        affects = _findtext(item, "Affects")
        url = _affected_url(start_url, affects)

        param = _findtext(item, "Parameter") or None
        request = _findtext(item, "TechnicalDetails/Request")
        blob = " ".join(
            _strip_tags(_findtext(item, p))
            for p in ("Details", "Description", "DetailedInformation", "TechnicalDetails")
        )
        blob = f"{blob} {affects} {param or ''}".strip()

        if not param:
            pm = _PARAM_RE.search(blob)
            if pm:
                param = pm.group(1)
        method = None
        mm = _METHOD_RE.search(request) or _METHOD_RE.search(blob)
        if mm:
            method = mm.group(1)

        findings.append(
            {
                "severity": normalize_severity(severity),
                "type": name.strip() or "Unknown",
                "url": url,
                "param": param,
                "method": method,
                "status": _status_from(_findtext(item, "Status") or blob),
                "description": "\n\n".join(filter(None, [blob, request, _findtext(item, "TechnicalDetails/Response")]))[:20000] or None,
            }
        )
    return ParsedReport(findings=findings, declared=len(findings))


def _parse_html(content: str) -> ParsedReport:
    findings = _parse_tables(content)
    if not findings:
        findings = _parse_sections(content)
    # Финальный фильтр референс-хостов на всякий случай.
    findings = [f for f in findings if not is_reference_host(f.get("url"))]
    return ParsedReport(findings=findings, declared=len(findings))


def parse_report(content: str) -> ParsedReport:
    """Единая точка входа: сам определяет XML Acunetix или HTML-отчёт."""
    xml = _parse_acunetix_xml(content)
    if xml is not None:
        return xml
    return _parse_html(content)


# Обратная совместимость: старое имя функции (вызовы в сервисах/тестах).
def parse_html(content: str) -> ParsedReport:
    return parse_report(content)


def dedup_key(f: dict) -> str:
    return "|".join(
        [
            str(f.get("type", "")).strip().lower(),
            str(f.get("url", "") or "").strip().lower(),
            str(f.get("param", "") or "").strip().lower(),
        ]
    )
