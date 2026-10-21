# -*- coding: utf-8 -*-
"""Install hook.

Odoo 19 hides pricelists unless the *Pricelists* setting (Sales or
Inventory > Settings) is enabled, and only then creates a default pricelist
per company. A catalogue cannot exist without a pricelist, so installing this
module enables the setting the same way the Settings checkbox does.
"""


def post_init_hook(env):
    group_user = env.ref("base.group_user")
    pricelist_group = env.ref("product.group_product_pricelist")
    if pricelist_group not in group_user.implied_ids:
        group_user.write({"implied_ids": [(4, pricelist_group.id)]})
    env["res.company"].search([])._activate_or_create_pricelists()
