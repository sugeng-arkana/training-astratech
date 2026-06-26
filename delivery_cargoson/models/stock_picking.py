from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_is_zero
import logging

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    @api.model
    def _register_hook(self):
        """Ensure cargoson columns exist (handles DBs upgraded from older module versions)."""
        super()._register_hook()
        cr = self.env.cr
        for col in ('cargoson_query_id', 'cargoson_orders'):
            cr.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'stock_picking' AND column_name = %s
                """,
                (col,),
            )
            if not cr.fetchone():
                cr.execute("ALTER TABLE stock_picking ADD COLUMN %s VARCHAR" % col)

    cargoson_orders = fields.Char(
        string="cargoson Order(s)",
        copy=False, readonly=True,
        help="Store cargoson order(s) in a (+) separated string, used in cancelling the order."
    )
    cargoson_query_id = fields.Char(
        string='Cargoson Query ID',
        copy=False,
        help='Cargoson query/shipment ID. Used for PATCH when updating a draft before it is booked.'
    )
    label_url = fields.Char('Label URL')
    cmr_url = fields.Char('CMR URL')
    bol_url = fields.Char('BOL URL')
    waybill_url = fields.Char('Waybill URL')
    tracking_url = fields.Char('Cargoson Tracking URL')
    error = fields.Text('Cargoson Error', help='Error message from Cargoson booking creation, if any. Click the Cargoson link to view the query in Cargoson.')
    # PDF attachment fields
    label_attachment_id = fields.Many2one('ir.attachment', string='Label PDF', copy=False, readonly=True)
    cmr_attachment_id = fields.Many2one('ir.attachment', string='CMR PDF', copy=False, readonly=True)
    bol_attachment_id = fields.Many2one('ir.attachment', string='BOL PDF', copy=False, readonly=True)
    waybill_attachment_id = fields.Many2one('ir.attachment', string='Waybill PDF', copy=False, readonly=True)

    package_type = fields.Many2one('stock.package.type', string='Package Type')
    package_qty = fields.Integer('Package Qty', default=1)
    height = fields.Float('Height')
    width = fields.Float('Width')
    depth = fields.Float('Depth')
    comment = fields.Char('Comment')
    incoterms_id = fields.Many2one('account.incoterms', string='Incoterms')
    package_types = fields.One2many('picking.choose.package.types', 'picking_id', string='Package Types')
    cargoson_weight_uom = fields.Selection(related='carrier_id.cargoson_weight_uom', readonly=True)
    cargoson_length_uom = fields.Selection(related='carrier_id.cargoson_length_uom', readonly=True)
    delivery_weight_uom = fields.Char(
        string='Weight Unit',
        compute='_compute_delivery_units',
        store=True,
        readonly=True,
        help='Unit for package weights (from shipping method).')
    delivery_length_uom = fields.Char(
        string='Dimension Unit',
        compute='_compute_delivery_units',
        store=True,
        readonly=True,
        help='Unit for package dimensions (from shipping method).')

    @api.depends('carrier_id', 'carrier_id.delivery_type', 'carrier_id.cargoson_weight_uom', 'carrier_id.cargoson_length_uom')
    def _compute_delivery_units(self):
        for picking in self:
            if picking.carrier_id and picking.carrier_id.delivery_type == 'cargoson':
                picking.delivery_weight_uom = picking.carrier_id.cargoson_weight_uom or 'kg'
                picking.delivery_length_uom = picking.carrier_id.cargoson_length_uom or 'm'
            else:
                picking.delivery_weight_uom = False
                picking.delivery_length_uom = False

    def _cargoson_reserved_product_weight_kg(self):
        """Sum of product.weight × Quantity on each move (same as Operations list), in product UoM.

        Uses ``stock.move.quantity`` (reserved/picked qty), not ``product_uom_qty`` (demand).
        """
        self.ensure_one()
        total = 0.0
        for move in self.move_ids.filtered(
            lambda m: m.state != 'cancel'
            and m.product_id
            and m.product_id.type in ('product', 'consu')
        ):
            qty = move.product_uom._compute_quantity(
                move.quantity, move.product_id.uom_id, rounding_method='HALF-UP'
            )
            if float_is_zero(qty, precision_rounding=move.product_id.uom_id.rounding):
                continue
            total += (move.product_id.weight or 0.0) * qty
        return total

    def _cargoson_replan_apply_reserved_product_weight(self, package_line_vals_list):
        """Scale wizard package lines so total product weight matches reserved qty on the picking."""
        self.ensure_one()
        if not package_line_vals_list:
            return
        reserved = self._cargoson_reserved_product_weight_kg()
        if float_is_zero(reserved, precision_rounding=0.0001):
            return
        sum_tw = sum(v.get('total_weight') or 0.0 for v in package_line_vals_list)
        sum_w = sum(v.get('weight') or 0.0 for v in package_line_vals_list)
        if not float_is_zero(sum_tw, precision_rounding=0.0001):
            scale = reserved / sum_tw
            for v in package_line_vals_list:
                v['total_weight'] = (v.get('total_weight') or 0.0) * scale
                v['weight'] = (v.get('weight') or 0.0) * scale
            return
        if not float_is_zero(sum_w, precision_rounding=0.0001):
            scale = reserved / sum_w
            for v in package_line_vals_list:
                v['total_weight'] = (v.get('total_weight') or 0.0) * scale
                v['weight'] = (v.get('weight') or 0.0) * scale
            return
        if package_line_vals_list:
            package_line_vals_list[0]['weight'] = reserved
            package_line_vals_list[0]['total_weight'] = reserved

    # Carrier service information (copied from sale order)
    service_id = fields.Char('Service ID')
    service_name = fields.Char('Service Name')
    choose_service = fields.Char('Service')
    service_type = fields.Char('Service Type')
    carrier_cost = fields.Float('Carrier Cost')


    def action_cargoson_label_url(self):
        """Open label PDF attachment or URL"""
        self.ensure_one()
        if self.label_attachment_id:
            return {
                'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % self.label_attachment_id.id,
                'target': 'self',
            }
        elif self.label_url:
            # Try to download if carrier settings allow it
            if self.carrier_id and self.carrier_id.delivery_type == 'cargoson' and self.carrier_id.download_label:
                from .cargoson_request import cargoson
                cargoson_api = cargoson(self.carrier_id)
                delay_seconds = self.carrier_id.download_delay_seconds or 3
                max_retries = self.carrier_id.download_max_retries or 2
                attachment = cargoson_api._download_pdf_as_attachment(
                    self.label_url,
                    f'Cargoson_Label_{self.name or self.origin or "Unknown"}.pdf',
                    'stock.picking',
                    self.id,
                    'label',
                    delay_seconds=delay_seconds,
                    max_retries=max_retries
                )
                if attachment:
                    self.label_attachment_id = attachment.id
                    return {
                        'type': 'ir.actions.act_url',
                        'url': '/web/content/%s?download=true' % attachment.id,
                        'target': 'self',
                    }
            # Fallback to opening URL
            return {
                'type': 'ir.actions.act_url',
                'url': self.label_url,
                'target': 'new',
            }
        else:
            raise UserError(_('Label document is not available.'))

    def action_cargoson_cmr_url(self):
        """Open CMR PDF attachment or URL"""
        self.ensure_one()
        if self.cmr_attachment_id:
            return {
                'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % self.cmr_attachment_id.id,
                'target': 'self',
            }
        elif self.cmr_url:
            # Try to download if carrier settings allow it
            if self.carrier_id and self.carrier_id.delivery_type == 'cargoson' and self.carrier_id.download_cmr:
                from .cargoson_request import cargoson
                cargoson_api = cargoson(self.carrier_id)
                delay_seconds = self.carrier_id.download_delay_seconds or 3
                max_retries = self.carrier_id.download_max_retries or 2
                attachment = cargoson_api._download_pdf_as_attachment(
                    self.cmr_url,
                    f'Cargoson_CMR_{self.name or self.origin or "Unknown"}.pdf',
                    'stock.picking',
                    self.id,
                    'cmr',
                    delay_seconds=delay_seconds,
                    max_retries=max_retries
                )
                if attachment:
                    self.cmr_attachment_id = attachment.id
                    return {
                        'type': 'ir.actions.act_url',
                        'url': '/web/content/%s?download=true' % attachment.id,
                        'target': 'self',
                    }
            # Fallback to opening URL
            return {
                'type': 'ir.actions.act_url',
                'url': self.cmr_url,
                'target': 'new',
            }
        else:
            raise UserError(_('CMR document is not available.'))

    def action_cargoson_bol_url(self):
        """Open BOL PDF attachment or URL"""
        self.ensure_one()
        if self.bol_attachment_id:
            return {
                'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % self.bol_attachment_id.id,
                'target': 'self',
            }
        elif self.bol_url:
            # Try to download if carrier settings allow it
            if self.carrier_id and self.carrier_id.delivery_type == 'cargoson' and self.carrier_id.download_bol:
                from .cargoson_request import cargoson
                cargoson_api = cargoson(self.carrier_id)
                delay_seconds = self.carrier_id.download_delay_seconds or 3
                max_retries = self.carrier_id.download_max_retries or 2
                attachment = cargoson_api._download_pdf_as_attachment(
                    self.bol_url,
                    f'Cargoson_BOL_{self.name or self.origin or "Unknown"}.pdf',
                    'stock.picking',
                    self.id,
                    'bol',
                    delay_seconds=delay_seconds,
                    max_retries=max_retries
                )
                if attachment:
                    self.bol_attachment_id = attachment.id
                    return {
                        'type': 'ir.actions.act_url',
                        'url': '/web/content/%s?download=true' % attachment.id,
                        'target': 'self',
                    }
            # Fallback to opening URL
            return {
                'type': 'ir.actions.act_url',
                'url': self.bol_url,
                'target': 'new',
            }
        else:
            raise UserError(_('BOL document is not available.'))

    def action_replan_delivery(self):
        """Open Replan Delivery wizard to fetch new prices and select another carrier."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Replan Delivery'),
            'res_model': 'replan.delivery.carrier',
            'view_mode': 'form',
            'target': 'new',
            'context': {'active_id': self.id, 'default_picking_id': self.id},
        }

    def cargoson_link(self):
        if not self.carrier_tracking_ref or len(self.carrier_tracking_ref) < 2:
            raise UserError(_('Cargoson reference is not available'))
        
        # Determine base URL based on environment
        if self.carrier_id and self.carrier_id.prod_environment:
            # Production environment
            base_url = 'https://www.cargoson.com/queries/'
        else:
            # Test/Staging environment
            base_url = 'https://cargoson-staging.herokuapp.com/queries/'
        
        url = base_url + self.carrier_tracking_ref[2:]
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    def action_cargoson_waybill_url(self):
        """Open waybill PDF attachment or URL"""
        self.ensure_one()
        if self.waybill_attachment_id:
            return {
                'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % self.waybill_attachment_id.id,
                'target': 'self',
            }
        elif self.waybill_url:
            # Try to download if carrier settings allow it
            if self.carrier_id and self.carrier_id.delivery_type == 'cargoson' and self.carrier_id.download_waybill:
                from .cargoson_request import cargoson
                cargoson_api = cargoson(self.carrier_id)
                delay_seconds = self.carrier_id.download_delay_seconds or 3
                max_retries = self.carrier_id.download_max_retries or 2
                attachment = cargoson_api._download_pdf_as_attachment(
                    self.waybill_url,
                    f'Cargoson_Waybill_{self.name or self.origin or "Unknown"}.pdf',
                    'stock.picking',
                    self.id,
                    'waybill',
                    delay_seconds=delay_seconds,
                    max_retries=max_retries
                )
                if attachment:
                    self.waybill_attachment_id = attachment.id
                    return {
                        'type': 'ir.actions.act_url',
                        'url': '/web/content/%s?download=true' % attachment.id,
                        'target': 'self',
                    }
            # Fallback to opening URL
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

    def open_website_url(self):
        self.ensure_one()
        if not self.tracking_url:
            raise UserError(_("Your delivery method has no redirect on courier provider's website to track this order."))

        client_action = {
            'type': 'ir.actions.act_url',
            'name': "Shipment Tracking Page",
            'target': 'new',
            'url': self.tracking_url,
        }
        return client_action

    def send_to_shipper(self):
        """
        Standard Odoo propagates carrier_tracking_ref to every picking in the move chain (orig/dest).
        Cargoson books one shipment per transfer; follow-up/backorder pickings must keep their own ref.
        """
        self.ensure_one()
        if self.carrier_id and self.carrier_id.delivery_type == 'cargoson':
            res = self.carrier_id.send_shipping(self)[0]
            if self.carrier_id.free_over and self.sale_id:
                amount_without_delivery = self.sale_id._compute_amount_total_without_delivery()
                if self.carrier_id._compute_currency(
                    self.sale_id, amount_without_delivery, 'pricelist_to_company'
                ) >= self.carrier_id.amount:
                    res['exact_price'] = 0.0
            # Odoo 19+: _apply_margins(price, order). Odoo 17/18: _apply_margins(price) + context order=
            carrier = self.carrier_id
            try:
                self.carrier_price = carrier._apply_margins(res['exact_price'], self.sale_id)
            except TypeError:
                self.carrier_price = carrier.with_context(order=self.sale_id)._apply_margins(res['exact_price'])
            if res.get('tracking_number'):
                if self.carrier_tracking_ref and res['tracking_number'] not in self.carrier_tracking_ref:
                    self.carrier_tracking_ref += "," + res['tracking_number']
                elif not self.carrier_tracking_ref:
                    self.carrier_tracking_ref = res['tracking_number']
            order_currency = self.sale_id.currency_id or self.company_id.currency_id
            msg = (
                _("Shipment sent to carrier %(carrier_name)s for shipping with tracking number %(ref)s",
                  carrier_name=self.carrier_id.name,
                  ref=self.carrier_tracking_ref)
                + Markup("<br/>")
                + _("Cost: %(price).2f %(currency)s",
                    price=self.carrier_price,
                    currency=order_currency.name)
            )
            self.message_post(body=msg)
            self._add_delivery_cost_to_so()
            return
        return super().send_to_shipper()

    def _create_backorder_picking(self):
        """Backorders are created with copy(); clear first-shipment Cargoson data so the follow-up is independent."""
        backorder_picking = super()._create_backorder_picking()
        if backorder_picking.carrier_id and backorder_picking.carrier_id.delivery_type == 'cargoson':
            backorder_picking.sudo().write(dict(
                self._cargoson_backorder_clear_shipment_identity_vals(),
                **self._cargoson_backorder_clear_initial_move_snapshot_vals(),
            ))
        return backorder_picking

    def _cargoson_backorder_clear_shipment_identity_vals(self):
        return {
            'carrier_tracking_ref': False,
            'label_url': False,
            'cmr_url': False,
            'bol_url': False,
            'waybill_url': False,
            'tracking_url': False,
            'error': False,
            'cargoson_query_id': False,
            'cargoson_orders': False,
        }

    def _cargoson_backorder_clear_initial_move_snapshot_vals(self):
        """
        Backorders must not keep the first delivery's package lines, dimensions, or chosen Cargoson
        service/cost (draft & booking). Those describe the initial WH move; the remainder shipment
        needs its own weights/packages after replan / Get rate.
        """
        return {
            'package_types': [(5, 0, 0)],
            'package_type': False,
            'package_qty': 1,
            'height': 0.0,
            'width': 0.0,
            'depth': 0.0,
            'comment': False,
            'service_id': False,
            'service_name': False,
            'choose_service': False,
            'service_type': False,
            'carrier_cost': 0.0,
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # copy() backorders include parent's tracking ref — never treat as this transfer's Cargoson id
            if vals.get('backorder_id'):
                vals.update(self._cargoson_backorder_clear_shipment_identity_vals())
                vals.update(self._cargoson_backorder_clear_initial_move_snapshot_vals())
            if 'origin' in vals:
                package_line_list = []
                origin = vals.get('origin')
                if origin is not False and origin:
                    record_id = False
                    # Resolve order by exact name: works with any sequence (S1, S10, SO001, TLL001, etc.)
                    so = self.env['sale.order'].search([('name', '=', origin)], limit=1)
                    if so:
                        record_id = so
                        if not vals.get('backorder_id'):
                            vals['carrier_tracking_ref'] = record_id.cargoson_ref
                    else:
                        purchase_module = self.env['ir.module.module'].sudo().search(
                            [('name', '=', 'delivery_purchase'), ('state', '=', 'installed')])
                        if purchase_module:
                            po = self.env['purchase.order'].search([('name', '=', origin)], limit=1)
                            if po:
                                record_id = po
                                if not vals.get('backorder_id'):
                                    vals['carrier_tracking_ref'] = record_id.cargoson_ref
                    if record_id:
                        # Copy carrier so all pickings (Pick/Pack/Out) get it when SO has a carrier.
                        # Otherwise only pickings whose route rule has propagate_carrier get it (often
                        # only Out), so trigger step (e.g. Pack) has no carrier_id when shipping was
                        # added before SO confirmation and nothing happens on validate.
                        if getattr(record_id, 'carrier_id', None):
                            vals['carrier_id'] = record_id.carrier_id.id
                        if not vals.get('backorder_id'):
                            vals['label_url'] = record_id.label_url
                            vals['cmr_url'] = record_id.cmr_url
                            vals['bol_url'] = getattr(record_id, 'bol_url', None) or None
                            vals['waybill_url'] = record_id.waybill_url
                            vals['tracking_url'] = record_id.tracking_url
                            vals['package_type'] = record_id.package_type.id if record_id.package_type else None
                            vals['package_qty'] = record_id.package_qty
                            vals['comment'] = record_id.comment
                            vals['height'] = record_id.height
                            vals['width'] = record_id.width
                            vals['depth'] = record_id.depth
                            vals['incoterms_id'] = record_id.incoterm.id if getattr(record_id, 'incoterm', None) else None
                            vals['service_id'] = record_id.service_id
                            vals['service_name'] = record_id.service_name
                            vals['choose_service'] = record_id.choose_service
                            vals['service_type'] = record_id.service_type
                            vals['carrier_cost'] = record_id.carrier_cost
                            for package_line in record_id.package_types:
                                package_line_list.append((0, 0, {
                                    'depth': package_line.depth,
                                    'width': package_line.width,
                                    'height': package_line.height,
                                    'qty': package_line.qty,
                                    'package_type': package_line.package_type.id,
                                    'weight': package_line.weight,
                                    'total_weight': package_line.total_weight,
                                }))
                            vals['package_types'] = package_line_list
                        elif getattr(record_id, 'incoterm', None):
                            vals['incoterms_id'] = record_id.incoterm.id
        res = super(StockPicking, self).create(vals_list)
        return res

    def _send_confirmation_email(self):
        """Override to automatically trigger booking creation for sale orders with rate_and_ship integration"""
        # First, handle sale order pickings for booking version (rate_and_ship)
        # This ensures booking is created even when shipping is added before sales order confirmation
        for picking in self.filtered(
            lambda r: (r.carrier_id and 
                      r.carrier_id.delivery_type == 'cargoson' and
                      r.carrier_id.integration_level == 'rate_and_ship' and
                      r.sale_id and
                      not r.carrier_tracking_ref)  # Only if booking doesn't exist yet
        ):
            # Check if this picking should trigger booking creation based on trigger_picking_type_id
            if picking._should_trigger_cargoson_action():
                try:
                    _logger.info(f"Auto-triggering booking creation for picking {picking.name} from sale order {picking.sale_id.name}")
                    # Call send_to_shipper which will trigger cargoson_send_shipping
                    picking.sudo().send_to_shipper()
                except Exception as e:
                    _logger.error(f"Error auto-triggering booking creation for picking {picking.name}: {e}", exc_info=True)
                    # Don't raise - let the standard Odoo flow handle errors
        
        # Call parent method to handle standard Odoo delivery flow
        return super()._send_confirmation_email()

    def _should_trigger_cargoson_action(self):
        """
        Check if this picking should trigger Cargoson actions (booking creation, label download)
        based on carrier's trigger_picking_type_id setting.
        
        Returns:
            bool: True if this picking matches the configured trigger picking type
        """
        self.ensure_one()
        if not self.carrier_id or self.carrier_id.delivery_type != 'cargoson':
            return False
        
        carrier = self.carrier_id
        
        # If no trigger picking type is configured, default to outgoing (current behavior)
        if not carrier.trigger_picking_type_id:
            # Default: only trigger on outgoing pickings
            return self.picking_type_id.code == 'outgoing'
        
        # Check if this picking's type matches the configured trigger type
        return self.picking_type_id.id == carrier.trigger_picking_type_id.id

    def _action_done(self):
        """Override to trigger booking creation for internal pickings when trigger matches"""
        result = super(StockPicking, self)._action_done()
        
        # For booking version (rate_and_ship), trigger booking creation on internal pickings
        # if they match the trigger picking type
        for picking in self:
            if (picking.carrier_id and 
                picking.carrier_id.delivery_type == 'cargoson' and
                picking.carrier_id.integration_level == 'rate_and_ship'):
                
                # Check if this picking should trigger booking creation
                if picking._should_trigger_cargoson_action():
                    # For internal pickings, send_to_shipper() might not be called automatically
                    # So we need to manually trigger booking creation
                    if picking.picking_type_id.code != 'outgoing':
                        try:
                            # Directly call cargoson_send_shipping to create booking
                            # This bypasses send_to_shipper() which might have restrictions for internal pickings
                            picking.carrier_id.cargoson_send_shipping(picking)
                        except Exception as e:
                            import logging
                            _logger = logging.getLogger(__name__)
                            _logger.error(f"Error triggering booking creation for picking {picking.name}: {e}", exc_info=True)
        
        return result

    def button_validate(self):
        """Override to download Cargoson documents when picking is validated"""
        result = super(StockPicking, self).button_validate()
        
        # Download Cargoson documents if carrier is Cargoson and has URLs
        # This works for both Draft (queries) and Booking (rate_and_ship) modes
        for picking in self:
            if picking.carrier_id and picking.carrier_id.delivery_type == 'cargoson':
                # Check if this picking type should trigger the action
                if not picking._should_trigger_cargoson_action():
                    continue
                
                # Invalidate cache and re-read the record to get latest URLs (they might be set during validation)
                picking.invalidate_recordset(['label_url', 'cmr_url', 'bol_url', 'waybill_url', 'label_attachment_id', 'cmr_attachment_id', 'bol_attachment_id', 'waybill_attachment_id'])
                # Re-read the fields from database
                picking = picking.env['stock.picking'].browse(picking.id)
                
                # Check if we have URLs and carrier settings allow downloads
                carrier = picking.carrier_id
                
                if (carrier.download_label or carrier.download_cmr or carrier.download_bol or carrier.download_waybill):
                    # Check if we have at least one URL that needs downloading
                    needs_download = False
                    if carrier.download_label and picking.label_url and not picking.label_attachment_id:
                        needs_download = True
                    if carrier.download_cmr and picking.cmr_url and not picking.cmr_attachment_id:
                        needs_download = True
                    if carrier.download_bol and picking.bol_url and not picking.bol_attachment_id:
                        needs_download = True
                    if carrier.download_waybill and picking.waybill_url and not picking.waybill_attachment_id:
                        needs_download = True
                    
                    if needs_download:
                        from .cargoson_request import cargoson
                        cargoson_api = cargoson(picking.carrier_id)
                        cargoson_api._download_cargoson_documents(picking)
        
        return result
