"""Тесты парсера Acunetix: таблицы, DOM-секции, фильтр референсов, статус (§5.4)."""
from __future__ import annotations

from app.models.enums import Severity
from app.services import acunetix

_TABLE_HTML = """
<html><body>
<h1>Acunetix Report</h1>
<table>
  <tr><th>Severity</th><th>Alert type</th><th>Affected URL</th><th>Parameter</th><th>Method</th><th>Status</th></tr>
  <tr><td>High</td><td>SQL Injection</td><td>https://target.example.com/p?id=1</td><td>id</td><td>GET</td><td>Confirmed</td></tr>
  <tr><td>Medium</td><td>XSS</td><td>https://target.example.com/s?q=x</td><td>q</td><td>GET</td><td>Unconfirmed</td></tr>
  <tr><td>High</td><td>SQL Injection</td><td>https://target.example.com/p?id=1</td><td>id</td><td>GET</td><td>Confirmed</td></tr>
</table>
<!-- ссылка на документацию в шаблоне отчёта -->
<a href="https://github.com/acunetix">docs</a>
</body></html>
"""

_DOM_HTML = """
<div class="vuln">
  <h3 class="vuln-name">SQL Injection</h3>
  <span class="severity">High</span>
  <div>Affected: <a href="https://shop.example.com/item?id=5">https://shop.example.com/item?id=5</a>
       Parameter: id · GET</div>
</div>
<div class="vuln">
  <h3 class="vuln-name">Cross-site Scripting</h3>
  <span class="severity">Medium</span>
  <div>Affected: https://shop.example.com/search?q=1 Variable: q</div>
</div>
<footer><a href="https://owasp.org/xss">reference</a></footer>
"""


def test_table_parse_and_status():
    parsed = acunetix.parse_html(_TABLE_HTML)
    assert parsed.declared == 3
    first = parsed.findings[0]
    assert first["type"] == "SQL Injection"
    assert first["severity"] == Severity.high
    assert first["url"] == "https://target.example.com/p?id=1"
    assert first["status"] == "confirmed"
    # github-ссылка из шаблона не попала в находки.
    assert all("github.com" not in (f.get("url") or "") for f in parsed.findings)


def test_table_dedup():
    parsed = acunetix.parse_html(_TABLE_HTML)
    keys = {acunetix.dedup_key(f) for f in parsed.findings}
    assert len(keys) == 2


def test_dom_sections_extract_type_and_url():
    parsed = acunetix.parse_html(_DOM_HTML)
    types = {f["type"] for f in parsed.findings}
    assert "SQL Injection" in types
    assert any("Cross-site" in t for t in types)
    # реальная цель, а не owasp.org
    assert any((f.get("url") or "").startswith("https://shop.example.com") for f in parsed.findings)
    assert all("owasp.org" not in (f.get("url") or "") for f in parsed.findings)


def test_reference_host_helper():
    assert acunetix.is_reference_host("https://github.com/x")
    assert acunetix.is_reference_host("https://owasp.org/y")
    assert not acunetix.is_reference_host("https://target.example.com/z")


def test_sha256_stable():
    assert acunetix.sha256_of("abc") == acunetix.sha256_of("abc")
    assert len(acunetix.sha256_of("abc")) == 64
