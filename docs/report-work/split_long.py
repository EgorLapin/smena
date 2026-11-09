# Дополнительные кадры для длинных экранов: режем по границам карточек
# (пустые зазоры), чтобы не резать карточку посередине и сохранить шапку приложения.
import os
from PIL import Image

SRC = r"C:\Users\Egor\codesss\proj\Smena\docs\accept"
DST = r"C:\Users\Egor\codesss\proj\Smena\docs\report-work\shots"
os.makedirs(DST, exist_ok=True)

# (файл, результат, y_начало, y_конец) — границы выбраны по зазорам без «краски»
CUTS = [
    ("05-history-issues.png", "05a-history-filters.png", 0, 1000),
    ("05-history-issues.png", "05b-history-list.png", 1000, 2270),
    ("06-staff.png", "06a-staff-list.png", 0, 1542),
    ("06-staff.png", "06b-staff-add.png", 1500, 2675),
    ("03-overview.png", "03a-overview-top.png", 0, 850),
    ("03-overview.png", "03b-overview-bottom.png", 850, 1526),
]

for src_name, dst_name, y0, y1 in CUTS:
    im = Image.open(os.path.join(SRC, src_name)).convert("RGB")
    out = im.crop((0, y0, im.size[0], y1))
    out.save(os.path.join(DST, dst_name), optimize=True)
    print(f"{dst_name:28s} {out.size[0]}x{out.size[1]} ratio={out.size[1]/out.size[0]:.2f}")
