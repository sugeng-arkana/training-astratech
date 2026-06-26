# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)


class ReplanCarrierRates(models.TransientModel):
    _name = 'replan.carrier.rates'
    _description = 'Replan Carrier Rates'

    name = fields.Char('Name')
    price = fields.Char('Rate')
    service = fields.Char('Service')
    service_id = fields.Char('Service ID')
    estimated_delivery_date = fields.Char('Estimated Delivery Date')
    wizard_id = fields.Many2one('replan.delivery.carrier', string='Replan Wizard')
    choose = fields.Boolean('Select')
    carrier_id = fields.Many2one('delivery.carrier', string='Carrier')


class ReplanPackageTypes(models.TransientModel):
    _name = 'replan.package.types'
    _description = 'Replan Package Types'

    wizard_id = fields.Many2one('replan.delivery.carrier', string='Replan Wizard')
    package_type = fields.Many2one('stock.package.type', string='Package Type')
    height = fields.Float('Height')
    width = fields.Float('Width')
    depth = fields.Float('Depth')
    qty = fields.Integer('QTY')
    weight = fields.Float('Weight')
    total_weight = fields.Float('Total Weight')

    def _picking_base_product_weight_kg(self):
        if not self.wizard_id or not self.wizard_id.picking_id:
            return 0.0
        p = self.wizard_id.picking_id
        if hasattr(p, '_cargoson_reserved_product_weight_kg'):
            w = p._cargoson_reserved_product_weight_kg()
            if w:
                return w
        return p.shipping_weight or 0.0

    def _get_crate_weight_for_replan_line(self):
        if not self.package_type or not self.package_type.is_crate:
            return 0.0
        order = self.wizard_id.picking_id.sale_id if self.wizard_id and self.wizard_id.picking_id else False
        if not order:
            return 0.0
        crate_lines = order.order_line.filtered(
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
        if not self.package_type or not self.wizard_id or not self.wizard_id.picking_id:
            return
        self.height = self.package_type.height
        self.width = self.package_type.width
        self.depth = self.package_type.packaging_length
        self.qty = self.package_type.default_qty
        product_weight = self._picking_base_product_weight_kg()
        self.weight = product_weight
        package_qty = self.qty or 1
        crate_w = self._get_crate_weight_for_replan_line()
        base_w = (self.package_type.base_weight * package_qty) if self.package_type.base_weight else 0.0
        self.total_weight = product_weight + crate_w + base_w

    @api.onchange('weight')
    def onchange_weight(self):
        if not self.package_type:
            return
        product_weight = self.weight or 0.0
        package_qty = self.qty or 1
        crate_w = self._get_crate_weight_for_replan_line() if self.package_type.is_crate else 0.0
        base_w = (self.package_type.base_weight * package_qty) if self.package_type.base_weight else 0.0
        self.total_weight = product_weight + crate_w + base_w

    @api.onchange('qty')
    def onchange_qty(self):
        if not self.package_type:
            return
        product_weight = self.weight or 0.0
        package_qty = self.qty or 1
        crate_w = self._get_crate_weight_for_replan_line() if self.package_type.is_crate else 0.0
        base_w = (self.package_type.base_weight * package_qty) if self.package_type.base_weight else 0.0
        self.total_weight = product_weight + crate_w + base_w


class ReplanDeliveryCarrier(models.TransientModel):
    _name = 'replan.delivery.carrier'
    _description = 'Replan Delivery Carrier'

    picking_id = fields.Many2one('stock.picking', string='Picking', required=True)
    carrier_id = fields.Many2one('delivery.carrier', string='Carrier', required=True)
    carrier_rates = fields.One2many('replan.carrier.rates', 'wizard_id', string='Carrier Rates')
    package_types = fields.One2many('replan.package.types', 'wizard_id', string='Package Types')
    comment = fields.Char('Comment')
    incoterms_id = fields.Many2one('account.incoterms', string='Incoterms')
    cargoson_weight_uom = fields.Selection(related='carrier_id.cargoson_weight_uom', string='Weight Unit')
    cargoson_length_uom = fields.Selection(related='carrier_id.cargoson_length_uom', string='Length Unit')
    has_selected_rate = fields.Boolean(compute='_compute_has_selected_rate', string='Has Selected Rate')
    available_carrier_ids = fields.Many2many('delivery.carrier', compute='_compute_available_carrier_ids', string='Available Carriers')

    @api.depends('picking_id.partner_id.country_id')
    def _compute_available_carrier_ids(self):
        for rec in self:
            carriers = self.env['delivery.carrier'].search([('delivery_type', '=', 'cargoson')])
            partner = rec.picking_id and rec.picking_id.partner_id
            result = []
            for c in carriers:
                if not c.use_destination_filtering:
                    result.append(c.id)
                else:
                    if not c.country_ids or (partner and partner.country_id and partner.country_id in c.country_ids):
                        result.append(c.id)
            rec.available_carrier_ids = [(6, 0, result)]

    @api.depends('carrier_rates.choose')
    def _compute_has_selected_rate(self):
        for record in self:
            record.has_selected_rate = bool(record.carrier_rates.filtered(lambda r: r.choose))

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        picking_id = self.env.context.get('active_id')
        if not picking_id:
            return res
        picking = self.env['stock.picking'].browse(picking_id)
        if not picking.exists():
            return res
        # Load from picking
        carrier = picking.carrier_id
        if not carrier or carrier.delivery_type != 'cargoson':
            return res
        res['carrier_id'] = carrier.id
        res['comment'] = picking.comment
        res['incoterms_id'] = picking.incoterms_id.id if picking.incoterms_id else (carrier.incoterms_id.id if carrier.incoterms_id else False)
        # Build package_types from picking
        package_lines = []
        if picking.package_types:
            for line in picking.package_types:
                package_lines.append((0, 0, {
                    'package_type': line.package_type.id,
                    'weight': line.weight,
                    'total_weight': line.total_weight,
                    'height': line.height,
                    'width': line.width,
                    'depth': line.depth,
                    'qty': line.qty,
                }))
        elif picking.package_type:
            # Single package from picking
            total_w = picking.shipping_weight or 0
            if picking.package_types:
                total_w = sum(pkg.total_weight for pkg in picking.package_types)
            package_lines.append((0, 0, {
                'package_type': picking.package_type.id,
                'weight': 0.0,
                'total_weight': total_w,
                'height': picking.height,
                'width': picking.width,
                'depth': picking.depth,
                'qty': picking.package_qty or 1,
            }))
        elif picking.sale_id and picking.sale_id.package_types:
            # From sale order package types
            for line in picking.sale_id.package_types:
                package_lines.append((0, 0, {
                    'package_type': line.package_type.id,
                    'weight': line.weight,
                    'total_weight': line.total_weight,
                    'height': line.height,
                    'width': line.width,
                    'depth': line.depth,
                    'qty': line.qty,
                }))
        if package_lines:
            vals_only = [pl[2] for pl in package_lines]
            picking._cargoson_replan_apply_reserved_product_weight(vals_only)
            res['package_types'] = [(0, 0, v) for v in vals_only]
        return res

    def _get_order_for_api(self):
        """Get sale order for API calls - from picking."""
        self.ensure_one()
        return self.picking_id.sale_id

    def button_set_package(self):
        """Set package types from picking's sale order or picking package_types."""
        self.ensure_one()
        picking = self.picking_id
        order = picking.sale_id
        if not order:
            raise ValidationError(_('Picking must be linked to a Sale Order to replan delivery.'))
        carrier = self.carrier_id
        package_type = carrier.cargoson_default_package_type_id
        if not package_type:
            raise ValidationError(_('Please configure default package type on the carrier.'))
        # Use sale order's package_types if exists, else create from order
        result = []
        if order.package_types:
            for line in order.package_types:
                result.append((0, 0, {
                    'package_type': line.package_type.id,
                    'weight': line.weight,
                    'total_weight': line.total_weight,
                    'height': line.height,
                    'width': line.width,
                    'depth': line.depth,
                    'qty': line.qty,
                }))
        else:
            reserved_w = picking._cargoson_reserved_product_weight_kg()
            fallback = order._get_estimated_weight() if hasattr(order, '_get_estimated_weight') else 0
            estimated_weight = reserved_w if reserved_w else fallback
            result = [(0, 0, {
                'package_type': package_type.id,
                'weight': estimated_weight,
                'total_weight': estimated_weight + (package_type.base_weight * (package_type.default_qty or 1)) if package_type.base_weight else estimated_weight,
                'height': package_type.height,
                'width': package_type.width,
                'depth': package_type.packaging_length,
                'qty': package_type.default_qty or 1,
            })]
        if result:
            vals_only = [r[2] for r in result]
            picking._cargoson_replan_apply_reserved_product_weight(vals_only)
            result = [(0, 0, v) for v in vals_only]
        self.package_types = [(5, 0, 0)] + result
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'replan.delivery.carrier',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def button_get_prices(self):
        """Fetch new prices from Cargoson API."""
        self.ensure_one()
        order = self._get_order_for_api()
        if not order:
            raise ValidationError(_('Picking must be linked to a Sale Order to fetch prices.'))
        if not self.carrier_id or self.carrier_id.delivery_type != 'cargoson':
            raise ValidationError(_('Carrier must be Cargoson to fetch prices.'))
        if not self.package_types:
            raise ValidationError(_('Please add package types first (use Set Packages).'))

        # Validate total weight
        total = sum(pt.total_weight for pt in self.package_types)
        if total <= 0:
            raise ValidationError(_('Total weight cannot be zero.'))

        # Flush any pending edits so Cargoson API receives the current package data
        self.env.flush_all()

        data = self.carrier_id.cargoson_rate_shipment(self.package_types, order, self.carrier_id)
        required_courier = self.carrier_id.cargoson_courier_ids.mapped('service_id')
        if 'error' not in data:
            result = []
            new = []
            for line in data:
                if str(line.get('service_id')) in required_courier:
                    result.append((0, 0, {
                        'name': line.get('courier_name'),
                        'price': line.get('price'),
                        'service': line.get('service'),
                        'service_id': line.get('service_id'),
                        'estimated_delivery_date': line.get('estimated_delivery_date'),
                        'carrier_id': self.carrier_id.id,
                    }))
                    new.append(line.get('service_id'))
            extra_lines = self.carrier_id.cargoson_courier_ids.filtered(lambda x: int(x.service_id) not in new)
            for extra_line in extra_lines:
                result.append((0, 0, {
                    'name': extra_line.name,
                    'price': 0.0,
                    'service': extra_line.service_name,
                    'service_id': extra_line.service_id,
                    'carrier_id': self.carrier_id.id,
                }))
            package_types = [(0, 0, {
                'package_type': line.package_type.id,
                'total_weight': line.total_weight,
                'weight': line.weight,
                'height': line.height,
                'width': line.width,
                'depth': line.depth,
                'qty': line.qty,
            }) for line in self.package_types]

            self.write({
                'carrier_rates': [(5, 0, 0)] + result,
                'package_types': [(5, 0, 0)] + package_types,
            })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Replan Delivery'),
            'res_model': 'replan.delivery.carrier',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def button_confirm(self):
        """Apply replan: update picking and sale order with new carrier/rate."""
        self.ensure_one()
        if not self.carrier_id:
            raise ValidationError(_('Please select a carrier first!'))
        picking = self.picking_id
        order = picking.sale_id
        if not order:
            raise ValidationError(_('Picking must be linked to a Sale Order.'))

        choose_rate = self.carrier_rates.filtered(lambda r: r.choose)
        if self.carrier_id.delivery_type == 'cargoson':
            if not choose_rate:
                raise ValidationError(_('Please select a carrier rate first!'))
            if len(choose_rate) > 1:
                raise ValidationError(_('You can choose only one option.'))
            if not self.incoterms_id:
                raise ValidationError(_('Incoterms is required!'))

        # Save old sales price if we should keep it
        old_sales_price = None
        if self.carrier_id.replan_keep_sales_price:
            delivery_line = order.order_line.filtered(lambda l: l.is_delivery)
            if delivery_line:
                old_sales_price = delivery_line[:1].price_unit

        # Update sale order
        order_vals = {
            'recompute_delivery_price': False,
            'carrier_id': self.carrier_id.id,
            'comment': self.comment or order.comment,
        }
        if self.carrier_id.delivery_type == 'cargoson':
            order.set_delivery_line(self.carrier_id, float(choose_rate.price) if choose_rate else 0.0)
            # Restore old sales price if option is set
            if old_sales_price is not None:
                new_delivery_line = order.order_line.filtered(lambda l: l.is_delivery)
                if new_delivery_line:
                    new_delivery_line[:1].write({'price_unit': old_sales_price})
            order_vals.update({
                'service_id': choose_rate.service_id if choose_rate else False,
                'service_name': choose_rate.name if choose_rate else False,
                'choose_service': choose_rate.service if choose_rate else False,
                'carrier_cost': choose_rate.price if choose_rate else 0.0,
                'incoterm': self.incoterms_id.id if self.incoterms_id else False,
            })
            # Package types
            package_list = [(5, 0, 0)]
            for line in self.package_types:
                package_list.append((0, 0, {
                    'sale_id': order.id,
                    'package_type': line.package_type.id,
                    'total_weight': line.total_weight,
                    'weight': line.weight,
                    'height': line.height,
                    'width': line.width,
                    'depth': line.depth,
                    'qty': line.qty,
                }))
            order_vals['package_types'] = package_list
        order.write(order_vals)

        # Sync to picking (only the transfer being replanned — not every WH step with same SO origin)
        order.update_picking(only_pickings=picking)

        # Update picking with new carrier/service
        picking_vals = {
            'carrier_id': self.carrier_id.id,
            'comment': self.comment or picking.comment,
            'incoterms_id': self.incoterms_id.id if self.incoterms_id else False,
        }
        if self.carrier_id.delivery_type == 'cargoson' and choose_rate:
            picking_vals.update({
                'service_id': choose_rate.service_id,
                'service_name': choose_rate.name,
                'choose_service': choose_rate.service,
                'carrier_cost': choose_rate.price,
            })
        picking.write(picking_vals)

        # Clear error if any
        picking.write({'error': False})
        order.write({'error': False})

        # Cargoson: draft (rate) PATCH/POST; booking (rate_and_ship) PATCH primary first delivery only.
        # Remainder shipments (follow-up): never PATCH — clear ref so validate creates a new booking.
        if self.carrier_id.delivery_type == 'cargoson' and choose_rate:
            # PATCH/create draft read picking.package_types; flush + refresh so DB package lines
            # (synced in update_picking) are not stale in cache.
            self.env.flush_all()
            picking.invalidate_recordset()
            picking = picking.browse(picking.id)
            carrier = self.carrier_id
            doc_ref = carrier._cargoson_cargoson_document_ref(picking, order)
            is_followup = carrier._cargoson_is_followup_cargoson_delivery(picking, order)
            if carrier.integration_level == 'rate':
                if doc_ref:
                    carrier.cargoson_update_shipping(picking)
                else:
                    carrier.cargoson_create_draft_query(picking)
            else:
                if is_followup:
                    picking.write({'carrier_tracking_ref': False})
                elif doc_ref:
                    carrier.cargoson_update_shipping(picking)
                else:
                    order.write({'cargoson_ref': False})
                    picking.write({'carrier_tracking_ref': False})
        return {'type': 'ir.actions.act_window_close'}
