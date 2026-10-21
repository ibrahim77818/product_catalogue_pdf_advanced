#!/usr/bin/env python3
"""Check a rendered catalogue PDF from the outside (no Odoo needed).

Requires poppler-utils (pdfinfo, pdftoppm, pdftotext) and Pillow; QR decoding
also uses OpenCV when it is installed.

    python3 inspect_catalog_pdf.py catalogue.pdf --ref-regex 'NOI-[A-Z]{3}-\\d{3}'

Reports, per document:
  * edge_to_edge_pages - pages whose top accent bar spans the full sheet
    width (catches gutters, smart shrinking, wrong dpi);
  * bottom_clean_pages - pages whose last 0.8 mm is blank (no overflow);
  * refs_on_multiple_pages - product references printed on more than one
    page, i.e. a card split across sheets (must be empty);
  * qr_decoded - QR codes that decode, with a sample of their URLs.
"""
import argparse
import json
import os
import re
import subprocess
import tempfile

from PIL import Image

DPI = 254  # 10 pixels per millimetre


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pdf")
    parser.add_argument("--ref-regex", default=r"[A-Z]{2,5}-[A-Z0-9-]{2,}",
                        help="regular expression matching printed internal references")
    args = parser.parse_args()

    info = subprocess.check_output(["pdfinfo", args.pdf]).decode()
    pages = int(re.search(r"Pages:\s+(\d+)", info).group(1))
    try:
        import cv2
        detector = cv2.QRCodeDetector()
    except ImportError:
        cv2 = detector = None

    edge, clean, refs, qrs = [], 0, {}, []
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "page")
        for page in range(1, pages + 1):
            subprocess.check_call(["pdftoppm", "-r", str(DPI), "-png", "-singlefile",
                                   "-f", str(page), "-l", str(page), args.pdf, base])
            image = Image.open(base + ".png").convert("RGB")
            width, height = image.size
            row = [x for x in range(width) if image.getpixel((x, 8)) != (255, 255, 255)]
            if row and row[0] == 0 and row[-1] >= width - 5:
                edge.append(page)
            bottom = (image.getpixel((x, y)) for y in range(height - 8, height)
                      for x in range(0, width, 7))
            clean += all(px == (255, 255, 255) for px in bottom)
            text = subprocess.check_output(["pdftotext", "-layout", "-f", str(page), "-l", str(page),
                                            args.pdf, "-"]).decode()
            for ref in set(re.findall(args.ref_regex, text)):
                refs.setdefault(ref, []).append(page)
            if detector is not None:
                ok, decoded, _points, _ = detector.detectAndDecodeMulti(cv2.imread(base + ".png"))
                if ok:
                    qrs += [d for d in decoded if d]

    print(json.dumps({
        "pdf": os.path.basename(args.pdf),
        "pages": pages,
        "edge_to_edge_pages": len(edge),
        "bottom_clean_pages": clean,
        "distinct_refs": len(refs),
        "refs_on_multiple_pages": {k: v for k, v in refs.items() if len(v) > 1},
        "qr_decoded": len(qrs) if detector is not None else "opencv not installed",
        "qr_sample": sorted(qrs)[:3],
    }, indent=1))


if __name__ == "__main__":
    main()
