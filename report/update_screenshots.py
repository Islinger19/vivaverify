"""Crops the screenshots from backend/eval/ui_demo.py like the report's figures, saves them to report/assets
and swaps them into the finished report, keeping each figure's width (the height follows the new image).

Run after ui_demo.py:  python report/update_screenshots.py
"""
import hashlib
import io
from pathlib import Path

import numpy as np
from docx import Document
from docx.oxml.ns import qn
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
shots = ROOT / "backend" / "eval" / "screenshots"
docx_path = ROOT / "report" / "CA3_Agentic_AI_Report_VivaVerify.docx"
assets = ROOT / "report" / "assets"
FIGURES = {   # report asset <- screenshot from eval/ui_demo.py
    "ui_01_setup.png": "01_setup.png", "ui_02_analysed.png": "02_analysed.png",
    "ui_03_followup.png": "03_followup.png", "ui_04_guardrail.png": "04_guardrail.png",
    "ui_05_result.png": "05_result_summary.png", "ui_06_transcript.png": "06_result_transcript.png",
    "ui_07_improve.png": "07_how_to_improve.png", "ui_08_how.png": "08_how_it_works.png",
    "ui_09_mobile.png": "09_mobile_improve.png",
}


def crop(im: Image.Image, aspect: float) -> Image.Image:
    """Content between the header rule and the footer rule, trimmed to the cards, at most `aspect` tall."""
    a = np.asarray(im.convert("RGB")).astype(int)
    h, w, _ = a.shape
    bg = a[h // 2, 3]
    dark = (a.sum(axis=2) < 200).sum(axis=1)
    top = next(y for y in range(min(400, h)) if dark[y] > 0.5 * w) + 4                 # under the 2px ink rule
    rule = (np.abs(a - np.array([0xdd, 0xd6, 0xc8])).sum(axis=2) < 12).sum(axis=1)
    bottom = max(y for y in range(h) if rule[y] > 0.5 * w)                             # the footer's rule
    band = np.abs(a[top:bottom] - bg).sum(axis=2) > 18
    ys, xs = np.where(band)
    pad = 14
    x0, x1 = max(0, xs.min() - pad), min(w, xs.max() + pad)
    y0, y1 = top + max(0, ys.min() - pad), top + min(bottom - top, ys.max() + pad)
    y1 = min(y1, y0 + int((x1 - x0) * aspect))                                        # keep the figure's height
    return im.crop((x0, y0, x1, y1))


doc = Document(str(docx_path))
blobs = {}
for asset, shot in FIGURES.items():
    old = (assets / asset).read_bytes()
    old_im = Image.open(io.BytesIO(old))
    new_im = Image.open(shots / shot)
    if asset != "ui_09_mobile.png":   # the phone screenshot is used whole
        new_im = crop(new_im, old_im.height / old_im.width)   # never taller than before, so pages do not move
    buf = io.BytesIO()
    new_im.save(buf, "PNG", optimize=True)
    blobs[hashlib.sha256(old).hexdigest()] = (asset, buf.getvalue(), new_im.size)

swapped = []
for rel in doc.part.rels.values():
    if "image" not in rel.reltype:
        continue
    part = rel.target_part
    key = hashlib.sha256(part.blob).hexdigest()
    if key not in blobs:
        continue
    asset, data, (w, h) = blobs[key]
    part._blob = data
    for blip in doc.element.body.iter(qn("a:blip")):                     # keep the width, fix the height
        if blip.get(qn("r:embed")) != rel.rId:
            continue
        inline = blip.getparent().getparent().getparent().getparent().getparent()
        extent = inline.find(qn("wp:extent"))
        cx = int(extent.get("cx"))
        cy = str(round(cx * h / w))
        extent.set("cy", cy)
        for ext in inline.iter(qn("a:ext")):
            ext.set("cy", cy)
    swapped.append(asset)
    (assets / asset).write_bytes(data)
doc.save(str(docx_path))
print("swapped", len(swapped), sorted(swapped))
