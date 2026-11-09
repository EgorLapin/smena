# Кадрирование скриншотов приёмки для отчёта: автоматический поиск последней
# «строки с краской» (текст/кнопка) и отсечение пустой области карточки снизу.
# Источник (не изменяется): docs/accept/*.png ; результат: docs/report-work/shots/*.png
import os
from PIL import Image

SRC = r"C:\Users\Egor\codesss\proj\Smena\docs\accept"
DST = r"C:\Users\Egor\codesss\proj\Smena\docs\report-work\shots"
os.makedirs(DST, exist_ok=True)

PAD = 26          # запас под последней строкой контента
FOOTER_TEXT = "СменаПринята"  # футер повторяется на всех экранах — отрезаем

for name in sorted(os.listdir(SRC)):
    if not name.endswith(".png"):
        continue
    im = Image.open(os.path.join(SRC, name)).convert("RGB")
    w, h = im.size
    px = im.load()
    # «краска» = заметно темнее фона (фон белый/светло-серый)
    def row_has_ink(y):
        for x in range(0, w, 3):
            r, g, b = px[x, y]
            if r < 150 and g < 150 and b < 150:
                return True
        return False

    # ищем снизу вверх последнюю строку контента, пропуская футер:
    # футер = последняя группа строк, отделённая от контента светлым зазором > 60 px
    ink_rows = [y for y in range(h) if row_has_ink(y)]
    if not ink_rows:
        print(name, "no ink")
        continue
    groups = []
    start = prev = ink_rows[0]
    for y in ink_rows[1:]:
        if y - prev > 40:
            groups.append((start, prev))
            start = y
        prev = y
    groups.append((start, prev))

    # отбрасываем футер: последние 2 строки текста, прижатые к низу и отделённые
    # от контента большим светлым зазором (> 100 px)
    last, prev_last = groups[-1], groups[-2]
    is_footer = (
        len(groups) > 2
        and (last[1] - last[0]) < 130
        and last[0] > int(0.80 * h)
        and last[0] - prev_last[1] > 100
    )
    body = groups[:-1] if is_footer else groups
    bottom = min(h, body[-1][1] + PAD)
    out = im.crop((0, 0, w, bottom))
    out.save(os.path.join(DST, name), optimize=True)
    print(f"{name:26s} {w}x{h} -> {out.size[0]}x{out.size[1]} ratio={out.size[1]/w:.2f} "
          f"groups={[(a, b) for a, b in groups][-3:]}")
