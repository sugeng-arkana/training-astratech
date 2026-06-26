from odoo import fields, models, api, _
from odoo.exceptions import UserError


class PackageType(models.Model):
    _inherit = 'stock.package.type'

    package_carrier_type = fields.Selection(selection_add=[('cargoson', 'cargoson')])
    use_product_dimensions = fields.Boolean('Use Product Dimensions')
    is_crate = fields.Boolean('Shipping with Crate')
    is_active = fields.Boolean('Active')
    required_length = fields.Boolean('Required Length')
    required_width = fields.Boolean('Required Width')
    required_height = fields.Boolean('Required Height')
    qty_editable = fields.Boolean('Edit Quantity')
    default_qty = fields.Float('Default Qty')
    default_product_id = fields.Many2one('product.template', string='Default Product')
    one_line_all = fields.Boolean('Oneline for All Product?')
    use_default_qty = fields.Boolean('Use Default Qty?')

    # @api.constrains('packaging_length', 'width', 'height', 'package_carrier_type')
    # def _check_cargoson_required_value(self):
    #     for record in self:
    #         if record.package_carrier_type == 'cargoson' and \
    #                 any(dim <= 0 for dim in (record.packaging_length, record.width, record.height)):
    #             raise ValidationError(_('Length, Width and Height is necessary for cargoson Package.'))

    @api.depends('package_carrier_type')
    def _compute_length_uom_name(self):
        super()._compute_length_uom_name()
        for package in self.filtered(lambda p: p.package_carrier_type == 'cargoson'):
            package.length_uom_name = 'cm'

    @api.constrains('is_crate', 'use_product_dimensions')
    def _constrains_crate_dimension(self):
        for record in self:
            if record.is_crate and record.use_product_dimensions:
                raise UserError(_('Pick only one either shipping with. Crate or User Product dimensions'))
            if record.is_crate and (record.required_length or record.required_width or record.required_height):
                raise UserError(_('Default Size Checkbox must be disable.'))

    @api.onchange('required_length', 'required_width', 'required_height')
    def onchange_sizes(self):
        for record in self:
            if record.is_crate:
                raise UserError(_('Crate must be disable!'))