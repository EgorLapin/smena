"""Извлечение полного текста ТЗ (параграфы + таблицы) в _tz_extract.txt."""
import docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn

SRC = r"C:\Users\Egor\Downloads\СменаПринята. Техническое задание на MVP (v1.1.2) (1).docx"
DST = r"C:\Users\Egor\codesss\proj\Smena\_tz_extract.txt"

d = docx.Document(SRC)


def iter_blocks(doc):
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


out = []
for block in iter_blocks(d):
    if isinstance(block, Paragraph):
        style = block.style.name if block.style else ""
        t = block.text.strip()
        if t:
            prefix = f"[{style}] " if style.lower().startswith(("heading", "title")) else ""
            out.append(prefix + t)
    else:
        out.append("=== TABLE ===")
        for row in block.rows:
            cells = [c.text.strip().replace("\n", " / ") for c in row.cells]
            out.append(" | ".join(cells))
        out.append("=== /TABLE ===")

text = "\n".join(out)
with open(DST, "w", encoding="utf-8") as f:
    f.write(text)
print(f"lines={len(out)}, chars={len(text)}")
