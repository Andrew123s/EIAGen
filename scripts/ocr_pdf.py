"""OCR a scanned PDF (CPU, RapidOCR) into a page-keyed sidecar used by ingestion.

    python scripts/ocr_pdf.py data/regulations/official/gh_act1124.pdf

Writes <file>.ocr.json = {"engine": ..., "pages": {"1": "text", ...}}. During
ingestion, any PDF page without a text layer is read from this sidecar, so
citations keep the original page numbers. OCR text can contain recognition
errors: documents ingested from OCR are flagged in their metadata.

Requires: pip install rapidocr-onnxruntime
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def ocr_pdf(path: Path, dpi: int = 200, first: int | None = None, last: int | None = None) -> Path:
    import numpy as np
    import pymupdf
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    out = path.with_suffix(path.suffix + ".ocr.json")
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"engine": "rapidocr-onnxruntime", "dpi": dpi, "pages": {}}
    with pymupdf.open(path) as doc:
        start, end = (first or 1), (last or doc.page_count)
        for n in range(start, end + 1):
            if str(n) in data["pages"]:
                continue
            pix = doc[n - 1].get_pixmap(dpi=dpi)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            result, _ = engine(img[:, :, :3] if pix.n >= 3 else img)
            lines = []
            # Sort boxes top-to-bottom, then left-to-right, merging boxes on the same visual line.
            for box, text, _score in sorted(result or [], key=lambda r: (round(r[0][0][1] / 12), r[0][0][0])):
                lines.append((round(box[0][1] / 12), text))
            merged: list[str] = []
            current_row = None
            for row, text in lines:
                if row == current_row and merged:
                    merged[-1] += " " + text
                else:
                    merged.append(text)
                    current_row = row
            data["pages"][str(n)] = "\n".join(merged)
            if n % 10 == 0:
                out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                print(f"page {n}/{end}", flush=True)
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--first", type=int)
    ap.add_argument("--last", type=int)
    a = ap.parse_args()
    print(ocr_pdf(a.pdf, a.dpi, a.first, a.last))
