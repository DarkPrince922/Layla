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


# --------------------------------------------------------------------------- #
# Нативный XML Acunetix (основной машинный формат)
# --------------------------------------------------------------------------- #
_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ScanGroup ExportedOn="24/09/2018, 21:42:41">
  <Scan>
    <Name>Shop scan</Name>
    <StartURL>https://shop.example.com/app</StartURL>
    <StartTime>24/09/2018, 18:09:55</StartTime>
    <ReportItems>
      <ReportItem id="1">
        <Name>SQL Injection</Name>
        <Details><![CDATA[Confirmed via error-based payload]]></Details>
        <Affects><![CDATA[/product.php]]></Affects>
        <Parameter>id</Parameter>
        <IsFalsePositive>False</IsFalsePositive>
        <Severity>high</Severity>
        <Type>sql</Type>
        <Description>Classic SQL injection</Description>
        <TechnicalDetails>
          <Request>GET /product.php?id=1 HTTP/1.1
Host: shop.example.com</Request>
          <Response>HTTP/1.1 500</Response>
        </TechnicalDetails>
        <CWEList><CWE id="89"><![CDATA[CWE-89]]></CWE></CWEList>
        <References>
          <Reference><Database>OWASP</Database><URL>https://owasp.org/sqli</URL></Reference>
          <Reference><Database>CWE</Database><URL>https://cwe.mitre.org/data/89.html</URL></Reference>
        </References>
      </ReportItem>
      <ReportItem id="2">
        <Name>Cross-site Scripting</Name>
        <Details>Reflected XSS. Parameter: q . Method: POST</Details>
        <Affects>https://api.example.com/search?q=x</Affects>
        <Parameter></Parameter>
        <IsFalsePositive>False</IsFalsePositive>
        <Severity>2</Severity>
        <Description>Reflected XSS</Description>
      </ReportItem>
      <ReportItem id="3">
        <Name>Should Be Skipped</Name>
        <Affects>/noise</Affects>
        <IsFalsePositive>True</IsFalsePositive>
        <Severity>low</Severity>
      </ReportItem>
    </ReportItems>
  </Scan>
</ScanGroup>
"""


def test_xml_parse_basic_and_starturl_join():
    parsed = acunetix.parse_report(_XML)
    # Ложное срабатывание (id=3) отброшено.
    assert parsed.declared == 2
    first = parsed.findings[0]
    assert first["type"] == "SQL Injection"
    assert first["severity"] == Severity.high
    # Affects (путь) склеен со StartURL в полный URL цели.
    assert first["url"] == "https://shop.example.com/product.php"
    assert first["param"] == "id"
    assert first["method"] == "GET"


def test_xml_numeric_severity_and_param_from_details():
    parsed = acunetix.parse_report(_XML)
    xss = parsed.findings[1]
    assert "Cross-site" in xss["type"]
    # Числовая важность "2" → medium.
    assert xss["severity"] == Severity.medium
    # Affects уже полный URL — берётся как есть.
    assert xss["url"] == "https://api.example.com/search?q=x"
    # Параметр и метод извлечены из Details, раз <Parameter> пуст.
    assert xss["param"] == "q"
    assert xss["method"] == "POST"


def test_xml_reference_urls_never_become_targets():
    parsed = acunetix.parse_report(_XML)
    urls = [f.get("url") or "" for f in parsed.findings]
    assert all("owasp.org" not in u for u in urls)
    assert all("cwe.mitre.org" not in u for u in urls)


def test_xml_hosts_are_real_targets():
    parsed = acunetix.parse_report(_XML)
    hosts = {(acunetix.urlparse(f["url"]).hostname) for f in parsed.findings if f.get("url")}
    assert hosts == {"shop.example.com", "api.example.com"}


def test_xml_preferred_over_html_parser():
    # XML-содержимое не должно уходить в HTML-ветку (иначе «криво»).
    parsed = acunetix.parse_report(_XML)
    types = {f["type"] for f in parsed.findings}
    assert types == {"SQL Injection", "Cross-site Scripting"}


def test_xml_doctype_rejected_as_xxe_guard():
    payload = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        "<ScanGroup><Scan><StartURL>https://x.example</StartURL>"
        "<ReportItems><ReportItem><Name>&xxe;</Name><Severity>high</Severity>"
        "<Affects>/a</Affects></ReportItem></ReportItems></Scan></ScanGroup>"
    )
    parsed = acunetix.parse_report(payload)
    # DOCTYPE/ENTITY отклонён: находок нет, падения нет.
    assert parsed.declared == 0
    assert parsed.findings == []


def test_parse_html_alias_still_works():
    # Старое имя функции сохранено для совместимости.
    assert acunetix.parse_html(_XML).declared == 2


_AFFECTED_REPORT = """
<html><head><style>.fake { color: red }</style></head><body>
<h1>Acunetix Security Audit</h1>
<h2>Total alerts found</h2><b>Critical</b><span>4</span>
<table><tr><th>Severity</th><th>Affected items</th></tr><tr><td>High</td><td>3</td></tr></table>
<p>Target: https://shop.example.com/</p>
<h2>High</h2>
<h3><span>SQL</span> Injection</h3>
<h4>Severity: High</h4>
<h4>Description</h4><p>A SQL error was observed. Confidence: 95%.</p>
<h4>Affected items</h4><p>GET https://shop.example.com/a?id=1 Parameter: id</p>
<p>GET https://shop.example.com/b?id=2 Parameter: id</p>
<h4>References</h4><a href="https://docs.example.org/sql">Documentation</a>
<h3>Cross-site Scripting</h3><span>Medium</span>
<h4>Affected items</h4><p>POST https://shop.example.com/search Parameter: q</p>
<h4>Attack details</h4><p>Status: Unconfirmed</p>
<h4>Web references</h4><p>https://code.jquery.com/jquery.js</p>
<h2>Informational</h2><h3>Server version disclosure</h3>
<h4>Affected items</h4><p>/status</p>
<script>const fake = 'Critical https://evil.example/';</script>
</body></html>
"""


def test_affected_items_report_preserves_names_instances_and_ignores_summaries():
    parsed = acunetix.parse_report(_AFFECTED_REPORT)
    assert len(parsed.findings) == 4
    assert [f["type"] for f in parsed.findings] == ["SQL Injection", "SQL Injection", "Cross-site Scripting", "Server version disclosure"]
    assert [f["severity"] for f in parsed.findings] == [Severity.high, Severity.high, Severity.medium, Severity.info]
    assert [f["url"] for f in parsed.findings] == ["https://shop.example.com/a?id=1", "https://shop.example.com/b?id=2", "https://shop.example.com/search", "https://shop.example.com/status"]
    assert all(f["status"] is None for f in parsed.findings)
    assert 'A SQL error was observed' in parsed.findings[0]['description']


def test_vertical_alert_group_labels_and_nested_headings():
    report = """<h1>Acunetix report</h1><p>https://shop.example.com/</p>
    <table><tr><td>Alert group</td><td>Blind SQL Injection</td></tr>
    <tr><td>Severity</td><td>High</td></tr></table>
    <h4>Affected items</h4><p>/login?id=1</p><p>Parameter: id Method: POST</p>
    <h4>References</h4><a href='https://elsewhere.example/reference'>Reference</a>"""
    item = acunetix.parse_report(report).findings[0]
    assert item['type'] == 'Blind SQL Injection'
    assert item['url'] == 'https://shop.example.com/login?id=1'
    assert item['method'] == 'POST'
    assert item['param'] == 'id'


def test_confidence_and_negative_status_are_not_confirmation():
    for text in ('95%', 'Confidence: 100%', 'not confirmed', 'Unconfirmed', 'not verified'):
        assert acunetix._status_from(text) is None
    assert acunetix._status_from('Confirmed') == 'confirmed'
    assert acunetix._status_from('Status: Verified') == 'confirmed'


def test_summary_only_does_not_create_fake_critical_findings():
    parsed = acunetix.parse_report('<h1>Acunetix</h1><h2>Total alerts found</h2>Critical 4<h2>High</h2><h3>Affected items</h3>https://example.com')
    assert not parsed.findings


def test_xml_keeps_technical_evidence():
    parsed = acunetix.parse_report(_XML)
    assert 'GET /product.php?id=1 HTTP/1.1' in parsed.findings[0]['description']
    assert 'HTTP/1.1 500' in parsed.findings[0]['description']



def test_each_affected_item_retains_own_method_and_parameter():
    report = """<p class='MsoHeading3'><span>SQL Injection</span></p><p>High</p>
    <h4>Affected items</h4><p>GET https://shop.example.com/a Parameter: id</p>
    <p>POST https://shop.example.com/b Parameter: sort</p>"""
    items = acunetix.parse_report(report).findings
    assert [(i['method'], i['param']) for i in items] == [('GET', 'id'), ('POST', 'sort')]



def test_nested_named_span_does_not_replace_outer_alert_title():
    report = """<h3>SQL <span class='alert-name'>Injection</span> (error based)</h3>
    <span>High</span><h4>Affected items</h4><p>https://shop.example.com/p</p>"""
    assert acunetix.parse_report(report).findings[0]['type'] == 'SQL Injection (error based)'


def _vertical_alert(location, name, severity, details, request='', description='Scanner evidence'):
    return f'''<table><tr><td><b>{location}</b></td></tr>
    <tr><td>Alert group</td><td><b>{name}</b></td></tr>
    <tr><td>Severity</td><td>{severity}</td></tr>
    <tr><td>Description</td><td>{description}</td></tr>
    <tr><td>Details</td><td>{details}</td></tr>
    <tr><td colspan="2"><code>{request}</code></td></tr></table>'''


def test_affected_items_vertical_tables_keep_paths_requests_and_server_evidence():
    report = '<table><tr><td>Start url</td><td>https://shop.example.com</td></tr></table>'
    report += _vertical_alert('/legacy/', 'SQL Injection', 'Critical', 'Database error', 'GET /legacy/test HTTP/1.1')
    report += _vertical_alert('Web Server', 'Library vulnerability', 'Medium', 'Version 1', description='CVE example A')
    report += _vertical_alert('Web Server', 'Library vulnerability', 'Medium', 'Version 1', description='CVE example B')
    parsed = acunetix.parse_report(report)
    assert parsed.declared == 3
    assert parsed.findings[0]['url'] == 'https://shop.example.com/legacy/'
    assert parsed.findings[0]['method'] == 'GET'
    assert 'GET /legacy/test HTTP/1.1' in parsed.findings[0]['description']
    assert len({acunetix.dedup_key(f) for f in parsed.findings}) == 3
    assert all(f['status'] is None for f in parsed.findings)


def test_vertical_lists_split_affected_paths_without_adding_external_resources():
    report = '<table><tr><td>Start url</td><td>https://shop.example.com</td></tr></table>'
    report += _vertical_alert('Web Server', 'Directory listings (verified)', 'Medium',
        'Folders with directory listing enabled:<ul><li>https://shop.example.com/a/</li><li>https://shop.example.com/b/</li></ul>')
    report += _vertical_alert('/page/', 'Mixed content', 'Medium', 'Script URL: https://cdn.example.org/code.js', 'GET /page/ HTTP/1.1')
    parsed = acunetix.parse_report(report)
    assert parsed.declared == 2
    assert [f['url'] for f in parsed.findings] == ['https://shop.example.com/a/', 'https://shop.example.com/b/', 'https://shop.example.com/page/']
    assert all(f['status'] is None for f in parsed.findings)


def test_vertical_request_fallback_and_no_cross_table_method_leak():
    report = '<table><tr><td>Start url</td><td>https://shop.example.com</td></tr></table>'
    report += _vertical_alert('Web Server', 'Library issue', 'Medium', 'Resource', 'POST /js/app.js HTTP/1.1')
    report += _vertical_alert('Web Server', 'Weak cipher', 'Medium', 'TLS configuration')
    items = acunetix.parse_report(report).findings
    assert items[0]['url'] == 'https://shop.example.com/js/app.js'
    assert items[0]['method'] == 'POST'
    assert items[1]['url'] == 'https://shop.example.com'
    assert items[1]['method'] is None
