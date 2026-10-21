# -*- coding: utf-8 -*-
"""Deterministic page layout engine for product catalogues.

Why this exists
---------------
Odoo 17 renders PDFs with wkhtmltopdf 0.12.x (QtWebKit, ~2012). That engine has
no CSS Grid, unreliable ``flex-wrap``, ignores ``@page`` margin boxes / named
pages and only honours ``page-break-*`` as a hint. Relying on it to decide
where pages break gives catalogues whose cards are sliced in half and whose
headers drift.

So the layout is decided *here*, in plain Python, with every box given a fixed
height in millimetres:

* each card style has a metric model (``card_metrics``) that returns the exact
  height of one row of cards for the configured toggles, column count and
  image size;
* text is truncated to what fits in its fixed box (``fit_text``), the CSS
  ``overflow: hidden`` is only a safety net;
* ``paginate`` packs category headings and card rows into pages whose content
  height is known, keeping headings with their first row and repeating them
  ("continued") when a category spills over;
* the QWeb template then draws each page as a fixed-size box with
  ``page-break-after: always``. The engine never has to choose a break.

The module is ORM-free so it can be unit tested in isolation and reasoned
about with a ruler.
"""
import math
from dataclasses import dataclass, field

PT_TO_MM = 0.3528
# Average advance width of a glyph as a fraction of the em, deliberately on
# the wide side for IBM Plex Sans / Plex Sans Arabic so truncation errs short.
AVG_GLYPH_EM = 0.56

PAPER_SIZES_MM = {
    "a4": (210.0, 297.0),
    "letter": (215.9, 279.4),
}

# Head-room left at the bottom of every page box so sub-pixel rounding in
# wkhtmltopdf can never push a page box onto an extra sheet.
PAGE_SAFETY_MM = 1.0

# Effective print resolution (pixels per inch) for each quality level; used to
# pick the smallest stored Odoo image variant that is still sharp.
PRINT_DPI = {"screen": 110, "office": 150, "press": 300}
IMAGE_FIELDS = (  # (field name, max pixel edge)
    ("image_128", 128),
    ("image_256", 256),
    ("image_512", 512),
    ("image_1024", 1024),
    ("image_1920", 1920),
)

STYLE_COLUMN_LIMITS = {"grid": (1, 4), "list": (1, 2), "showcase": (1, 2)}


@dataclass(frozen=True)
class PageGeometry:
    """Physical page box and the area left for products, in millimetres."""

    width: float
    height: float
    margin_x: float = 12.0
    margin_top: float = 10.0
    margin_bottom: float = 8.0
    header_h: float = 12.0
    footer_h: float = 11.0
    gap: float = 4.0

    @classmethod
    def from_paper(cls, paper, orientation="portrait"):
        w, h = PAPER_SIZES_MM[paper]
        if orientation == "landscape":
            w, h = h, w
        return cls(width=w, height=h)

    @property
    def box_height(self):
        """Height given to the page <div>; slightly under the sheet height."""
        return round(self.height - PAGE_SAFETY_MM, 2)

    @property
    def content_width(self):
        return round(self.width - 2 * self.margin_x, 2)

    @property
    def content_height(self):
        return round(
            self.box_height - self.margin_top - self.margin_bottom
            - self.header_h - self.footer_h, 2)


@dataclass(frozen=True)
class CardSpec:
    """Everything that influences how tall one card is."""

    style: str = "grid"
    columns: int = 3
    image_size: str = "medium"
    show_image: bool = True
    show_ref: bool = True
    show_description: bool = True
    show_price: bool = True
    show_link: bool = False
    show_stock: bool = False

    def clamped_columns(self):
        lo, hi = STYLE_COLUMN_LIMITS.get(self.style, (1, 4))
        return max(lo, min(hi, int(self.columns or lo)))


@dataclass
class CardMetrics:
    """Result of the metric model; all lengths in mm, font sizes in pt."""

    columns: int
    column_width: float
    card_height: float
    row_height: float
    image_height: float
    image_width: float
    qr_size: float
    name_pt: float
    name_lines: int
    desc_pt: float
    desc_lines: int
    text_width: float
    desc_width: float
    extra: dict = field(default_factory=dict)


# Line height (mm) for a given font size (pt) and CSS line-height factor.
def line_mm(pt, factor):
    return pt * PT_TO_MM * factor


def chars_per_line(width_mm, pt):
    glyph = pt * PT_TO_MM * AVG_GLYPH_EM
    return max(4, int(width_mm / glyph))


def fit_text(text, width_mm, pt, lines):
    """Truncate ``text`` so it fits ``lines`` lines of ``width_mm`` at ``pt``.

    Cuts on a word boundary when one is reasonably close and appends an
    ellipsis. Whitespace (including newlines from HTML-to-text) is collapsed.
    """
    if not text:
        return ""
    text = " ".join(str(text).split())
    budget = chars_per_line(width_mm, pt) * lines
    if len(text) <= budget:
        return text
    cut = text[: max(1, budget - 1)]
    space = cut.rfind(" ")
    if space > budget * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-–—") + "…"


# ---------------------------------------------------------------------------
# Metric models, one per style. Constants are the CSS values in
# catalog_report.scss; keep both in sync (the SCSS reads them from inline
# style attributes wherever a value is computed here).
# ---------------------------------------------------------------------------
GRID = {
    "pad": 3.0,
    "image": {"small": 30.0, "medium": 42.0, "large": 56.0},
    "name_pt": 8.8, "name_lh": 1.25, "name_lines": 2, "name_gap": 2.2,
    "ref_h": 3.6,
    "desc_pt": 7.0, "desc_lh": 1.35, "desc_lines": 3, "desc_gap": 1.4,
    "meta_h": 9.0, "meta_gap": 2.0, "qr": 14.0,
}
LIST = {
    "pad": 2.6,
    "thumb": {"small": 16.0, "medium": 22.0, "large": 30.0},
    "name_pt": 9.0, "name_lh": 1.25,
    "ref_h": 3.6,
    "desc_pt": 7.2, "desc_lh": 1.35,
    "price_col": 30.0, "qr": 15.0, "col_gap": 3.5,
}
SHOWCASE = {
    "pad": 4.0,
    "image_1col": {"small": 62.0, "medium": 82.0, "large": 104.0},
    "image_2col": {"small": 52.0, "medium": 66.0, "large": 82.0},
    "name_pt": 13.0, "name_lh": 1.2,
    "ref_h": 4.2,
    "desc_pt": 8.4, "desc_lh": 1.4,
    "meta_h": 12.0, "qr": 18.0,
}
HEADING_H = 10.0  # category band, excluding the gap below it


def _grid_metrics(spec, geo, max_card_h):
    c = GRID
    cols = spec.clamped_columns()
    col_w = (geo.content_width - geo.gap * (cols - 1)) / cols
    inner_w = col_w - 2 * c["pad"]
    name_h = line_mm(c["name_pt"], c["name_lh"]) * c["name_lines"] + c["name_gap"]
    desc_h = (line_mm(c["desc_pt"], c["desc_lh"]) * c["desc_lines"] + c["desc_gap"]
              if spec.show_description else 0.0)
    ref_h = c["ref_h"] if spec.show_ref else 0.0
    has_meta = spec.show_price or spec.show_stock or spec.show_link
    qr = min(c["qr"], inner_w * 0.42) if spec.show_link else 0.0
    meta_h = (max(c["meta_h"], qr) + c["meta_gap"]) if has_meta else 0.0
    fixed = 2 * c["pad"] + name_h + desc_h + ref_h + meta_h
    image_h = 0.0
    if spec.show_image:
        image_h = min(c["image"][spec.image_size], inner_w)
        image_h = max(12.0, min(image_h, max_card_h - fixed))
    card_h = fixed + image_h
    return CardMetrics(
        columns=cols, column_width=col_w, card_height=card_h,
        row_height=card_h + geo.gap, image_height=image_h, image_width=inner_w,
        qr_size=qr, name_pt=c["name_pt"], name_lines=c["name_lines"],
        desc_pt=c["desc_pt"], desc_lines=c["desc_lines"] if spec.show_description else 0,
        text_width=inner_w, desc_width=inner_w,
        extra={"meta_h": meta_h - c["meta_gap"] if has_meta else 0.0,
               "price_width": inner_w - qr - (2.0 if qr else 0.0)},
    )


def _list_metrics(spec, geo, max_card_h):
    c = LIST
    cols = spec.clamped_columns()
    col_w = (geo.content_width - geo.gap * (cols - 1)) / cols
    thumb = c["thumb"][spec.image_size] if spec.show_image else 0.0
    qr = c["qr"] if spec.show_link else 0.0
    price_col = c["price_col"] if (spec.show_price or spec.show_stock) else 0.0
    # Narrow two-column lists drop the separate price column under the text.
    text_w = col_w - 2 * c["pad"] - (thumb + c["col_gap"] if thumb else 0.0) \
        - (price_col + c["col_gap"] if price_col else 0.0) - (qr + c["col_gap"] if qr else 0.0)
    text_w = max(text_w, 20.0)
    name_h = line_mm(c["name_pt"], c["name_lh"])
    desc_lines = 2 if spec.show_description else 0
    desc_h = line_mm(c["desc_pt"], c["desc_lh"]) * desc_lines + (1.0 if desc_lines else 0.0)
    text_h = name_h + (c["ref_h"] if spec.show_ref else 0.0) + desc_h
    price_h = 9.0 if price_col else 0.0
    card_h = max(thumb, qr, text_h, price_h) + 2 * c["pad"]
    card_h = min(card_h, max_card_h)
    return CardMetrics(
        columns=cols, column_width=col_w, card_height=card_h,
        row_height=card_h + 1.6, image_height=thumb, image_width=thumb,
        qr_size=qr, name_pt=c["name_pt"], name_lines=1,
        desc_pt=c["desc_pt"], desc_lines=desc_lines,
        text_width=text_w, desc_width=text_w,
        extra={"price_col": price_col},
    )


def _showcase_metrics(spec, geo, max_card_h):
    c = SHOWCASE
    cols = spec.clamped_columns()
    col_w = (geo.content_width - geo.gap * (cols - 1)) / cols
    inner_w = col_w - 2 * c["pad"]
    qr = c["qr"] if spec.show_link else 0.0
    name_lines = 2
    name_h = line_mm(c["name_pt"], c["name_lh"]) * name_lines + 2.0
    ref_h = c["ref_h"] if spec.show_ref else 0.0
    has_meta = spec.show_price or spec.show_stock or spec.show_link
    meta_h = max(c["meta_h"], qr) + 3.0 if has_meta else 0.0
    if cols == 1:
        # Image on one side, story on the other.
        image_w = inner_w * 0.46 if spec.show_image else 0.0
        image_h = c["image_1col"][spec.image_size] if spec.show_image else 0.0
        image_h = min(image_h, max_card_h - 2 * c["pad"])
        text_w = inner_w - (image_w + 6.0 if image_w else 0.0)
        body_h = max(image_h, name_h + ref_h + meta_h + 20.0)
        desc_room = body_h - name_h - ref_h - meta_h - 2.0
        desc_lines = max(0, int(desc_room / line_mm(c["desc_pt"], c["desc_lh"]))) \
            if spec.show_description else 0
        card_h = body_h + 2 * c["pad"]
    else:
        image_w = inner_w
        desc_lines = 4 if spec.show_description else 0
        desc_h = line_mm(c["desc_pt"], c["desc_lh"]) * desc_lines + (2.0 if desc_lines else 0.0)
        fixed = 2 * c["pad"] + name_h + ref_h + desc_h + meta_h + 3.0
        image_h = 0.0
        if spec.show_image:
            image_h = min(c["image_2col"][spec.image_size], inner_w * 0.85)
            image_h = max(20.0, min(image_h, max_card_h - fixed))
        text_w = inner_w
        card_h = fixed + image_h
    return CardMetrics(
        columns=cols, column_width=col_w, card_height=card_h,
        row_height=card_h + geo.gap, image_height=image_h, image_width=image_w,
        qr_size=qr, name_pt=c["name_pt"], name_lines=name_lines,
        desc_pt=c["desc_pt"], desc_lines=desc_lines,
        text_width=text_w, desc_width=text_w,
        extra={"meta_h": meta_h},
    )


METRIC_MODELS = {"grid": _grid_metrics, "list": _list_metrics, "showcase": _showcase_metrics}


def card_metrics(spec, geo):
    """Return the CardMetrics for ``spec`` on ``geo``.

    A row (plus a category heading above it) always fits on one page: tall
    images are shrunk rather than letting a row overflow the page box.
    """
    max_card_h = geo.content_height - HEADING_H - geo.gap * 2
    model = METRIC_MODELS.get(spec.style, _grid_metrics)
    return model(spec, geo, max_card_h)


def pick_image_field(image_mm, quality="office"):
    """Smallest stored image variant that still prints sharp.

    ``image_mm`` is the larger edge of the printed box. At 150 ppi a 42 mm box
    needs ~248 px, so ``image_256`` is enough; reading ``image_1920`` instead
    would put ~30x more bytes into the HTML handed to wkhtmltopdf.
    """
    needed_px = image_mm / 25.4 * PRINT_DPI.get(quality, 150)
    for fname, px in IMAGE_FIELDS:
        if px >= needed_px:
            return fname
    return IMAGE_FIELDS[-1][0]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
def paginate(groups, metrics, geo, *, show_headings=True, break_per_group=False,
             max_rows_per_page=0):
    """Pack ``groups`` into pages.

    :param groups: list of ``(heading_label or None, [card, ...])``
    :param metrics: CardMetrics
    :param geo: PageGeometry
    :param show_headings: draw a band per group
    :param break_per_group: every group starts on a fresh page
    :param max_rows_per_page: hard cap on card rows per page (0 = auto)
    :return: list of pages; a page is a list of blocks, each block either
        ``{"type": "heading", "label", "continued", "height"}`` or
        ``{"type": "row", "cards": [...], "height"}``.
    """
    cols = metrics.columns
    heading_block_h = HEADING_H + geo.gap
    budget = geo.content_height
    pages, page, used, rows_on_page = [], [], 0.0, 0

    def flush():
        nonlocal page, used, rows_on_page
        if page:
            pages.append(page)
        page, used, rows_on_page = [], 0.0, 0

    for label, cards in groups:
        if not cards:
            continue
        if break_per_group and page:
            flush()
        rows = [cards[i:i + cols] for i in range(0, len(cards), cols)]
        for idx, row in enumerate(rows):
            needs_heading = show_headings and label and (idx == 0 or not page)
            need = metrics.row_height + (heading_block_h if needs_heading else 0.0)
            full = used + need > budget + 1e-6
            capped = max_rows_per_page and rows_on_page >= max_rows_per_page
            if page and (full or capped):
                flush()
                needs_heading = show_headings and bool(label)
            if needs_heading:
                page.append({
                    "type": "heading", "label": label,
                    "continued": idx > 0, "height": HEADING_H,
                })
                used += heading_block_h
            page.append({"type": "row", "cards": row, "height": metrics.card_height})
            used += metrics.row_height
            rows_on_page += 1
    flush()
    return pages


TOC_TITLE_H = 26.0
TOC_ENTRY_H = 7.5


def toc_capacity(geo):
    return max(1, int((geo.content_height - TOC_TITLE_H) / TOC_ENTRY_H))


def build_plan(groups, metrics, geo, *, show_cover=True, show_toc=False,
               show_headings=True, break_per_group=False, max_rows_per_page=0):
    """Full document plan: cover, table of contents, product pages.

    Page numbers are final because the plan is complete before rendering, so
    the TOC can point at real pages and the footer can print "n / N".
    """
    product_pages = paginate(
        groups, metrics, geo, show_headings=show_headings,
        break_per_group=break_per_group, max_rows_per_page=max_rows_per_page)

    labels = [label for label, cards in groups if label and cards]
    toc_entries_count = len(labels) if (show_toc and show_headings) else 0
    toc_pages = math.ceil(toc_entries_count / toc_capacity(geo)) if toc_entries_count else 0
    offset = (1 if show_cover else 0) + toc_pages

    first_page_of = {}
    for n, blocks in enumerate(product_pages, start=1 + offset):
        for block in blocks:
            if block["type"] == "heading" and not block["continued"]:
                first_page_of.setdefault(block["label"], n)

    plan = []
    if show_cover:
        plan.append({"kind": "cover", "blocks": []})
    if toc_pages:
        entries = [{"label": label, "page": first_page_of.get(label)} for label in labels]
        cap = toc_capacity(geo)
        for i in range(toc_pages):
            plan.append({"kind": "toc", "entries": entries[i * cap:(i + 1) * cap],
                         "blocks": []})
    for blocks in product_pages:
        plan.append({"kind": "products", "blocks": blocks})
    total = len(plan)
    for n, page in enumerate(plan, start=1):
        page["number"] = n
        page["total"] = total
    return plan
