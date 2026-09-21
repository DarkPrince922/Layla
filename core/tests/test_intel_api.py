"""Passive egress contract: fixed provider hosts, GET only, no target contact."""

from __future__ import annotations

import httpx
import pytest

from app.services import intel_api
from app.services.intel_targets import normalize_target

SAMPLES = {
    "shodan": {
        "domain": "example.com",
        "data": [{"type": "A", "value": "1.1.1.1"}],
        "subdomains": ["www"],
    },
    "virustotal": {"data": {"attributes": {"last_analysis_stats": {"malicious": 2}}}},
    "securitytrails": {
        "hostname": "example.com",
        "current_dns": {"a": {"values": [{"ip": "1.1.1.1"}]}},
    },
    "urlscan": {
        "results": [
            {
                "_id": "8848d620-00b1-4c0a-a46a-83d369529487",
                "page": {"url": "https://example.com", "ip": "1.1.1.1"},
                "task": {"time": "2026-09-21T12:00:00Z"},
            }
        ]
    },
}


@pytest.mark.parametrize(
    "value",
    [
        "127.0.0.1",
        "10.0.0.1",
        "::1",
        "169.254.169.254",
        "224.0.0.1",
        "localhost",
        "https://example.com/",
        "example.com/../../",
        "example.com?key=oops",
        "evil.com:443",
        "example.com OR domain:other.com",
        "*.example.com",
        "1.2.3.999",
        "a..com",
        "a.local",
        "a\n.com",
        "-a.com",
        "a@b.com",
        "",
        ".",
        "123.123",
        "[::1]",
    ],
)
async def test_bad_subject_never_reaches_network(value):
    def forbidden(_):
        pytest.fail("Invalid target caused network traffic")

    result = await intel_api.lookup("urlscan", value, transport=httpx.MockTransport(forbidden))
    assert result.error_code == "invalid_target"


def test_normalization_idn_and_public_ip():
    assert normalize_target(" EXAMPLE.COM. ") == "example.com"
    assert normalize_target("пример.рф") == "xn--e1afmkfd.xn--p1ai"
    assert normalize_target("2606:4700:4700::1111") == "2606:4700:4700::1111"


@pytest.mark.parametrize(
    "provider,host,path,header",
    [
        ("shodan", "api.shodan.io", "/dns/domain/example.com", None),
        ("virustotal", "www.virustotal.com", "/api/v3/domains/example.com", "x-apikey"),
        ("securitytrails", "api.securitytrails.com", "/v1/domain/example.com", "apikey"),
        ("urlscan", "urlscan.io", "/api/v1/search/", "api-key"),
    ],
)
async def test_documented_provider_contracts(provider, host, path, header):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == host and request.url.path == path
        if header:
            assert request.headers[header] == "test-api-secret"
        else:
            assert request.url.params["key"] == "test-api-secret"
        if provider == "urlscan":
            assert request.url.params["q"] == "domain:example.com"
            assert request.url.params["size"] == "20"
        return httpx.Response(200, json=SAMPLES[provider])

    result = await intel_api.lookup(
        provider, "example.com", "test-api-secret", transport=httpx.MockTransport(handle)
    )
    assert result.status == "ok" and len(result.artifacts) == 1
    assert len(calls) == 1
    assert "test-api-secret" not in result.model_dump_json()
    assert result.artifacts[0].source_url.startswith("https://")


@pytest.mark.parametrize(
    "provider,path",
    [
        ("shodan", "/shodan/host/1.1.1.1"),
        ("virustotal", "/api/v3/ip_addresses/1.1.1.1"),
        ("urlscan", "/api/v1/search/"),
    ],
)
async def test_ip_contracts(provider, path):
    def handle(request):
        assert request.url.path == path
        if provider == "urlscan":
            assert request.url.params["q"] == "ip:1.1.1.1"
        if provider == "shodan":
            assert request.url.params["minify"] == "true"
            return httpx.Response(200, json={"ip_str": "1.1.1.1", "ports": [443]})
        return httpx.Response(200, json=SAMPLES[provider])

    assert (
        await intel_api.lookup(provider, "1.1.1.1", "key", transport=httpx.MockTransport(handle))
    ).status == "ok"


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication"),
        (403, "authentication"),
        (429, "rate_limited"),
        (500, "unavailable"),
        (302, "unavailable"),
    ],
)
async def test_upstream_errors_are_redacted_and_not_retried(status, code):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={"message": "secret-key"},
            headers={"Location": "http://target.invalid/steal"},
        )

    result = await intel_api.lookup(
        "shodan", "example.com", "secret-key", transport=httpx.MockTransport(handle)
    )
    assert result.error_code == code and not result.artifacts
    assert "secret-key" not in result.model_dump_json()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>bad</html>"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"results": "oops"}),
        httpx.Response(200, content=b"x" * (intel_api.MAX_RESPONSE_BYTES + 1)),
    ],
)
async def test_invalid_responses_are_bounded(response):
    result = await intel_api.lookup(
        "urlscan", "example.com", transport=httpx.MockTransport(lambda _: response)
    )
    assert result.error_code == "invalid_response"


async def test_empty_missing_keys_timeout_and_unsupported():
    assert (await intel_api.lookup("shodan", "example.com")).error_code == "not_configured"
    assert (
        await intel_api.lookup("securitytrails", "1.1.1.1", "key")
    ).error_code == "unsupported_target"
    for response in [httpx.Response(404, text="secret"), httpx.Response(200, json={"results": []})]:
        result = await intel_api.lookup(
            "urlscan", "example.com", transport=httpx.MockTransport(lambda _, reply=response: reply)
        )
        assert result.status == "empty"

    def timeout(_):
        raise httpx.ReadTimeout("url contains secret-key")

    assert (
        await intel_api.lookup("urlscan", "example.com", transport=httpx.MockTransport(timeout))
    ).error_code == "timeout"


async def test_provider_echoed_credentials_are_scrubbed():
    result = await intel_api.lookup(
        "shodan",
        "example.com",
        "secret-key",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    **SAMPLES["shodan"],
                    "apikey": "another-secret",
                    "nested": {"echo": "prefix secret-key"},
                },
            )
        ),
    )
    assert result.status == "ok"
    assert "secret-key" not in result.model_dump_json()
    assert "another-secret" not in result.model_dump_json()
