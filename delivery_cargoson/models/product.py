# -*- coding: utf-8 -*-

from odoo import models, fields, api, _


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def _get_length_uom_domain(self):
        """Domain for dimension/length UoM. Odoo 19 removed uom.category; use empty domain then."""
        if 'uom.category' not in self.env:
            return []
        try:
            cat = self.env.ref('uom.uom_categ_length', raise_if_not_found=False)
            if cat:
                return [('category_id', '=', cat.id)]
        except Exception:
            pass
        return []

    def _get_weight_uom_domain(self):
        """Domain for weight UoM. Odoo 19 removed uom.category; use empty domain then."""
        if 'uom.category' not in self.env:
            return []
        try:
            cat = self.env.ref('uom.product_uom_categ_kgm', raise_if_not_found=False)
            if cat:
                return [('category_id', '=', cat.id)]
        except Exception:
            pass
        return []

    dimensions_uom_id = fields.Many2one(
        'uom.uom',
        'Dimension(UOM)',
        domain=lambda self: self._get_length_uom_domain(),
        help="Default Unit of Measure used for dimension."
    )

    weight_uom_id = fields.Many2one(
        'uom.uom',
        'Weight(UOM)',
        domain=lambda self: self._get_weight_uom_domain(),
        help="Default Unit of Measure used for weight."
    )
    is_crate = fields.Boolean('Is Crate')
