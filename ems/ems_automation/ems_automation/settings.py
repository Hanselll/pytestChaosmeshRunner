from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

BASE_URL = os.getenv("EMS_BASE_URL", "https://10.230.246.195:32346").strip().rstrip("/")
HOME_URL = BASE_URL + "/index#/topoOverview"
LOGIN_URL = BASE_URL + "/index#/login"
DEFAULT_VIEWPORT = {"width": 1600, "height": 1000}


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_artifacts_root() -> Path:
    root = get_project_root() / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_output_path(path: str, category: str) -> Path:
    target = Path(path)
    if target.is_absolute():
        return target
    return get_artifacts_root() / category / target


def get_auth_state_path() -> Path:
    env_path = os.getenv("EMS_STORAGE_STATE", "").strip()
    if env_path:
        return Path(env_path)
    return get_artifacts_root() / "auth" / "storage_state.json"


def get_inventory_path() -> Path:
    env_path = os.getenv("EMS_INVENTORY_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return get_project_root() / "inventory.json"


def load_cookie_string() -> str:
    env_cookie = os.getenv("EMS_COOKIE", "").strip()
    if env_cookie:
        return env_cookie

    cookie_file = get_project_root() / "cookie.txt"
    if cookie_file.exists():
        return cookie_file.read_text(encoding="utf-8").strip()

    raise RuntimeError("未找到 EMS Cookie。请设置 EMS_COOKIE 或在项目根目录放置 cookie.txt。")


def build_cookies(cookie_string: str) -> list[dict]:
    cookies = []
    host = urlparse(BASE_URL).hostname or "10.230.246.195"
    for part in cookie_string.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies.append(
            {
                "name": name.strip(),
                "value": value.strip(),
                "domain": host,
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "sameSite": "Lax",
            }
        )
    return cookies
