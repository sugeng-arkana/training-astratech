from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class PickingChoosePackageTypes(models.Model):
    _name = 'picking.choose.package.types'
    _description = 'picking choose package types'

    sale_id = fields.Many2one('sale.order', string='Sale')
    purchase_id = fields.Many2one('purchase.order', string='Purchase')
    picking_id = fields.Many2one('stock.picking', string='Picking')
    package_type = fields.Many2one('stock.package.type', string='Package Type')
    height = fields.Float('Height')
    width = fields.Float('Width')
    depth = fields.Float('Depth')
    qty = fields.Integer('QTY')
    weight = fields.Float('Weight')
    total_weight = fields.Float('Total Weight')

    def _picking_base_product_weight_kg(self):
        """Match ``choose.package.types`` / Operations: prefer reserved weight, else shipping_weight."""
        if not self.picking_id:
            return 0.0
        p = self.picking_id
        if hasattr(p, '_cargoson_reserved_product_weight_kg'):
            w = p._cargoson_reserved_product_weight_kg()
            if w:
                return w
        return p.shipping_weight or 0.0

    def _get_crate_weight_for_picking_line(self):
        if not self.package_type or not self.package_type.is_crate:
            return 0.0
        order = self.picking_id.sale_id if self.picking_id else False
        if not order:
            return 0.0
        crate_lines = order.order_line.filtered(
            lambda pl: pl.product_id.is_crate is True and pl.product_id.type != 'service'
        )
        if not crate_lines:
            return 0.0
        if len(crate_lines) == 1:
            return sum(
                pl.product_template_id.weight * pl.product_uom_qty
                for pl in crate_lines
            )
        return 0.0

    @api.onchange('package_type')
    def onchange_package_type(self):
        if not self.package_type or not self.picking_id:
            return
        self.height = self.package_type.height
        self.width = self.package_type.width
        self.depth = self.package_type.packaging_length
        self.qty = self.package_type.default_qty
        product_weight = self._picking_base_product_weight_kg()
        self.weight = product_weight
        package_qty = self.qty or 1
        crate_w = self._get_crate_weight_for_picking_line()
        base_w = (self.package_type.base_weight * package_qty) if self.package_type.base_weight else 0.0
        self.total_weight = product_weight + crate_w + base_w

    @api.onchange('weight')
    def onchange_weight(self):
        if not self.package_type:
            return
        product_weight = self.weight or 0.0
        package_qty = self.qty or 1
        crate_w = self._get_crate_weight_for_picking_line() if self.package_type.is_crate else 0.0
        base_w = (self.package_type.base_weight * package_qty) if self.package_type.base_weight else 0.0
        self.total_weight = product_weight + crate_w + base_w

    @api.onchange('qty')
    def onchange_qty(self):
        if not self.package_type:
            return
        product_weight = self.weight or 0.0
        package_qty = self.qty or 1
        crate_w = self._get_crate_weight_for_picking_line() if self.package_type.is_crate else 0.0
        base_w = (self.package_type.base_weight * package_qty) if self.package_type.base_weight else 0.0
        self.total_weight = product_weight + crate_w + base_w


class SaleChoosePackageTypes(models.Model):
    _name = 'sale.choose.package.types'
    _description = 'sale choose package types'

    sale_id = fields.Many2one('sale.order', string='Sale')
    picking_id = fields.Many2one('stock.picking', string='Picking')
    package_type = fields.Many2one('stock.package.type', string='Package Type')
    height = fields.Float('Height')
    width = fields.Float('Width')
    depth = fields.Float('Depth')
    qty = fields.Integer('QTY')
    weight = fields.Float('Weight')
    total_weight = fields.Float('Total Weight')

    def _sale_base_product_weight_kg(self):
        if not self.sale_id:
            return 0.0
        return self.sale_id.shipping_weight or 0.0

    def _get_crate_weight_for_sale_line(self):
        if not self.package_type or not self.package_type.is_crate or not self.sale_id:
            return 0.0
        crate_lines = self.sale_id.order_line.filtered(
            lambda pl: pl.product_id.is_crate is True and pl.product_id.type != 'service'
        )
        if not crate_lines or len(crate_lines) != 1:
            return 0.0
        return sum(
            pl.product_template_id.weight * pl.product_uom_qty
            for pl in crate_lines
        )

    @api.onchange('package_type')
    def onchange_package_type(self):
        if not self.package_type or not self.sale_id:
            return
        self.height = self.package_type.height
        self.width = self.package_type.width
        self.depth = self.package_type.packaging_length
        self.qty = self.package_type.default_qty
        product_weight = self._sale_base_product_weight_kg()
        self.weight = product_weight
        package_qty = self.qty or 1
        crate_w = self._get_crate_weight_for_sale_line()
        base_w = (self.package_type.base_weight * package_qty) if self.package_type.base_weight else 0.0
        self.total_weight = product_weight + crate_w + base_w

    @api.onchange('weight')
    def onchange_weight(self):
        if not self.package_type:
            return
        product_weight = self.weight or 0.0
        package_qty = self.qty or 1
        crate_w = self._get_crate_weight_for_sale_line() if self.package_type.is_crate else 0.0
        base_w = (self.package_type.base_weight * package_qty) if self.package_type.base_weight else 0.0
        self.total_weight = product_weight + crate_w + base_w

    @api.onchange('qty')
    def onchange_qty(self):
        if not self.package_type:
            return
        product_weight = self.weight or 0.0
        package_qty = self.qty or 1
        crate_w = self._get_crate_weight_for_sale_line() if self.package_type.is_crate else 0.0
        base_w = (self.package_type.base_weight * package_qty) if self.package_type.base_weight else 0.0
        self.total_weight = product_weight + crate_w + base_w


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    cargoson_ref = fields.Char(
        string="Cargoson Ref",
        copy=False, readonly=False
    )
    package_types = fields.One2many('sale.choose.package.types', 'sale_id', string='Package Types')
    carrier_id = fields.Many2one('delivery.carrier', string='Carrier')
    cargoson_weight_uom = fields.Selection(related='carrier_id.cargoson_weight_uom', readonly=True)
    cargoson_length_uom = fields.Selection(related='carrier_id.cargoson_length_uom', readonly=True)
    delivery_weight_uom = fields.Char(
        string='Weight Unit',
        compute='_compute_delivery_units',
        store=True,
        readonly=True,
        help='Unit for package weights (from shipping method). Shown when Cargoson carrier is used.')
    delivery_length_uom = fields.Char(
        string='Dimension Unit',
        compute='_compute_delivery_units',
        store=True,
        readonly=True,
        help='Unit for package dimensions (from shipping method). Shown when Cargoson carrier is used.')
    carrier_cost = fields.Float('Carrier Cost')

    @api.depends('carrier_id', 'carrier_id.delivery_type', 'carrier_id.cargoson_weight_uom', 'carrier_id.cargoson_length_uom')
    def _compute_delivery_units(self):
        for order in self:
            if order.carrier_id and order.carrier_id.delivery_type == 'cargoson':
                order.delivery_weight_uom = order.carrier_id.cargoson_weight_uom or 'kg'
                order.delivery_length_uom = order.carrier_id.cargoson_length_uom or 'm'
            else:
                order.delivery_weight_uom = False
                order.delivery_length_uom = False
    label_url = fields.Char('Label URL', copy=False, readonly=True)
    cmr_url = fields.Char('CMR URL', copy=False, readonly=True)
    bol_url = fields.Char('BOL URL', copy=False, readonly=True)
    waybill_url = fields.Char('Waybill URL', copy=False, readonly=True)
    tracking_url = fields.Char('Cargoson Tracking URL', copy=False, readonly=True)
    # PDF attachment fields
    label_attachment_id = fields.Many2one('ir.attachment', string='Label PDF', copy=False, readonly=True)
    cmr_attachment_id = fields.Many2one('ir.attachment', string='CMR PDF', copy=False, readonly=True)
    bol_attachment_id = fields.Many2one('ir.attachment', string='BOL PDF', copy=False, readonly=True)
    waybill_attachment_id = fields.Many2one('ir.attachment', string='Waybill PDF', copy=False, readonly=True)
    temp_min = fields.Char('Min Temp')
    temp_max = fields.Char('Max Temp')
    service_id = fields.Char('Service ID')
    service_name = fields.Char('Service Name')
    choose_service = fields.Char('Service')
    service_type = fields.Char('Service Type')
    pick_up_date = fields.Datetime(string='Pickup Date')
    error = fields.Text('Error')

    package_type = fields.Many2one('stock.package.type', string='Package Type')
    package_qty = fields.Integer('Package Qty', default=1)
    height = fields.Float('Height')
    width = fields.Float('Width')
    depth = fields.Float('Depth')
    comment = fields.Char('Comment')
    package_product_id = fields.Many2one('product.template', string='Package Product')

    def _clear_cargoson_delivery_info_vals(self):
        """Return values that reset Cargoson-specific delivery tab data."""
        return {
            'cargoson_ref': False,
            'package_types': [(5, 0, 0)],
            'carrier_cost': 0.0,
            'label_url': False,
            'cmr_url': False,
            'bol_url': False,
            'waybill_url': False,
            'tracking_url': False,
            'label_attachment_id': False,
            'cmr_attachment_id': False,
            'bol_attachment_id': False,
            'waybill_attachment_id': False,
            'temp_min': False,
            'temp_max': False,
            'service_id': False,
            'service_name': False,
            'choose_service': False,
            'service_type': False,
            'pick_up_date': False,
            'error': False,
            'package_type': False,
            'package_qty': 1,
            'height': 0.0,
            'width': 0.0,
            'depth': 0.0,
            'comment': False,
            'package_product_id': False,
        }

    def _clear_cargoson_delivery_info(self):
        """Clear Cargoson delivery tab data and sync empty values to related pickings."""
        orders = self.filtered(lambda o: o._has_cargoson_delivery_info())
        if not orders:
            return
        orders.write(orders._clear_cargoson_delivery_info_vals())
        orders.update_picking()

    def _has_cargoson_delivery_info(self):
        self.ensure_one()
        return bool(
            self.cargoson_ref
            or self.package_types
            or self.carrier_cost
            or self.label_url
            or self.cmr_url
            or self.bol_url
            or self.waybill_url
            or self.tracking_url
            or self.label_attachment_id
            or self.cmr_attachment_id
            or self.bol_attachment_id
            or self.waybill_attachment_id
            or self.temp_min
            or self.temp_max
            or self.service_id
            or self.service_name
            or self.choose_service
            or self.service_type
            or self.pick_up_date
            or self.error
            or self.package_type
            or self.comment
            or self.package_product_id
            or self.height
            or self.width
            or self.depth
            or (self.package_qty and self.package_qty != 1)
        )

    @api.onchange('carrier_id')
    def _onchange_carrier_id_clear_cargoson(self):
        if self.carrier_id and self.carrier_id.delivery_type == 'cargoson':
            return
        if not self._has_cargoson_delivery_info():
            return
        clear_vals = self._clear_cargoson_delivery_info_vals()
        for field, value in clear_vals.items():
            setattr(self, field, value)

    def write(self, vals):
        orders_to_clear = self.env['sale.order']
        if 'carrier_id' in vals:
            new_carrier = (
                self.env['delivery.carrier'].browse(vals['carrier_id'])
                if vals['carrier_id']
                else self.env['delivery.carrier']
            )
            if not new_carrier or new_carrier.delivery_type != 'cargoson':
                orders_to_clear = self.filtered(
                    lambda o: o.carrier_id and o.carrier_id.delivery_type == 'cargoson'
                )
        res = super().write(vals)
        if orders_to_clear:
            orders_to_clear._clear_cargoson_delivery_info()
        return res

    def cargoson_link(self):
        if not self.cargoson_ref or len(self.cargoson_ref) < 2:
            raise UserError(_('Cargoson reference is not available'))
        
        # Determine base URL based on environment
        if self.carrier_id and self.carrier_id.prod_environment:
            # Production environment
            base_url = 'https://www.cargoson.com/queries/'
        else:
            # Test/Staging environment
            base_url = 'https://cargoson-staging.herokuapp.com/queries/'
        
        url = base_url + self.cargoson_ref[2:]
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    def action_cargoson_label_url(self):
        """Open label PDF attachment or URL"""
        if self.label_attachment_id:
            return {
                'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % self.label_attachment_id.id,
                'target': 'self',
            }
        elif self.label_url:
            return {
            'type': 'ir.actions.act_url',
            'url': self.label_url,
            'target': 'new',
        }
        else:
            raise UserError(_('Label document is not available.'))

    def action_cargoson_cmr_url(self):
        """Open CMR PDF attachment or URL"""
        if self.cmr_attachment_id:
            return {
                'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % self.cmr_attachment_id.id,
                'target': 'self',
            }
        elif self.cmr_url:
            return {
            'type': 'ir.actions.act_url',
            'url': self.cmr_url,
            'target': 'new',
        }
        else:
            raise UserError(_('CMR document is not available.'))

    def action_cargoson_bol_url(self):
        """Open BOL PDF attachment or URL"""
        if self.bol_attachment_id:
            return {
                'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % self.bol_attachment_id.id,
                'target': 'self',
            }
        elif self.bol_url:
            return {
                'type': 'ir.actions.act_url',
                'url': self.bol_url,
                'target': 'new',
            }
        else:
            raise UserError(_('BOL document is not available.'))

    def action_cargoson_waybill_url(self):
        """Open waybill PDF attachment or URL"""
        if self.waybill_attachment_id:
            return {
                'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % self.waybill_attachment_id.id,
                'target': 'self',
            }
        elif self.waybill_url:
            return {
            'type': 'ir.actions.act_url',
            'url': self.waybill_url,
            'target': 'new',
        }
        else:
            raise UserError(_('Waybill document is not available.'))

    def action_cargoson_tracking_url(self):
        return {
            'type': 'ir.actions.act_url',
            'url': self.tracking_url,
            'target': 'new',
        }

    def update_picking(self, only_pickings=None):
        """Push SO carrier/packages to pickings with this origin.

        :param only_pickings: optional ``stock.picking`` recordset (e.g. replan wizard) to update
            only those transfers. When omitted, all open pickings with ``origin == order name`` are synced.
        """
        if only_pickings is not None:
            # v19: origin may not equal SO name (references, MRP links, renamings). Replan must still
            # sync when the transfer is linked to this order via sale_id.
            picking_ids = only_pickings.filtered(lambda p: p.sale_id == self)
            if not picking_ids:
                picking_ids = only_pickings.filtered(lambda p: p.origin == self.name)
            if not picking_ids:
                _logger.warning(
                    "update_picking(only_pickings=…): no pickings match SO %s (sale_id or origin).",
                    self.name,
                )
                return
        else:
            picking_ids = self.env['stock.picking'].search([
                ('origin', '=', self.name)
            ])
        if not picking_ids:
            _logger.warning(f"No picking found for SO {self.name}")
            return

        _logger.info(f"Updating {len(picking_ids)} pickings for SO {self.name}: {picking_ids.mapped('id')}")
        _logger.info(f"New package types count: {len(self.package_types)}")
        
        # Update each picking
        for picking_id in picking_ids:
            carrier = picking_id.carrier_id
            if (
                only_pickings is None
                and carrier
                and carrier.delivery_type == 'cargoson'
                and carrier._cargoson_is_followup_cargoson_delivery(picking_id, self)
            ):
                _logger.info(
                    "Skipping SO→picking sync for follow-up Cargoson transfer %s (own booking/labels).",
                    picking_id.name,
                )
                continue
            _logger.info(f"Updating picking {picking_id.id} - existing package types count: {len(picking_id.package_types)}")
            
            # Update basic picking information
            picking_id.incoterms_id = self.incoterm.id if self.incoterm else False
            picking_id.label_url = self.label_url
            picking_id.cmr_url = self.cmr_url
            picking_id.bol_url = self.bol_url
            picking_id.waybill_url = self.waybill_url
            picking_id.tracking_url = self.tracking_url
            
            # Update carrier information (CRITICAL: This ensures the correct carrier method is used)
            _logger.info(f"Updating picking {picking_id.id} carrier from {picking_id.carrier_id.name if picking_id.carrier_id else 'None'} to {self.carrier_id.name if self.carrier_id else 'None'}")
            picking_id.carrier_id = self.carrier_id
            
            # Update carrier service information
            picking_id.service_id = self.service_id
            picking_id.service_name = self.service_name
            picking_id.choose_service = self.choose_service
            picking_id.service_type = self.service_type
            picking_id.carrier_cost = self.carrier_cost
            
            # Clear existing package types to avoid duplicates
            if picking_id.package_types:
                _logger.info(f"Clearing {len(picking_id.package_types)} existing package types from picking {picking_id.id}")
                picking_id.package_types.unlink()
                
            # Add new package types
            for record in self.package_types:
                _logger.info(f"Creating package type: {record.package_type.name} (qty: {record.qty}, weight: {record.weight}, total_weight: {record.total_weight}) for picking {picking_id.id}")
                self.env['picking.choose.package.types'].create({
                    'picking_id': picking_id.id,
                    'package_type': record.package_type.id,
                    'height': record.height,
                    'width': record.width,
                    'depth': record.depth,
                    'qty': record.qty,
                    'weight': record.weight,
                    'total_weight': record.total_weight
                })
            
            _logger.info(f"Package sync completed for picking {picking_id.id}. Final package types count: {len(picking_id.package_types)}")

    def set_delivery_line(self, carrier, amount):
        """Override to automatically sync package information to picking when delivery line is set"""
        result = super(SaleOrder, self).set_delivery_line(carrier, amount)
        
        # Note: Package sync is now handled in the wizard after package types are updated
        # This prevents syncing with stale package data
        
        return result

    def _prepare_delivery_line_vals(self, carrier, price_unit):
        res = super(SaleOrder, self)._prepare_delivery_line_vals(carrier, price_unit)
        sale_margin_module = self.env['ir.module.module'].sudo().search([
            ('name', '=', 'sale_margin'),('state', '=', 'installed')
        ])
        unit_price = price_unit * (1.0 + self.carrier_id.margin) + self.carrier_id.fixed_margin
        res.update({'price_unit': unit_price})
        if sale_margin_module:
            res.update({'purchase_price': price_unit})
        return res

