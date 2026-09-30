# -*- coding: utf-8 -*-
"""
dns_backend.py — загрузка dnsproxy, управление процессом и сетевым адаптером Windows.
"""

import os
import sys
import subprocess
import threading
import zipfile
import time
import socket
import logging
import winreg
import ctypes
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

from app.config import (
    APP_DIR, DNSPROXY_EXE, GITHUB_API,
    get_active_profile, APP_NAME,
)

logger = logging.getLogger("dns_backend")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(APP_DIR / "app.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

# ----------------------------------------------------------------─────────────
# dnsproxy менеджер
# ----------------------------------------------------------------─────────────

class DnsproxyManager:
    """Управляет загрузкой и жизненным циклом процесса dnsproxy."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    # ── Загрузка ──────────────────────────────────────────────────────────────

    def ensure_dnsproxy(self, progress_cb=None) -> bool:
        """Скачивает dnsproxy или копирует встроенный, если его нет. progress_cb(0..1, message)."""
        # Сначала проверяем встроенный бинарник (из дистрибутива PyInstaller или app/bin)
        bundled_candidates = [
            Path(getattr(sys, "_MEIPASS", "")) / "app" / "bin" / "dnsproxy.exe",
            Path(__file__).resolve().parent / "bin" / "dnsproxy.exe",
        ]
        bundled_exe = next((p for p in bundled_candidates if p.is_file()), None)

        if not DNSPROXY_EXE.exists() and bundled_exe:
            try:
                logger.info("Копирование встроенного dnsproxy.exe из %s -> %s", bundled_exe, DNSPROXY_EXE)
                import shutil
                shutil.copy2(bundled_exe, DNSPROXY_EXE)
                if progress_cb:
                    progress_cb(1.0, "Встроенный модуль dnsproxy готов")
                return True
            except Exception as exc:
                logger.warning("Не удалось скопировать встроенный dnsproxy: %s", exc)

        if DNSPROXY_EXE.exists():
            logger.info("dnsproxy.exe найден: %s", DNSPROXY_EXE)
            return True

        try:
            if progress_cb:
                progress_cb(0.05, "Получение информации о последней версии…")

            req = Request(GITHUB_API, headers={"User-Agent": "DoH-DNS-Manager/1.0"})
            with urlopen(req, timeout=15) as resp:
                import json
                data = json.loads(resp.read())

            assets = data.get("assets", [])
            asset = next(
                (a for a in assets if "windows-amd64" in a["name"] and a["name"].endswith(".zip")),
                None,
            )
            if not asset:
                logger.error("Не найден windows-amd64 asset в релизе")
                return False

            download_url = asset["browser_download_url"]
            zip_path = APP_DIR / asset["name"]

            if progress_cb:
                progress_cb(0.10, f"Скачивание {asset['name']}…")

            logger.info("Скачиваю %s -> %s", download_url, zip_path)
            self._download_file(download_url, zip_path, progress_cb)

            if progress_cb:
                progress_cb(0.90, "Распаковка…")

            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.namelist():
                    if member.lower().endswith("dnsproxy.exe"):
                        data_bytes = zf.read(member)
                        DNSPROXY_EXE.write_bytes(data_bytes)
                        logger.info("Извлечён: %s -> %s", member, DNSPROXY_EXE)
                        break

            zip_path.unlink(missing_ok=True)

            if progress_cb:
                progress_cb(1.0, "Готово!")

            return DNSPROXY_EXE.exists()

        except Exception as exc:
            logger.error("Ошибка загрузки dnsproxy: %s", exc, exc_info=True)
            return False

    def _download_file(self, url: str, dest: Path, progress_cb=None):
        req = Request(url, headers={"User-Agent": "DoH-DNS-Manager/1.0"})
        with urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk = 65536
            with open(dest, "wb") as f:
                while True:
                    block = resp.read(chunk)
                    if not block:
                        break
                    f.write(block)
                    downloaded += len(block)
                    if progress_cb and total:
                        pct = 0.10 + 0.78 * downloaded / total
                        progress_cb(pct, f"Скачано {downloaded // 1024} / {total // 1024} КБ…")

    # ── Запуск / останов ──────────────────────────────────────────────────────

    @staticmethod
    def kill_all_dnsproxy():
        """Принудительно завершает любые висящие процессы dnsproxy.exe и освобождает порт 53."""
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/IM", "dnsproxy.exe"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            time.sleep(0.3)
        except Exception as exc:
            logger.warning("kill_all_dnsproxy: %s", exc)

    @staticmethod
    def verify_dns(server: str = "127.0.0.1", port: int = 53, domain: str = "xbox.com", timeout: float = 1.5) -> bool:
        """Проверяет работоспособность DNS-сервера через UDP-запрос."""
        try:
            import struct
            header = struct.pack("!HHHHHH", 0x4321, 0x0100, 1, 0, 0, 0)
            qname = b"".join(bytes([len(p)]) + p.encode() for p in domain.split(".")) + b"\x00"
            packet = header + qname + struct.pack("!HH", 1, 1)
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            try:
                sock.sendto(packet, (server, port))
                data, _ = sock.recvfrom(512)
                return len(data) >= 12
            finally:
                sock.close()
        except Exception as exc:
            logger.debug("verify_dns failed: %s", exc)
            return False

    def start(self, settings: dict) -> bool:
        with self._lock:
            # 1. Освобождаем порт 53 от любых старых висящих процессов dnsproxy
            self.kill_all_dnsproxy()

            if not DNSPROXY_EXE.exists():
                logger.error("dnsproxy.exe не найден!")
                return False

            profile = get_active_profile(settings)
            listen_addr = str(settings.get("listen_addr", "127.0.0.1"))
            listen_port = str(settings.get("listen_port", 53))
            log_file = APP_DIR / "dnsproxy.log"

            # Очищаем старый лог перед новым запуском
            try:
                log_file.unlink(missing_ok=True)
            except Exception:
                pass

            upstream_url = profile["url"]
            raw_boot = profile.get("bootstrap", "").strip() or "111.88.96.54, 111.88.96.55, 1.1.1.1"
            bootstraps = [b.strip() for b in raw_boot.replace(",", " ").split() if b.strip()]
            if not bootstraps:
                bootstraps = ["111.88.96.54", "111.88.96.55", "1.1.1.1"]

            cmd = [
                str(DNSPROXY_EXE),
                "-l", listen_addr,
                "-p", listen_port,
                "-u", upstream_url,
            ]
            for b in bootstraps:
                cmd.extend(["-b", b])

            cmd.extend([
                "--cache",
                "--cache-optimistic",
                "-o", str(log_file),
            ])

            if settings.get("verbose_logging", True):
                cmd.append("-v")

            logger.info("Запуск dnsproxy: %s", " ".join(cmd))
            try:
                # ВАЖНО: используем DEVNULL вместо PIPE!
                # dnsproxy пишет все логи в файл через -o. Использование PIPE без непрерывного чтения
                # приводило к переполнению буфера ОС Windows (4KB-64KB) и зависанию DNS!
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )

                # Проверяем старт процесса в течение 1.5 секунд
                for _ in range(15):
                    time.sleep(0.1)
                    if self._proc.poll() is not None:
                        details = ""
                        if log_file.exists():
                            try:
                                details = log_file.read_text(encoding="utf-8", errors="replace").strip()
                            except Exception:
                                pass
                        logger.error("dnsproxy завершился сразу! Код=%s, лог: %s", self._proc.returncode, details)
                        return False

                # Проверяем реальный отклик на DNS-порт
                if self.verify_dns(listen_addr, int(listen_port)):
                    logger.info("dnsproxy запущен и успешно отвечает на запросы, PID=%s", self._proc.pid)
                else:
                    logger.warning("dnsproxy PID=%s запущен, ожидает первого сетевого отклика", self._proc.pid)

                return True
            except Exception as exc:
                logger.error("Ошибка запуска dnsproxy: %s", exc, exc_info=True)
                return False

    def stop(self) -> None:
        with self._lock:
            if self._proc is not None:
                try:
                    if self._proc.poll() is None:
                        self._proc.terminate()
                        try:
                            self._proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            self._proc.kill()
                    logger.info("dnsproxy остановлен")
                except Exception as exc:
                    logger.warning("Ошибка при остановке dnsproxy: %s", exc)
                finally:
                    self._proc = None
            # Гарантированное освобождение порта 53
            self.kill_all_dnsproxy()

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None



# ----------------------------------------------------------------─────────────
# Управление сетевым адаптером
# ----------------------------------------------------------------─────────────

class NetworkManager:
    """Изменяет DNS-настройки активного сетевого адаптера Windows."""

    def __init__(self):
        self._original_dns: list[str] = []
        self._adapter_name: str = ""
        self._original_dhcp: bool = True
        self._backup_file = APP_DIR / "adapter_backup.json"
        self._lock = threading.Lock()

    # ── Определение активного адаптера ────────────────────────────────────────

    def get_active_adapter(self) -> tuple[str, int] | tuple[None, None]:
        """Возвращает (alias, interface_index) активного адаптера с маршрутом 0.0.0.0/0."""
        try:
            result = subprocess.run(
                ["powershell", "-NonInteractive", "-Command",
                 "Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
                 "Sort-Object RouteMetric | "
                 "Select-Object -First 1 InterfaceAlias,InterfaceIndex | "
                 "ConvertTo-Json"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json
                info = json.loads(result.stdout.strip())
                return info["InterfaceAlias"], int(info["InterfaceIndex"])
        except Exception as exc:
            logger.debug("get_active_adapter via NetRoute failed: %s", exc)

        # Fallback: первый поднятый физический адаптер
        try:
            result = subprocess.run(
                ["powershell", "-NonInteractive", "-Command",
                 "Get-NetAdapter | Where-Object Status -eq 'Up' | "
                 "Select-Object -First 1 Name,InterfaceIndex | ConvertTo-Json"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json
                info = json.loads(result.stdout.strip())
                return info["Name"], int(info["InterfaceIndex"])
        except Exception as exc:
            logger.error("get_active_adapter fallback error: %s", exc)

        return None, None

    def get_current_dns(self, interface_index: int) -> list[str]:
        try:
            result = subprocess.run(
                ["powershell", "-NonInteractive", "-Command",
                 f"(Get-DnsClientServerAddress -InterfaceIndex {interface_index} "
                 f"-AddressFamily IPv4).ServerAddresses | ConvertTo-Json"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return []
            import json
            raw = result.stdout.strip()
            parsed = json.loads(raw)
            if isinstance(parsed, str):
                return [parsed]
            return list(parsed)
        except Exception as exc:
            logger.error("get_current_dns: %s", exc, exc_info=True)
            return []

    def _is_dhcp(self, adapter_name: str) -> bool:
        """Проверяет, используется ли DHCP для DNS на данном адаптере."""
        try:
            result = subprocess.run(
                ["netsh", "interface", "ip", "show", "dns", f'name="{adapter_name}"'],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # В русской и английской Windows строка содержит "DHCP"
            return "dhcp" in result.stdout.lower() or "dhcp" in result.stderr.lower()
        except Exception:
            return True

    # ── Включение ─────────────────────────────────────────────────────────────

    def apply_dns(self, dns_ip: str = "127.0.0.1") -> bool:
        with self._lock:
            adapter_name, iface_idx = self.get_active_adapter()
            if not adapter_name:
                logger.error("Не удалось определить активный адаптер")
                return False

            self._adapter_name = adapter_name
            raw_dns = self.get_current_dns(iface_idx)
            is_dhcp = self._is_dhcp(adapter_name)

            # ВАЖНО: фильтруем loopback / localhost адреса!
            # Ни при каких обстоятельствах 127.* не должно сохраняться как "исходный DNS",
            # иначе при restore_dns() система навсегда останется с DNS 127.0.0.1 и без интернета!
            clean_dns = [
                ip.strip() for ip in raw_dns
                if ip.strip() and not ip.strip().startswith("127.") and ip.strip() not in ("::1", "localhost")
            ]

            if not clean_dns:
                # Если в системе стоял 127.0.0.1 от предыдущего сбоя или DNS был пуст/DHCP:
                self._original_dhcp = True
                self._original_dns = []
            else:
                self._original_dhcp = is_dhcp
                self._original_dns = clean_dns

            logger.info(
                "Адаптер: %s | Исходный DNS (очищенный): %s | DHCP: %s",
                adapter_name, self._original_dns, self._original_dhcp,
            )

            # Сохраняем состояние адаптера в файл для устойчивости к аварийным завершениям
            self._save_backup(adapter_name, self._original_dhcp, self._original_dns)

            # Устанавливаем 127.0.0.1
            ok = self._set_static_dns(adapter_name, dns_ip)
            if ok:
                self._flush_dns()
            return ok

    def _set_static_dns(self, adapter_name: str, dns_ip: str) -> bool:
        try:
            r = subprocess.run(
                ["netsh", "interface", "ip", "set", "dns",
                 f'name="{adapter_name}"', "static", dns_ip],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                logger.info("DNS установлен на %s для %s", dns_ip, adapter_name)
                return True
            # Fallback: PowerShell Set-DnsClientServerAddress
            r2 = subprocess.run(
                ["powershell", "-NonInteractive", "-Command",
                 f'Set-DnsClientServerAddress -InterfaceAlias "{adapter_name}" '
                 f'-ServerAddresses ("{dns_ip}")'],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            ok2 = r2.returncode == 0
            if ok2:
                logger.info("DNS установлен (PS) на %s для %s", dns_ip, adapter_name)
            else:
                logger.error("Ошибка установки DNS: %s / %s", r.stderr, r2.stderr)
            return ok2
        except Exception as exc:
            logger.error("_set_static_dns: %s", exc, exc_info=True)
            return False

    # ── Выключение ────────────────────────────────────────────────────────────

    def restore_dns(self) -> bool:
        with self._lock:
            adapter_name = self._adapter_name
            if not adapter_name:
                adapter_name, _ = self.get_active_adapter()
                if not adapter_name:
                    return True

            # Защита: проверяем, что в self._original_dns нет адресов 127.*
            clean_dns = [
                ip.strip() for ip in self._original_dns
                if ip.strip() and not ip.strip().startswith("127.") and ip.strip() not in ("::1", "localhost")
            ]

            logger.info(
                "Восстановление DNS: адаптер=%s dhcp=%s clean_old=%s",
                adapter_name, self._original_dhcp, clean_dns,
            )

            if self._original_dhcp or not clean_dns:
                ok = self._set_dhcp_dns(adapter_name)
            else:
                ok = self._restore_static(adapter_name, clean_dns)

            self._flush_dns()
            self._adapter_name = ""
            self._original_dns = []
            self._remove_backup()
            return ok

    def _set_dhcp_dns(self, adapter_name: str) -> bool:
        try:
            # 1. Сброс через PowerShell
            r_ps = subprocess.run(
                ["powershell", "-NonInteractive", "-Command",
                 f'Set-DnsClientServerAddress -InterfaceAlias "{adapter_name}" -ResetServerAddresses'],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # 2. Сброс через netsh
            r_netsh = subprocess.run(
                ["netsh", "interface", "ip", "set", "dns",
                 f'name="{adapter_name}"', "dhcp"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            logger.info("DNS переведен в DHCP для %s (PS=%s, netsh=%s)", adapter_name, r_ps.returncode, r_netsh.returncode)
            return r_ps.returncode == 0 or r_netsh.returncode == 0
        except Exception as exc:
            logger.error("_set_dhcp_dns: %s", exc, exc_info=True)
            return False

    def _restore_static(self, adapter_name: str, servers: list[str]) -> bool:
        try:
            addrs = '","'.join(servers)
            r = subprocess.run(
                ["powershell", "-NonInteractive", "-Command",
                 f'Set-DnsClientServerAddress -InterfaceAlias "{adapter_name}" '
                 f'-ServerAddresses ("{addrs}")'],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                logger.info("Статический DNS восстановлен: %s для %s", servers, adapter_name)
                return True
            # Fallback netsh
            subprocess.run(
                ["netsh", "interface", "ip", "set", "dns",
                 f'name="{adapter_name}"', "static", servers[0]],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return True
        except Exception as exc:
            logger.error("_restore_static: %s", exc, exc_info=True)
            return False

    def _flush_dns(self):
        try:
            subprocess.run(
                ["ipconfig", "/flushdns"],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            logger.info("DNS-кэш Windows очищен")
        except Exception as exc:
            logger.warning("flushdns: %s", exc)

    # ── Аварийное самовосстановление при старте ───────────────────────────────

    def check_and_repair_on_startup(self):
        """Если DNS остался на 127.0.0.1 от предыдущего аварийного завершения, лечим систему."""
        try:
            adapter_name, iface_idx = self.get_active_adapter()
            if not adapter_name or iface_idx is None:
                return

            current_dns = self.get_current_dns(iface_idx)
            is_stuck = any(ip.startswith("127.") for ip in current_dns)

            if is_stuck:
                logger.warning(
                    "ОБНАРУЖЕН ЗАВИСШИЙ DNS 127.0.0.1 на адаптере %s! Выполняем автолечение сети...",
                    adapter_name,
                )
                restored = False
                if self._backup_file.exists():
                    try:
                        import json
                        bdata = json.loads(self._backup_file.read_text(encoding="utf-8"))
                        saved_dns = [
                            ip for ip in bdata.get("dns", [])
                            if not ip.startswith("127.") and ip not in ("::1", "localhost")
                        ]
                        if saved_dns and not bdata.get("dhcp", True):
                            self._restore_static(adapter_name, saved_dns)
                            restored = True
                    except Exception as exc:
                        logger.warning("Не удалось прочесть adapter_backup.json: %s", exc)

                if not restored:
                    self._set_dhcp_dns(adapter_name)

                self._flush_dns()
                self._remove_backup()
                logger.info("Сеть успешно восстановлена в рабочее состояние.")
            else:
                self._remove_backup()
        except Exception as exc:
            logger.error("check_and_repair_on_startup error: %s", exc)

    def _save_backup(self, adapter_name: str, dhcp: bool, dns: list[str]):
        try:
            import json
            data = {"adapter": adapter_name, "dhcp": dhcp, "dns": dns}
            self._backup_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("save_backup error: %s", exc)

    def _remove_backup(self):
        try:
            self._backup_file.unlink(missing_ok=True)
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Автозагрузка Windows Task Scheduler (Наивысшие права / Без UAC)
# -----------------------------------------------------------------------------

TASK_NAME = "BuckshotDoH_Autostart"


def is_autostart_enabled() -> bool:
    """Проверяет, зарегистрирована ли задача автозапуска в Планировщике Windows."""
    try:
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            capture_output=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass

    # Fallback: проверка в реестре
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ,
        )
        try:
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            winreg.CloseKey(key)
    except Exception:
        pass

    return False


def set_autostart(enabled: bool, exe_path: str | None = None) -> tuple[bool, str]:
    """
    Настраивает автозапуск с Windows через Task Scheduler с наивысшими правами (HighestAvailable).
    Это исключает блокировку Windows и избавляет от всплывающих окон UAC при входе в систему.
    """
    # 1. Очищаем устаревшие записи из реестра HKCU\...\Run
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        for val_name in (APP_NAME, "Buckshot-DoH", "Buckshot-DoH-v2.1", "Buckshot-DoH-v2.2", "BuckshotDoH"):
            try:
                winreg.DeleteValue(key, val_name)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception:
        pass

    if not enabled:
        try:
            r = subprocess.run(
                ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                capture_output=True,
                encoding="cp866",
                errors="replace",
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            logger.info("Автозапуск через Планировщик удален: rc=%d", r.returncode)
            return True, "Автозапуск успешно отключен."
        except Exception as exc:
            logger.error("Ошибка при удалении задачи автозапуска: %s", exc)
            return False, f"Ошибка при удалении автозапуска: {exc}"

    # 2. Определение пути исполняемого файла и аргументов
    if exe_path:
        target_exe = exe_path
        target_args = "--autostart"
    elif getattr(sys, "frozen", False):
        target_exe = sys.executable
        target_args = "--autostart"
    else:
        target_exe = sys.executable
        target_args = f'"{os.path.abspath(sys.argv[0])}" --autostart'

    # 3. Получение SID текущего пользователя для интерактивной сессии
    user_sid = None
    try:
        r_sid = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r_sid.returncode == 0 and "," in r_sid.stdout:
            parts = [p.strip().strip('"') for p in r_sid.stdout.strip().split(",")]
            if len(parts) >= 2 and parts[1].startswith("S-1-"):
                user_sid = parts[1]
    except Exception as exc:
        logger.debug("whoami /user error: %s", exc)

    if user_sid:
        principal_xml = f"""    <Principal id="Author">
      <UserId>{user_sid}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>"""
    else:
        principal_xml = """    <Principal id="Author">
      <GroupId>S-1-5-32-544</GroupId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>"""

    # 4. XML-манифест задачи Task Scheduler
    # Включает:
    # - Задержку 3 сек для поднятия сетевого стека Windows
    # - Работу от батареи на ноутбуках
    # - Отсутствие лимита времени (PT0S)
    # - Наивысшие права без UAC (HighestAvailable)
    xml_task = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <URI>\\{TASK_NAME}</URI>
    <Description>Buckshot DoH Manager - Автозапуск защищенного DNS при входе в Windows</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT3S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
{principal_xml}
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{target_exe}</Command>
      <Arguments>{target_args}</Arguments>
    </Exec>
  </Actions>
</Task>"""

    xml_file = APP_DIR / "autostart_task.xml"
    try:
        xml_file.write_text(xml_task, encoding="utf-16")
        r = subprocess.run(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_file), "/F"],
            capture_output=True,
            encoding="cp866",
            errors="replace",
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r.returncode == 0:
            logger.info("Задача автозапуска %s успешно создана через XML", TASK_NAME)
            return True, "Автозапуск успешно настроен через Планировщик заданий (без UAC, задержка 3с, работа от батареи)."

        err_msg = r.stderr.strip() or r.stdout.strip()
        logger.warning("schtasks XML failed (%s), fallback to CLI...", err_msg)
        r_cli = subprocess.run(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", f'"{target_exe}" {target_args}',
             "/SC", "ONLOGON", "/RL", "HIGHEST", "/F"],
            capture_output=True,
            encoding="cp866",
            errors="replace",
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r_cli.returncode == 0:
            logger.info("Задача автозапуска %s создана через CLI schtasks", TASK_NAME)
            return True, "Автозапуск настроен через Планировщик заданий."

        return False, f"Ошибка создания задачи: {r_cli.stderr.strip() or err_msg}"
    except Exception as exc:
        logger.error("set_autostart failed: %s", exc, exc_info=True)
        return False, f"Сбой регистрации автозапуска: {exc}"
    finally:
        try:
            xml_file.unlink(missing_ok=True)
        except Exception:
            pass


# ----------------------------------------------------------------─────────────
# Пинг / проверка
# ----------------------------------------------------------------─────────────

def measure_ping(host: str = "xbox-dns.ru", port: int = 443, timeout: float = 2.5) -> float | None:
    """Измеряет TCP-latency до хоста (мс). Возвращает None при ошибке."""
    try:
        start = time.monotonic()
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return (time.monotonic() - start) * 1000
    except Exception:
        return None


def measure_dns_latency(server: str = "127.0.0.1", port: int = 53, domain: str = "xbox.com", timeout: float = 1.5) -> float | None:
    """Измеряет задержку реального DNS-запроса через UDP-сокет (мс)."""
    try:
        import struct
        header = struct.pack("!HHHHHH", 0x5678, 0x0100, 1, 0, 0, 0)
        qname = b"".join(bytes([len(p)]) + p.encode() for p in domain.split(".")) + b"\x00"
        packet = header + qname + struct.pack("!HH", 1, 1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        t0 = time.monotonic()
        try:
            sock.sendto(packet, (server, port))
            data, _ = sock.recvfrom(512)
            if len(data) >= 12:
                return (time.monotonic() - t0) * 1000
            return None
        finally:
            sock.close()
    except Exception:
        return None


# ----------------------------------------------------------------─────────────
# Комплексный Дебаг и Диагностика
# ----------------------------------------------------------------─────────────

class SystemDiagnostics:
    """Комплексная диагностика сети, процессов и DoH-шифрования."""

    @staticmethod
    def run_full_diagnostics(settings: dict, dnsproxy_mgr: DnsproxyManager, network_mgr: NetworkManager) -> list[str]:
        lines = []
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        lines.append("================================================================")
        lines.append(f"  BUCKSHOT DOH // ДИАГНОСТИЧЕСКИЙ АУДИТ СЕТИ  [{now_str}]")
        lines.append("================================================================")

        # 1. Права процесса
        try:
            is_adm = ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            is_adm = False
        adm_tag = "[OK] ПРАВА АДМИНИСТРАТОРА: ДА" if is_adm else "[FAIL] НЕТ ПРАВ АДМИНИСТРАТОРА (ТРЕБУЕТСЯ UAC)"
        lines.append(f"* СИСТЕМА: Windows | {adm_tag}")

        auto_on = is_autostart_enabled()
        auto_tag = f"[OK] АКТИВЕН (Планировщик: {TASK_NAME}, HighestAvailable)" if auto_on else "[ВЫКЛ] Отключен"
        lines.append(f"* АВТОЗАПУСК WINDOWS: {auto_tag}")

        # 2. Сетевой адаптер
        adapter, idx = network_mgr.get_active_adapter()
        curr_dns = []
        if adapter and idx is not None:
            curr_dns = network_mgr.get_current_dns(idx)
            is_dhcp = network_mgr._is_dhcp(adapter)
            lines.append(f"* АДАПТЕР: {adapter} (Interface Index {idx})")
            lines.append(f"    |-- ТЕКУЩИЙ DNS В СИСТЕМЕ: {curr_dns or '[ПУСТО / DHCP]'}")
            lines.append(f"    |-- РЕЖИМ DHCP: {'ДА (АВТОМАТИЧЕСКИ)' if is_dhcp else 'НЕТ (СТАТИЧЕСКИЙ)'}")
            if any(ip.startswith("127.") for ip in curr_dns):
                lines.append("    \\-- СТАТУС ПРИВЯЗКИ: [OK] ПЕРЕНАПРАВЛЕН НА ЛОКАЛЬНЫЙ ШИФРАТОР (127.0.0.1)")
            else:
                lines.append(f"    \\-- СТАТУС ПРИВЯЗКИ: [ИНФО] НАПРАВЛЕН НА РОУТЕР/ПРОВАЙДЕРА ({curr_dns})")
        else:
            lines.append("* АДАПТЕР: [WARN] Активный сетевой адаптер не определен!")

        # 3. Процесс dnsproxy
        is_running = dnsproxy_mgr.is_running()
        active_pid = dnsproxy_mgr._proc.pid if (is_running and dnsproxy_mgr._proc) else None

        # Проверяем наличие любых процессов dnsproxy в ОС
        system_pids = []
        try:
            r = subprocess.run(
                ["powershell", "-NonInteractive", "-Command",
                 "Get-Process dnsproxy -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"],
                capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW
            )
            if r.returncode == 0 and r.stdout.strip():
                system_pids = [int(p.strip()) for p in r.stdout.strip().splitlines() if p.strip().isdigit()]
        except Exception:
            pass

        if is_running and active_pid:
            lines.append(f"* DNSPROXY ПРОЦЕСС: [OK] ЗАПУЩЕН // PID = {active_pid}")
        elif system_pids:
            lines.append(f"* DNSPROXY ПРОЦЕСС: [WARN] ОБНАРУЖЕН РАНЕЕ ЗАПУЩЕННЫЙ DNSPROXY // PID = {system_pids}")
        else:
            lines.append("* DNSPROXY ПРОЦЕСС: [ВЫКЛ] Не активен")

        # 4. Локальный порт 53 (UDP)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.8)
            import struct
            hdr = struct.pack("!HHHHHH", 0x7777, 0x0100, 1, 0, 0, 0)
            qn = b"".join(bytes([len(x)]) + x.encode() for x in "xbox.com".split(".")) + b"\x00"
            t0 = time.monotonic()
            sock.sendto(hdr + qn + struct.pack("!HH", 1, 1), ("127.0.0.1", 53))
            try:
                data, _ = sock.recvfrom(512)
                lat = (time.monotonic() - t0) * 1000
                lines.append(f"* ЛОКАЛЬНЫЙ СОКЕТ 127.0.0.1:53: [OK] ОТВЕЧАЕТ // Задержка: {lat:.1f} мс")
            except socket.timeout:
                if is_running:
                    lines.append("* ЛОКАЛЬНЫЙ СОКЕТ 127.0.0.1:53: [WARN] Таймаут ответа (процесс запущен, но не ответил)")
                else:
                    lines.append("* ЛОКАЛЬНЫЙ СОКЕТ 127.0.0.1:53: [СВОБОДЕН] Готов к запуску")
            finally:
                sock.close()
        except Exception as exc:
            lines.append(f"* ЛОКАЛЬНЫЙ СОКЕТ: [FAIL] Ошибка сокета: {exc}")

        # 5. DoH Upstream проверка
        profile = get_active_profile(settings)
        doh_url = profile["url"]
        lines.append(f"* ЦЕЛЕВОЙ ШИФРОВАЛЬНЫЙ УЗЕЛ: {profile['name']} // {doh_url}")
        try:
            import urllib.request
            import ssl
            ctx = ssl.create_default_context()
            req = urllib.request.Request(doh_url, headers={"User-Agent": "dnsproxy-diagnostic"})
            t0 = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=3.5, context=ctx) as resp:
                    lat = (time.monotonic() - t0) * 1000
                    lines.append(f"    |-- HTTPS TLS СОЕДИНЕНИЕ: [OK] Доступен // HTTP {resp.status} // {lat:.1f} мс")
            except urllib.error.HTTPError as he:
                lat = (time.monotonic() - t0) * 1000
                lines.append(f"    |-- HTTPS TLS СОЕДИНЕНИЕ: [OK] Доступен // HTTP {he.code} // {lat:.1f} мс")
        except Exception as exc:
            lines.append(f"    |-- HTTPS TLS СОЕДИНЕНИЕ: [FAIL] Ошибка подключения к DoH: {exc}")

        # 6. Bootstrap DNS серверы
        raw_boot = profile.get("bootstrap", "").strip() or "111.88.96.54, 111.88.96.55, 1.1.1.1"
        boot_list = [b.strip() for b in raw_boot.replace(",", " ").split() if b.strip()]
        for b_ip in boot_list[:3]:
            b_ping = measure_ping(b_ip, port=53, timeout=1.5)
            if b_ping is None:
                b_ping = measure_ping(b_ip, port=443, timeout=1.5)
            status = f"{b_ping:.1f} мс" if b_ping is not None else "НЕТ ОТВЕТА (ICMP/TCP)"
            lines.append(f"    |-- BOOTSTRAP DNS {b_ip}: {status}")

        # 7. Сквозное разрешение доменов
        test_domains = ["xbox.com", "login.live.com", "chatgpt.com", "notion.so", "google.com"]
        lines.append("* СКВОЗНОЕ ТЕСТИРОВАНИЕ РЕЗОЛВИНГА:")
        for dom in test_domains:
            try:
                t0 = time.monotonic()
                resolved_ip = socket.gethostbyname(dom)
                lat = (time.monotonic() - t0) * 1000
                lines.append(f"    |-- {dom} -> {resolved_ip} ({lat:.1f} мс) [OK]")
            except Exception as exc:
                lines.append(f"    |-- {dom} -> [ОШИБКА РЕЗОЛВИНГА: {exc}]")

        # 8. Итоговый статус
        lines.append("----------------------------------------------------------------")
        has_127 = any(ip.startswith("127.") for ip in curr_dns)
        has_proc = is_running or bool(system_pids)

        if has_proc and has_127:
            lines.append("  ИТОГ: [OK] ШИФРОВАНИЕ DOH АКТИВНО. ВЕСЬ DNS-ТРАФИК ЗАЩИЩЕН.")
        elif not has_proc and not has_127:
            lines.append("  ИТОГ: [OFF] ШИФРОВАНИЕ ВЫКЛЮЧЕНО. СИСТЕМА РАБОТАЕТ В ШТАТНОМ РЕЖИМЕ (DHCP/ПРОВАЙДЕР).")
        elif has_proc and not has_127:
            lines.append("  ИТОГ: [WARN] ПРОЦЕСС ЗАПУЩЕН, НО DNS СЕТЕВОЙ КАРТЫ ЕЩЕ НЕ ПЕРЕКЛЮЧЕН НА 127.0.0.1.")
        else:
            lines.append("  ИТОГ: [ОШИБКА] DNS СЕТИ УКАЗЫВАЕТ НА 127.0.0.1, НО DNSPROXY НЕ РАБОТАЕТ! НАЖМИТЕ 'АВАРИЙНЫЙ СБРОС (DHCP)'.")
        lines.append("================================================================\n")
        return lines

    @staticmethod
    def emergency_repair_network(network_mgr: NetworkManager, dnsproxy_mgr: DnsproxyManager) -> list[str]:
        log = []
        log.append("[СБРОС СЕТИ] Запуск полной очистки сетевых настроек...")
        # 1. Останавливаем dnsproxy
        try:
            dnsproxy_mgr.stop()
            dnsproxy_mgr.kill_all_dnsproxy()
            log.append("[OK] Все фоновые процессы dnsproxy.exe принудительно завершены.")
        except Exception as e:
            log.append(f"[WARN] Ошибка завершения процессов: {e}")

        # 2. Сбрасываем все активные адаптеры в DHCP
        try:
            ps_script = (
                "Get-NetAdapter | Where-Object Status -eq 'Up' | "
                "ForEach-Object { "
                "  Set-DnsClientServerAddress -InterfaceAlias $_.Name -ResetServerAddresses; "
                "  $_.Name "
                "}"
            )
            r = subprocess.run(
                ["powershell", "-NonInteractive", "-Command", ps_script],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in r.stdout.strip().splitlines():
                if line.strip():
                    subprocess.run(
                        ["netsh", "interface", "ip", "set", "dns", f'name="{line.strip()}"', "dhcp"],
                        capture_output=True, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    log.append(f"[OK] Адаптер '{line.strip()}' переведен в режим DHCP (DNS от роутера).")
        except Exception as e:
            log.append(f"[FAIL] Ошибка сброса адаптеров через PowerShell: {e}")

        # 3. Сбрасываем DNS-кэш Windows
        try:
            subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            log.append("[OK] DNS-кэш Windows очищен (ipconfig /flushdns).")
        except Exception as e:
            log.append(f"[WARN] Сброс кэша: {e}")

        # 4. Удаляем файл бэкапа
        try:
            (APP_DIR / "adapter_backup.json").unlink(missing_ok=True)
            log.append("[OK] Резервный файл конфигурации очищен.")
        except Exception:
            pass

        log.append("[ГОТОВО] Сеть полностью восстановлена в заводское состояние (DHCP).")
        return log


import atexit
atexit.register(DnsproxyManager.kill_all_dnsproxy)



