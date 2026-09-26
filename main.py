# -*- coding: utf-8 -*-
"""
DoH DNS Manager – точка входа
Запрашивает UAC-повышение, если нет прав админа, затем запускает GUI.
"""

import sys
import os
import ctypes
import subprocess


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def elevate():
    """Перезапускает процесс с правами администратора через ShellExecute."""
    if getattr(sys, "frozen", False):
        executable = sys.executable
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        executable = sys.executable
        params = f'"{sys.argv[0]}" ' + " ".join(f'"{a}"' for a in sys.argv[1:])

    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", executable, params.strip(), None, 1
    )
    sys.exit(0)


if __name__ == "__main__":
    if not is_admin():
        elevate()
    else:
        try:
            # Импортируем GUI только после получения прав, чтобы не грузить tkinter дважды
            from app.gui import DNSManagerApp
            is_autostart = "--autostart" in sys.argv or "--minimized" in sys.argv
            app = DNSManagerApp(autostart=is_autostart)
            app.run()
        except Exception as exc:
            import traceback
            from app.config import APP_DIR
            crash_file = APP_DIR / "crash.log"
            try:
                crash_file.write_text(traceback.format_exc(), encoding="utf-8")
            except Exception:
                pass
            ctypes.windll.user32.MessageBoxW(
                0,
                f"Критическая ошибка запуска:\n\n{exc}\n\nЛог сохранен в: {crash_file}",
                "BUCKSHOT // SYSTEM FAILURE",
                0x10,
            )
            sys.exit(1)

