# -*- coding: utf-8 -*-
"""Product catalogue configuration and data preparation.

Responsibilities are split on purpose:

* this model knows *what* goes into a catalogue (filters, prices, stock,
  links) and how to read it from the database cheaply;
* ``catalog_layout`` knows *where* it goes on paper (pure Python, no ORM);
* the QWeb template only draws the plan it is given.
"""
import base64
import hashlib
import json
import logging
import re
import uuid
from collections import OrderedDict, defaultdict
from urllib.parse import quote

import qrcode
from markupsafe import Markup

from odoo import api, fields, models
from odoo.addons.base.models.res_partner import _lang_get
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import SQL, format_date, format_datetime, formatLang, html2plaintext
from odoo.tools.image import image_data_uri
from odoo.tools.pdf import merge_pdf
from odoo.tools.safe_eval import safe_eval

from . import catalog_layout as layout

_logger = logging.getLogger(__name__)

# Products are read in slices of this size; the record cache of each slice is
# released before the next one is read.
READ_CHUNK = 200
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
CACHE_PREFIX = "pcat:"
CACHE_KEEP = 8  # cached PDFs kept per catalogue
# Pages per wkhtmltopdf run; bounds the renderer's memory on big catalogues.
PAGE_BATCH = 40
# Data prepared once per rendering and shared by its page batches, keyed by a
# one-off token carried in the context (process-local, removed after use).
PREPARED_DATA = {}

# Fields whose value changes the rendered PDF (part of the cache fingerprint).
CONFIG_FIELDS = [
    "name", "catalog_type", "style", "columns", "image_size", "print_quality",
    "paper_size", "orientation", "accent_color", "show_cover", "cover_title",
    "cover_subtitle", "show_toc", "group_by_category", "page_break_per_category",
    "max_rows_per_page", "show_price", "show_description", "show_link",
    "show_default_code", "show_stock", "show_image", "show_compare_price",
    "description_source", "price_tax_display", "pricelist_id", "warehouse_id",
    "low_stock_threshold", "hide_out_of_stock", "categ_ids", "tag_ids",
    "attribute_value_ids", "product_tmpl_ids", "exclude_product_tmpl_ids",
    "only_saleable", "extra_domain", "sort_order", "link_url_pattern",
    "valid_from", "valid_until", "notes", "company_id",
]


class _SafeFormatDict(dict):
    """``str.format_map`` helper: unknown placeholders render empty."""

    def __missing__(self, key):
        return ""


class ProductCatalog(models.Model):
    _name = "product.catalog"
    _description = "Product Catalogue"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "sequence, name, id"
    _check_company_auto = True
    # Catalogue users (read-only) log the emails they send in the chatter.
    _mail_post_access = "read"

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        "res.company", required=True, index=True,
        default=lambda self: self.env.company)
    catalog_type = fields.Selection(
        [("general", "General range"),
         ("customer", "Customer-specific"),
         ("promotion", "Seasonal / Promotion"),
         ("internal", "Internal / Sales team")],
        string="Catalogue Type", required=True, default="general", tracking=True)
    lang = fields.Selection(
        _lang_get, string="Language",
        default=lambda self: self.env.lang,
        help="Language the catalogue is printed in. Arabic and other "
             "right-to-left languages mirror the whole layout. When sending, "
             "each recipient can receive their own language instead.")
    notes = fields.Text(
        string="Footer Note", translate=True,
        help="Short line printed in the footer of every page, e.g. terms.")
    valid_from = fields.Date()
    valid_until = fields.Date()

    # ------------------------------------------------------------------
    # Design
    # ------------------------------------------------------------------
    style = fields.Selection(
        [("grid", "Grid cards"),
         ("list", "Compact list"),
         ("showcase", "Showcase")],
        required=True, default="grid", tracking=True,
        help="Grid cards: 1-4 products per row, image on top.\n"
             "Compact list: one dense line per product, ideal for long ranges.\n"
             "Showcase: large imagery and full descriptions, 1-2 per row.")
    columns = fields.Integer(
        string="Boxes per Row", default=3, required=True,
        help="Grid: 1-4. Compact list and Showcase: 1-2.")
    image_size = fields.Selection(
        [("small", "Small"), ("medium", "Medium"), ("large", "Large")],
        required=True, default="medium")
    print_quality = fields.Selection(
        [("screen", "Screen (lightest file)"),
         ("office", "Office printer"),
         ("press", "Professional print")],
        required=True, default="office",
        help="Decides which stored image resolution is embedded. Higher "
             "quality means larger PDFs and slower rendering.")
    paper_size = fields.Selection(
        [("a4", "A4"), ("letter", "US Letter")], required=True, default="a4")
    orientation = fields.Selection(
        [("portrait", "Portrait"), ("landscape", "Landscape")],
        required=True, default="portrait")
    accent_color = fields.Char(default="#1D4E89", required=True)
    show_cover = fields.Boolean(string="Cover Page", default=True)
    cover_title = fields.Char(translate=True)
    cover_subtitle = fields.Char(translate=True)
    cover_image = fields.Image(max_width=1600, max_height=1600)
    show_toc = fields.Boolean(
        string="Table of Contents", default=False,
        help="Lists each category with its page number. Requires grouping "
             "by category.")

    # ------------------------------------------------------------------
    # Page break controls
    # ------------------------------------------------------------------
    group_by_category = fields.Boolean(default=True)
    page_break_per_category = fields.Boolean(
        string="New Page per Category", default=False)
    max_rows_per_page = fields.Integer(
        default=0,
        help="Upper bound of product rows per page. 0 fits as many rows as "
             "the page height allows.")

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------
    show_image = fields.Boolean(string="Show Image", default=True)
    show_price = fields.Boolean(string="Show Price", default=True)
    show_compare_price = fields.Boolean(
        string="Show Price Before Discount", default=True,
        help="Strike through the price before discount when the applied "
             "pricelist rule is a percentage discount.")
    show_description = fields.Boolean(string="Show Description", default=True)
    description_source = fields.Selection(
        [("sale", "Sales description"), ("product", "Product description")],
        default="sale", required=True)
    show_link = fields.Boolean(
        string="Show Product Link (QR)", default=False,
        help="Prints a QR code per product. Uses the URL pattern below, or "
             "the eCommerce page when the Website Sale app is installed.")
    link_url_pattern = fields.Char(
        string="Product URL Pattern",
        help="Placeholders: {base_url}, {id}, {default_code}, {barcode}.\n"
             "Example: https://shop.example.com/p/{default_code}")
    show_default_code = fields.Boolean(string="Show Internal Reference", default=True)
    show_stock = fields.Boolean(
        string="Show Availability", default=False,
        help="Prints an availability badge (In stock / Low stock / On "
             "request). Exact quantities are never printed.")

    # ------------------------------------------------------------------
    # Commercial context
    # ------------------------------------------------------------------
    pricelist_id = fields.Many2one(
        "product.pricelist", required=True, check_company=True, tracking=True,
        default=lambda self: self.env["product.pricelist"].search(
            [("company_id", "in", [False, self.env.company.id])], limit=1),
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    currency_id = fields.Many2one(related="pricelist_id.currency_id")
    price_tax_display = fields.Selection(
        [("excluded", "Tax excluded"), ("included", "Tax included")],
        string="Prices", default="excluded", required=True,
        help="Tax included needs the Invoicing app (customer taxes).")
    warehouse_id = fields.Many2one(
        "stock.warehouse", check_company=True,
        default=lambda self: self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1),
        domain="[('company_id', '=', company_id)]",
        help="Availability is computed for this warehouse only.")
    low_stock_threshold = fields.Float(default=5.0)
    hide_out_of_stock = fields.Boolean(
        string="Hide Unavailable Products", default=False,
        help="Skip storable products with no free quantity.")

    # ------------------------------------------------------------------
    # Product selection
    # ------------------------------------------------------------------
    categ_ids = fields.Many2many(
        "product.category", string="Categories",
        help="Includes sub-categories. Empty means all categories.")
    tag_ids = fields.Many2many("product.tag", string="Product Tags")
    attribute_value_ids = fields.Many2many(
        "product.attribute.value", string="Attribute Values",
        help="Products offering at least one of these values.")
    product_tmpl_ids = fields.Many2many(
        "product.template", "product_catalog_include_tmpl_rel",
        "catalog_id", "tmpl_id", string="Only These Products")
    exclude_product_tmpl_ids = fields.Many2many(
        "product.template", "product_catalog_exclude_tmpl_rel",
        "catalog_id", "tmpl_id", string="Excluded Products")
    only_saleable = fields.Boolean(string="Can be Sold Only", default=True)
    extra_domain = fields.Char(
        string="Advanced Filter", default="[]",
        help="Additional domain on product templates.")
    sort_order = fields.Selection(
        [("name", "Name"),
         ("default_code", "Internal reference"),
         ("price_asc", "Price: low to high"),
         ("price_desc", "Price: high to low")],
        default="name", required=True,
        help="Order of products inside each category.")
    max_products = fields.Integer(
        default=1500,
        help="Safety limit. Very large catalogues should be split by "
             "category so each PDF stays fast to render and easy to email.")
    cache_ttl_minutes = fields.Integer(
        string="Reuse Generated PDF For (min)", default=60,
        help="A PDF generated for the same configuration, pricelist and "
             "language is reused for this long when sending. 0 disables it.")

    # ------------------------------------------------------------------
    # Informational
    # ------------------------------------------------------------------
    product_count = fields.Integer(compute="_compute_product_count")
    last_generated_on = fields.Datetime(readonly=True, copy=False)

    _max_products_positive = models.Constraint(
        'CHECK(max_products > 0)',
        "The product limit must be positive.",
    )
    _ttl_positive = models.Constraint(
        'CHECK(cache_ttl_minutes >= 0)',
        "The PDF reuse delay cannot be negative.",
    )

    # ------------------------------------------------------------------
    # Constraints & onchanges
    # ------------------------------------------------------------------
    @api.constrains("style", "columns")
    def _check_columns(self):
        for catalog in self:
            lo, hi = layout.STYLE_COLUMN_LIMITS[catalog.style]
            if not lo <= catalog.columns <= hi:
                style_label = dict(self._fields["style"].selection)[catalog.style]
                raise ValidationError(self.env._(
                    "%(style)s supports %(lo)s to %(hi)s boxes per row.",
                    style=style_label, lo=lo, hi=hi))

    @api.constrains("accent_color")
    def _check_accent_color(self):
        for catalog in self:
            if not HEX_COLOR_RE.match(catalog.accent_color or ""):
                raise ValidationError(self.env._("The accent colour must look like #1D4E89."))

    @api.constrains("valid_from", "valid_until")
    def _check_validity(self):
        for catalog in self:
            if catalog.valid_from and catalog.valid_until \
                    and catalog.valid_from > catalog.valid_until:
                raise ValidationError(self.env._("The validity end must follow its start."))

    @api.constrains("extra_domain")
    def _check_extra_domain(self):
        for catalog in self:
            try:
                domain = catalog._parse_extra_domain()
                self.env["product.template"].search(domain, limit=1)
            except Exception as exc:  # pylint: disable=broad-except
                raise ValidationError(self.env._("The advanced filter is invalid: %s", exc)) from exc

    @api.onchange("style")
    def _onchange_style(self):
        lo, hi = layout.STYLE_COLUMN_LIMITS[self.style]
        if not lo <= self.columns <= hi:
            self.columns = {"grid": 3, "list": 1, "showcase": 2}[self.style]

    # ------------------------------------------------------------------
    # Product selection
    # ------------------------------------------------------------------
    def _parse_extra_domain(self):
        self.ensure_one()
        domain = safe_eval(self.extra_domain or "[]", {})
        if not isinstance(domain, (list, tuple)):
            raise ValueError(self.env._("a domain must be a list"))
        return list(domain)

    def _get_product_domain(self):
        """Domain on product.template for this catalogue."""
        self.ensure_one()
        domain = [
            ("active", "=", True),
            ("company_id", "in", [False, self.company_id.id]),
        ]
        if self.only_saleable:
            domain.append(("sale_ok", "=", True))
        if self.categ_ids:
            domain.append(("categ_id", "child_of", self.categ_ids.ids))
        if self.tag_ids:
            domain.append(("product_tag_ids", "in", self.tag_ids.ids))
        if self.attribute_value_ids:
            domain.append(("attribute_line_ids.value_ids", "in", self.attribute_value_ids.ids))
        if self.product_tmpl_ids:
            domain.append(("id", "in", self.product_tmpl_ids.ids))
        if self.exclude_product_tmpl_ids:
            domain.append(("id", "not in", self.exclude_product_tmpl_ids.ids))
        return Domain.AND([domain, self._parse_extra_domain()])

    def _get_search_order(self):
        order = []
        if self.group_by_category:
            order.append("categ_id")  # product.category._order = complete_name
        if self.sort_order == "default_code":
            order.append("default_code")
        order += ["name", "id"]
        return ", ".join(order)

    @api.depends("categ_ids", "tag_ids", "attribute_value_ids", "product_tmpl_ids",
                 "exclude_product_tmpl_ids", "only_saleable", "extra_domain", "company_id")
    def _compute_product_count(self):
        Template = self.env["product.template"]
        for catalog in self:
            try:
                catalog.product_count = Template.search_count(catalog._get_product_domain())
            except Exception:  # pylint: disable=broad-except
                catalog.product_count = 0

    # ------------------------------------------------------------------
    # Layout inputs
    # ------------------------------------------------------------------
    def _get_geometry(self):
        self.ensure_one()
        return layout.PageGeometry.from_paper(self.paper_size, self.orientation)

    def _get_card_spec(self):
        self.ensure_one()
        return layout.CardSpec(
            style=self.style,
            columns=self.columns,
            image_size=self.image_size,
            show_image=self.show_image,
            show_ref=self.show_default_code,
            show_description=self.show_description,
            show_price=self.show_price,
            show_link=self.show_link,
            show_stock=self.show_stock,
        )

    def _get_paperformat(self):
        self.ensure_one()
        return self.env.ref(
            "product_catalogue_pdf_advanced.paperformat_catalog_%s_%s"
            % (self.paper_size, self.orientation))

    # ------------------------------------------------------------------
    # Data preparation (batched)
    # ------------------------------------------------------------------
    def _compute_template_prices(self, templates, pricelist):
        """Prices per template using Odoo's own pricelist engine.

        Variants are priced individually (so attribute extras and
        variant-specific rules are honoured) and folded into a min/max per
        template, giving "from X" when variants differ.

        :return: {template_id: {"price", "price_max", "compare"}}
        """
        variants = templates.with_context(active_test=True).product_variant_ids
        if not variants:
            return {}
        currency = pricelist.currency_id
        company = self.company_id
        now = fields.Datetime.now()
        rule_prices = pricelist._compute_price_rule(variants, 1.0, date=now)
        # Odoo 18 removed the pricelist "discount policy". A discount is shown
        # when the applied rule is a percentage rule, and the price before
        # discount comes from the rule itself (same logic as sale orders).
        public = {}
        if self.show_compare_price:
            Item = self.env["product.pricelist.item"]
            for variant in variants:
                rule = Item.browse(rule_prices[variant.id][1])
                if rule and rule.compute_price == "percentage":
                    public[variant.id] = rule._compute_price_before_discount(
                        product=variant, quantity=1.0, uom=variant.uom_id, date=now,
                        currency=currency)
        tax_included = self.price_tax_display == "included" \
            and "taxes_id" in variants._fields

        result = {}
        for variant in variants:
            price = rule_prices[variant.id][0]
            compare = public.get(variant.id, 0.0)
            if tax_included:
                taxes = variant.taxes_id.filtered(lambda t: t.company_id == company)
                if taxes:
                    price = taxes.compute_all(price, currency, 1.0, product=variant)["total_included"]
                    if compare:
                        compare = taxes.compute_all(
                            compare, currency, 1.0, product=variant)["total_included"]
            entry = result.setdefault(variant.product_tmpl_id.id, {
                "price": price, "price_max": price, "compare": compare})
            if price < entry["price"]:
                entry["price"], entry["compare"] = price, compare
            entry["price_max"] = max(entry["price_max"], price)
        for entry in result.values():
            if currency.compare_amounts(entry["compare"], entry["price"]) <= 0:
                entry["compare"] = 0.0
        return result

    def _compute_template_stock(self, templates):
        """Free quantity per storable template in the catalogue warehouse.

        Runs as superuser: availability bands are shown to people who may
        not have Inventory rights, and only the band leaves this method.

        :return: {template_id: free_qty} (storable templates only)
        """
        storable = templates.filtered("is_storable")
        if not storable:
            return {}
        ctx = {"warehouse": self.warehouse_id.id} if self.warehouse_id else {}
        variants = storable.with_context(active_test=True).product_variant_ids
        quantities = variants.sudo().with_context(**ctx)._compute_quantities_dict(
            None, None, None)
        result = defaultdict(float)
        for variant in variants:
            result[variant.product_tmpl_id.id] += quantities[variant.id]["free_qty"]
        return result

    def _stock_band(self, free_qty):
        if free_qty is None:
            return False
        if free_qty <= 0:
            return "out"
        if free_qty < self.low_stock_threshold:
            return "low"
        return "in"

    def _get_product_url(self, values, base_url):
        """Public URL for a product, or False.

        A pattern placeholder that resolves to nothing (e.g. {default_code}
        on a product without a reference) yields no link rather than a
        broken one.
        """
        if self.link_url_pattern:
            mapping = {
                "base_url": base_url,
                "id": str(values["id"]),
                "default_code": quote(values.get("default_code") or "", safe=""),
                "barcode": quote(values.get("barcode") or "", safe=""),
            }
            used = set(re.findall(r"{(\w+)}", self.link_url_pattern))
            if any(not mapping.get(key) for key in used):
                return False
            try:
                return self.link_url_pattern.format_map(_SafeFormatDict(mapping))
            except (ValueError, IndexError):
                return False
        if values.get("website_url"):
            return base_url + values["website_url"]
        return False

    @api.model
    def _qr_data_uri(self, url):
        """QR code as an inline SVG data URI.

        Vector output stays sharp at any print size and weighs a few hundred
        bytes, against ~5 kB for a PNG; it uses ``qrcode``, already part of
        Odoo's requirements, rather than reportlab's raster backend.
        """
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M, border=0, box_size=1)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        size = len(matrix)
        path = []
        for y, row in enumerate(matrix):
            x = 0
            while x < size:
                if row[x]:
                    start = x
                    while x < size and row[x]:
                        x += 1
                    path.append("M%d %dh%dv1h-%dz" % (start, y, x - start, x - start))
                else:
                    x += 1
        # Explicit width/height: without them QtWebKit assumes a 300x150
        # intrinsic size and stretches the code out of square.
        px = size * 8
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
               'viewBox="0 0 %d %d" shape-rendering="crispEdges">'
               '<path fill="#0F172A" d="%s"/></svg>'
               % (px, px, size, size, "".join(path)))
        return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()

    def _prepare_cards(self, pricelist, metrics):
        """Read products slice by slice and build card dicts.

        Only the fields that are printed are read, the image field is the
        smallest one that prints sharp, and each slice's cache is dropped
        before reading the next so memory stays flat with catalogue size.
        """
        self.ensure_one()
        Template = self.env["product.template"]
        ids = Template.search(self._get_product_domain(), order=self._get_search_order()).ids
        if len(ids) > self.max_products:
            raise UserError(self.env._(
                "This catalogue selects %(count)s products, above its limit of "
                "%(limit)s. Narrow the filters or split it by category.",
                count=len(ids), limit=self.max_products))

        image_field = layout.pick_image_field(
            max(metrics.image_height, metrics.image_width), self.print_quality)
        has_website = "website_url" in Template._fields
        base_url = self.get_base_url()
        read_fields = ["name", "default_code", "barcode", "categ_id", "is_storable", "uom_id"]
        if self.show_description:
            read_fields.append(
                "description_sale" if self.description_source == "sale" else "description")
        if self.show_image:
            read_fields.append(image_field)
        if self.show_link and has_website and not self.link_url_pattern:
            read_fields.append("website_url")

        name_w = metrics.text_width
        lang_env = self.env
        category_labels = {}
        cards = []
        for start in range(0, len(ids), READ_CHUNK):
            chunk = Template.browse(ids[start:start + READ_CHUNK])
            rows = chunk.with_context(bin_size=False).read(read_fields)
            prices = self._compute_template_prices(chunk, pricelist) \
                if self.show_price or self.sort_order.startswith("price") else {}
            stock = self._compute_template_stock(chunk) \
                if self.show_stock or self.hide_out_of_stock else {}
            missing = {v["categ_id"][0] for v in rows if v["categ_id"]} - set(category_labels)
            category_labels.update(self._category_labels(missing))
            for values in rows:
                free_qty = stock.get(values["id"]) if values["is_storable"] else None
                band = self._stock_band(free_qty) if self.warehouse_id else False
                if self.hide_out_of_stock and band == "out":
                    continue
                price = prices.get(values["id"])
                if self.show_price and not price:
                    # Template without an active variant: nothing sellable.
                    continue
                description = ""
                if self.show_description:
                    raw = values.get("description_sale") if self.description_source == "sale" \
                        else html2plaintext(values.get("description") or "")
                    description = layout.fit_text(
                        raw, metrics.desc_width, metrics.desc_pt, metrics.desc_lines)
                image = values.get(image_field) if self.show_image else False
                url = self._get_product_url(values, base_url) if self.show_link else False
                card = {
                    "id": values["id"],
                    "name": layout.fit_text(
                        values["name"], name_w, metrics.name_pt, metrics.name_lines),
                    "full_name": values["name"],
                    "initial": (values["name"] or "?").strip()[:1].upper(),
                    "default_code": values.get("default_code") or "",
                    "category": category_labels.get(values["categ_id"][0], "")
                    if values["categ_id"] else "",
                    "description": description,
                    "image": image_data_uri(image) if image else False,
                    "url": url,
                    "qr": self._qr_data_uri(url) if url else False,
                    "stock_band": band if self.show_stock else False,
                    "price_value": price["price"] if price else 0.0,
                }
                if self.show_price and price:
                    currency = pricelist.currency_id
                    card.update({
                        "price": formatLang(lang_env, price["price"], currency_obj=currency),
                        "price_from": currency.compare_amounts(
                            price["price_max"], price["price"]) > 0,
                        "compare": formatLang(lang_env, price["compare"], currency_obj=currency)
                        if price["compare"] else False,
                    })
                cards.append(card)
            # Release this slice (images included) from the record cache.
            chunk.invalidate_recordset()
            chunk.product_variant_ids.invalidate_recordset()
        return cards

    def _category_labels(self, categ_ids):
        """Translated, human category labels: "Furniture / Chairs".

        ``complete_name`` is stored untranslated and always starts with the
        technical root ("All / ..."), so labels are rebuilt from translated
        names, dropping the root when it is a mere container.
        """
        labels = {}
        for categ in self.env["product.category"].browse(categ_ids):
            chain = []
            node = categ
            while node:
                chain.append(node.name)
                node = node.parent_id
            if len(chain) > 1:
                chain.pop()  # root category
            labels[categ.id] = " / ".join(reversed(chain))
        return labels

    def _group_cards(self, cards):
        groups = OrderedDict()
        for card in cards:
            key = card["category"] if self.group_by_category else None
            groups.setdefault(key, []).append(card)
        result = list(groups.items())
        if self.sort_order in ("price_asc", "price_desc"):
            reverse = self.sort_order == "price_desc"
            result = [(k, sorted(v, key=lambda c: c["price_value"], reverse=reverse))
                      for k, v in result]
        return result

    def _prepare_catalog_data(self, pricelist=None):
        """Everything the QWeb template needs for one catalogue.

        Must be called in the language the PDF is printed in (product names,
        number formats and dates follow ``self.env.lang``).
        """
        self.ensure_one()
        pricelist = pricelist or self.pricelist_id
        geo = self._get_geometry()
        metrics = layout.card_metrics(self._get_card_spec(), geo)
        cards = self._prepare_cards(pricelist, metrics)
        groups = self._group_cards(cards)
        plan = layout.build_plan(
            groups, metrics, geo,
            show_cover=self.show_cover,
            show_toc=self.show_toc and self.group_by_category,
            show_headings=self.group_by_category,
            break_per_group=self.group_by_category and self.page_break_per_category,
            max_rows_per_page=self.max_rows_per_page,
        )
        lang = self.env["res.lang"]._lang_get(self.env.lang or "en_US")
        company = self.company_id
        tz = self.env.user.tz or "UTC"
        scope = "o_pcat_%s" % self.id
        type_label = dict(self._fields["catalog_type"]._description_selection(self.env))
        return {
            "plan": plan,
            "geo": geo,
            "metrics": metrics,
            "spec": self._get_card_spec(),
            "product_count": len(cards),
            "category_count": len([g for g, c in groups if g]),
            "category_names": [g for g, c in groups if g],
            "direction": lang.direction or "ltr",
            "lang": self.env.lang,
            "pricelist": pricelist,
            "currency": pricelist.currency_id,
            "company": company,
            "warehouse": self.warehouse_id,
            "generated_on": format_datetime(
                self.env, fields.Datetime.now(), tz=tz, dt_format="medium"),
            "valid_from": format_date(self.env, self.valid_from) if self.valid_from else False,
            "valid_until": format_date(self.env, self.valid_until) if self.valid_until else False,
            "scope": scope,
            "theme_css": Markup(build_theme_css(scope, self.accent_color)),
            "type_label": type_label.get(self.catalog_type, ""),
            "title": self.cover_title or self.name,
            "tax_included": self.price_tax_display == "included",
            "footer_contact": " · ".join(
                x for x in (company.phone, company.email, company.website) if x),
            "address": ", ".join(x for x in (
                company.street, company.city, company.country_id.name) if x),
        }

    # ------------------------------------------------------------------
    # PDF generation, caching and actions
    # ------------------------------------------------------------------
    def _get_cache_fingerprint(self, pricelist, lang):
        """Hash of everything the PDF depends on, bucketed by the TTL.

        Configuration is hashed by value (not by write_date: writing
        ``last_generated_on`` must not invalidate the cache). Products and
        pricelist rules are tracked by their latest write_date. Stock is not
        hashed (it moves constantly): it is covered by the TTL bucket, and
        the PDF prints its own generation time.
        """
        self.ensure_one()
        self.env.flush_all()
        tmpl_ids = self.env["product.template"].search(self._get_product_domain()).ids
        cr = self.env.cr
        cr.execute(SQL("""
            SELECT max(t.write_date), max(p.write_date)
              FROM product_template t
         LEFT JOIN product_product p ON p.product_tmpl_id = t.id
             WHERE t.id = ANY(%s)
        """, tmpl_ids or [0]))
        tmpl_wd, variant_wd = cr.fetchone()
        cr.execute(SQL(
            "SELECT max(write_date) FROM product_pricelist_item WHERE pricelist_id = %s",
            pricelist.id))
        items_wd = cr.fetchone()[0]
        ttl = max(self.cache_ttl_minutes, 1) * 60
        bucket = int(fields.Datetime.now().timestamp() // ttl)
        config = self.read(CONFIG_FIELDS)[0]
        payload = json.dumps({
            "config": config,
            "company": str(self.company_id.write_date),
            "pricelist": [pricelist.id, str(pricelist.write_date), str(items_wd)],
            "lang": lang,
            "products": [hashlib.sha1(str(tmpl_ids).encode()).hexdigest(),
                         str(tmpl_wd), str(variant_wd)],
            "bucket": bucket,
        }, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def _render_pdf(self, pricelist=None, lang=None):
        """Render the catalogue PDF in batches of pages.

        wkhtmltopdf, not the ORM, is the memory hot spot: its footprint grows
        with the HTML it lays out (~1 GB for 150 pages). The page plan is
        final before rendering, so it is cut into independent batches,
        each rendered by its own wkhtmltopdf run, and the PDFs are merged.
        Data is prepared once and shared by the batches.
        """
        self.ensure_one()
        pricelist = pricelist or self.pricelist_id
        lang = lang or self.lang or self.env.lang
        data = self.with_context(lang=lang)._prepare_catalog_data(pricelist=pricelist)
        token = uuid.uuid4().hex
        PREPARED_DATA[token] = {self.id: data}
        try:
            total = len(data["plan"])
            streams, fmt = [], "pdf"
            for start in range(0, total, PAGE_BATCH):
                report = self.env["ir.actions.report"].with_context(
                    lang=lang, pcat_force_lang=lang, pcat_pricelist_id=pricelist.id,
                    pcat_prepared=token, pcat_page_range=(start, min(start + PAGE_BATCH, total)))
                content, fmt = report._render_qweb_pdf(
                    "product_catalogue_pdf_advanced.action_report_product_catalog", self.ids)
                streams.append(content)
        finally:
            PREPARED_DATA.pop(token, None)
        if fmt != "pdf" or len(streams) == 1:
            # Test mode returns HTML (one document per batch).
            return b"".join(streams)
        return merge_pdf(streams)

    def _get_pdf_attachment(self, pricelist=None, lang=None):
        """Return an ir.attachment with the PDF, reusing a fresh cached one."""
        self.ensure_one()
        pricelist = pricelist or self.pricelist_id
        lang = lang or self.lang or self.env.lang
        Attachment = self.env["ir.attachment"].sudo()
        fingerprint = self._get_cache_fingerprint(pricelist, lang)
        domain = [("res_model", "=", self._name), ("res_id", "=", self.id),
                  ("description", "=", CACHE_PREFIX + fingerprint)]
        if self.cache_ttl_minutes:
            cached = Attachment.search(domain, limit=1)
            if cached:
                return cached
        pdf = self._render_pdf(pricelist=pricelist, lang=lang)
        suffix = []
        if pricelist != self.pricelist_id:
            suffix.append(pricelist.name)
        if lang != (self.lang or self.env.lang):
            suffix.append(lang)
        filename = "%s%s.pdf" % (
            self.with_context(lang=lang).name,
            (" - " + " - ".join(suffix)) if suffix else "")
        attachment = Attachment.create({
            "name": re.sub(r'[\\/:*?"<>|]+', "-", filename),
            "type": "binary",
            "raw": pdf,
            "mimetype": "application/pdf",
            "res_model": self._name,
            "res_id": self.id,
            "description": CACHE_PREFIX + fingerprint,
        })
        self.sudo().last_generated_on = fields.Datetime.now()
        stale = Attachment.search(
            [("res_model", "=", self._name), ("res_id", "=", self.id),
             ("description", "=like", CACHE_PREFIX + "%")],
            order="id desc", offset=CACHE_KEEP)
        stale.unlink()
        return attachment

    def action_print(self):
        """Download the PDF (batched rendering, cached for the TTL)."""
        self.ensure_one()
        attachment = self._get_pdf_attachment()
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }

    def action_preview_html(self):
        self.ensure_one()
        action = self.env.ref(
            "product_catalogue_pdf_advanced.action_report_product_catalog"
        ).report_action(self)
        action["report_type"] = "qweb-html"
        return action

    def action_open_send_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Send Catalogue"),
            "res_model": "product.catalog.send",
            "view_mode": "form",
            "target": "new",
            "context": {"default_catalog_id": self.id},
        }

    def action_view_products(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Products in %s", self.name),
            "res_model": "product.template",
            "view_mode": "kanban,list,form",
            "domain": list(self._get_product_domain()),
        }

    def copy_data(self, default=None):
        vals_list = super().copy_data(default=default)
        if "name" not in (default or {}):
            for catalog, vals in zip(self, vals_list, strict=True):
                vals["name"] = self.env._("%s (copy)", catalog.name)
        return vals_list


def _rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _mix_with_white(hex_color, ratio):
    """Tint ``hex_color`` towards white by ``ratio`` (0..1)."""
    try:
        r, g, b = _rgb(hex_color)
    except (TypeError, ValueError):
        return "#EEF2F7"
    mix = lambda c: round(c + (255 - c) * ratio)  # noqa: E731
    return "#%02X%02X%02X" % (mix(r), mix(g), mix(b))


def _relative_luminance(hex_color):
    def channel(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(color_a, color_b):
    """WCAG 2.x contrast ratio between two #RRGGBB colours."""
    la, lb = sorted((_relative_luminance(color_a), _relative_luminance(color_b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable_on_white(hex_color, minimum=4.5):
    """Darken ``hex_color`` until it reaches WCAG AA contrast on white.

    Prices and labels are printed in the accent colour; a pale brand colour
    (yellow, mint...) would otherwise be unreadable on paper.
    """
    r, g, b = _rgb(hex_color)
    color = hex_color.upper()
    while contrast_ratio(color, "#FFFFFF") < minimum and (r or g or b):
        r, g, b = (int(c * 0.9) for c in (r, g, b))
        color = "#%02X%02X%02X" % (r, g, b)
    return color


def build_theme_css(scope, accent):
    """Per-catalogue colour rules, scoped to ``.{scope}``.

    QtWebKit has no CSS variables, so the accent is written into a small
    scoped stylesheet instead. ``accent`` is validated as #RRGGBB upstream.
    """
    text = readable_on_white(accent)
    soft = _mix_with_white(accent, 0.92)
    mid = _mix_with_white(accent, 0.6)
    # Light brand colours get dark text on the cover instead of white.
    on_accent = "#FFFFFF" if contrast_ratio(accent, "#FFFFFF") >= 3.0 else "#0F172A"
    s = "." + scope
    rules = [
        f"{s} .o_pcat_topbar, {s} .o_pcat_cover_panel, {s} .o_pcat_cover_bar,"
        f" {s} .o_pcat_toc_rule {{ background: {accent}; }}",
        f"{s} .o_pcat_heading {{ border-color: {accent}; background: {soft}; }}",
        f"{s} .o_pcat_price, {s} .o_pcat_toc_page, {s} .o_pcat_eyebrow,"
        f" {s} .o_pcat_heading_label {{ color: {text}; }}",
        f"{s} .o_pcat_monogram {{ color: {mid}; }}",
        f"{s} .o_pcat_noimg {{ background: {soft}; }}",
        f"{s} .o_pcat_cover_text, {s} .o_pcat_cover_title {{ color: {on_accent}; }}",
        f"{s} .o_pcat_cover_text .o_pcat_eyebrow, {s} .o_pcat_cover_subtitle"
        f" {{ color: {on_accent}; opacity: 0.85; }}",
    ]
    return "\n".join(rules)
