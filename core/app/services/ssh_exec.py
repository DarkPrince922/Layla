"""SSH-исполнитель attack box: подключение к серверу пользователя и запуск команд.

Это транспорт «последнего метра» для активных действий пентеста. Он НЕ решает,
что запускать, и не обходит проверки: команды приходят уже после гейтов
(scope/venue/egress) и HITL-подтверждения оператора — см. venue_executor.execute.
Сервер (host/port/user, ключ или пароль) добавляет и выбирает сам оператор для
своей авторизованной инфраструктуры.

Защита от MITM: ключ хоста закрепляется при первом успешном подключении (TOFU) и
проверяется при последующих. Смена ключа хоста → отказ (возможна подмена).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import asyncssh

CONNECT_TIMEOUT = 15
MAX_OUTPUT = 200_000  # байт вывода команды, дальше обрезается


class SSHError(RuntimeError):
    """Не удалось подключиться или выполнить команду — текст для оператора."""


class HostKeyChanged(SSHError):
    """Ключ хоста не совпал с закреплённым — подключение отклонено (возможен MITM)."""


@dataclass
class SSHConfig:
    host: str
    port: int
    user: str
    auth: str            # "key" | "password"
    secret: str | None   # приватный ключ или пароль (расшифрованные)
    host_key: str | None  # закреплённый публичный ключ хоста (export_public_key) или None


@dataclass
class SSHResult:
    exit_code: int | None
    output: str
    truncated: bool
    host_key: str        # актуальный публичный ключ хоста (для закрепления)


def _client_keys(cfg: SSHConfig) -> list:
    if cfg.auth != "key" or not cfg.secret:
        return []
    try:
        return [asyncssh.import_private_key(cfg.secret)]
    except (asyncssh.KeyImportError, ValueError) as exc:
        raise SSHError(f"Не удалось прочитать приватный ключ: {exc}") from exc


def _verify_host_key(cfg: SSHConfig, presented: asyncssh.SSHKey) -> None:
    """Сверить предъявленный ключ хоста с закреплённым (если он есть)."""
    if not cfg.host_key:
        return
    try:
        pinned = asyncssh.import_public_key(cfg.host_key)
    except (asyncssh.KeyImportError, ValueError):
        return  # некорректно сохранённый отпечаток — перезакрепим ниже
    if pinned.export_public_key() != presented.export_public_key():
        raise HostKeyChanged(
            "Ключ хоста изменился с прошлого подключения — соединение отклонено "
            "(возможна подмена сервера). Проверьте сервер и при необходимости "
            "удалите и добавьте его заново."
        )


async def _connect(cfg: SSHConfig):
    """Открыть соединение, не доверяя known_hosts ОС; проверку делаем сами (TOFU/pin)."""
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                cfg.host, port=cfg.port, username=cfg.user,
                client_keys=_client_keys(cfg),
                password=cfg.secret if cfg.auth == "password" else None,
                known_hosts=None,          # проверку хоста делаем вручную ниже
                agent_path=None,           # не трогаем ssh-agent оператора
                config=None,               # игнорируем ~/.ssh/config
            ),
            timeout=CONNECT_TIMEOUT,
        )
    except asyncssh.PermissionDenied as exc:
        raise SSHError("Отказано в доступе: проверьте пользователя, ключ или пароль") from exc
    except (TimeoutError, OSError, asyncssh.Error) as exc:
        raise SSHError(f"Не удалось подключиться к {cfg.host}:{cfg.port}: {type(exc).__name__}") from exc
    presented = conn.get_server_host_key()
    try:
        _verify_host_key(cfg, presented)
    except HostKeyChanged:
        conn.close()
        raise
    return conn, presented.export_public_key().decode().strip()


async def probe(cfg: SSHConfig) -> SSHResult:
    """Проверить, что до сервера есть SSH-доступ (для кнопки «Проверить подключение»)."""
    conn, host_key = await _connect(cfg)
    try:
        result = await conn.run("echo layla-ok", timeout=CONNECT_TIMEOUT)
        out = (result.stdout or "").strip()
        return SSHResult(exit_code=result.exit_status, output=out, truncated=False, host_key=host_key)
    finally:
        conn.close()


async def run(cfg: SSHConfig, command: str, timeout: int = 300) -> SSHResult:
    """Выполнить команду на attack box и вернуть объединённый вывод (stdout+stderr)."""
    conn, host_key = await _connect(cfg)
    try:
        try:
            result = await conn.run(command, timeout=timeout, check=False)
        except asyncssh.TimeoutError as exc:
            raise SSHError(f"Команда не завершилась за {timeout} c") from exc
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        combined = stdout if not stderr else f"{stdout}\n{stderr}" if stdout else stderr
        truncated = len(combined) > MAX_OUTPUT
        return SSHResult(
            exit_code=result.exit_status,
            output=combined[:MAX_OUTPUT],
            truncated=truncated,
            host_key=host_key,
        )
    finally:
        conn.close()
