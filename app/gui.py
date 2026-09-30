# -*- coding: utf-8 -*-
"""
gui.py — Главный интерфейс DoH DNS Manager в стиле Buckshot Roulette
(Ретро-индустриальный стиль: CRT-монитор с осциллографом, скайлайны, гильзы, терминал)
"""

import sys
import math
import random
import threading
import webbrowser
import time
import logging
from io import BytesIO
from tkinter import StringVar, BooleanVar, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk
import pystray

from app.config import (
    APP_NAME, APP_VERSION, DOH_PROFILES, APP_DIR,
    TG_CHANNEL, TG_SUPPORT_BOT, SITE_URL, DONATION_URL,
    GEOHIDE_SITE, GEOHIDE_TG,
    load_settings, save_settings, get_active_profile,
)
from app.dns_backend import (
    DnsproxyManager, NetworkManager,
    set_autostart, is_autostart_enabled,
    get_canonical_exe_path, get_autostart_target,
    measure_ping, measure_dns_latency,
    SystemDiagnostics,
)
from app.icons import make_tray_icon, make_app_icon

logger = logging.getLogger("gui")

# ─── Тема и Палитра Buckshot Roulette ──────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# Ржавое железо, оружейная сталь, ЭЛТ-фосфор, кровь и латунь
C_BG            = "#0c0d0b"   # Глубокий темный фон бункера
C_PANEL         = "#141512"   # Корпус аппаратуры
C_CARD          = "#181a16"   # Внутренние модули
C_BORDER        = "#3a372f"   # Потертый металл рамки
C_BORDER_MUTED  = "#282620"   # Тонкие разделители

# CRT ЭЛТ-экран
C_CRT_GLASS     = "#070a07"   # Стекло кинескопа
C_CRT_BORDER    = "#232e20"   # Фаска монитора
C_CRT_GRID      = "#0d140d"   # Сетка осциллографа
C_CRT_OFF       = "#4a4030"   # Цвет луча при отключении (тусклая латунь)
C_CRT_ON        = "#38ef7d"   # Радиоактивный фосфорный зеленый (Live)
C_CRT_GLOW      = "#166534"   # Свечение вокруг луча

# Текстовые акценты
C_TEXT_MAIN     = "#ece2d0"   # Старая бумага / состаренная кость
C_TEXT_MUTED    = "#827b6e"   # Пепел / металлическая пыль
C_AMBER         = "#f59e0b"   # Янтарный свет ЭЛТ / лампы
C_AMBER_BRIGHT  = "#fbbf24"   # Яркий янтарь
C_GREEN         = "#22c55e"   # Зеленый индикатор
C_RED           = "#dc2626"   # Кровь / гильза Live-патрона
C_BRASS         = "#d97706"   # Латунь

FONT_TERM       = "Consolas"


# ─────────────────────────────────────────────────────────────────────────────
# Виджет: ЭЛТ-Монитор (CRT Oscilloscope Screen)
# ─────────────────────────────────────────────────────────────────────────────

class CRTMonitor(ctk.CTkCanvas):
    """
    ЭЛТ-дисплей с кинескопом: сетка, развертка, осциллограмма сигнала и статус.
    В стиле диагностического монитора из Buckshot Roulette.
    """

    WIDTH = 420
    HEIGHT = 160
    FPS = 30

    def __init__(self, master, **kwargs):
        super().__init__(
            master,
            width=self.WIDTH,
            height=self.HEIGHT,
            bg=C_CRT_GLASS,
            highlightbackground=C_CRT_BORDER,
            highlightthickness=2,
            **kwargs,
        )
        self._active = False
        self._phase = 0.0
        self._running = True
        self._anim_id = None
        self._upstream_text = "XBOX-DNS.RU"
        self._flicker = 1.0

        self._draw_frame()
        self._tick()

    def set_active(self, active: bool):
        self._active = active
        self._draw_frame()

    def set_upstream(self, text: str):
        self._upstream_text = text.upper()
        self._draw_frame()

    def _draw_frame(self):
        self.delete("all")
        w, h = self.WIDTH, self.HEIGHT

        # 1. Горизонтальные линии развертки кинескопа (CRT Scanlines на фоне)
        for y in range(0, h, 3):
            self.create_line(0, y, w, y, fill="#040704", width=1)

        # 2. Тонкая координатная сетка осциллографа
        grid_step = 22
        for x in range(0, w, grid_step):
            self.create_line(x, 0, x, h, fill="#0d160d", width=1)
        for y in range(0, h, grid_step):
            self.create_line(0, y, w, y, fill="#0d160d", width=1)

        # Центральное перекрестье
        cx, cy = w // 2, h // 2
        self.create_line(cx - 16, cy, cx + 16, cy, fill="#182c18", width=1)
        self.create_line(cx, cy - 16, cx, cy + 16, fill="#182c18", width=1)

        # 3. Осциллограмма (луч кинескопа) — рисуется ПОВЕРХ сетки, гладкая и четкая
        points = []
        if self._active:
            # Живая пульсирующая синусоида зашифрованного канала
            for x in range(8, w - 8, 3):
                nx = (x - 8) / (w - 16)
                harm1 = math.sin(nx * 12.0 + self._phase * 1.5) * 18.0
                harm2 = math.cos(nx * 24.0 - self._phase * 2.0) * 6.0
                noise = random.uniform(-0.8, 0.8)
                envelope = math.sin(nx * math.pi)
                y = cy + (harm1 + harm2 + noise) * envelope
                points.extend([x, y])
            if len(points) >= 4:
                self.create_line(points, fill="#14532d", width=5, smooth=True)
                self.create_line(points, fill="#4ade80", width=2, smooth=True)
        else:
            # Разряженный / плоский сигнал (flatline)
            for x in range(8, w - 8, 4):
                nx = (x - 8) / (w - 16)
                noise = random.uniform(-0.6, 0.6)
                pulse = math.sin(nx * 3.0 + self._phase * 0.3) * 1.4
                y = cy + noise + pulse
                points.extend([x, y])
            if len(points) >= 4:
                self.create_line(points, fill="#241e17", width=3, smooth=True)
                self.create_line(points, fill="#6b5b45", width=2, smooth=True)

        # 4. Текстовые данные OSD — рисуются ПОВЕРХ всего с подложкой для четкости
        # Подложки под текст
        self.create_rectangle(10, 8, 230, 38, fill="#050805", outline="")
        self.create_rectangle(w - 210, 8, w - 10, 26, fill="#050805", outline="")
        self.create_rectangle(10, h - 26, 220, h - 8, fill="#050805", outline="")
        self.create_rectangle(w - 180, h - 26, w - 10, h - 8, fill="#050805", outline="")

        if self._active:
            status_text = "[ LIVE // ЗАЩИЩЕНО ]"
            status_color = "#4ade80"
            sub_text = "БИНД: 127.0.0.1:53"
            sub_color = "#86efac"
            cipher_label = "ПРОТОКОЛ: DOH (TLS 1.3)"
            cipher_color = "#f59e0b"
        else:
            status_text = "[ BLANK // ОТКЛЮЧЕНО ]"
            status_color = "#a89f91"
            sub_text = "ОТКРЫТЫЙ DNS ПРОВАЙДЕРА"
            sub_color = "#787163"
            cipher_label = "ПРОТОКОЛ: RAW UDP/53"
            cipher_color = "#6b6355"

        # Левый верхний угол: статус
        self.create_text(
            14, 15,
            text=status_text,
            fill=status_color,
            font=(FONT_TERM, 11, "bold"),
            anchor="w",
        )
        self.create_text(
            14, 29,
            text=sub_text,
            fill=sub_color,
            font=(FONT_TERM, 9),
            anchor="w",
        )

        # Правый верхний угол: шифрование
        self.create_text(
            w - 14, 16,
            text=cipher_label,
            fill=cipher_color,
            font=(FONT_TERM, 9, "bold"),
            anchor="e",
        )

        # Левый нижний угол: целевой узел
        self.create_text(
            14, h - 16,
            text=f"УЗЕЛ: [ {self._upstream_text} ]",
            fill=C_TEXT_MAIN,
            font=(FONT_TERM, 9, "bold"),
            anchor="w",
        )

        # Правый нижний угол: канал
        self.create_text(
            w - 14, h - 16,
            text="КАНАЛ: 53/TCP+UDP",
            fill=C_TEXT_MUTED,
            font=(FONT_TERM, 9),
            anchor="e",
        )

        # 5. Угловые скобки кинескопа
        corner_len = 8
        self.create_line(4, 4, 4 + corner_len, 4, fill=C_BORDER, width=2)
        self.create_line(4, 4, 4, 4 + corner_len, fill=C_BORDER, width=2)
        self.create_line(w - 4, 4, w - 4 - corner_len, 4, fill=C_BORDER, width=2)
        self.create_line(w - 4, 4, w - 4, 4 + corner_len, fill=C_BORDER, width=2)
        self.create_line(4, h - 4, 4 + corner_len, h - 4, fill=C_BORDER, width=2)
        self.create_line(4, h - 4, 4, h - 4 - corner_len, fill=C_BORDER, width=2)
        self.create_line(w - 4, h - 4, w - 4 - corner_len, h - 4, fill=C_BORDER, width=2)
        self.create_line(w - 4, h - 4, w - 4, h - 4 - corner_len, fill=C_BORDER, width=2)

    def _tick(self):
        if not self._running:
            return
        self._phase += 0.22 if self._active else 0.08
        self._draw_frame()
        self._anim_id = self.after(int(1000 / self.FPS), self._tick)

    def destroy(self):
        self._running = False
        if self._anim_id:
            try:
                self.after_cancel(self._anim_id)
            except Exception:
                pass
        super().destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Виджет: Тяжелая индустриальная кнопка-тумблер
# ─────────────────────────────────────────────────────────────────────────────

class IndustrialButton(ctk.CTkButton):
    """Массивная кнопка в стиле пультов управления Buckshot Roulette."""

    def __init__(self, master, callback, **kwargs):
        self._active = False
        self._cb = callback
        super().__init__(
            master,
            text="[ ▶ АКТИВИРОВАТЬ ЗАЩИТУ ]",
            font=ctk.CTkFont(family=FONT_TERM, size=15, weight="bold"),
            fg_color="#b45309",         # Янтарная латунь
            hover_color="#d97706",
            text_color="#fffbeb",
            border_color="#f59e0b",
            border_width=2,
            corner_radius=4,            # Грубые рубленые углы
            height=54,
            command=self._on_click,
            **kwargs,
        )

    def _on_click(self):
        self._cb()

    def set_active(self, active: bool):
        self._active = active
        if active:
            self.configure(
                text="[ ■ СБРОСИТЬ СОЕДИНЕНИЕ ]",
                fg_color="#991b1b",     # Кровь / патрон
                hover_color="#b91c1c",
                border_color="#ef4444",
                text_color="#fee2e2",
            )
        else:
            self.configure(
                text="[ ▶ АКТИВИРОВАТЬ ЗАЩИТУ ]",
                fg_color="#b45309",
                hover_color="#d97706",
                border_color="#f59e0b",
                text_color="#fffbeb",
            )

    def set_loading(self, loading: bool):
        if loading:
            self.configure(
                text="[ ⏳ КАЛИБРОВКА КАНАЛА... ]",
                state="disabled",
                fg_color="#2b2822",
                border_color="#453e34",
                text_color=C_TEXT_MUTED,
            )
        else:
            self.set_active(self._active)
            self.configure(state="normal")


# ─────────────────────────────────────────────────────────────────────────────
# Главное Окно Приложения
# ─────────────────────────────────────────────────────────────────────────────

class DNSManagerApp:
    WIDTH = 540
    HEIGHT = 760

    def __init__(self, autostart: bool = False):
        self.settings = load_settings()
        self._is_autostart = autostart
        self._active = False
        self._shutdown_requested = False
        self._dnsproxy = DnsproxyManager()
        self._network = NetworkManager()
        # Гарантированная очистка любых зависших процессов от прошлых сессий
        self._dnsproxy.kill_all_dnsproxy()
        # Автоматическое самовосстановление сети при обнаружении сбоя от предыдущего запуска
        self._network.check_and_repair_on_startup()

        # Автоматическая синхронизация и самовосстановление автозапуска с Windows
        if self.settings.get("autostart", False):
            canonical_target = get_canonical_exe_path() if getattr(sys, "frozen", False) else sys.executable
            current_target = get_autostart_target()
            needs_repair = False
            if not is_autostart_enabled():
                needs_repair = True
            elif current_target and os.path.normpath(current_target).lower() != os.path.normpath(canonical_target).lower():
                needs_repair = True

            if needs_repair:
                logger.info("Автозапуск включен в настройках: выполняем самовосстановление задачи в Планировщике...")
                ok, msg = set_autostart(True)
                logger.info("Результат автовосстановления автозапуска: %s (%s)", ok, msg)
        else:
            if is_autostart_enabled():
                set_autostart(False)

        self._tray: pystray.Icon | None = None
        self._tray_thread: threading.Thread | None = None
        self._ping_thread: threading.Thread | None = None
        self._ping_running = False

        self._build_window()
        self._build_ui()
        self._start_ping_loop()

        # Если запущен через автозапуск с Windows и включено сворачивание в трей
        if self._is_autostart and self.settings.get("minimize_to_tray", True):
            self.root.withdraw()
            self._tray_thread = threading.Thread(target=self._start_tray, daemon=True)
            self._tray_thread.start()

        # Автоматическая активация шифрования при старте
        if self.settings.get("auto_activate_on_startup", True) or self._is_autostart:
            def _auto_start_doh():
                time.sleep(1.5)
                if not self._active and not self._shutdown_requested:
                    self._safe_call(lambda: self._append_debug_line("[AUTOSTART] Запуск фонового шифрования DNS..."))
                    self._do_activate()
            threading.Thread(target=_auto_start_doh, daemon=True).start()

    # ── Окно ──────────────────────────────────────────────────────────────────

    def _build_window(self):
        self.root = ctk.CTk()
        self.root.title("BUCKSHOT // DOH CIPHER")
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.root.resizable(False, False)
        self.root.configure(fg_color=C_BG)

        # Иконка окна
        app_img = make_app_icon(256)
        ico_bytes = BytesIO()
        app_img.save(ico_bytes, format="ICO", sizes=[(256, 256), (64, 64), (32, 32)])
        ico_bytes.seek(0)
        self._tk_icon = ImageTk.PhotoImage(app_img.resize((32, 32)))
        try:
            self.root.iconphoto(True, self._tk_icon)
        except Exception:
            pass

        self.root.protocol("WM_DELETE_WINDOW", self._on_close_btn)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # 1. Верхняя панель в стиле металлической таблички
        header = ctk.CTkFrame(
            self.root,
            fg_color=C_PANEL,
            corner_radius=0,
            height=54,
            border_color=C_BORDER,
            border_width=1,
        )
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        # Левая часть заголовка
        head_left = ctk.CTkFrame(header, fg_color="transparent")
        head_left.pack(side="left", padx=16, pady=10)

        ctk.CTkLabel(
            head_left,
            text="[ ⌖ BUCKSHOT DOH ]",
            font=ctk.CTkFont(family=FONT_TERM, size=15, weight="bold"),
            text_color=C_TEXT_MAIN,
        ).pack(side="left")

        ctk.CTkLabel(
            head_left,
            text=" // SECURE RELAY",
            font=ctk.CTkFont(family=FONT_TERM, size=12),
            text_color=C_AMBER,
        ).pack(side="left")

        # Версия
        ctk.CTkLabel(
            header,
            text=f"SYS_REV {APP_VERSION}",
            font=ctk.CTkFont(family=FONT_TERM, size=11),
            text_color=C_TEXT_MUTED,
        ).pack(side="right", padx=16)

        # 2. Вкладки терминала
        self._tabs = ctk.CTkTabview(
            self.root,
            fg_color=C_BG,
            segmented_button_fg_color=C_PANEL,
            segmented_button_selected_color="#2b261f",
            segmented_button_selected_hover_color="#3d372b",
            segmented_button_unselected_color=C_PANEL,
            segmented_button_unselected_hover_color=C_CARD,
            text_color=C_TEXT_MAIN,
            corner_radius=4,
        )
        self._tabs.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        self._build_main_tab(self._tabs.add(" [ КОНСОЛЬ ] "))
        self._build_settings_tab(self._tabs.add(" [ НАСТРОЙКИ ] "))
        self._build_debug_tab(self._tabs.add(" [ ДИАГНОСТИКА ] "))

    # ── Главная Вкладка ───────────────────────────────────────────────────────

    def _build_main_tab(self, tab):
        tab.configure(fg_color=C_BG)

        # Декоративная рамка для ЭЛТ-монитора
        screen_frame = ctk.CTkFrame(
            tab,
            fg_color=C_CARD,
            corner_radius=6,
            border_color=C_BORDER,
            border_width=2,
        )
        screen_frame.pack(fill="x", padx=14, pady=(12, 10))

        # Заголовок блока ЭЛТ
        top_bar = ctk.CTkFrame(screen_frame, fg_color="transparent")
        top_bar.pack(fill="x", padx=12, pady=(8, 4))

        ctk.CTkLabel(
            top_bar,
            text="/// ЭЛТ-ОСЦИЛЛОГРАФ СЕТЕВОГО ТРАФИКА ///",
            font=ctk.CTkFont(family=FONT_TERM, size=10, weight="bold"),
            text_color=C_TEXT_MUTED,
        ).pack(side="left")

        self._power_indicator = ctk.CTkLabel(
            top_bar,
            text="[○ OFFLINE]",
            font=ctk.CTkFont(family=FONT_TERM, size=10, weight="bold"),
            text_color=C_TEXT_MUTED,
        )
        self._power_indicator.pack(side="right")

        # Сам ЭЛТ Монитор
        self._crt = CRTMonitor(screen_frame)
        self._crt.pack(padx=10, pady=(4, 10))
        self._crt.set_upstream(self._get_active_hostname())

        # Кнопка Переключения
        self._toggle_btn = IndustrialButton(tab, callback=self._on_toggle)
        self._toggle_btn.pack(fill="x", padx=14, pady=(6, 8))

        # Индикатор загрузки / скачивания dnsproxy
        self._progress_frame = ctk.CTkFrame(tab, fg_color="transparent")
        self._progress_frame.pack(fill="x", padx=14)

        self._progress_bar = ctk.CTkProgressBar(
            self._progress_frame,
            fg_color=C_PANEL,
            progress_color=C_AMBER,
            corner_radius=2,
            height=6,
        )
        self._progress_label = ctk.CTkLabel(
            self._progress_frame,
            text="",
            font=ctk.CTkFont(family=FONT_TERM, size=10),
            text_color=C_AMBER,
        )

        # Телеметрия и аппаратные показатели
        stats_box = ctk.CTkFrame(
            tab,
            fg_color=C_CARD,
            corner_radius=4,
            border_color=C_BORDER,
            border_width=1,
        )
        stats_box.pack(fill="x", padx=14, pady=(6, 8))

        # Заголовок телеметрии в виде металлической плашки
        stat_head = ctk.CTkFrame(stats_box, fg_color="#1a1c18", corner_radius=2, height=24)
        stat_head.pack(fill="x", padx=6, pady=(6, 6))
        stat_head.pack_propagate(False)
        ctk.CTkLabel(
            stat_head,
            text="/// АППАРАТНАЯ ТЕЛЕМЕТРИЯ СЕТЕВОГО ШЛЮЗА ///",
            font=ctk.CTkFont(family=FONT_TERM, size=10, weight="bold"),
            text_color=C_AMBER,
        ).pack(side="left", padx=8)

        self._ping_label = self._make_stat_line(
            stats_box, "ЗАДЕРЖКА СИГНАЛА (PING)", "—  мс"
        )
        self._make_divider(stats_box)

        self._dns_label = self._make_stat_line(
            stats_box, "ЛОКАЛЬНЫЙ ШЛЮЗ (BIND)", "127.0.0.1:53"
        )
        self._make_divider(stats_box)

        self._route_label = self._make_stat_line(
            stats_box, "АКТИВНЫЙ МАРШРУТ", "ИНИЦИАЛИЗАЦИЯ..."
        )

        # Отступ снизу блока телеметрии
        ctk.CTkFrame(stats_box, fg_color="transparent", height=4).pack()

        # Кнопка проверки в браузере (с гарантированным отступом снизу)
        ctk.CTkButton(
            tab,
            text="[ ⌖ ПРОВЕРИТЬ КАНАЛ СВЯЗИ (ТЕСТ) ]",
            font=ctk.CTkFont(family=FONT_TERM, size=12, weight="bold"),
            fg_color=C_PANEL,
            hover_color="#2b2820",
            text_color=C_AMBER,
            border_color=C_BORDER,
            border_width=1,
            corner_radius=4,
            height=38,
            command=self._open_check_url,
        ).pack(fill="x", padx=14, pady=(4, 14))

        # Определяем начальный маршрут в фоне
        threading.Thread(target=self._init_route_display, daemon=True).start()

    def _init_route_display(self):
        alias, idx = self._network.get_active_adapter()
        text = f"{alias} (ID {idx})" if alias else "НЕ ОПРЕДЕЛЕН"
        self._safe_call(lambda: self._route_label.configure(text=text))

    def _make_stat_line(self, parent, label: str, value: str) -> ctk.CTkLabel:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=5)
        ctk.CTkLabel(
            row,
            text=f"• {label}",
            font=ctk.CTkFont(family=FONT_TERM, size=11),
            text_color=C_TEXT_MUTED,
        ).pack(side="left")
        val = ctk.CTkLabel(
            row,
            text=value,
            font=ctk.CTkFont(family=FONT_TERM, size=12, weight="bold"),
            text_color=C_TEXT_MAIN,
        )
        val.pack(side="right")
        return val

    def _make_divider(self, parent):
        ctk.CTkFrame(parent, fg_color=C_BORDER_MUTED, height=1).pack(fill="x", padx=14)

    # ── Вкладка Настроек ──────────────────────────────────────────────────────

    def _build_settings_tab(self, tab):
        tab.configure(fg_color=C_BG)

        scroll = ctk.CTkScrollableFrame(tab, fg_color=C_BG, corner_radius=0)
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        # ── Секция профилей ──────────────────────────────────────────────────
        self._section_banner(scroll, "ВЫБОР ЦЕЛЕВОГО ШИФРОВАЛЬНОГО УЗЛА")

        self._profile_var = ctk.IntVar(value=self.settings.get("profile_index", 0))

        profile_box = ctk.CTkFrame(
            scroll,
            fg_color=C_CARD,
            corner_radius=4,
            border_color=C_BORDER,
            border_width=1,
        )
        profile_box.pack(fill="x", padx=10, pady=(4, 10))

        for i, p in enumerate(DOH_PROFILES[:-1]):
            host = p['url'].replace('https://', '').split('/')[0]
            rb = ctk.CTkRadioButton(
                profile_box,
                text=f"[{i+1}] {p['name'].upper()} // {host} [IP: {p['bootstrap']}]",
                variable=self._profile_var,
                value=i,
                font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
                text_color=C_TEXT_MAIN,
                fg_color=C_AMBER,
                hover_color=C_BRASS,
                border_color=C_BORDER,
                command=self._on_profile_change,
            )
            rb.pack(anchor="w", padx=14, pady=6)

        # Кастомный
        custom_idx = len(DOH_PROFILES) - 1
        rb_custom = ctk.CTkRadioButton(
            profile_box,
            text=f"[{custom_idx+1}] РУЧНОЙ ВВОД (ПОЛЬЗОВАТЕЛЬСКИЙ СЕРВЕР)",
            variable=self._profile_var,
            value=custom_idx,
            font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
            text_color=C_TEXT_MAIN,
            fg_color=C_AMBER,
            hover_color=C_BRASS,
            border_color=C_BORDER,
            command=self._on_profile_change,
        )
        rb_custom.pack(anchor="w", padx=14, pady=6)

        # Поля кастомного профиля
        custom_inputs = ctk.CTkFrame(
            scroll,
            fg_color=C_PANEL,
            corner_radius=4,
            border_color=C_BORDER,
            border_width=1,
        )
        custom_inputs.pack(fill="x", padx=10, pady=(0, 12))

        self._custom_url_var = StringVar(value=self.settings.get("custom_url", ""))
        self._custom_boot_var = StringVar(value=self.settings.get("custom_bootstrap", ""))

        ctk.CTkLabel(
            custom_inputs,
            text="DOH ENDPOINT URL:",
            text_color=C_TEXT_MUTED,
            font=ctk.CTkFont(family=FONT_TERM, size=10, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(8, 2))

        ctk.CTkEntry(
            custom_inputs,
            textvariable=self._custom_url_var,
            placeholder_text="https://your-doh-server.com/dns-query",
            fg_color=C_CRT_GLASS,
            border_color=C_BORDER,
            text_color=C_AMBER_BRIGHT,
            font=ctk.CTkFont(family=FONT_TERM, size=11),
            corner_radius=2,
        ).pack(fill="x", padx=12, pady=(0, 6))

        ctk.CTkLabel(
            custom_inputs,
            text="BOOTSTRAP DNS IP:",
            text_color=C_TEXT_MUTED,
            font=ctk.CTkFont(family=FONT_TERM, size=10, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(2, 2))

        ctk.CTkEntry(
            custom_inputs,
            textvariable=self._custom_boot_var,
            placeholder_text="1.1.1.1",
            fg_color=C_CRT_GLASS,
            border_color=C_BORDER,
            text_color=C_AMBER_BRIGHT,
            font=ctk.CTkFont(family=FONT_TERM, size=11),
            corner_radius=2,
        ).pack(fill="x", padx=12, pady=(0, 10))

        # ── Секция параметров запуска ─────────────────────────────────────────
        self._section_banner(scroll, "СИСТЕМНЫЕ СЛУЖБЫ")

        opts_box = ctk.CTkFrame(
            scroll,
            fg_color=C_CARD,
            corner_radius=4,
            border_color=C_BORDER,
            border_width=1,
        )
        opts_box.pack(fill="x", padx=10, pady=(4, 12))

        self._autostart_var = BooleanVar(value=is_autostart_enabled())
        ctk.CTkSwitch(
            opts_box,
            text="АВТОЗАПУСК С WINDOWS (TASK SCHEDULER / БЕЗ UAC)",
            variable=self._autostart_var,
            font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
            text_color=C_TEXT_MAIN,
            fg_color="#2b2822",
            progress_color=C_AMBER,
            command=self._on_autostart_change,
        ).pack(anchor="w", padx=14, pady=(10, 2))

        ctk.CTkLabel(
            opts_box,
            text="• Запуск с правами Администратора без всплывающего окна UAC",
            font=ctk.CTkFont(family=FONT_TERM, size=9),
            text_color=C_TEXT_MUTED,
        ).pack(anchor="w", padx=18, pady=(0, 6))

        self._auto_activate_var = BooleanVar(value=self.settings.get("auto_activate_on_startup", True))
        ctk.CTkSwitch(
            opts_box,
            text="АВТОМАТИЧЕСКАЯ АКТИВАЦИЯ ШИФРОВАНИЯ",
            variable=self._auto_activate_var,
            font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
            text_color=C_TEXT_MAIN,
            fg_color="#2b2822",
            progress_color=C_AMBER,
            command=self._on_auto_activate_change,
        ).pack(anchor="w", padx=14, pady=(4, 2))

        ctk.CTkLabel(
            opts_box,
            text="• Авто-включение DoH сразу при загрузке системы или старте приложения",
            font=ctk.CTkFont(family=FONT_TERM, size=9),
            text_color=C_TEXT_MUTED,
        ).pack(anchor="w", padx=18, pady=(0, 6))

        self._tray_var = BooleanVar(value=self.settings.get("minimize_to_tray", True))
        ctk.CTkSwitch(
            opts_box,
            text="СВОРАЧИВАТЬ В ТРЕЙ ПРИ ЗАПУСКЕ / ЗАКРЫТИИ",
            variable=self._tray_var,
            font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
            text_color=C_TEXT_MAIN,
            fg_color="#2b2822",
            progress_color=C_AMBER,
            command=self._on_settings_changed,
        ).pack(anchor="w", padx=14, pady=(4, 2))

        ctk.CTkLabel(
            opts_box,
            text="• Фоновая работа в системном трее Windows",
            font=ctk.CTkFont(family=FONT_TERM, size=9),
            text_color=C_TEXT_MUTED,
        ).pack(anchor="w", padx=18, pady=(0, 10))

        # ── Сервисные действия ────────────────────────────────────────────────
        self._section_banner(scroll, "СЕРВИСНЫЕ КОМАНДЫ")

        ctk.CTkButton(
            scroll,
            text="[ 📁 ОТКРЫТЬ ДИРЕКТОРИЮ ЛОГОВ И ДАННЫХ ]",
            font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
            fg_color=C_PANEL,
            hover_color="#26241e",
            text_color=C_TEXT_MAIN,
            border_color=C_BORDER,
            border_width=1,
            corner_radius=4,
            height=36,
            command=lambda: __import__("os").startfile(str(APP_DIR)),
        ).pack(fill="x", padx=10, pady=(4, 6))

        ctk.CTkButton(
            scroll,
            text="[ 🌐 ПРОВЕРИТЬ РАБОТУ DOH В БРАУЗЕРЕ ]",
            font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
            fg_color=C_PANEL,
            hover_color="#26241e",
            text_color=C_AMBER,
            border_color=C_BORDER,
            border_width=1,
            corner_radius=4,
            height=36,
            command=self._open_check_url,
        ).pack(fill="x", padx=10, pady=(2, 6))

        ctk.CTkButton(
            scroll,
            text="[ 📢 СТАТУС СЕРВЕРОВ: TELEGRAM @XBOX_DNS ]",
            font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
            fg_color=C_PANEL,
            hover_color="#26241e",
            text_color="#60a5fa",
            border_color="#1d4ed8",
            border_width=1,
            corner_radius=4,
            height=36,
            command=lambda: webbrowser.open(TG_CHANNEL),
        ).pack(fill="x", padx=10, pady=(2, 6))

        ctk.CTkButton(
            scroll,
            text="[ 🤖 ТЕХПОДДЕРЖКА: TELEGRAM @XBOX_DNS_SUPPORT_BOT ]",
            font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
            fg_color=C_PANEL,
            hover_color="#26241e",
            text_color="#38bdf8",
            border_color="#0369a1",
            border_width=1,
            corner_radius=4,
            height=36,
            command=lambda: webbrowser.open(TG_SUPPORT_BOT),
        ).pack(fill="x", padx=10, pady=(2, 6))

        ctk.CTkButton(
            scroll,
            text="[ 🌍 ОФИЦИАЛЬНЫЙ САЙТ XBOX-DNS.RU ]",
            font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
            fg_color=C_PANEL,
            hover_color="#26241e",
            text_color="#a3e635",
            border_color="#4d7c0f",
            border_width=1,
            corner_radius=4,
            height=36,
            command=lambda: webbrowser.open(SITE_URL),
        ).pack(fill="x", padx=10, pady=(2, 6))

        ctk.CTkButton(
            scroll,
            text="[ 🛡 GEOHIDE DNS: САЙТ И НАСТРОЙКИ (GEOHIDE.RU) ]",
            font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
            fg_color=C_PANEL,
            hover_color="#26241e",
            text_color="#34d399",
            border_color="#059669",
            border_width=1,
            corner_radius=4,
            height=36,
            command=lambda: webbrowser.open(GEOHIDE_SITE),
        ).pack(fill="x", padx=10, pady=(2, 6))

        ctk.CTkButton(
            scroll,
            text="[ ☕ ПОДДЕРЖАТЬ ПРОЕКТ XBOX DNS ]",
            font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
            fg_color=C_PANEL,
            hover_color="#26241e",
            text_color="#f59e0b",
            border_color="#b45309",
            border_width=1,
            corner_radius=4,
            height=36,
            command=lambda: webbrowser.open(DONATION_URL),
        ).pack(fill="x", padx=10, pady=(2, 10))

        # Футер
        ctk.CTkLabel(
            scroll,
            text=f"// BUCKSHOT DOH TERMINAL // ADGUARD DNSPROXY // REV {APP_VERSION} //",
            font=ctk.CTkFont(family=FONT_TERM, size=9),
            text_color=C_TEXT_MUTED,
        ).pack(pady=(12, 16))

    def _section_banner(self, parent, text: str):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=10, pady=(12, 4))
        ctk.CTkLabel(
            f,
            text=f"/// {text} ///",
            font=ctk.CTkFont(family=FONT_TERM, size=10, weight="bold"),
            text_color=C_AMBER,
        ).pack(side="left")

    # ── Вкладка Дебага и Диагностики ──────────────────────────────────────────

    def _build_debug_tab(self, tab):
        tab.configure(fg_color=C_BG)

        # 1. Верхняя панель команд
        ctrl_frame = ctk.CTkFrame(tab, fg_color=C_PANEL, corner_radius=4, border_color=C_BORDER, border_width=1)
        ctrl_frame.pack(fill="x", padx=10, pady=(8, 6))

        btn_row = ctk.CTkFrame(ctrl_frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=8, pady=(8, 6))

        self._btn_audit = ctk.CTkButton(
            btn_row,
            text="[ ⟳ ПОЛНЫЙ АУДИТ СЕТИ ]",
            font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
            fg_color="#b45309",
            hover_color="#d97706",
            text_color="#fffbeb",
            border_color="#f59e0b",
            border_width=1,
            corner_radius=4,
            height=34,
            command=self._on_run_audit,
        )
        self._btn_audit.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self._btn_rescue = ctk.CTkButton(
            btn_row,
            text="[ 🧹 АВАРИЙНЫЙ СБРОС (DHCP) ]",
            font=ctk.CTkFont(family=FONT_TERM, size=11, weight="bold"),
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            text_color="#fee2e2",
            border_color="#ef4444",
            border_width=1,
            corner_radius=4,
            height=34,
            command=self._on_emergency_rescue,
        )
        self._btn_rescue.pack(side="left", fill="x", expand=True, padx=(4, 0))

        sub_row = ctk.CTkFrame(ctrl_frame, fg_color="transparent")
        sub_row.pack(fill="x", padx=8, pady=(0, 8))

        ctk.CTkButton(
            sub_row,
            text="[ 📋 КОПИРОВАТЬ ОТЧЕТ ]",
            font=ctk.CTkFont(family=FONT_TERM, size=10, weight="bold"),
            fg_color=C_CARD,
            hover_color="#2b2820",
            text_color=C_TEXT_MAIN,
            border_color=C_BORDER,
            border_width=1,
            corner_radius=4,
            height=28,
            command=self._copy_debug_log,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        ctk.CTkButton(
            sub_row,
            text="[ 🗑 ОЧИСТИТЬ ОКНО ]",
            font=ctk.CTkFont(family=FONT_TERM, size=10, weight="bold"),
            fg_color=C_CARD,
            hover_color="#2b2820",
            text_color=C_TEXT_MUTED,
            border_color=C_BORDER,
            border_width=1,
            corner_radius=4,
            height=28,
            command=self._clear_debug_log,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        # 2. Переключатели параметров
        opts_frame = ctk.CTkFrame(tab, fg_color="transparent")
        opts_frame.pack(fill="x", padx=12, pady=(0, 4))

        self._verbose_var = BooleanVar(value=self.settings.get("verbose_logging", True))
        ctk.CTkSwitch(
            opts_frame,
            text="ПОДРОБНЫЙ ЛОГ (-V)",
            variable=self._verbose_var,
            font=ctk.CTkFont(family=FONT_TERM, size=10, weight="bold"),
            text_color=C_AMBER,
            fg_color="#2b2822",
            progress_color=C_AMBER,
            command=self._on_verbose_toggle,
        ).pack(side="left")

        self._autoscroll_var = BooleanVar(value=self.settings.get("autoscroll", True))
        ctk.CTkSwitch(
            opts_frame,
            text="АВТОСКРОЛЛ",
            variable=self._autoscroll_var,
            font=ctk.CTkFont(family=FONT_TERM, size=10, weight="bold"),
            text_color=C_TEXT_MUTED,
            fg_color="#2b2822",
            progress_color=C_AMBER,
            command=self._on_autoscroll_toggle,
        ).pack(side="right")

        # 3. Консоль дебага (CRT Terminal)
        term_frame = ctk.CTkFrame(tab, fg_color="#050805", corner_radius=4, border_color=C_BORDER, border_width=2)
        term_frame.pack(fill="both", expand=True, padx=10, pady=(2, 8))

        term_head = ctk.CTkFrame(term_frame, fg_color="#0d140d", corner_radius=2, height=24)
        term_head.pack(fill="x", padx=4, pady=4)
        term_head.pack_propagate(False)

        ctk.CTkLabel(
            term_head,
            text="/// BUCKSHOT DEBUG CONSOLE // LIVE STREAM ///",
            font=ctk.CTkFont(family=FONT_TERM, size=10, weight="bold"),
            text_color=C_GREEN,
        ).pack(side="left", padx=8)

        self._stream_indicator = ctk.CTkLabel(
            term_head,
            text="[● LIVE STREAM]",
            font=ctk.CTkFont(family=FONT_TERM, size=9, weight="bold"),
            text_color=C_GREEN,
        )
        self._stream_indicator.pack(side="right", padx=8)

        self._debug_box = ctk.CTkTextbox(
            term_frame,
            fg_color="#050805",
            text_color="#86efac",
            font=ctk.CTkFont(family=FONT_TERM, size=10),
            wrap="none",
            corner_radius=0,
        )
        self._debug_box.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        # Приветственные строки
        self._append_debug_line("[SYSTEM] Консоль диагностики и дебага готова.")
        self._append_debug_line("[SYSTEM] Нажмите '[ ПОЛНЫЙ АУДИТ СЕТИ ]' для проверки DNS и соединения.")
        self._append_debug_line("────────────────────────────────────────────────────────────────")

        # Запуск фонового стрима логов
        self._start_log_stream()

    def _append_debug_line(self, line: str):
        if not hasattr(self, "_debug_box") or not self._debug_box.winfo_exists():
            return
        self._debug_box.insert("end", line + "\n")
        if getattr(self, "_autoscroll_var", None) and self._autoscroll_var.get():
            self._debug_box.see("end")

    def _start_log_stream(self):
        def _streamer():
            log_path = APP_DIR / "dnsproxy.log"
            last_pos = 0
            if log_path.exists():
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(0, 2)  # SEEK_END
                        last_pos = f.tell()
                except Exception:
                    pass

            while not getattr(self, "_shutdown_requested", False):
                time.sleep(0.5)
                if not log_path.exists():
                    continue
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(last_pos)
                        new_lines = f.readlines()
                        last_pos = f.tell()
                        if new_lines:
                            for l in new_lines:
                                l_str = l.strip()
                                if l_str:
                                    self._safe_call(lambda text=l_str: self._append_debug_line(f"[LOG] {text}"))
                except Exception:
                    pass

        self._stream_thread = threading.Thread(target=_streamer, daemon=True)
        self._stream_thread.start()

    def _on_run_audit(self):
        self._btn_audit.configure(state="disabled", text="[ ⏳ ИДЕТ АУДИТ... ]")

        def _worker():
            self._safe_call(lambda: self._append_debug_line("\n>>> ЗАПУСК ПОЛНОЙ ДИАГНОСТИКИ СЕТИ..."))
            report = SystemDiagnostics.run_full_diagnostics(self.settings, self._dnsproxy, self._network)
            for r in report:
                self._safe_call(lambda line=r: self._append_debug_line(line))
            self._safe_call(lambda: self._btn_audit.configure(state="normal", text="[ ⟳ ПОЛНЫЙ АУДИТ СЕТИ ]"))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_emergency_rescue(self):
        if not messagebox.askyesno(
            "АВАРИЙНЫЙ СБРОС СЕТИ",
            "Выполнить принудительный сброс настроек DNS всех сетевых адаптеров в режим DHCP и остановить dnsproxy?",
        ):
            return

        self._btn_rescue.configure(state="disabled")

        def _worker():
            self._safe_call(lambda: self._append_debug_line("\n>>> ЗАПУСК АВАРИЙНОГО СБРОСА СЕТИ..."))
            report = SystemDiagnostics.emergency_repair_network(self._network, self._dnsproxy)
            for r in report:
                self._safe_call(lambda line=r: self._append_debug_line(line))
            self._active = False
            self._safe_call(self._refresh_ui)
            self._safe_call(lambda: self._btn_rescue.configure(state="normal"))
            self._safe_call(lambda: messagebox.showinfo(
                "СБРОС ВЫПОЛНЕН",
                "Сетевой адаптер переведен в чистый DHCP (DNS от роутера).\nИнтернет восстановлен в заводское состояние."
            ))

        threading.Thread(target=_worker, daemon=True).start()

    def _copy_debug_log(self):
        try:
            content = self._debug_box.get("1.0", "end")
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            messagebox.showinfo("БУФЕР ОБМЕНА", "Отчет дебага скопирован в буфер обмена!")
        except Exception as e:
            messagebox.showerror("ОШИБКА", f"Не удалось скопировать: {e}")

    def _clear_debug_log(self):
        self._debug_box.delete("1.0", "end")
        self._append_debug_line("[SYSTEM] Окно очищено.")

    def _on_verbose_toggle(self):
        val = self._verbose_var.get()
        self.settings["verbose_logging"] = val
        self._save_settings()
        self._append_debug_line(f"[SYSTEM] Подробный лог (-v): {'ВКЛЮЧЕН' if val else 'ВЫКЛЮЧЕН'}")
        if self._active:
            self._append_debug_line("[SYSTEM] Перезапуск dnsproxy с новыми параметрами логирования...")
            threading.Thread(target=self._restart_dnsproxy, daemon=True).start()

    def _on_autoscroll_toggle(self):
        val = self._autoscroll_var.get()
        self.settings["autoscroll"] = val
        self._save_settings()


    # ── Логика переключения ───────────────────────────────────────────────────

    def _on_toggle(self):
        if self._active:
            threading.Thread(target=self._do_deactivate, daemon=True).start()
        else:
            threading.Thread(target=self._do_activate, daemon=True).start()

    def _do_activate(self):
        self._set_loading(True)

        # 1. Скачиваем dnsproxy если отсутствует
        if not self._dnsproxy.ensure_dnsproxy(progress_cb=self._on_progress):
            self._safe_call(lambda: messagebox.showerror(
                "ОШИБКА ИНИЦИАЛИЗАЦИИ",
                "Не удалось загрузить бинарный файл dnsproxy.exe.\n"
                "Проверьте соединение с сетью."
            ))
            self._set_loading(False)
            return

        self._on_progress(0.95, "[ ЗАПУСК ПРОЦЕССА DNSPROXY... ]")

        # 2. Запуск процесса dnsproxy
        ok = self._dnsproxy.start(self.settings)
        if not ok:
            diag = ""
            log_path = APP_DIR / "dnsproxy.log"
            if log_path.exists():
                try:
                    raw = log_path.read_text(encoding="utf-8", errors="replace")
                    errs = [l.strip() for l in raw.splitlines() if "ERROR" in l]
                    if errs:
                        diag = "\n\nДетали сбоя:\n" + "\n".join(errs[-2:])
                except Exception:
                    pass
            self._safe_call(lambda: messagebox.showerror(
                "ОШИБКА БИНДИНГА",
                f"Не удалось запустить процесс dnsproxy.{diag}\n\n"
                "Убедитесь, что порт 53 свободен и есть права Администратора."
            ))
            self._set_loading(False)
            return

        self._on_progress(0.98, "[ ПЕРЕКЛЮЧЕНИЕ СЕТЕВОГО АДАПТЕРА... ]")

        # 3. Переключаем DNS на сетевом интерфейсе
        ok2 = self._network.apply_dns("127.0.0.1")
        if not ok2:
            self._dnsproxy.stop()
            self._safe_call(lambda: messagebox.showerror(
                "ОШИБКА МАРШРУТИЗАЦИИ",
                "Не удалось выставить DNS 127.0.0.1 на активном адаптере.\n"
                "Требуются права Администратора Windows."
            ))
            self._set_loading(False)
            return

        self._on_progress(1.0, "[ ШИФРОВАНИЕ АКТИВИРОВАНО ]")

        self._active = True
        self._safe_call(self._refresh_ui)
        self._set_loading(False)
        self._hide_progress()
        self._update_tray()

    def _do_deactivate(self):
        self._set_loading(True)
        self._network.restore_dns()
        self._dnsproxy.stop()
        self._active = False
        self._safe_call(self._refresh_ui)
        self._set_loading(False)
        self._update_tray()

    def _refresh_ui(self):
        self._crt.set_active(self._active)
        self._crt.set_upstream(self._get_active_hostname())
        self._toggle_btn.set_active(self._active)

        if self._active:
            self._power_indicator.configure(
                text="[● LIVE ONLINE]",
                text_color=C_GREEN,
            )
        else:
            self._power_indicator.configure(
                text="[○ BLANK OFFLINE]",
                text_color=C_TEXT_MUTED,
            )

    def _get_active_hostname(self) -> str:
        profile = get_active_profile(self.settings)
        url = profile.get("url", "xbox-dns.ru")
        try:
            from urllib.parse import urlparse
            return urlparse(url).hostname or url
        except Exception:
            return url

    # ── Прогресс ──────────────────────────────────────────────────────────────

    def _on_progress(self, value: float, msg: str):
        def _update():
            if not self._progress_bar.winfo_ismapped():
                self._progress_bar.pack(fill="x", pady=(2, 2))
                self._progress_label.pack(anchor="w")
            self._progress_bar.set(value)
            self._progress_label.configure(text=msg)
        self._safe_call(_update)

    def _hide_progress(self):
        def _hide():
            self._progress_bar.pack_forget()
            self._progress_label.pack_forget()
        self._safe_call(_hide)

    def _set_loading(self, loading: bool):
        self._safe_call(lambda: self._toggle_btn.set_loading(loading))

    # ── Пинг / Мониторинг ─────────────────────────────────────────────────────

    def _start_ping_loop(self):
        self._ping_running = True

        def _loop():
            time.sleep(1.0)
            while self._ping_running:
                try:
                    if self._active:
                        lat = measure_dns_latency("127.0.0.1", 53)
                        if lat is not None:
                            bars = int(max(1, min(5, (100 - lat) / 15))) if lat < 100 else 1
                            meter = "█" * bars + "░" * (5 - bars)
                            txt = f"{lat:.0f} мс  [{meter}]"
                            col = C_GREEN
                        else:
                            txt = "ТАЙМАУТ  [░░░░░]"
                            col = C_CRIMSON
                    else:
                        profile = get_active_profile(self.settings)
                        host = profile.get("bootstrap") or self._get_active_hostname()
                        lat = measure_ping(host, port=443, timeout=2.0)
                        if lat is not None:
                            bars = int(max(1, min(5, (100 - lat) / 15))) if lat < 100 else 1
                            meter = "█" * bars + "░" * (5 - bars)
                            txt = f"{lat:.0f} мс  [{meter}]"
                            col = C_AMBER
                        else:
                            txt = "—  мс  [░░░░░]"
                            col = C_TEXT_MUTED

                    self._safe_call(lambda t=txt, c=col: self._ping_label.configure(text=t, text_color=c))
                except Exception as exc:
                    logger.debug("ping loop error: %s", exc)

                time.sleep(4)

        self._ping_thread = threading.Thread(target=_loop, daemon=True)
        self._ping_thread.start()

    # ── Обработчики настроек ──────────────────────────────────────────────────

    def _on_profile_change(self):
        self.settings["profile_index"] = self._profile_var.get()
        self._crt.set_upstream(self._get_active_hostname())
        self._save_settings()

        if self._active:
            threading.Thread(target=self._restart_dnsproxy, daemon=True).start()

    def _restart_dnsproxy(self):
        self._set_loading(True)
        self._dnsproxy.stop()
        time.sleep(0.5)
        self._dnsproxy.start(self.settings)
        self._set_loading(False)

    def _on_autostart_change(self):
        enabled = self._autostart_var.get()
        ok, msg = set_autostart(enabled)
        real_state = is_autostart_enabled()
        self.settings["autostart"] = real_state
        self._autostart_var.set(real_state)
        self._save_settings()

        if ok:
            self._append_debug_line(f"[AUTOSTART] {msg}")
            messagebox.showinfo(
                "АВТОЗАПУСК WINDOWS",
                f"{msg}\n\nСлужба добавлена в Планировщик заданий Windows (HighestAvailable) и будет запускаться с правами Администратора БЕЗ всплывающего окна UAC."
                if enabled else msg,
            )
        else:
            self._append_debug_line(f"[AUTOSTART] Ошибка: {msg}")
            messagebox.showerror("ОШИБКА АВТОЗАПУСКА", msg)

    def _on_auto_activate_change(self):
        enabled = self._auto_activate_var.get()
        self.settings["auto_activate_on_startup"] = enabled
        self._save_settings()
        self._append_debug_line(f"[AUTOSTART] Авто-активация шифрования: {'ВКЛЮЧЕНА' if enabled else 'ВЫКЛЮЧЕНА'}")

    def _on_settings_changed(self):
        self.settings["minimize_to_tray"] = self._tray_var.get()
        if hasattr(self, "_auto_activate_var"):
            self.settings["auto_activate_on_startup"] = self._auto_activate_var.get()
        if hasattr(self, "_custom_url_var"):
            self.settings["custom_url"] = self._custom_url_var.get()
        if hasattr(self, "_custom_boot_var"):
            self.settings["custom_bootstrap"] = self._custom_boot_var.get()
        self._save_settings()

    def _save_settings(self):
        if hasattr(self, "_custom_url_var"):
            self.settings["custom_url"] = self._custom_url_var.get()
        if hasattr(self, "_custom_boot_var"):
            self.settings["custom_bootstrap"] = self._custom_boot_var.get()
        if hasattr(self, "_auto_activate_var"):
            self.settings["auto_activate_on_startup"] = self._auto_activate_var.get()
        save_settings(self.settings)

    # ── Трей ──────────────────────────────────────────────────────────────────

    def _open_check_url(self):
        profile = get_active_profile(self.settings)
        url = profile.get("check_url") or "https://xbox-dns.ru/test"
        webbrowser.open(url)

    def _start_tray(self):
        icon_img = make_tray_icon(self._active, size=64)
        menu = pystray.Menu(
            pystray.MenuItem("Открыть консоль", self._tray_show, default=True),
            pystray.MenuItem(
                lambda _: "Сбросить соединение" if self._active else "Активировать защиту",
                self._tray_toggle,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход из системы", self._tray_quit),
        )
        self._tray = pystray.Icon(APP_NAME, icon_img, "Buckshot DoH Relay", menu)
        self._tray.run()

    def _update_tray(self):
        if self._tray is not None:
            self._tray.icon = make_tray_icon(self._active, size=64)

    def _tray_show(self, icon=None, item=None):
        def _show():
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        self._safe_call(_show)

    def _tray_toggle(self, icon=None, item=None):
        self._on_toggle()

    def _tray_quit(self, icon=None, item=None):
        self._shutdown()

    # ── Завершение работы ─────────────────────────────────────────────────────

    def _on_close_btn(self):
        if self.settings.get("minimize_to_tray", True):
            self.root.withdraw()
            if self._tray is None or not self._tray.visible:
                self._tray_thread = threading.Thread(target=self._start_tray, daemon=True)
                self._tray_thread.start()
        else:
            self._shutdown()

    def _shutdown(self):
        self._shutdown_requested = True
        self._ping_running = False
        try:
            if self._active:
                self._network.restore_dns()
                self._dnsproxy.stop()
        except Exception as exc:
            logger.error("Ошибка при остановке: %s", exc)

        if self._tray:
            try:
                self._tray.stop()
            except Exception:
                pass

        self._save_settings()
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass

    def _safe_call(self, fn):
        try:
            self.root.after(0, fn)
        except Exception:
            pass

    # ── Запуск ────────────────────────────────────────────────────────────────

    def run(self):
        self._refresh_ui()
        threading.Thread(
            target=lambda: self._dnsproxy.ensure_dnsproxy(self._on_progress),
            daemon=True,
        ).start()
        self.root.mainloop()
