# -*- coding: utf-8 -*-
from odoo import models
from odoo.exceptions import UserError

CATALOG_REPORT = "product_catalogue_pdf_advanced.report_product_catalog"


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    def get_paperformat(self):
        """Per-catalogue paper size/orientation.

        Odoo ties one paperformat to a report action; catalogues choose their
        own, so the catalogue's format travels in the context.
        """
        paperformat_id = self.env.context.get("pcat_paperformat_id")
        if paperformat_id and self.report_name == CATALOG_REPORT:
            return self.env["report.paperformat"].browse(paperformat_id)
        return super().get_paperformat()

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        report = self._get_report(report_ref)
        if report.report_name == CATALOG_REPORT and res_ids:
            catalogs = self.env["product.catalog"].browse(res_ids)
            formats = {c._get_paperformat() for c in catalogs}
            if len(formats) > 1:
                raise UserError(self.env._(
                    "Catalogues with different paper sizes or orientations "
                    "cannot be printed in one PDF. Print them separately."))
            self = self.with_context(pcat_paperformat_id=formats.pop().id)
        return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
