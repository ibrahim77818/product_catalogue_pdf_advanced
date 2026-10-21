# -*- coding: utf-8 -*-
from collections import OrderedDict

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError


class ProductCatalogSend(models.TransientModel):
    """Email a catalogue to several customers without leaking prices.

    Recipients are grouped by (pricelist, language). Each group receives one
    PDF rendered for its own pricelist and language, so a customer never sees
    another customer's prices. Identical groups reuse a cached PDF.
    """

    _name = "product.catalog.send"
    _description = "Send Product Catalogue"

    catalog_id = fields.Many2one("product.catalog", required=True, ondelete="cascade")
    partner_ids = fields.Many2many(
        "res.partner", string="Recipients", required=True,
        domain="[('email', '!=', False)]")
    template_id = fields.Many2one(
        "mail.template", string="Email Template",
        domain="[('model', '=', 'product.catalog')]",
        default=lambda self: self.env.ref(
            "product_catalogue_pdf_advanced.mail_template_product_catalog",
            raise_if_not_found=False))
    use_partner_pricelist = fields.Boolean(
        string="Use Each Recipient's Pricelist", default=True,
        help="Render the catalogue with the pricelist of each recipient. "
             "Otherwise everybody receives the catalogue's own pricelist.")
    use_partner_lang = fields.Boolean(
        string="Use Each Recipient's Language", default=True)
    group_summary = fields.Html(compute="_compute_group_summary", sanitize=False)
    group_count = fields.Integer(compute="_compute_group_summary")

    def _group_recipients(self):
        """{(pricelist, lang): partners} in a stable order."""
        self.ensure_one()
        catalog = self.catalog_id
        groups = OrderedDict()
        for partner in self.partner_ids:
            pricelist = catalog.pricelist_id
            if self.use_partner_pricelist:
                partner_pl = partner.with_company(catalog.company_id).property_product_pricelist
                if partner_pl and partner_pl.company_id in (catalog.company_id, self.env["res.company"]):
                    pricelist = partner_pl
            lang = (partner.lang if self.use_partner_lang and partner.lang else None) \
                or catalog.lang or self.env.lang
            key = (pricelist, lang)
            groups[key] = groups.get(key, self.env["res.partner"]) | partner
        return groups

    @api.depends("partner_ids", "use_partner_pricelist", "use_partner_lang", "catalog_id")
    def _compute_group_summary(self):
        lang_names = dict(self.env["res.lang"].get_installed())
        for wizard in self:
            if not wizard.catalog_id or not wizard.partner_ids:
                wizard.group_summary = False
                wizard.group_count = 0
                continue
            groups = wizard._group_recipients()
            wizard.group_count = len(groups)
            rows = Markup("").join(
                Markup("<tr><td>%s</td><td>%s</td><td>%s</td></tr>") % (
                    pricelist.display_name,
                    lang_names.get(lang, lang),
                    # name, not display_name: Odoo 17 appends the email
                    # to display_name when the tags widget sets show_email.
                    ", ".join(partners.mapped("name")),
                )
                for (pricelist, lang), partners in groups.items())
            wizard.group_summary = Markup(
                "<table class='table table-sm o_pcat_send_summary'>"
                "<thead><tr><th>%s</th><th>%s</th><th>%s</th></tr></thead>"
                "<tbody>%s</tbody></table>"
            ) % (self.env._("Pricelist"), self.env._("Language"), self.env._("Recipients"), rows)

    def action_send(self):
        self.ensure_one()
        if not self.template_id:
            raise UserError(self.env._("Choose an email template."))
        missing = self.partner_ids.filtered(lambda p: not p.email)
        if missing:
            raise UserError(self.env._(
                "These recipients have no email address: %s",
                ", ".join(missing.mapped("display_name"))))
        catalog = self.catalog_id
        lang_names = dict(self.env["res.lang"].get_installed())
        summary = []
        for (pricelist, lang), partners in self._group_recipients().items():
            attachment = catalog._get_pdf_attachment(pricelist=pricelist, lang=lang)
            # A copy per email keeps the cached PDF out of mail cleanups.
            mail_attachment = attachment.sudo().copy({"description": False})
            self.template_id.with_context(lang=lang).send_mail(
                catalog.id,
                force_send=False,
                email_values={
                    "recipient_ids": [(6, 0, partners.ids)],
                    "attachment_ids": [(4, mail_attachment.id)],
                },
                email_layout_xmlid="mail.mail_notification_light",
            )
            summary.append(Markup("<li>%s — %s: %s</li>") % (
                pricelist.display_name, lang_names.get(lang, lang),
                ", ".join(partners.mapped("name"))))
        catalog.message_post(
            body=Markup("<p>%s</p><ul>%s</ul>") % (
                self.env._("Catalogue emailed:"), Markup("").join(summary)),
            message_type="comment", subtype_xmlid="mail.mt_note")
        return {"type": "ir.actions.act_window_close"}
