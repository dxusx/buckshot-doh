# -*- coding: utf-8 -*-
"""
icons.py — генерация иконок в стиле Buckshot Roulette (дробовик, патроны, ретро-терминал)
"""

from PIL import Image, ImageDraw
import io


def make_tray_icon(active: bool, size: int = 64) -> Image.Image:
    """Иконка в трее: гильза патрона (Красная 'Live' = Активно, Черная 'Blank' = Отключено)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = size

    # Внешний металлический круг/гайка
    draw.ellipse([2, 2, s - 2, s - 2], fill=(22, 23, 21, 255), outline=(66, 60, 52, 255), width=max(1, s // 32))

    # Свечение индикатора
    if active:
        # Радиоактивный фосфорный / красный Live-патрон
        # Внутреннее свечение
        draw.ellipse([s * 0.18, s * 0.18, s * 0.82, s * 0.82], fill=(34, 197, 94, 90))
        draw.ellipse([s * 0.25, s * 0.25, s * 0.75, s * 0.75], fill=(34, 197, 94, 255), outline=(134, 239, 172, 255), width=2)
        # Капсюль по центру (латунь)
        draw.ellipse([s * 0.40, s * 0.40, s * 0.60, s * 0.60], fill=(234, 179, 8, 255), outline=(161, 98, 7, 255), width=1)
    else:
        # Разряжено / Blank
        draw.ellipse([s * 0.25, s * 0.25, s * 0.75, s * 0.75], fill=(55, 50, 42, 255), outline=(82, 75, 63, 255), width=2)
        draw.ellipse([s * 0.42, s * 0.42, s * 0.58, s * 0.58], fill=(35, 33, 28, 255))

    return img


def make_app_icon(size: int = 256) -> Image.Image:
    """Иконка приложения — бронированная табличка с патроном 12 калибра (Buckshot Roulette)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = size

    # Металлическая пластина с грубыми скошенными углами (шестигранник/табличка)
    pad = int(s * 0.05)
    plate = [
        (pad + s * 0.15, pad),
        (s - pad - s * 0.15, pad),
        (s - pad, pad + s * 0.15),
        (s - pad, s - pad - s * 0.15),
        (s - pad - s * 0.15, s - pad),
        (pad + s * 0.15, s - pad),
        (pad, s - pad - s * 0.15),
        (pad, pad + s * 0.15),
    ]
    # Тень / ржавый контур
    draw.polygon(plate, fill=(20, 19, 17, 255), outline=(74, 67, 56, 255))

    # Заклёпки по 4 углам
    rivet_r = max(3, int(s * 0.03))
    rivets = [
        (int(s * 0.22), int(s * 0.18)),
        (int(s * 0.78), int(s * 0.18)),
        (int(s * 0.22), int(s * 0.82)),
        (int(s * 0.78), int(s * 0.82)),
    ]
    for rx, ry in rivets:
        draw.ellipse([rx - rivet_r, ry - rivet_r, rx + rivet_r, ry + rivet_r],
                     fill=(45, 42, 36, 255), outline=(100, 92, 78, 255), width=1)

    # Внутренний темный дисплей / окно
    in_pad = int(s * 0.22)
    draw.rectangle([in_pad, in_pad, s - in_pad, s - in_pad],
                   fill=(10, 12, 10, 255), outline=(40, 50, 38, 255), width=2)

    # Патрон 12 калибра (Shotgun Shell)
    # Латунная юбка гильзы (низ)
    bx0, by0 = int(s * 0.38), int(s * 0.58)
    bx1, by1 = int(s * 0.62), int(s * 0.73)
    draw.rectangle([bx0, by0, bx1, by1], fill=(194, 134, 34, 255), outline=(245, 180, 60, 255), width=1)

    # Кант гильзы (закраина)
    rim_h = int(s * 0.035)
    draw.rectangle([bx0 - 3, by1 - rim_h, bx1 + 3, by1], fill=(245, 190, 70, 255))

    # Корпус гильзы (красный пластик / Live shell)
    cx0, cy0 = int(s * 0.38), int(s * 0.30)
    cx1, cy1 = int(s * 0.62), int(s * 0.58)
    draw.rectangle([cx0, cy0, cx1, cy1], fill=(185, 28, 28, 255), outline=(239, 68, 68, 255), width=1)

    # Закатка / складки патрона наверху
    draw.line([cx0, cy0 + int(s * 0.04), cx1, cy0 + int(s * 0.04)], fill=(127, 29, 29, 255), width=2)

    # Перекрестье прицела / лучи CRT (зеленый фосфор)
    beam_color = (74, 222, 128, 160)
    center_y = int(s * 0.50)
    draw.line([in_pad + 4, center_y, in_pad + 16, center_y], fill=beam_color, width=1)
    draw.line([s - in_pad - 16, center_y, s - in_pad - 4, center_y], fill=beam_color, width=1)

    return img


def image_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()
