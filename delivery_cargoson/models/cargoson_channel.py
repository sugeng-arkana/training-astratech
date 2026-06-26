from odoo import fields, models


class cargosonChannel(models.Model):
    _name = 'cargoson.channel'
    _description = 'cargoson Channel'
    _order = 'name'

    name = fields.Char('Channel Name', readonly=True)
    channel_code = fields.Integer('Channel Code', readonly=True)
    cargoson_code = fields.Char(string='Cargoson Code')
