"""Общая фикстура: временный корень песочницы вместо /work."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from layla_sandbox import config, server
from layla_sandbox.workspace import Users, prepare_root


@pytest.fixture
def work(monkeypatch):
    # /var/tmp, а не tmp_path pytest: каталоги pytest закрыты (0700) для uid песочницы.
    path = Path(tempfile.mkdtemp(prefix="layla-sandbox-", dir="/var/tmp"))
    os.chmod(path, 0o711)
    prepare_root(path)
    monkeypatch.setattr(config, "WORK", path)
    monkeypatch.setattr(server, "users", Users(path))
    monkeypatch.setattr(config, "PROXY", "")
    yield path
    shutil.rmtree(path, ignore_errors=True)
