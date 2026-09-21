"""Normalize lookup subjects without DNS resolution or contact with a target."""

from __future__ import annotations

import ipaddress
import re


def normalize_target(value: str) -> str:
    value = value.strip().rstrip(".")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        if not address.is_global or address.is_multicast:
            raise ValueError("Для поиска нужен публичный IP-адрес")
        return str(address)

    try:
        name = value.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Некорректное доменное имя") from exc
    labels = name.split(".")
    if (
        len(name) > 253
        or len(labels) < 2
        or re.fullmatch(r"[0-9.]+", name)
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in labels)
        or labels[-1].isdigit()
        or labels[-1] in {"local", "localhost", "internal", "invalid", "test"}
    ):
        raise ValueError("Укажите домен или публичный IP без URL, пути и поисковых операторов")
    return name


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False
