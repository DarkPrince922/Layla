"""Тесты парсера Acunetix и дедупликации (спец. §5.4, §9)."""
from __future__ import annotations

from app.models.enums import Severity
from app.services import acunetix

_TABLE_HTML = """
<html><body>
<h1>Acunetix Report</h1>
<table>
  <tr><th>Severity</th><th>Alert type</th><th>Affected URL</th><th>Parameter</th><th>Method</th></tr>
  <tr><td>High</td><td>SQL Injection</td><td>https://ex.com/p?id=1</td><td>id</td><td>GET</td></tr>
  <tr><td>Medium</td><td>XSS</td><td>https://ex.com/s?q=x</td><td>q</td><td>GET</td></tr>
  <tr><td>High</td><td>SQL Injection</td><td>https://ex.com/p?id=1</td><td>id</td><td>GET</td></tr>
</table>
</body></html>
"""


def test_parse_table_findings():
    parsed = acunetix.parse_html(_TABLE_HTML)
    assert parsed.declared == 3
    assert parsed.findings[0]["severity"] == Severity.high
    assert parsed.findings[0]["type"] == "SQL Injection"
    assert parsed.findings[0]["url"] == "https://ex.com/p?id=1"
    assert parsed.findings[0]["param"] == "id"


def test_dedup_key_collapses_duplicates():
    parsed = acunetix.parse_html(_TABLE_HTML)
    keys = {acunetix.dedup_key(f) for f in parsed.findings}
    # 3 строки, но 2 уникальных (третья дублирует первую).
    assert len(keys) == 2


def test_sha256_stable():
    a = acunetix.sha256_of("abc")
    b = acunetix.sha256_of("abc")
    assert a == b and len(a) == 64


def test_section_fallback():
    html = "<div>High severity found at https://ex.com/x and more</div>"
    parsed = acunetix.parse_html(html)
    assert parsed.declared >= 1
    assert parsed.findings[0]["url"].startswith("https://ex.com/x")
