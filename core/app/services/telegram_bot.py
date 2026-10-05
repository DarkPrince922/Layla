"""Private Telegram control panel using Layla's existing authenticated API.

Each callback is an opaque, expiring, owner-bound capability stored server-side.
No Telegram input becomes an arbitrary API path or a shell command.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from app.models.telegram import TelegramConfig
from app.models.telegram_bot import TelegramBotState
from app.models.user import User
from app.security import crypto
from app.security.jwt import COOKIE_NAME, create_session_token
from app.services import telegram

DOMAINS = {"code": "Код", "pentest": "Пентест", "osint": "OSINT", "design": "Дизайн"}
HELP = """Лейла — управление агентом
/menu — панель управления
/domains — разделы
/targets — проекты, сайты и дела
/models — модель и провайдер
/chats — история чатов / запусков
/new — новый чат / проект / сайт
/mode — план / с подтверждением / авто
/tasks — задачи, результат, остановка и решения
/history — история текущего чата
/result — последний результат целиком
/files — файлы, папки и архив проекта
/file путь — скачать текстовый файл
/findings — находки пентеста
/reports — отчёты пентеста
/workers — воркеры текущего запуска
/artifacts — материалы OSINT
/lookup provider target — поиск OSINT
/generate описание — новый макет Design
/rename название — переименовать чат
/compact — сжать контекст чата
/notifications — настройки уведомлений
/cancel — отменить ввод / вернуться в меню
/web — полный веб-интерфейс
Просто отправьте сообщение, чтобы дать агенту задачу в выбранном контексте."""


async def api_call(owner_id, method, path, body=None, *, binary=False):
    from app.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://layla.internal",
        cookies={COOKIE_NAME: create_session_token(owner_id)}, timeout=90) as client:
        response = await client.request(method, "/api" + path, json=body)
    if response.is_error:
        try:
            detail = response.json().get("detail")
            if not isinstance(detail, str):
                detail = "Проверьте параметры запроса"
        except ValueError:
            detail = "Сервис временно недоступен"
        raise RuntimeError(f"{detail[:700]} (HTTP {response.status_code})")
    if binary:
        if len(response.content) > 20_000_000:
            raise RuntimeError("Архив больше 20 МБ. Скачайте через веб-интерфейс.")
        return response.content
    return response.json() if response.content else None


class Panel:
    def __init__(self, maker, cfg, bot):
        self.maker, self.cfg, self.bot = maker, cfg, bot
        self.state = dict(bot.state or {})
        self.original_state = dict(self.state)
        self.removed_callbacks = set()
        self.callbacks = {k: v for k, v in self.state.get("callbacks", {}).items() if v["expires"] > time.time()}
        self.original_callbacks = set(self.callbacks)
        self.token = crypto.decrypt(cfg.bot_token_ref)
        self.chat_id = bot.controller_chat_id

    async def save(self):
        async with self.maker() as session:
            current = await session.get(TelegramBotState, self.bot.id, with_for_update=True)
            if (current and current.controller_user_id == self.bot.controller_user_id
                    and current.token_hash == self.bot.token_hash):
                # Merge notification capabilities without replacing concurrent selections.
                merged = dict(current.state or {})
                for key in self.original_state.keys() - self.state.keys():
                    if key != "callbacks":
                        merged.pop(key, None)
                for key, value in self.state.items():
                    if key != "callbacks" and (key not in self.original_state or value != self.original_state[key]):
                        merged[key] = value
                callbacks = {k: v for k, v in merged.get("callbacks", {}).items()
                             if v["expires"] > time.time()}
                callbacks.update({k: v for k, v in self.callbacks.items() if k not in self.original_callbacks})
                for key in self.removed_callbacks:
                    callbacks.pop(key, None)
                merged["callbacks"] = dict(list(callbacks.items())[-500:])
                current.state = merged
                await session.commit()
                self.original_state = dict(self.state)

    def button(self, label, action, **data):
        key = secrets.token_hex(8)
        self.callbacks[key] = {"action": action, "data": data, "expires": time.time() + 86400}
        return {"text": str(label)[:60], "callback_data": "b:" + key}

    async def reply(self, text, rows=None):
        await self.save()  # capabilities must exist before Telegram displays their buttons
        await telegram.send_message(self.token, self.chat_id, text,
                                    reply_markup={"inline_keyboard": rows} if rows else None)

    async def call(self, method, path, body=None, **kw):
        return await api_call(self.cfg.owner_id, method, path, body, **kw)

    @property
    def domain(self):
        return self.state.get("domain", "code")

    async def menu(self):
        await self.reply(f"Лейла · {DOMAINS[self.domain]}\n"
            f"Цель: {self.state.get('target_name') or 'не выбрана'}\n"
            f"Модель: {self.state.get('model') or 'не выбрана'}\n"
            f"Режим: {self.state.get('mode', 'confirm')}\nОтправьте задачу текстом или выберите действие.", [
            [self.button("Разделы", "domains"), self.button("Сайты / проекты", "targets")],
            [self.button("Модель", "models"), self.button("Режим", "mode")],
            [self.button("Чаты", "chats"), self.button("Новый", "new")],
            [self.button("Задачи", "tasks"), self.button("Результат", "result")],
            [self.button("Файлы", "files"), self.button("Уведомления", "notifications")],
            [self.button("Помощь", "help"), self.button("Веб-интерфейс", "web")]])

    async def choose(self, title, items, action, page=0, **extra):
        start = page * 8
        rows = [[self.button(label, action, **data)] for label, data in items[start:start + 8]]
        nav = []
        if page:
            nav.append(self.button("←", "page", kind=extra["kind"], page=page - 1))
        if start + 8 < len(items):
            nav.append(self.button("→", "page", kind=extra["kind"], page=page + 1))
        if nav:
            rows.append(nav)
        rows.append([self.button("Меню", "menu")])
        await self.reply(title if items else title + "\nПока ничего нет. Используйте /new.", rows)

    async def domains(self):
        await self.reply("Выберите раздел", [[self.button(label, "domain", domain=key)] for key, label in DOMAINS.items()])

    async def targets(self, page=0):
        path = {"code": "/projects", "pentest": "/engagements", "osint": "/osint/cases", "design": "/designs"}[self.domain]
        rows = await self.call("GET", path)
        items = []
        for row in rows:
            if self.domain == "code" and row.get("kind") == "chat_workspace":
                continue
            name = row.get("name") or row.get("target") or row.get("subject") or (row.get("brief") or {}).get("brand") or row["id"][:8]
            items.append((name, {"id": row["id"], "domain": self.domain}))
        await self.choose("Выберите сайт / проект / дело", items, "target", page, kind="targets")

    async def select_target(self, id, domain):
        if domain != self.domain:
            raise RuntimeError("Сначала выберите соответствующий раздел")
        path = {"code": "/projects", "pentest": "/engagements", "osint": "/osint/cases", "design": "/designs"}[domain]
        if domain == "code":
            rows = await self.call("GET", path)
            item = next((r for r in rows if r["id"] == id), None)
            if not item:
                raise RuntimeError("Проект недоступен")
        else:
            item = await self.call("GET", path + "/" + id)
        self.state.update(target_id=id, target_name=item.get("name") or item.get("target") or item.get("subject") or id[:8])
        self.state.pop("chat_id", None); self.state.pop("run_id", None)
        if domain == "design":
            chat = await self.call("POST", f"/designs/{id}/chat")
            self.state["chat_id"] = chat["id"]
        await self.menu()

    async def models(self, page=0):
        rows = await self.call("GET", "/models")
        await self.choose("Выберите модель и провайдера", [(f"{r['name']} · {r.get('provider_name') or r.get('provider_id', '')[:8]}",
            {"name": r["name"], "provider_id": r.get("provider_id")}) for r in rows], "model", page, kind="models")

    async def select_model(self, name, provider_id=None):
        rows = await self.call("GET", "/models")
        if not any(r["name"] == name and r.get("provider_id") == provider_id for r in rows):
            raise RuntimeError("Модель больше недоступна. Обновите /models.")
        self.state.update(model=name, provider_id=provider_id)
        await self.menu()

    async def mode(self):
        await self.reply("Режим работы", [[self.button(label, "set_mode", mode=mode)] for mode, label in [
            ("plan", "План — без изменений"), ("confirm", "С подтверждением"), ("auto", "Авто")]])

    async def chats(self, page=0):
        if self.domain == "pentest":
            target = self.state.get("target_id")
            if not target:
                return await self.targets()
            rows = await self.call("GET", f"/engagements/{target}/agent/runs")
            items = [(r.get("task", "Запуск")[:60], {"id": r["id"], "domain": self.domain}) for r in rows]
        else:
            params = {"domain": self.domain}
            if self.domain == "code" and self.state.get("target_id"):
                params["project_id"] = self.state["target_id"]
            rows = await self.call("GET", "/chats?" + urlencode(params))
            items = [(r.get("title") or "Чат", {"id": r["id"], "domain": self.domain}) for r in rows]
        await self.choose("Выберите чат / запуск", items, "chat", page, kind="chats")

    async def select_chat(self, id, domain):
        if domain != self.domain:
            raise RuntimeError("Раздел изменился. Обновите /chats.")
        path = f"/agent/runs/{id}" if domain == "pentest" else f"/chats/{id}"
        item = await self.call("GET", path)
        if domain == "pentest":
            if item.get("engagement_id") != self.state.get("target_id"):
                raise RuntimeError("Запуск относится к другому сайту")
            self.state["run_id"] = id
        else:
            if item["domain"] != domain:
                raise RuntimeError("Чат относится к другому разделу")
            self.state["chat_id"] = id
            if domain == "code":
                self.state["target_id"] = item.get("project_id")
        if item.get("model"):
            self.state["model"] = item["model"]
            self.state["provider_id"] = item.get("provider_id")
        await self.menu()

    async def new(self):
        await self.reply("Что создать?", [[self.button("Новый чат / запуск", "new_chat")],
            [self.button("Новый сайт / проект / дело", "new_target")], [self.button("Меню", "menu")]])

    async def new_chat(self):
        self.state.pop("chat_id", None); self.state.pop("run_id", None)
        await self.reply("Новый диалог выбран. Следующее сообщение начнёт работу агента.")

    async def new_target(self):
        self.state["input"] = "new_target"
        await self.reply({"code": "Введите название проекта", "pentest": "Введите домен или URL сайта",
            "osint": "Введите домен для нового OSINT-дела", "design": "Опишите новый макет сайта"}[self.domain] + ". /cancel — отмена.")

    async def create_target(self, text):
        if self.domain == "design":
            return await self.generate(text)
        paths = {"code": "/projects", "pentest": "/engagements", "osint": "/osint/cases"}
        fields = {"code": "name", "pentest": "target", "osint": "subject"}
        item = await self.call("POST", paths[self.domain], {fields[self.domain]: text})
        self.state.pop("input", None)
        await self.select_target(item["id"], self.domain)

    async def message(self, text, update_id):
        if not text.strip():
            return
        if self.state.get("input") == "new_target":
            return await self.create_target(text)
        if not self.state.get("model"):
            raise RuntimeError("Сначала выберите /models")
        mode = self.state.get("mode", "confirm")
        if self.domain == "pentest":
            target = self.state.get("target_id")
            if not target:
                raise RuntimeError("Сначала выберите сайт в /targets")
            body = {"content": text, "model": self.state["model"], "mode": mode}
            if self.state.get("run_id"):
                body["run_id"] = self.state["run_id"]
            result = await self.call("POST", f"/engagements/{target}/agent/chat", body)
            self.state["run_id"] = result["id"]
            await self.reply(f"Запуск {result['id'][:8]} принят. Результат и подтверждения доступны в /tasks и /workers.")
        else:
            if not self.state.get("chat_id"):
                body = {"domain": self.domain, "model": self.state["model"], "provider_id": self.state.get("provider_id"),
                        "title": text[:80]}
                if self.domain == "code" and self.state.get("target_id"):
                    body["project_id"] = self.state["target_id"]
                chat = await self.call("POST", "/chats", body)
                self.state["chat_id"] = chat["id"]
                await self.save()
            result = await self.call("POST", f"/chats/{self.state['chat_id']}/run", {
                "content": (f"Выбранное OSINT-дело: {self.state.get('target_name')} (id {self.state.get('target_id')})\n\n" + text
                    if self.domain == "osint" and self.state.get("target_id") else text),
                "model": self.state["model"], "provider_id": self.state.get("provider_id"),
                "mode": mode, "request_id": f"tg-{self.cfg.id[:8]}-{update_id}"})
            self.state["last_job_id"] = result["id"]
            await self.reply(f"Задача {result['id'][:8]} принята. Уведомление придёт после завершения.",
                [[self.button("Статус", "job", id=result["id"]), self.button("Остановить", "stop_job", id=result["id"]) ]])

    async def generate(self, text):
        if not self.state.get("model"):
            raise RuntimeError("Сначала выберите /models")
        result = await self.call("POST", "/designs/generate", {"brief": {"notes": text}, "stack": "html", "model": self.state["model"]})
        self.state.pop("input", None)
        self.state["last_job_id"] = result["id"]
        await self.reply(f"Генерация макета {result['id'][:8]} началась. Уведомим о результате.")

    async def tasks(self, page=0):
        rows = await self.call("GET", "/jobs")
        await self.choose("Задачи всех разделов", [(f"{r['status']} · {r['domain']} · {r.get('title', '')[:40]}", {"id": r["id"]})
            for r in rows], "job", page, kind="tasks")

    async def job(self, id):
        item = await self.call("GET", "/jobs/" + id)
        self.state["last_job_id"] = id
        steps = item.get("steps") or []
        text = f"{item.get('title')}\n{item['domain']} · {item['status']} · {int(item.get('progress', 0) * 100)}%\n"
        text += "\n".join(str(s.get("text", "")) for s in steps[-5:])
        if item.get("error"):
            text += "\n" + item["error"]
        rows = [[self.button("Обновить", "job", id=id), self.button("Полный результат", "job_result", id=id)]]
        if item["status"] in ("queued", "running"):
            rows.append([self.button("Остановить", "stop_job", id=id)])
        approval = (item.get("result") or {}).get("approval")
        if approval:
            text += "\nТребуется решение:\n" + str(approval.get("name", "")) + "\n" + str(approval.get("command") or approval.get("path") or "")
            rows.append([self.button("Применить", "decision", id=id, approval_id=approval["id"], decision="approve"),
                         self.button("Отклонить", "decision", id=id, approval_id=approval["id"], decision="reject")])
        rows.append([self.button("Меню", "menu")])
        await self.reply(text, rows)

    async def job_result(self, id):
        item = await self.call("GET", "/jobs/" + id)
        result = item.get("result") or {}
        text = result.get("content") or result.get("draft") or result.get("text") or result.get("verdict") or ""
        if result.get("worker_id"):
            worker = await self.call("GET", "/agent/workers/" + result["worker_id"])
            text = worker.get("result") or worker.get("error") or text
        if result.get("design_id"):
            design = await self.call("GET", "/designs/" + result["design_id"])
            text = "\n\n".join(f"--- {f.get('name', 'file')} ---\n{f.get('content', '')}" for f in design.get("files", []))
        if item.get("chat_id"):
            chat = await self.call("GET", "/chats/" + item["chat_id"])
            message = next((m for m in chat["messages"] if m["id"] == result.get("message_id")), None)
            if message:
                text = message["content"]
        if not text:
            text = item.get("error") or str(result) or "Результат ещё не готов"
        await self.reply(f"{item.get('title')} · {item['status']}\n" + str(text)[:11000])
        await telegram.send_document(self.token, self.chat_id, str(text), f"result-{id[:8]}.txt")

    async def result(self):
        if self.domain == "pentest" and self.state.get("run_id"):
            item = await self.call("GET", "/agent/runs/" + self.state["run_id"])
            text = "\n\n".join(str(s.get("output") or s.get("summary") or "") for s in item.get("steps", []) if s.get("role") == "assistant")
            await self.reply(text[:11000] or "Результат ещё не готов")
            if text:
                await telegram.send_document(self.token, self.chat_id, text)
        elif self.state.get("last_job_id"):
            await self.job_result(self.state["last_job_id"])
        elif self.state.get("chat_id"):
            await self.history()
        else:
            await self.reply("Выберите задачу в /tasks")

    async def history(self):
        if self.domain == "pentest":
            return await self.result()
        if not self.state.get("chat_id"):
            return await self.chats()
        item = await self.call("GET", "/chats/" + self.state["chat_id"])
        lines = [f"{m['role']}: {m['content']}" for m in item["messages"][-10:] if m["role"] in ("user", "assistant")]
        await self.reply("\n\n".join(lines)[-11000:] or "В чате пока нет сообщений")

    async def project_id(self):
        pid = self.state.get("target_id") if self.domain == "code" else None
        if self.state.get("chat_id"):
            chat = await self.call("GET", "/chats/" + self.state["chat_id"])
            pid = chat.get("project_id") or pid
        if not pid:
            raise RuntimeError("Выберите проект / чат с файлами. Архив воркера доступен в /workers.")
        return pid

    async def files(self, path=".", page=0):
        pid = await self.project_id()
        rows = await self.call("GET", f"/projects/{pid}/files?" + urlencode({"path": path}))
        page = max(0, min(int(page), max(0, (len(rows) - 1) // 8)))
        buttons = [[self.button(("📁 " if row["is_dir"] else "📄 ") + row["name"],
            "files" if row["is_dir"] else "file", path=row["path"])] for row in rows[page * 8:page * 8 + 8]]
        navigation = []
        if page:
            navigation.append(self.button("←", "files", path=path, page=page - 1))
        if (page + 1) * 8 < len(rows):
            navigation.append(self.button("→", "files", path=path, page=page + 1))
        if navigation:
            buttons.append(navigation)
        if path != ".":
            buttons.append([self.button("Корень проекта", "files")])
        buttons.append([self.button("Скачать ZIP", "archive", id=pid), self.button("Меню", "menu")])
        await self.reply(f"Файлы: {path} · страница {page + 1}" if rows else "Папка пуста", buttons)

    async def file(self, path):
        pid = await self.project_id()
        item = await self.call("GET", f"/projects/{pid}/file?" + urlencode({"path": path}))
        await self.reply(f"{item['path']}\n" + item["content"][:3500])
        await telegram.send_document(self.token, self.chat_id, item["content"],
                                     item["path"].replace("\\", "/").rsplit("/", 1)[-1] or "file.txt")

    async def findings(self):
        target = self.state.get("target_id") if self.domain == "pentest" else None
        if not target:
            raise RuntimeError("Выберите сайт в разделе Пентест")
        rows = await self.call("GET", f"/engagements/{target}/findings")
        text = "\n\n".join(f"{r.get('severity', '')} · {r.get('title') or r.get('type', '')}\n{r.get('status', '')}\n{r.get('url') or ''}\n{r.get('description') or ''}" for r in rows)
        await self.reply(text[:11000] or "Находок пока нет")
        if text:
            await telegram.send_document(self.token, self.chat_id, text, "findings.txt")

    async def reports(self):
        target = self.state.get("target_id") if self.domain == "pentest" else None
        if not target:
            raise RuntimeError("Выберите сайт в разделе Пентест")
        rows = await self.call("GET", f"/engagements/{target}/reports")
        text = "\n".join(f"{r['id']} · {r.get('format', '')} · {r.get('status', '')} · {r.get('stats', {})}" for r in rows)
        await self.reply(text[:11000] or "Отчётов пока нет", [
            [self.button("Сформировать отчёт", "generate_report", id=target)],
            [self.button("Скачать текущую сводку", "report_snapshot", id=target)]])

    async def report_snapshot(self, id):
        engagement = await self.call("GET", "/engagements/" + id)
        findings = await self.call("GET", f"/engagements/{id}/findings")
        lines = [f"# Сводка находок: {engagement['target']}",
                 "Текущее состояние находок, включая статус подтверждения.", f"Всего: {len(findings)}", ""]
        for item in findings:
            lines.extend([f"## [{item.get('severity', '')}] {item.get('title') or item.get('type', '')}",
                f"Статус: {item.get('status', '')}", f"URL: {item.get('url') or ''}",
                f"Параметр: {item.get('param') or ''}", item.get("description") or "", ""])
        await telegram.send_document(self.token, self.chat_id, "\n".join(lines), "findings-report.md")

    async def workers(self):
        if not self.state.get("run_id") or self.domain != "pentest":
            raise RuntimeError("Выберите запуск пентеста в /chats")
        run = await self.call("GET", "/agent/runs/" + self.state["run_id"])
        rows = [[self.button(f"{w['status']} · {w['task'][:35]}", "worker", id=w["id"])] for w in run.get("workers", [])]
        for step in run.get("steps", []):
            if step["status"] == "awaiting_approval":
                rows.append([self.button("Подтвердить: " + str(step.get("summary") or step.get("command") or "действие")[:35], "step", id=step["id"])])
        rows.append([self.button("Остановить запуск", "stop_run", id=run["id"])])
        await self.reply("Воркеры и подтверждения запуска", rows)

    async def worker(self, id):
        w = await self.call("GET", "/agent/workers/" + id)
        rows = [[self.button("Обновить", "worker", id=id), self.button("Архив", "worker_archive", id=id)]]
        if w["status"] in ("running", "queued", "awaiting_approval"):
            rows.append([self.button("Остановить", "stop_worker", id=id)])
        else:
            rows.append([self.button("Продолжить", "resume_worker", id=id)])
        await self.reply(f"{w['task']}\n{w['status']} · {w.get('activity') or ''}\n{w.get('result') or w.get('error') or ''}"[:11000], rows)

    async def step(self, id):
        step = await self.call("GET", "/agent/steps/" + id)
        await self.reply(f"Решение по действию\n{step.get('summary') or ''}\n{step.get('command') or ''}\nСтатус: {step['status']}",
            [[self.button("Подтвердить", "approve_step", id=id), self.button("Отклонить", "deny_step", id=id)]])

    async def artifacts(self):
        if self.domain != "osint" or not self.state.get("target_id"):
            raise RuntimeError("Выберите OSINT-дело")
        rows = await self.call("GET", f"/osint/cases/{self.state['target_id']}/artifacts")
        text = "\n\n".join(f"{r['title']}\n{r['summary']}\n{r['source_url']}" for r in rows)
        await self.reply(text[:11000] or "Материалов пока нет")
        if text:
            await telegram.send_document(self.token, self.chat_id, text, "osint.txt")

    async def notifications(self):
        async with self.maker() as session:
            bot = await session.get(TelegramBotState, self.bot.id)
            rows = [[self.button(("✅ " if getattr(bot, key) else "⬜ ") + label, "toggle_notification", field=key)] for key, label in [
                ("notify_done", "Работа завершена"), ("notify_error", "Ошибка"),
                ("notify_cancelled", "Остановка"), ("notify_approval", "Требуется решение")]]
        await self.reply("Уведомления", rows)

    async def web(self):
        if not self.bot.public_url:
            raise RuntimeError("Укажите HTTPS-адрес Лейлы в настройках Telegram веб-интерфейса")
        await self.reply("Полный интерфейс: файлы, редактор, Git, отчёты, настройки и остальные возможности.",
                         [[{"text": "Открыть Лейлу", "web_app": {"url": self.bot.public_url}}]])

    async def action(self, action, data):
        if action in ("menu", "domains", "targets", "models", "mode", "chats", "new", "new_chat", "new_target", "tasks", "history", "result", "files", "file", "findings", "reports", "report_snapshot", "workers", "artifacts", "notifications", "web"):
            return await getattr(self, action)(**data)
        if action == "help":
            return await self.reply(HELP)
        if action == "page":
            if data["kind"] not in ("targets", "models", "chats", "tasks"):
                raise RuntimeError("Неизвестное меню")
            return await getattr(self, data["kind"])(page=data["page"])
        if action == "domain":
            if data["domain"] not in DOMAINS:
                raise RuntimeError("Неизвестный раздел")
            self.state = {k: v for k, v in self.state.items() if k in ("model", "provider_id", "mode", "callbacks")}
            self.state["domain"] = data["domain"]
            return await self.menu()
        if action == "target":
            return await self.select_target(**data)
        if action == "model":
            return await self.select_model(**data)
        if action == "chat":
            return await self.select_chat(**data)
        if action == "set_mode":
            if data["mode"] not in ("plan", "confirm", "auto"):
                raise RuntimeError("Неизвестный режим")
            self.state["mode"] = data["mode"]
            return await self.menu()
        if action in ("job", "job_result", "worker", "step"):
            return await getattr(self, action)(**data)
        if action in ("stop_job", "stop_run", "stop_worker"):
            path = {"stop_job": "/jobs/", "stop_run": "/agent/runs/", "stop_worker": "/agent/workers/"}[action]
            await self.call("GET", path + data["id"])
            return await self.reply("Остановить эту задачу?", [[self.button("Да, остановить", "confirm_stop", kind=action, id=data["id"]), self.button("Назад", "menu")]])
        if action == "confirm_stop":
            prefix = {"stop_job": "/jobs/", "stop_run": "/agent/runs/", "stop_worker": "/agent/workers/"}.get(data["kind"])
            if not prefix:
                raise RuntimeError("Неизвестная задача")
            await self.call("POST", prefix + data["id"] + ("/cancel" if data["kind"] == "stop_job" else "/stop"))
            return await self.reply("Остановка запрошена. Уже выполненные изменения сохранены.")
        if action == "decision":
            await self.call("POST", f"/jobs/{data['id']}/decision", {"approval_id": data["approval_id"], "decision": data["decision"]})
            return await self.reply("Решение отправлено")
        if action in ("approve_step", "deny_step"):
            await self.call("POST", f"/agent/steps/{data['id']}/" + ("approve" if action == "approve_step" else "deny"))
            return await self.reply("Решение отправлено")
        if action == "resume_worker":
            await self.call("POST", f"/agent/workers/{data['id']}/resume")
            return await self.reply("Воркер продолжает работу")
        if action in ("archive", "worker_archive"):
            path = f"/projects/{data['id']}/archive" if action == "archive" else f"/agent/workers/{data['id']}/archive"
            raw = await self.call("GET", path, binary=True)
            return await telegram.send_document(self.token, self.chat_id, raw, "project.zip")
        if action == "generate_report":
            await self.call("POST", f"/engagements/{data['id']}/reports/generate")
            return await self.reports()
        if action == "toggle_notification":
            if data["field"] not in ("notify_done", "notify_error", "notify_cancelled", "notify_approval"):
                raise RuntimeError("Неизвестное уведомление")
            async with self.maker() as session:
                bot = await session.get(TelegramBotState, self.bot.id)
                setattr(bot, data["field"], not getattr(bot, data["field"]))
                await session.commit()
            return await self.notifications()
        raise RuntimeError("Кнопка устарела. Откройте /menu.")

    async def command(self, text, update_id):
        command, _, argument = text.partition(" ")
        name = command.split("@")[0].lstrip("/")
        if name in ("start", "cancel"):
            self.state.pop("input", None)
            return await self.menu()
        if name == "help":
            return await self.reply(HELP)
        if name in ("menu", "domains", "targets", "models", "mode", "chats", "new", "tasks", "history", "result", "files", "findings", "reports", "workers", "artifacts", "notifications", "web"):
            return await getattr(self, name)()
        if name == "file" and argument:
            return await self.file(argument)
        if name == "generate" and argument:
            return await self.generate(argument)
        if name == "rename" and argument and self.state.get("chat_id"):
            await self.call("PATCH", f"/chats/{self.state['chat_id']}", {"title": argument})
            return await self.reply("Чат переименован")
        if name == "compact" and self.state.get("chat_id"):
            job = await self.call("POST", f"/chats/{self.state['chat_id']}/compact")
            self.state["last_job_id"] = job["id"]
            return await self.reply("Сжатие контекста началось")
        if name == "lookup" and self.domain == "osint" and self.state.get("target_id"):
            provider, _, target = argument.partition(" ")
            result = await self.call("POST", f"/osint/cases/{self.state['target_id']}/lookups", {"provider": provider, "target": target or self.state["target_name"]})
            return await self.reply(f"Поиск: {result['status']} · новых материалов {result.get('artifact_count', 0)}")
        await self.reply("Неизвестная команда или не выбран контекст. /help — список команд.")


async def handle_update(maker, config_id, update):
    callback = update.get("callback_query")
    message = (callback or {}).get("message") if callback else update.get("message")
    sender = (callback or message or {}).get("from", {})
    if not message or sender.get("is_bot"):
        return
    chat = message.get("chat", {})
    private = chat.get("type") == "private" and str(chat.get("id")) == str(sender.get("id"))
    async with maker() as session:
        cfg = await session.get(TelegramConfig, config_id)
        bot = await session.scalar(select(TelegramBotState).where(TelegramBotState.config_id == config_id).with_for_update())
        user = await session.get(User, cfg.owner_id) if cfg else None
        if not cfg or not cfg.enabled or not bot or not bot.control_enabled or not user or not user.is_active:
            return
        token = crypto.decrypt(cfg.bot_token_ref)
        text = message.get("text", "")
        code = text.partition(" ")[2] if text.split(" ")[0].split("@")[0] == "/start" else ""
        state = dict(bot.state or {})
        if private and code and state.get("pair_hash") and state.get("pair_expires", 0) > time.time() and secrets.compare_digest(
            hashlib.sha256(code.encode()).hexdigest(), state["pair_hash"]):
            bot.controller_user_id, bot.controller_chat_id = str(sender["id"]), str(chat["id"])
            bot.state = {"domain": "code", "mode": "confirm"}
            cfg.default_chat_id = str(chat["id"])
            await session.commit()
        authorized = private and str(sender.get("id")) == bot.controller_user_id and str(chat.get("id")) == bot.controller_chat_id
    if callback:
        await telegram.call(token, "answerCallbackQuery", {"callback_query_id": callback["id"],
            "text": "" if authorized else "Этот бот не подключён к вашему аккаунту Лейлы"})
    if not authorized:
        if private and not callback:
            await telegram.send_message(token, str(chat["id"]), "Подключите аккаунт через Настройки → Интеграции → Telegram в Лейле.")
        return
    panel = Panel(maker, cfg, bot)
    try:
        if callback:
            key = str(callback.get("data", "")).removeprefix("b:")
            operation = panel.callbacks.pop(key, None)
            panel.removed_callbacks.add(key)
            if not operation:
                raise RuntimeError("Кнопка устарела. Откройте /menu.")
            # One-use capability: consumed before an external side effect.
            await panel.save()
            await panel.action(operation["action"], operation["data"])
        elif text.startswith("/"):
            await panel.command(text, update["update_id"])
        elif text:
            await panel.message(text, update["update_id"])
        else:
            await panel.reply("Пока отправьте текст задачи. Файлы можно загрузить через /web.")
    except RuntimeError as exc:
        await panel.reply(str(exc)[:1000])
    await panel.save()
