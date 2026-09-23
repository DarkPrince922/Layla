"""Настройки песочницы из окружения (задаются в docker-compose / .env)."""
from __future__ import annotations

import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# Рабочие копии проектов, домашние каталоги пользователей и служебное состояние.
WORK = Path(os.environ.get("SANDBOX_WORK", "/work"))
# Ядро говорит с песочницей через unix-сокет: у кода в песочнице нет к нему доступа
# (каталог сокета — только для root), а у песочницы нет сети до ядра и базы.
SOCKET = os.environ.get("SANDBOX_SOCKET", "/run/layla-sandbox/sandbox.sock")
# Выход в интернет — только через прокси (он пускает к реестрам пакетов). Пусто — сети нет.
PROXY = os.environ.get("SANDBOX_PROXY", "").strip()

# Пользователь Layla -> свой uid в песочнице: чужие рабочие копии и кеши недоступны.
UID_MIN = _int("SANDBOX_UID_MIN", 20000)
UID_MAX = _int("SANDBOX_UID_MAX", 59999)

MAX_TIMEOUT = _int("SANDBOX_MAX_TIMEOUT", 900)  # секунд на команду
DEFAULT_TIMEOUT = _int("SANDBOX_DEFAULT_TIMEOUT", 120)
MAX_OUTPUT = _int("SANDBOX_MAX_OUTPUT", 1_000_000)  # байт вывода, дальше обрезается
MAX_PARALLEL = _int("SANDBOX_MAX_PARALLEL", 4)  # одновременных команд на весь сервер
MAX_UPLOAD = _int("SANDBOX_MAX_UPLOAD", 300 * 1024 * 1024)
MAX_FILES = _int("SANDBOX_MAX_FILES", 20_000)
# Пределы процесса команды (prlimit): процессы пользователя, размер файла, открытые файлы.
NPROC = _int("SANDBOX_NPROC", 512)
FSIZE = _int("SANDBOX_FSIZE", 2 * 1024 * 1024 * 1024)
NOFILE = _int("SANDBOX_NOFILE", 4096)

# Переменные с путём к своему корневому сертификату передаются командам как есть.
CA_ENV = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "PIP_CERT", "NODE_EXTRA_CA_CERTS", "CURL_CA_BUNDLE",
          "GIT_SSL_CAINFO", "CARGO_HTTP_CAINFO")

# Необязательные языки ставятся в образ флагами сборки; их пути — в PATH команд.
EXTRA_PATH = ("/usr/local/go/bin", "/opt/cargo/bin")
BASE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Долгоживущие процессы (превью приложения): простой и срок жизни, лимиты числа.
SERVICE_IDLE = _int("SANDBOX_SERVICE_IDLE", 30 * 60)
SERVICE_LIFETIME = _int("SANDBOX_SERVICE_LIFETIME", 6 * 60 * 60)
SERVICE_START_WAIT = _int("SANDBOX_SERVICE_START_WAIT", 180)
MAX_SERVICES = _int("SANDBOX_MAX_SERVICES", 8)
MAX_SERVICES_PER_USER = _int("SANDBOX_MAX_SERVICES_PER_USER", 3)
