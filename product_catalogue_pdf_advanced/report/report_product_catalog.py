# -*- coding: utf-8 -*-
from odoo import api, models

from ..models.product_catalog import PREPARED_DATA


def _mm(value):
    """Format a length from the layout plan as a CSS millimetre value."""
    return "%.2fmm" % value


class ReportProductCatalog(models.AbstractModel):
    """Feeds the QWeb template with a precomputed page plan per catalogue.

    The template never queries the database: all reading, pricing and
    pagination happens once, here, in the language being printed.
    """

    _name = "report.product_catalogue_pdf_advanced.report_product_catalog"
    _description = "Product Catalogue PDF"

    @api.model
    def _get_report_values(self, docids, data=None):
        catalogs = self.env["product.catalog"].browse(docids)
        pricelist = self.env["product.pricelist"].browse(
            self.env.context.get("pcat_pricelist_id") or [])
        forced_lang = self.env.context.get("pcat_force_lang")
        prepared = PREPARED_DATA.get(self.env.context.get("pcat_prepared"), {})
        page_range = self.env.context.get("pcat_page_range")
        payload = {}
        for catalog in catalogs:
            data = prepared.get(catalog.id)
            if data is None:
                lang = forced_lang or catalog.lang or self.env.context.get("lang")
                data = catalog.with_context(lang=lang)._prepare_catalog_data(
                    pricelist=pricelist.exists() or catalog.pricelist_id)
            if page_range:
                # One batch of a larger document: page numbers stay global.
                data = dict(data, plan=data["plan"][page_range[0]:page_range[1]])
            payload[catalog.id] = data
        return {
            "doc_ids": docids,
            "doc_model": "product.catalog",
            "docs": catalogs,
            "catalog_data": payload,
            "mm": _mm,
        }
