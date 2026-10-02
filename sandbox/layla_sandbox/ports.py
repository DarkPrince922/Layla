"""Какие TCP-порты слушают процессы одного сеанса. Запускается ОТ ИМЕНИ пользователя песочницы.

python -I ports.py <sid> → JSON [{"port": 5173, "host": "127.0.0.1"}, …]

Сокеты процесса видны в /proc/<pid>/fd только своему uid — поэтому скрипт работает от
uid владельца, а не от root: так он точно видит именно процессы этого превью.
"""
from __future__ import annotations

import json
import os
import sys


def _session(pid: str) -> int | None:
    try:
        with open(f"/proc/{pid}/stat") as handle:
            stat = handle.read()
        return int(stat[stat.rindex(")") + 2:].split()[3])  # state ppid pgrp session
    except (OSError, ValueError, IndexError):
        return None


def _host(addr: str, v6: bool) -> str:
    if not v6:
        return {"0100007F": "127.0.0.1", "00000000": "0.0.0.0"}.get(addr, "127.0.0.1")
    if addr == "0" * 24 + "01000000":
        return "::1"
    if addr == "0" * 32:
        return "::"
    return "::1"


def listening(sid: int) -> list[dict]:
    inodes: set[str] = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or _session(pid) != sid:
            continue
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if target.startswith("socket:["):
                inodes.add(target[8:-1])
    found = []
    for name, v6 in (("tcp", False), ("tcp6", True)):
        try:
            with open(f"/proc/net/{name}") as handle:
                lines = handle.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10 or parts[3] != "0A" or parts[9] not in inodes:
                continue
            addr, port = parts[1].split(":")
            found.append({"port": int(port, 16), "host": _host(addr, v6)})
    return found


if __name__ == "__main__":
    print(json.dumps(listening(int(sys.argv[1]))))
