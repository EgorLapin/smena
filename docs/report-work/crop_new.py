# -*- coding: utf-8 -*-
# Автообрезка новых кадров: снизу убираются серый фон и подвал,
# но белая карточка с рамкой сохраняется целиком (режем по последнему
# ряду, отличающемуся от цвета фона страницы, + небольшой отступ).
import os
from PIL import Image

D = r"C:\Users\Egor\codesss\proj\Smena\docs\report-work\shots"
BG = (246, 248, 247)  # цвет фона страницы под карточками


def far(c, ref, d=6):
    return any(abs(a - b) > d for a, b in zip(c, ref))


for name in ["20-journal-open.png", "21-handover-check.png",
             "22-journal-accepted.png", "23-snapshot-submitted.png"]:
    p = os.path.join(D, name)
    im = Image.open(p).convert("RGB")
    w, h = im.size
    px = im.load()

    def ink(y):
        for x in range(0, w, 2):
            r, g, b = px[x, y]
            if r < 150 and g < 150 and b < 150:
                return True
        return False

    rows = [y for y in range(h) if ink(y)]
    groups = []
    s = prev = rows[0]
    for y in rows[1:]:
        if y - prev > 40:
            groups.append((s, prev))
            s = y
        prev = y
    groups.append((s, prev))

    # подвал: короткая последняя группа ниже 80 % высоты, отделённая пустотой
    if len(groups) > 2 and (groups[-1][1] - groups[-1][0]) < 130 \
            and groups[-1][0] > 0.8 * h and groups[-1][0] - groups[-2][1] > 100:
        limit = groups[-1][0] - 40
    else:
        limit = h

    # последний ряд, в котором что-то есть кроме серого фона
    bottom = limit - 1
    while bottom > 0 and not any(
            far(px[x, bottom], BG) for x in range(0, w, 2)):
        bottom -= 1
    bottom = min(h, bottom + 20)
    im.crop((0, 0, w, bottom)).save(p, optimize=True)
    print(name, f"{w}x{h} -> {w}x{bottom}")
