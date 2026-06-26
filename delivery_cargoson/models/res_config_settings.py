from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    module_delivery_cargoson = fields.Boolean("cargoson Connector")
