"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, ShieldCheck, ShieldOff, Trash2, UserPlus, Power } from "lucide-react";
import { api, type AdminUser, type AdminUserCreated } from "@/lib/api";
import { useAuth } from "@/store/auth";

interface Credential {
  email: string;
  password: string;
  note: string;
}

export function UsersSettings() {
  const qc = useQueryClient();
  const meId = useAuth((s) => s.user?.id);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [makeAdmin, setMakeAdmin] = useState(false);
  const [cred, setCred] = useState<Credential | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: users = [], isPending } = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => api.get<AdminUser[]>("/admin/users"),
  });

  const refresh = () => qc.invalidateQueries({ queryKey: ["admin-users"] });
  const fail = (e: unknown) =>
    setError(e instanceof Error ? e.message : "Не удалось выполнить действие");

  const create = useMutation({
    mutationFn: () =>
      api.post<AdminUserCreated>("/admin/users", {
        email: email.trim(),
        display_name: name.trim() || null,
        password: password.trim() || null,
        is_admin: makeAdmin,
      }),
    onSuccess: (res) => {
      setError(null);
      if (res.generated_password)
        setCred({
          email: res.user.email,
          password: res.generated_password,
          note: "Новый пользователь создан. Передайте пароль — он показан один раз.",
        });
      setEmail("");
      setName("");
      setPassword("");
      setMakeAdmin(false);
      refresh();
    },
    onError: fail,
  });

  const patch = useMutation({
    mutationFn: (v: { id: string; body: Record<string, unknown> }) =>
      api.patch<AdminUserCreated>(`/admin/users/${v.id}`, v.body),
    onSuccess: (res) => {
      setError(null);
      if (res.generated_password)
        setCred({
          email: res.user.email,
          password: res.generated_password,
          note: "Пароль сброшен. Передайте новый — он показан один раз.",
        });
      refresh();
    },
    onError: fail,
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/admin/users/${id}`),
    onSuccess: () => {
      setError(null);
      refresh();
    },
    onError: fail,
  });

  return (
    <div>
      <h1 className="text-xl font-semibold">Пользователи</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Учётные записи, роли и сброс паролей. У каждого пользователя свои чаты,
        проекты, провайдеры и рабочее пространство.
      </p>

      {cred && (
        <div className="mt-4 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-xs">
          <div className="mb-2 flex items-start justify-between gap-2">
            <p className="font-semibold text-amber-300">{cred.note}</p>
            <button
              onClick={() => setCred(null)}
              className="shrink-0 text-amber-200/70 hover:text-white"
            >
              Скрыть
            </button>
          </div>
          <div className="space-y-1 font-mono text-[13px]">
            <div>
              <span className="text-neutral-400">Логин: </span>
              <span className="select-all text-white">{cred.email}</span>
            </div>
            <div>
              <span className="text-neutral-400">Пароль: </span>
              <span className="select-all text-white">{cred.password}</span>
            </div>
          </div>
        </div>
      )}

      {error && (
        <p role="alert" className="mt-4 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error}
        </p>
      )}

      {/* Создание пользователя */}
      <div className="mt-6 rounded-lg border border-ink-700 bg-ink-900 p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <UserPlus className="h-4 w-4 text-accent-400" /> Добавить пользователя
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            type="email"
            placeholder="e-mail"
            className="rounded-md border border-ink-700 bg-ink-800 px-2.5 py-1.5 text-sm outline-none focus:border-accent-500"
          />
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Имя (необязательно)"
            className="rounded-md border border-ink-700 bg-ink-800 px-2.5 py-1.5 text-sm outline-none focus:border-accent-500"
          />
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Пароль (пусто — сгенерировать)"
            className="rounded-md border border-ink-700 bg-ink-800 px-2.5 py-1.5 text-sm outline-none focus:border-accent-500"
          />
          <label className="flex items-center gap-2 px-1 text-xs text-neutral-300">
            <input
              type="checkbox"
              checked={makeAdmin}
              onChange={(e) => setMakeAdmin(e.target.checked)}
            />
            Администратор
          </label>
        </div>
        <button
          onClick={() => create.mutate()}
          disabled={!email.trim() || create.isPending}
          className="mt-3 rounded-md bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-500 disabled:opacity-40"
        >
          {create.isPending ? "Создание…" : "Создать"}
        </button>
      </div>

      {/* Список пользователей */}
      <div className="mt-6 space-y-2">
        {isPending ? (
          <p className="text-sm text-neutral-500">Загрузка…</p>
        ) : (
          users.map((u) => (
            <div
              key={u.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2.5"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm text-neutral-200">{u.email}</span>
                  {u.is_admin && (
                    <span className="rounded bg-accent-500/20 px-1.5 py-0.5 text-[10px] text-accent-300">
                      админ
                    </span>
                  )}
                  {!u.is_active && (
                    <span className="rounded bg-red-500/20 px-1.5 py-0.5 text-[10px] text-red-300">
                      отключён
                    </span>
                  )}
                  {u.id === meId && (
                    <span className="rounded bg-ink-700 px-1.5 py-0.5 text-[10px] text-neutral-400">
                      это вы
                    </span>
                  )}
                </div>
                {u.display_name && (
                  <p className="truncate text-xs text-neutral-500">{u.display_name}</p>
                )}
              </div>
              <div className="flex items-center gap-1">
                <button
                  title="Сбросить пароль"
                  onClick={() => patch.mutate({ id: u.id, body: { reset_password: true } })}
                  className="rounded p-1.5 text-neutral-400 hover:bg-ink-700 hover:text-white"
                >
                  <KeyRound className="h-4 w-4" />
                </button>
                <button
                  title={u.is_admin ? "Разжаловать" : "Сделать админом"}
                  onClick={() => patch.mutate({ id: u.id, body: { is_admin: !u.is_admin } })}
                  className="rounded p-1.5 text-neutral-400 hover:bg-ink-700 hover:text-white"
                >
                  {u.is_admin ? <ShieldOff className="h-4 w-4" /> : <ShieldCheck className="h-4 w-4" />}
                </button>
                <button
                  title={u.is_active ? "Отключить" : "Включить"}
                  onClick={() => patch.mutate({ id: u.id, body: { is_active: !u.is_active } })}
                  className="rounded p-1.5 text-neutral-400 hover:bg-ink-700 hover:text-white"
                >
                  <Power className={`h-4 w-4 ${u.is_active ? "" : "text-red-400"}`} />
                </button>
                {u.id !== meId && (
                  <button
                    title="Удалить"
                    onClick={() => {
                      if (confirm(`Удалить пользователя ${u.email}? Данные будут удалены безвозвратно.`))
                        remove.mutate(u.id);
                    }}
                    className="rounded p-1.5 text-neutral-400 hover:bg-red-500/20 hover:text-red-300"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
