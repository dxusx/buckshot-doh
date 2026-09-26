# -*- coding: utf-8 -*-
"""
config.py — настройки и профили DNS
"""

import os
import json
from pathlib import Path

# --- Пути ---
APP_DIR = Path(os.environ.get("LOCALAPPDATA", "C:/ProgramData")) / "DoH-DNS-Manager"
APP_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = APP_DIR / "settings.json"
DNSPROXY_EXE = APP_DIR / "dnsproxy.exe"

# --- Официальный канал и ресурсы ---
TG_CHANNEL = "https://t.me/xbox_dns"

# --- Известные DoH-профили ---
DOH_PROFILES = [
    {
        "name": "Xbox DNS",
        "url": "https://xbox-dns.ru/dns-query",
        "bootstrap": "111.88.96.54, 111.88.96.55, 1.1.1.1",
        "check_url": "https://xbox-dns.ru/test",
    },
    {
        "name": "Cloudflare",
        "url": "https://cloudflare-dns.com/dns-query",
        "bootstrap": "1.1.1.1, 1.0.0.1",
        "check_url": "https://1.1.1.1",
    },
    {
        "name": "AdGuard DNS",
        "url": "https://dns.adguard-dns.com/dns-query",
        "bootstrap": "94.140.14.14, 94.140.15.15",
        "check_url": "https://adguard-dns.com",
    },
    {
        "name": "Пользовательский",
        "url": "",
        "bootstrap": "",
        "check_url": "",
    },
]

DEFAULT_SETTINGS = {
    "profile_index": 0,
    "custom_url": "",
    "custom_bootstrap": "",
    "autostart": False,
    "auto_activate_on_startup": True,
    "minimize_to_tray": True,
    "listen_addr": "127.0.0.1",
    "listen_port": 53,
    "verbose_logging": True,
    "autoscroll": True,
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            merged = {**DEFAULT_SETTINGS, **data}
            return merged
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_active_profile(settings: dict) -> dict:
    idx = settings.get("profile_index", 0)
    if idx < 0 or idx >= len(DOH_PROFILES):
        idx = 0
    profile = DOH_PROFILES[idx].copy()
    if idx == len(DOH_PROFILES) - 1:  # Кастомный
        custom_url = (settings.get("custom_url") or "").strip()
        custom_boot = (settings.get("custom_bootstrap") or "").strip()
        profile["url"] = custom_url if custom_url else "https://xbox-dns.ru/dns-query"
        profile["bootstrap"] = custom_boot if custom_boot else "111.88.96.50"
        profile["check_url"] = "https://xbox-dns.ru/test"
    else:
        if not profile.get("url"):
            profile["url"] = "https://xbox-dns.ru/dns-query"
        if not profile.get("bootstrap"):
            profile["bootstrap"] = "111.88.96.50"

    # Гарантируем корректный префикс схемы
    url = profile["url"]
    if not any(url.startswith(scheme) for scheme in ("https://", "tls://", "quic://", "h3://", "sdns://")):
        profile["url"] = f"https://{url}"

    return profile


APP_VERSION = "2.3.0"
APP_NAME = "DoH DNS Manager"
GITHUB_API = "https://api.github.com/repos/AdguardTeam/dnsproxy/releases/latest"

