#!/usr/bin/env python3
# server.py — MCP-сервер "mcp_1c_read".
# Тонкий міст: дає Claude Desktop інструменти ЧИТАННЯ даних 1С через ваш vps_api.
# Нічого не пише. Уся логіка — у vps_api; тут лише HTTP-виклики + автологін.
#
# Запуск: Claude Desktop стартує його сам за конфігом (claude_desktop_config.json).
# Залежності: pip install mcp httpx
# Конфіг через змінні оточення (у claude_desktop_config.json → "env"):
#   VPS_API_URL   напр. http://192.168.91.15:8000
#   VPS_USERNAME  логін користувача vps_api
#   VPS_PASSWORD  пароль користувача vps_api

import os

import httpx
from mcp.server.fastmcp import FastMCP

# Необов'язкове підвантаження .env (для локального тесту з консолі).
# У Claude Desktop змінні йдуть через claude_desktop_config.json → "env",
# тож відсутність python-dotenv не критична.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Конфіг з оточення ──
API_URL = os.getenv("VPS_API_URL", "").rstrip("/")
USERNAME = os.getenv("VPS_USERNAME", "")
PASSWORD = os.getenv("VPS_PASSWORD", "")

mcp = FastMCP("mcp_1c_read")

# ── Автентифікація: токен у пам'яті, автологін + перелогін на 401 ──
_token = {"value": None}


def _login():
    """Логіниться у vps_api, зберігає токен у пам'яті. Кидає виняток при невдачі."""
    if not API_URL:
        raise RuntimeError("Не задано VPS_API_URL у конфігу MCP")
    if not USERNAME or not PASSWORD:
        raise RuntimeError("Не задано VPS_USERNAME / VPS_PASSWORD у конфігу MCP")
    resp = httpx.post(
        f"{API_URL}/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Логін у vps_api не вдався: HTTP {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    tok = data.get("token")
    if not tok:
        raise RuntimeError("vps_api не повернув token при логіні")
    _token["value"] = tok
    return tok


def _headers():
    """Заголовки з поточним токеном (логіниться, якщо токена ще нема)."""
    if not _token["value"]:
        _login()
    return {
        "Authorization": "Bearer " + _token["value"],
        "Content-Type": "application/json",
    }


def _call(path: str, payload: dict) -> dict:
    """POST у vps_api з автентифікацією. На 401 — перелогін і один повтор."""
    url = f"{API_URL}{path}"
    try:
        resp = httpx.post(url, json=payload, headers=_headers(), timeout=60)
        if resp.status_code == 401:
            # токен протух — перелогінитись і повторити один раз
            _login()
            resp = httpx.post(url, json=payload, headers=_headers(), timeout=60)
    except httpx.RequestError as exc:
        raise RuntimeError(f"vps_api недоступний: {exc}")

    if resp.status_code != 200:
        # спробувати дістати текст помилки з JSON
        detail = resp.text[:300]
        try:
            j = resp.json()
            detail = j.get("detail", detail)
        except Exception:
            pass
        raise RuntimeError(f"vps_api HTTP {resp.status_code}: {detail}")

    return resp.json()


# ═══ ІНСТРУМЕНТИ (тільки читання) ═══

@mcp.tool()
def list_objects() -> dict:
    """Список об'єктів конфігурації 1С (довідники + документи).
    Повертає {total, objects:[{type, name, synonym}]}."""
    return _call("/1c/metadata_objects", {})


@mcp.tool()
def describe_object(object_type: str, object_name: str) -> dict:
    """Опис об'єкта 1С: реквізити (з типами) + табличні частини.
    object_type: "Справочник" | "Документ"; object_name: ім'я об'єкта (напр. "Контрагенты").
    Повертає {type, name, synonym, attributes[], tabular_sections[]}."""
    return _call("/1c/metadata_describe", {"type": object_type, "name": object_name})


@mcp.tool()
def list_queries(object_type: str, object_name: str) -> dict:
    """Наявні іменовані запити (.sel/.json), прив'язані до об'єкта 1С.
    Повертає {total, queries:[{query_name, info, file, fields_count, mcp_allowed}]}.
    mcp_allowed=true → цей запит можна виконати через run_query (інакше 403)."""
    return _call("/metadata/queries", {"object_type": object_type, "object_name": object_name})


@mcp.tool()
def get_query(query_name: str) -> dict:
    """Сирий вміст запиту: текст .sel і метадані .json (поля, типи).
    Повертає {query_name, file, sel, meta}."""
    return _call("/metadata/query_get", {"query_name": query_name})


@mcp.tool()
def run_query(
    query_name: str,
    filters: str = None,
    params: dict = None,
    order: str = None,
    offset: int = 0,
    limit: int = 100,
) -> dict:
    """Виконати іменований запит і отримати дані 1С для аналізу.
    query_name: ім'я запиту з системи (див. list_queries / get_query).
    filters: відбір по аліасах, напр. "name ПОДОБНО &search" (значення — у params).
    params: значення параметрів, формат {ім'я:{type, value}} (напр. {"search":{"type":"string","value":"ТОВ"}}).
    order: сортування по аліасах, напр. "name" або "name УБЫВ".
    offset/limit: посторінкова вибірка.
    Повертає {total, rows[], total_time}."""
    payload = {"query": query_name, "offset": offset, "limit": limit, "mcp": True}
    if filters:
        payload["filters"] = filters
    if params:
        payload["params"] = params
    if order:
        payload["order"] = order
    return _call("/1c/query", payload)


if __name__ == "__main__":
    mcp.run()