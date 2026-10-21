# -*- coding: utf-8 -*-
"""Tests for product_catalogue_pdf_advanced.

Where possible the expected value comes from an oracle that is not this
module: prices from Odoo's pricelist engine, taxes from account.tax, page
counts from the PDF bytes wkhtmltopdf produced.
"""
import base64
import io
import itertools
import random
from unittest.mock import patch

from PIL import Image

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, HttpCase, TransactionCase, new_test_user, tagged

from ..models import catalog_layout as layout
from ..models.product_catalog import contrast_ratio, readable_on_white

REPORT = "product_catalogue_pdf_advanced.action_report_product_catalog"


def _png(color=(30, 90, 160), size=64):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "PNG")
    return base64.b64encode(buf.getvalue())


# ---------------------------------------------------------------------------
# Layout engine (pure Python)
# ---------------------------------------------------------------------------
@tagged("post_install", "-at_install", "pcat")
class TestCatalogLayout(TransactionCase):

    def _all_specs(self):
        for style, (lo, hi) in layout.STYLE_COLUMN_LIMITS.items():
            for cols, size, flags in itertools.product(
                    range(lo, hi + 1), ("small", "medium", "large"),
                    itertools.product((True, False), repeat=5)):
                image, ref, desc, price, link = flags
                yield layout.CardSpec(
                    style=style, columns=cols, image_size=size, show_image=image,
                    show_ref=ref, show_description=desc, show_price=price,
                    show_link=link, show_stock=price)

    def test_geometry(self):
        geo = layout.PageGeometry.from_paper("a4")
        self.assertEqual((geo.width, geo.height), (210.0, 297.0))
        self.assertLess(geo.box_height, geo.height, "page box keeps a safety margin")
        self.assertAlmostEqual(geo.content_width, 186.0)
        land = layout.PageGeometry.from_paper("letter", "landscape")
        self.assertEqual((land.width, land.height), (279.4, 215.9))

    def test_every_row_fits_on_a_page(self):
        """No combination of options can produce a row taller than a page."""
        for paper, orientation in itertools.product(("a4", "letter"), ("portrait", "landscape")):
            geo = layout.PageGeometry.from_paper(paper, orientation)
            for spec in self._all_specs():
                m = layout.card_metrics(spec, geo)
                self.assertLessEqual(
                    m.row_height + layout.HEADING_H + geo.gap, geo.content_height + 1e-6,
                    "%s %s %s" % (paper, orientation, spec))
                self.assertGreater(m.card_height, 0)
                total_w = m.columns * m.column_width + (m.columns - 1) * geo.gap
                self.assertAlmostEqual(total_w, geo.content_width, places=6)

    def test_pagination_never_overflows_or_loses_cards(self):
        rnd = random.Random(42)
        geo = layout.PageGeometry.from_paper("a4")
        for spec in itertools.islice(self._all_specs(), 0, None, 7):
            m = layout.card_metrics(spec, geo)
            groups = [("Cat %d" % g, list(range(g * 1000, g * 1000 + rnd.randint(0, 23))))
                      for g in range(rnd.randint(1, 6))]
            for break_per_group, cap in ((False, 0), (True, 0), (False, 2)):
                pages = layout.paginate(groups, m, geo, break_per_group=break_per_group,
                                        max_rows_per_page=cap)
                seen = []
                for blocks in pages:
                    used = sum(b["height"] + (geo.gap if b["type"] == "heading" else
                                              m.row_height - m.card_height) for b in blocks)
                    self.assertLessEqual(used, geo.content_height + 1e-6)
                    self.assertEqual(blocks[-1]["type"], "row", "orphan heading at page bottom")
                    rows = [b for b in blocks if b["type"] == "row"]
                    if cap:
                        self.assertLessEqual(len(rows), cap)
                    for row in rows:
                        self.assertLessEqual(len(row["cards"]), m.columns)
                        seen += row["cards"]
                self.assertEqual(seen, [c for _label, cards in groups for c in cards])

    def test_continued_heading_and_break_per_group(self):
        geo = layout.PageGeometry.from_paper("a4")
        m = layout.card_metrics(layout.CardSpec(style="list", columns=1), geo)
        groups = [("A", list(range(60))), ("B", list(range(3)))]
        pages = layout.paginate(groups, m, geo)
        self.assertGreater(len(pages), 1)
        second = pages[1][0]
        self.assertEqual((second["type"], second["label"], second["continued"]), ("heading", "A", True))
        pages = layout.paginate(groups, m, geo, break_per_group=True)
        firsts = [p[0]["label"] for p in pages if p[0]["type"] == "heading" and not p[0]["continued"]]
        self.assertEqual(firsts, ["A", "B"])

    def test_plan_numbers_and_toc(self):
        geo = layout.PageGeometry.from_paper("a4")
        m = layout.card_metrics(layout.CardSpec(style="grid", columns=2, image_size="large"), geo)
        groups = [("Chairs", list(range(9))), ("Desks", list(range(5))), ("Lamps", [1])]
        plan = layout.build_plan(groups, m, geo, show_cover=True, show_toc=True)
        self.assertEqual(plan[0]["kind"], "cover")
        self.assertEqual(plan[1]["kind"], "toc")
        self.assertEqual([p["number"] for p in plan], list(range(1, len(plan) + 1)))
        self.assertTrue(all(p["total"] == len(plan) for p in plan))
        for entry in plan[1]["entries"]:
            target = plan[entry["page"] - 1]
            labels = [b["label"] for b in target["blocks"]
                      if b["type"] == "heading" and not b["continued"]]
            self.assertIn(entry["label"], labels, "TOC must point at the page of the heading")

    def test_fit_text(self):
        self.assertEqual(layout.fit_text("Short name", 40, 9, 2), "Short name")
        long = "Executive high-back leather chair " * 6
        fitted = layout.fit_text(long, 40, 9, 2)
        self.assertTrue(fitted.endswith("…"))
        self.assertLessEqual(len(fitted), layout.chars_per_line(40, 9) * 2)
        self.assertEqual(layout.fit_text("  a\n\n b  ", 40, 9, 1), "a b")
        self.assertEqual(layout.fit_text(False, 40, 9, 1), "")

    def test_image_field_follows_print_resolution(self):
        self.assertEqual(layout.pick_image_field(40, "office"), "image_256")
        self.assertEqual(layout.pick_image_field(80, "office"), "image_512")
        self.assertEqual(layout.pick_image_field(80, "press"), "image_1024")
        self.assertEqual(layout.pick_image_field(400, "press"), "image_1920")

    def test_accent_contrast(self):
        for color in ("#FACC15", "#A7F3D0", "#FFFFFF", "#1D4E89"):
            self.assertGreaterEqual(contrast_ratio(readable_on_white(color), "#FFFFFF"), 4.5)
        self.assertEqual(readable_on_white("#1D4E89"), "#1D4E89")


# ---------------------------------------------------------------------------
# Data, pricing, security, sending
# ---------------------------------------------------------------------------
@tagged("post_install", "-at_install", "pcat")
class CatalogCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company = env.company
        cls.currency = cls.company.currency_id
        cls.warehouse = env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        root = env["product.category"].create({"name": "Root"})
        cls.categ_chairs = env["product.category"].create({"name": "Chairs", "parent_id": root.id})
        cls.categ_office = env["product.category"].create(
            {"name": "Office", "parent_id": cls.categ_chairs.id})
        cls.categ_lamps = env["product.category"].create({"name": "Lamps", "parent_id": root.id})
        cls.tag_new = env["product.tag"].create({"name": "PCat New"})
        cls.attr = env["product.attribute"].create({"name": "PCat Colour", "create_variant": "always"})
        cls.val_red, cls.val_blue = env["product.attribute.value"].create([
            {"name": "Red", "attribute_id": cls.attr.id},
            {"name": "Blue", "attribute_id": cls.attr.id}])
        cls.tax = None
        if "taxes_id" in env["product.template"]._fields:
            country = cls.company.account_fiscal_country_id or env.ref("base.sa")
            cls.tax = env["account.tax"].create({
                "name": "PCat VAT 15", "amount": 15, "type_tax_use": "sale",
                "company_id": cls.company.id, "country_id": country.id})
        Tmpl = env["product.template"]
        common = {"detailed_type": "product", "sale_ok": True}
        if cls.tax:
            common["taxes_id"] = [(6, 0, cls.tax.ids)]
        cls.chair = Tmpl.create(dict(common, name="PCat Chair", list_price=100.0,
                                     categ_id=cls.categ_office.id, default_code="PC-CH",
                                     description_sale="Mesh back.", image_1920=_png(),
                                     product_tag_ids=[(6, 0, cls.tag_new.ids)]))
        cls.multi = Tmpl.create(dict(common, name="PCat Variant Stool", list_price=50.0,
                                     categ_id=cls.categ_chairs.id, attribute_line_ids=[(0, 0, {
                                         "attribute_id": cls.attr.id,
                                         "value_ids": [(6, 0, [cls.val_red.id, cls.val_blue.id])]})]))
        blue = cls.multi.attribute_line_ids.product_template_value_ids.filtered(
            lambda v: v.product_attribute_value_id == cls.val_blue)
        blue.price_extra = 20.0
        cls.lamp = Tmpl.create(dict(common, name="PCat Lamp", list_price=30.0,
                                    categ_id=cls.categ_lamps.id, default_code="PC-LA"))
        cls.service = Tmpl.create({"name": "PCat Assembly", "detailed_type": "service",
                                   "list_price": 10.0, "categ_id": cls.categ_lamps.id})
        cls.not_saleable = Tmpl.create(dict(common, name="PCat Internal Part", sale_ok=False,
                                            categ_id=cls.categ_lamps.id))
        loc = cls.warehouse.lot_stock_id
        Quant = env["stock.quant"]
        Quant._update_available_quantity(cls.chair.product_variant_id, loc, 50)
        Quant._update_available_quantity(cls.lamp.product_variant_id, loc, 2)
        cls.public = env["product.pricelist"].create({
            "name": "PCat Public", "currency_id": cls.currency.id})
        cls.trade = env["product.pricelist"].create({
            "name": "PCat Trade", "currency_id": cls.currency.id,
            "discount_policy": "without_discount",
            "item_ids": [(0, 0, {"applied_on": "3_global", "compute_price": "percentage",
                                 "percent_price": 10})]})
        cls.catalog = env["product.catalog"].create({
            "name": "PCat Test Catalogue",
            "pricelist_id": cls.public.id,
            "warehouse_id": cls.warehouse.id,
            "categ_ids": [(6, 0, [cls.categ_chairs.id, cls.categ_lamps.id])],
            "show_stock": True,
            "lang": "en_US",
        })

    def _cards(self, catalog=None, pricelist=None):
        catalog = catalog or self.catalog
        data = catalog._prepare_catalog_data(pricelist=pricelist)
        return {c["id"]: c for page in data["plan"] for b in page["blocks"]
                if b["type"] == "row" for c in b["cards"]}, data

    def _html(self, catalog=None, **ctx):
        catalog = catalog or self.catalog
        html, _fmt = self.env["ir.actions.report"].with_context(**ctx)._render_qweb_html(
            REPORT, catalog.ids)
        return html.decode()


@tagged("post_install", "-at_install", "pcat")
class TestCatalogData(CatalogCase):

    def test_selection_filters(self):
        cards, _data = self._cards()
        ids = set(cards)
        self.assertTrue({self.chair.id, self.multi.id, self.lamp.id, self.service.id} <= ids,
                        "child categories are included")
        self.assertNotIn(self.not_saleable.id, ids, "only saleable products by default")
        self.catalog.tag_ids = self.tag_new
        self.assertEqual(set(self._cards()[0]), {self.chair.id})
        self.catalog.tag_ids = False
        self.catalog.attribute_value_ids = self.val_blue
        self.assertEqual(set(self._cards()[0]), {self.multi.id})
        self.catalog.attribute_value_ids = False
        self.catalog.exclude_product_tmpl_ids = self.lamp
        self.assertNotIn(self.lamp.id, self._cards()[0])
        self.catalog.write({"exclude_product_tmpl_ids": [(5,)], "extra_domain": "[('list_price', '>', 60)]"})
        self.assertEqual(set(self._cards()[0]), {self.chair.id})

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.catalog.extra_domain = "[('no_such_field', '=', 1)]"
        with self.assertRaises(ValidationError):
            self.catalog.write({"style": "list", "columns": 3})
        with self.assertRaises(ValidationError):
            self.catalog.accent_color = "blue"
        with self.assertRaises(ValidationError):
            self.catalog.write({"valid_from": "2027-02-01", "valid_until": "2027-01-01"})

    def test_form_view_and_style_onchange(self):
        with Form(self.env["product.catalog"]) as form:
            form.name = "Via form"
            form.pricelist_id = self.public
            form.style = "grid"
            form.columns = 4
            form.style = "showcase"
            self.assertEqual(form.columns, 2, "out-of-range columns snap to the style default")
            form.style = "grid"
            self.assertEqual(form.columns, 2, "values valid for the new style are kept")
        wizard = Form(self.env["product.catalog.send"].with_context(default_catalog_id=self.catalog.id))
        self.assertTrue(wizard.template_id)

    def test_product_limit(self):
        self.catalog.max_products = 2
        with self.assertRaises(UserError):
            self.catalog._prepare_catalog_data()

    def test_prices_match_odoo_pricelist_engine(self):
        cards, _data = self._cards(pricelist=self.trade)
        for tmpl in (self.chair, self.lamp, self.multi):
            expected = min(self.trade._get_product_price(v, 1.0) for v in tmpl.product_variant_ids)
            self.assertAlmostEqual(cards[tmpl.id]["price_value"], expected, places=2)
        self.assertTrue(cards[self.multi.id]["price_from"], "variants with extras show 'From'")
        self.assertFalse(cards[self.chair.id]["price_from"])
        # without_discount policy: public price struck through
        self.assertTrue(cards[self.chair.id]["compare"])
        cards, _data = self._cards(pricelist=self.public)
        self.assertFalse(cards[self.chair.id]["compare"], "no discount, no strike-through")

    def test_tax_included_prices(self):
        if not self.tax:
            self.skipTest("Invoicing (account) not installed")
        self.catalog.price_tax_display = "included"
        cards, _data = self._cards()
        expected = self.tax.compute_all(
            100.0, self.currency, 1.0, product=self.chair.product_variant_id)["total_included"]
        self.assertAlmostEqual(cards[self.chair.id]["price_value"], expected, places=2)
        self.assertAlmostEqual(expected, 115.0, places=2)

    def test_stock_bands(self):
        self.catalog.low_stock_threshold = 5
        cards, _data = self._cards()
        self.assertEqual(cards[self.chair.id]["stock_band"], "in")
        self.assertEqual(cards[self.lamp.id]["stock_band"], "low")
        self.assertEqual(cards[self.multi.id]["stock_band"], "out")
        self.assertFalse(cards[self.service.id]["stock_band"], "services carry no badge")
        self.catalog.hide_out_of_stock = True
        self.assertNotIn(self.multi.id, self._cards()[0])

    def test_visibility_toggles_in_rendered_report(self):
        html = self._html()
        self.assertIn("PC-CH", html)
        self.assertIn("Mesh back.", html)
        self.assertIn("o_pcat_price", html)
        self.assertIn("o_pcat_badge_in", html)
        self.catalog.write({"show_price": False, "show_default_code": False,
                            "show_description": False, "show_stock": False})
        html = self._html()
        for absent in ("PC-CH", "Mesh back.", 'class="o_pcat_price"', "o_pcat_badge_",
                       "Prices in"):
            self.assertNotIn(absent, html)
        self.assertIn("PCat Chair", html)

    def test_product_links(self):
        self.catalog.write({"show_link": True,
                            "link_url_pattern": "https://shop.example.com/p/{default_code}"})
        cards, _data = self._cards()
        self.assertEqual(cards[self.chair.id]["url"], "https://shop.example.com/p/PC-CH")
        self.assertTrue(cards[self.chair.id]["qr"].startswith("data:image/svg+xml;base64,"))
        self.assertFalse(cards[self.multi.id]["url"], "no reference: no broken link")
        self.assertFalse(cards[self.multi.id]["qr"])

    def test_category_labels_drop_root(self):
        cards, _data = self._cards()
        self.assertEqual(cards[self.chair.id]["category"], "Chairs / Office")
        self.assertEqual(cards[self.lamp.id]["category"], "Lamps")

    def test_report_pages_match_plan(self):
        self.catalog.show_toc = True
        _cards, data = self._cards()
        html = self._html()
        self.assertEqual(html.count('class="o_pcat_page '), len(data["plan"]))
        self.assertIn("%s / %s" % (len(data["plan"]), len(data["plan"])), " ".join(html.split()))

    def test_right_to_left(self):
        self.env["res.lang"]._activate_lang("ar_001")
        self.catalog.lang = "ar_001"
        _cards, data = self._cards(catalog=self.catalog.with_context(lang="ar_001"))
        self.assertEqual(data["direction"], "rtl")
        self.assertIn('dir="rtl"', self._html())

    def test_paperformat_follows_catalogue(self):
        self.catalog.write({"paper_size": "letter", "orientation": "landscape"})
        report = self.env["ir.actions.report"]._get_report(REPORT)
        paperformat = report.with_context(
            pcat_paperformat_id=self.catalog._get_paperformat().id).get_paperformat()
        self.assertEqual((paperformat.format, paperformat.orientation), ("Letter", "Landscape"))

    def test_no_n_plus_one_queries(self):
        """Query count must not grow with the number of products."""
        self.catalog.write({"categ_ids": [(5,)], "show_link": True,
                            "link_url_pattern": "https://x.example/{id}"})
        tag = self.env["product.tag"].create({"name": "PCat Bulk"})
        self.catalog.tag_ids = tag

        def make(n):
            self.env["product.template"].create([{
                "name": "Bulk %s" % i, "detailed_type": "product", "list_price": i,
                "categ_id": self.categ_lamps.id, "product_tag_ids": [(6, 0, tag.ids)],
            } for i in range(n)])

        def count():
            self.env.invalidate_all()
            self.env.flush_all()
            before = self.cr.sql_log_count
            self.catalog._prepare_catalog_data()
            return self.cr.sql_log_count - before

        make(15)
        small = count()
        make(45)
        large = count()
        self.assertLessEqual(large, small + 12, "queries: %s for 15 vs %s for 60" % (small, large))


@tagged("post_install", "-at_install", "pcat")
class TestCatalogSecurityAndSending(CatalogCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="pcat_user",
            groups="base.group_user,product_catalogue_pdf_advanced.group_catalog_user")
        cls.manager = new_test_user(
            cls.env, login="pcat_manager",
            groups="base.group_user,product_catalogue_pdf_advanced.group_catalog_manager")
        Partner = cls.env["res.partner"]
        cls.p_trade_en = Partner.create({"name": "Trade EN", "email": "a@example.com", "lang": "en_US"})
        cls.p_trade_en2 = Partner.create({"name": "Trade EN 2", "email": "b@example.com", "lang": "en_US"})
        cls.p_public = Partner.create({"name": "Public", "email": "c@example.com", "lang": "en_US"})
        (cls.p_trade_en | cls.p_trade_en2).property_product_pricelist = cls.trade
        cls.p_public.property_product_pricelist = cls.public

    def test_user_can_read_not_write(self):
        catalog = self.catalog.with_user(self.user)
        self.assertEqual(catalog.name, "PCat Test Catalogue")
        with self.assertRaises(AccessError):
            catalog.name = "Hacked"
        with self.assertRaises(AccessError):
            self.env["product.catalog"].with_user(self.user).create({
                "name": "x", "pricelist_id": self.public.id})
        created = self.env["product.catalog"].with_user(self.manager).create({
            "name": "By manager", "pricelist_id": self.public.id})
        self.assertTrue(created)

    def test_multi_company_rule(self):
        other = self.env["res.company"].create({"name": "PCat Other Co"})
        hidden = self.env["product.catalog"].create({
            "name": "Other", "company_id": other.id,
            "pricelist_id": self.env["product.pricelist"].create(
                {"name": "Other PL", "company_id": other.id}).id,
            "warehouse_id": False})
        visible = self.env["product.catalog"].with_user(self.user).search([("name", "=", "Other")])
        self.assertFalse(visible)
        self.assertTrue(hidden.exists())

    def test_send_groups_recipients_by_pricelist(self):
        wizard = self.env["product.catalog.send"].with_user(self.user).create({
            "catalog_id": self.catalog.id,
            "partner_ids": [(6, 0, (self.p_trade_en | self.p_trade_en2 | self.p_public).ids)],
        })
        groups = wizard._group_recipients()
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[(self.trade, "en_US")], self.p_trade_en | self.p_trade_en2)
        self.assertEqual(wizard.group_count, 2)

        rendered = []
        Catalog = type(self.env["product.catalog"])
        original = Catalog._render_pdf

        def spy(catalog, pricelist=None, lang=None):
            rendered.append((pricelist, lang))
            return original(catalog, pricelist=pricelist, lang=lang)

        Mail = self.env["mail.mail"].sudo()
        before = Mail.search([]).ids
        with patch.object(Catalog, "_render_pdf", spy):
            wizard.action_send()
        mails = Mail.search([("id", "not in", before), ("model", "=", "product.catalog")])
        self.assertEqual(len(mails), 2, "one email per pricelist/language group")
        self.assertEqual(sorted(len(m.recipient_ids) for m in mails), [1, 2])
        self.assertTrue(all(len(m.attachment_ids) == 1 for m in mails))
        self.assertEqual({pl for pl, _lang in rendered}, {self.trade, self.public})
        trade_mail = mails.filtered(lambda m: m.recipient_ids == self.p_trade_en | self.p_trade_en2)
        self.assertIn("PCat Trade", trade_mail.attachment_ids.name)

        # Same configuration again: cached PDFs are reused, nothing re-rendered.
        rendered.clear()
        with patch.object(Catalog, "_render_pdf", spy):
            self.env["product.catalog.send"].with_user(self.user).create({
                "catalog_id": self.catalog.id,
                "partner_ids": [(6, 0, self.p_public.ids)],
            }).action_send()
        self.assertEqual(rendered, [])

    def test_cache_invalidated_by_configuration_change(self):
        first = self.catalog._get_pdf_attachment()
        self.assertEqual(self.catalog._get_pdf_attachment(), first)
        self.catalog.columns = 2
        second = self.catalog._get_pdf_attachment()
        self.assertNotEqual(second, first)
        self.catalog.pricelist_id = self.trade
        self.assertNotEqual(self.catalog._get_pdf_attachment(), second)
        self.catalog.cache_ttl_minutes = 0
        self.assertNotEqual(self.catalog._get_pdf_attachment(), self.catalog._get_pdf_attachment(),
                            "a TTL of 0 always renders afresh")


# ---------------------------------------------------------------------------
# Real PDF through wkhtmltopdf
# ---------------------------------------------------------------------------

@tagged("post_install", "-at_install", "pcat", "pcat_pdf")
class TestCatalogPdf(HttpCase):
    """Renders actual PDFs; the page count comes from the PDF itself."""

    def setUp(self):
        super().setUp()
        if self.env["ir.actions.report"].get_wkhtmltopdf_state() != "ok":
            self.skipTest("wkhtmltopdf not available")
        self.env["ir.config_parameter"].sudo().set_param("report.url", self.base_url())
        categ = self.env["product.category"].create({"name": "PCat PDF"})
        self.env["product.template"].create([{
            "name": "PDF product %02d" % i, "list_price": 10 + i, "categ_id": categ.id,
            "default_code": "PDF-%02d" % i, "image_1920": _png((20 * i % 255, 80, 140)),
            "description_sale": "Line one of the description. " * 3,
        } for i in range(17)])
        self.catalog = self.env["product.catalog"].create({
            "name": "PDF Catalogue", "lang": "en_US", "show_toc": True,
            "pricelist_id": self.env["product.pricelist"].create({"name": "PCat PDF PL"}).id,
            "categ_ids": [(6, 0, categ.ids)], "columns": 3,
        })

    def _pdf(self):
        pdf, _fmt = self.env["ir.actions.report"].with_context(
            force_report_rendering=True)._render_qweb_pdf(REPORT, self.catalog.ids)
        from PyPDF2 import PdfReader
        return PdfReader(io.BytesIO(pdf))

    def test_pdf_page_count_matches_plan(self):
        for style, cols in (("grid", 3), ("list", 1), ("showcase", 2)):
            self.catalog.write({"style": style, "columns": cols})
            plan = self.catalog._prepare_catalog_data()["plan"]
            reader = self._pdf()
            self.assertEqual(len(reader.pages), len(plan), style)
            text = reader.pages[-1].extract_text()
            self.assertIn("%s / %s" % (len(plan), len(plan)), text)

    def test_batched_rendering_merges_into_one_document(self):
        """Batches rendered by separate wkhtmltopdf runs keep global numbering."""
        from ..models import product_catalog
        plan = self.catalog._prepare_catalog_data()["plan"]
        self.assertGreater(len(plan), 3)
        with patch.object(product_catalog, "PAGE_BATCH", 2):
            # Outside tests Odoo always renders; inside, it must be forced.
            pdf = self.catalog.with_context(force_report_rendering=True)._render_pdf()
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(pdf))
        self.assertEqual(len(reader.pages), len(plan))
        self.assertIn("3 / %s" % len(plan), reader.pages[2].extract_text())
        self.assertFalse(product_catalog.PREPARED_DATA, "shared batch data is released")

    def test_print_button_downloads_cached_pdf(self):
        action = self.catalog.action_print()
        self.assertEqual(action["type"], "ir.actions.act_url")
        attachment_id = int(action["url"].split("/")[3].split("?")[0])
        attachment = self.env["ir.attachment"].browse(attachment_id)
        self.assertEqual(attachment.res_id, self.catalog.id)
        self.assertEqual(self.catalog.action_print()["url"], action["url"], "reused")

    def test_hidden_prices_absent_from_pdf(self):
        self.catalog.show_price = False
        reader = self._pdf()
        text = " ".join(page.extract_text() for page in reader.pages)
        self.assertIn("PDF product 00", text)
        self.assertNotIn("Prices in", text)
        self.assertNotIn("26.00", text)
