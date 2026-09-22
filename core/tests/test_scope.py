"""Тесты жёсткого scope-enforcement (спец. §7.2, §9 — обязательны)."""
from __future__ import annotations

from app.services import scope


def test_empty_allow_denies_everything():
    # Fail-closed: без allow-list запрещено всё.
    d = scope.check_target("example.com", allow=[], deny=[])
    assert d.allowed is False


def test_exact_domain_allowed():
    d = scope.check_target("example.com", allow=["example.com"], deny=[])
    assert d.allowed is True


def test_subdomain_of_allowed_domain():
    d = scope.check_target("api.example.com", allow=["example.com"], deny=[])
    assert d.allowed is True


def test_unrelated_domain_blocked():
    d = scope.check_target("evil.com", allow=["example.com"], deny=[])
    assert d.allowed is False


def test_deny_takes_precedence_over_allow():
    d = scope.check_target(
        "secret.example.com", allow=["example.com"], deny=["secret.example.com"]
    )
    assert d.allowed is False
    assert "deny" in d.reason.lower()


def test_wildcard_domain():
    assert scope.check_target("a.example.com", allow=["*.example.com"], deny=[]).allowed
    # Голый apex тоже покрывается суффиксом.
    assert scope.check_target("example.com", allow=["*.example.com"], deny=[]).allowed


def test_url_target_uses_host():
    d = scope.check_target("https://example.com/admin?x=1", allow=["example.com"], deny=[])
    assert d.allowed is True


def test_ip_exact_and_cidr():
    assert scope.check_target("10.0.0.5", allow=["10.0.0.5"], deny=[]).allowed
    assert scope.check_target("10.0.0.5", allow=["10.0.0.0/24"], deny=[]).allowed
    assert not scope.check_target("10.0.1.5", allow=["10.0.0.0/24"], deny=[]).allowed


def test_ip_in_cidr_denied():
    d = scope.check_target("10.0.0.9", allow=["10.0.0.0/24"], deny=["10.0.0.9"])
    assert d.allowed is False


def test_lookalike_domain_not_matched():
    # notexample.com не должен матчить example.com.
    d = scope.check_target("notexample.com", allow=["example.com"], deny=[])
    assert d.allowed is False
