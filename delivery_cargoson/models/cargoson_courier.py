from odoo import api, fields, models


class CargosonCourier(models.Model):
    _name = 'cargoson.courier'
    _description = 'cargoson Courier'
    _order = 'name'

    carrier_id = fields.Many2one('delivery.carrier', 'Delivery Carrier', readonly=True)
    cargoson_courier_id = fields.Char('Courier ID', readonly=True)
    name = fields.Char('Courier Name', readonly=True)
    carrier_short_name = fields.Char('Service Short Name', readonly=True)
    reg_no = fields.Char('Reg No', readonly=True)
    vat_no = fields.Char('VAT No', readonly=True)
    service_id = fields.Char('Service ID', readonly=True)
    service_name = fields.Char('Service Name', readonly=True)
    service_type = fields.Char('Service Type', readonly=True)

    _unique_service_id_per_carrier = models.Constraint(
        'UNIQUE(service_id, carrier_id)',
        'Service ID must be unique per carrier!',
    )

