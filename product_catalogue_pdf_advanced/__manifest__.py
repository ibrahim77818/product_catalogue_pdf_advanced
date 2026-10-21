# -*- coding: utf-8 -*-
{
    "name": "Product Catalogue PDF Advanced",
    "summary": "Print-accurate, pricelist-aware, bilingual (LTR/RTL) PDF product "
               "catalogues with deterministic pagination, stock bands, QR links "
               "and per-pricelist email delivery.",
    "version": "18.0.1.0.0",
    "category": "Sales/Sales",
    "author": "Ibrahim Yahia",
    "website": "https://www.linkedin.com/in/ibrahim-yahia",
    "license": "LGPL-3",
    "depends": [
        "product",
        "stock",
        "mail",
    ],
    "external_dependencies": {"python": ["qrcode"]},
    "data": [
        "security/catalog_security.xml",
        "security/ir.model.access.csv",
        "data/report_paperformat.xml",
        "report/catalog_report.xml",
        "report/catalog_report_templates.xml",
        "data/mail_template.xml",
        "wizard/catalog_send_wizard_views.xml",
        "views/product_catalog_views.xml",
        "views/menus.xml",
    ],
    "demo": [
        "demo/product_catalog_demo.xml",
    ],
    "assets": {
        # Scoped under .o_pcat: nothing here leaks into other reports.
        "web.report_assets_common": [
            "product_catalogue_pdf_advanced/static/src/scss/catalog_fonts.scss",
            "product_catalogue_pdf_advanced/static/src/scss/catalog_report.scss",
        ],
    },
    "images": ["static/description/banner.png"],
    "post_init_hook": "post_init_hook",
    "application": True,
    "installable": True,
}
