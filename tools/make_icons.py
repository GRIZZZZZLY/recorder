"""Иконки приложения для домашнего экрана.

Рисуются скриптом, а не руками в редакторе, чтобы их можно было пересобрать
после смены палитры: цвета берутся отсюда же, из темы «Студия».

Запуск: python tools/make_icons.py
Результат: web/public/icon-192.png, icon-512.png, apple-touch-icon.png.

Форма простая до предела: красная точка записи с бликом линзы и тонкое
светлое кольцо вокруг. На экране телефона иконка размером с ноготь, и любая
деталь мельче этой в кашу превращается.
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "web" / "public"

CANVAS = (21, 23, 28)        # --color-canvas #15171c
ACCENT = (255, 69, 58)       # --color-accent #ff453a
RING = (255, 255, 255, 36)   # волос 14 %
GLINT = (255, 255, 255, 90)  # блик линзы

# Рисуем крупно и уменьшаем: сглаживание краёв бесплатно и без шрифтов.
SUPER = 8


def draw(size: int, pad: float) -> Image.Image:
    """pad — доля поля по краю. Для maskable нужно поле, для обычной иконки нет."""
    big = size * SUPER
    image = Image.new("RGBA", (big, big), (*CANVAS, 255))
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)

    middle = big / 2
    safe = big * (1 - 2 * pad) / 2
    dot = safe * 0.56
    ring = safe * 0.88

    pen.ellipse([middle - ring, middle - ring, middle + ring, middle + ring],
                outline=RING, width=max(1, int(big * 0.012)))
    pen.ellipse([middle - dot, middle - dot, middle + dot, middle + dot], fill=(*ACCENT, 255))

    image.alpha_composite(layer)

    # Блик — отдельным слоем поверх: в одном слое ImageDraw заменяет пиксели,
    # а не смешивает их, и белый с альфой просто вырезал бы в красном серое пятно.
    shine = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    glint = dot * 0.42
    cx = middle - dot * 0.34
    cy = middle - dot * 0.36
    ImageDraw.Draw(shine).ellipse(
        [cx - glint, cy - glint * 0.72, cx + glint, cy + glint * 0.72], fill=GLINT)
    image.alpha_composite(shine)

    return image.resize((size, size), Image.LANCZOS).convert("RGB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # maskable: iOS и Android обрезают углы сами, содержимое держим в центре.
    for name, size, pad in (("icon-192.png", 192, 0.16),
                            ("icon-512.png", 512, 0.16),
                            ("apple-touch-icon.png", 180, 0.10)):
        path = OUT / name
        draw(size, pad).save(path, optimize=True)
        print(f"{path.name}: {path.stat().st_size} байт")


if __name__ == "__main__":
    main()
