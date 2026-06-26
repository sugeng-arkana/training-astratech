import logging
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
from odoo.tools.date_utils import float_to_time

from .cargoson_request import cargoson

_logger = logging.getLogger(__name__)


class DeliverCarrier(models.Model):
    _inherit = 'delivery.carrier'

    delivery_type = fields.Selection(
        selection_add=[('cargoson', 'Cargoson')],
        ondelete={'cargoson': 'cascade'}
    )
    cargoson_access_token = fields.Text(
        string="Cargoson Access Token",
        help="Generate access token using Cargoson credentials", copy=False
    )

    cargoson_courier_ids = fields.Many2many(
        'cargoson.courier',
        string="Cargoson Couriers", copy=False,
        domain="[('carrier_id', '=', id)]",
        help="Get all the integrated Couriers from your cargoson account."
             "Based on the courier selections the rate will be fetched from the cargoson."
    )
    cargoson_default_package_type_id = fields.Many2one(
        "stock.package.type",
        string="Package Type",
        help="cargoson requires package dimensions for getting accurate rate, "
             "you can define these in a package type that you set as default"
    )
    cargoson_payment_method = fields.Selection(
        [('prepaid', 'Prepaid'), ('cod', 'COD')],
        default="prepaid",
        string="Payment Method",
        help="The method of payment. Can be either COD (Cash on delivery) Or Prepaid while creating cargoson order."
    )
    cargoson_email = fields.Char(
        string="Cargoson Email",
        help="Enter your Username from Cargoson account (API).",
        default='dummy'
    )
    cargoson_password = fields.Char(
        string="Cargoson Password",
        help="Enter your Password from Cargoson account (API).",
        default='dummy'
    )
    collection_time = fields.Float('Collection Time From')
    delivery_time = fields.Float('Collection Time To', default='18.00')
    crate_product_category = fields.Many2one('product.category', string='Crate product category')
    use_for = fields.Selection([
        ('sale', 'Sale'),
        ('purchase', 'Purchase'),
        ('picking', 'Picking')
    ], default="picking", string='Use For')
    production_url = fields.Char('Production URL', default='https://www.cargoson.com/api/')
    production_token = fields.Char('Production Token')
    use_cost_sale = fields.Boolean('Use Cost on Sales')
    incoterms_id = fields.Many2one('account.incoterms', string='Incoterms')
    cargoson_weight_uom = fields.Selection([
        ('kg', 'kg'),
        ('lb', 'lb'),
    ], string='Weight Unit', default='kg',
        help='Unit for weight in the shipping wizard and package forms. US users can select lb.')
    cargoson_length_uom = fields.Selection([
        ('m', 'm'),
        ('ft', 'ft'),
    ], string='Length Unit', default='m',
        help='Unit for dimensions (length, width, height) in the shipping wizard and package forms. US users can select ft.')

    def _weight_to_user(self, value_kg):
        if value_kg is None or value_kg is False:
            return value_kg
        if self.cargoson_weight_uom == 'lb':
            return round(value_kg * 2.20462, 2)
        return value_kg

    def _weight_from_user(self, value):
        if value is None or value is False:
            return value
        if self.cargoson_weight_uom == 'lb':
            return round(value / 2.20462, 2)
        return value

    def _length_to_user(self, value_cm):
        if value_cm is None or value_cm is False:
            return value_cm
        if self.cargoson_length_uom == 'ft':
            return round(value_cm * 0.0328084, 2)
        return round(value_cm * 0.01, 2)

    def _length_from_user(self, value):
        if value is None or value is False:
            return value
        if self.cargoson_length_uom == 'ft':
            return round(value / 0.0328084, 2)
        return round(value * 100, 2)

    def _weight_to_api(self, value_kg):
        """
        Convert weight from internal (kg) to the unit sent to Cargoson API.
        Metric: kg. Imperial: lb.
        """
        if value_kg is None or value_kg is False:
            return value_kg
        if self.cargoson_send_units == 'imperial':
            return round(value_kg * 2.20462, 2)
        return round(value_kg, 2)

    def _dimension_to_api(self, value_cm):
        """
        Convert dimension from internal (cm) to the unit sent to Cargoson API.
        Metric: cm. Imperial: inches.
        """
        if value_cm is None or value_cm is False:
            return value_cm
        if self.cargoson_send_units == 'imperial':
            return round(value_cm / 2.54, 2)
        return round(value_cm, 2)

    collection_with_tail_lift = fields.Boolean('Pick-up Tail Lift', default=False, 
        help='Enable tail lift service for collection/pick-up')
    delivery_with_tail_lift = fields.Boolean('Delivery Tail Lift', default=False,
        help='Enable tail lift service for delivery')
    delivery_sms_notification = fields.Boolean('Delivery SMS Notification', default=False,
        help='Enable SMS notifications for delivery. When enabled, delivery contact will receive SMS notifications about the shipment.')
    use_freight_payer = fields.Boolean('Use Freight Payer', default=False,
        help='Enable freight payer information in shipment creation. When enabled, you can specify whether the sender or receiver is the freight payer.')
    freight_payer_type = fields.Selection([
        ('sender', 'Sender'),
        ('receiver', 'Receiver')
    ], string='Freight Payer', default='sender',
        help='Select whether the sender or receiver is the freight payer. Sender uses collection contact details, Receiver uses delivery contact details.')
    collection_contact_id = fields.Many2one('res.partner', string='Collection Contact Person',
        help='If set, this contact person\'s name, phone, and email will be used for collection contact information. If not set, warehouse partner information will be used.')
    request_carrier_api_prices = fields.Boolean('Request Carrier API prices', default=True,
        help='When enabled, requests real-time prices from carrier APIs. When disabled, uses only pricelist prices.')
    send_goods_value = fields.Boolean(
        string='Send Goods Value',
        default=False,
        help='If enabled, sends the sale order total including taxes as goods value to Cargoson when creating or updating shipments.'
    )
    integration_level = fields.Selection([
        ('rate', 'Get rate'),
        ('rate_and_ship', 'Get rate and create shipment')
    ], default='rate', string='Integration Level',
        help='Rate: Only get shipping rates. Rate and Ship: Get rates and create shipment when picking is validated.')
    trigger_picking_type_id = fields.Many2one(
        'stock.picking.type',
        string='Trigger on Picking Type',
        help='Select which picking type should trigger booking creation and label download. '
             'For one-step delivery, select the outgoing picking type. '
             'For multi-step (pick/pack/ship), select the step where booking should happen (e.g., pack or ship step). '
             'If not set, defaults to outgoing picking type.',
        domain=[('code', 'in', ['outgoing', 'internal'])],
    )
    replan_delivery = fields.Boolean(
        string='Allow Replan Delivery',
        default=False,
        help='When enabled, adds a Replan Delivery button on stock pickings. Users can fetch new prices and select a different carrier before the delivery is confirmed in Cargoson.'
    )
    replan_keep_sales_price = fields.Boolean(
        string='Replan: Keep Sales Price',
        default=False,
        help='When enabled, Replan Delivery will not recalculate the customer-facing delivery price. The existing sales price is kept even when a different carrier/rate is selected.'
    )
    use_destination_filtering = fields.Boolean(
        string="Use destination filtering on Sales Order",
        help="If enabled, only show this shipping method when the delivery address on the Sales Order matches the allowed destinations (countries, states, or zip codes set below). If disabled, carrier is always available.",
        default=False
    )
    us_address = fields.Boolean(
        string="US Address",
        default=False,
        help="If enabled, Odoo address lines 1 and 2 (street + street2) are combined and sent to Cargoson as address line 1. "
             "Odoo state code is sent as Cargoson address line 2. Use for US-format addresses."
    )
    cargoson_send_units = fields.Selection([
        ('metric', 'Metric (cm, kg)'),
        ('imperial', 'Imperial (in, lb)'),
    ], string='Units sent to Cargoson', default='metric',
        help='Select which units to send to the Cargoson API. Metric: dimensions in cm, weight in kg. Imperial: dimensions in inches, weight in lb.')
    # Document download settings
    download_label = fields.Boolean(
        string="Download Label PDF",
        default=True,
        help="If enabled, carrier label will be downloaded and stored as PDF attachment when picking is validated."
    )
    download_cmr = fields.Boolean(
        string="Download CMR PDF",
        default=True,
        help="If enabled, CMR document will be downloaded and stored as PDF attachment when picking is validated."
    )
    download_waybill = fields.Boolean(
        string="Download Waybill PDF",
        default=False,
        help="If enabled, waybill document will be downloaded and stored as PDF attachment when picking is validated."
    )
    download_bol = fields.Boolean(
        string="Download BOL PDF",
        default=False,
        help="If enabled, BOL (Bill of Lading) document will be downloaded and stored as PDF attachment when picking is validated."
    )
    download_delay_seconds = fields.Integer(
        string="Download Delay (seconds)",
        default=3,
        help="Delay in seconds before attempting to download PDF documents. "
             "This gives Cargoson time to generate the documents after booking creation. "
             "Increase this value if downloads sometimes fail."
    )
    download_max_retries = fields.Integer(
        string="Max Download Retries",
        default=2,
        help="Maximum number of retry attempts if PDF download fails. "
             "Each retry will wait the configured delay before attempting again."
    )
    # Internal remark (private_remark) sent to Cargoson
    include_cost_in_private_remark = fields.Boolean(
        string="Include Cost in Internal Remark",
        default=True,
        help="If enabled, the internal remark sent to Cargoson will include the shipping cost (service name - carrier - cost). "
             "If disabled, only service name and carrier are included."
    )
    private_remark_cost_type = fields.Selection([
        ('cargoson', 'Carrier Cost (from Cargoson)'),
        ('sale_line', 'Gross Price with Margin (from Sales Order line)'),
    ], string="Cost Type for Internal Remark",
        default='cargoson',
        help="When including cost in internal remark: use the raw cost from Cargoson, or the gross price (with margin) from the sales order delivery line."
    )
    # Commitment date (SO commitment date) usage for pick-up or delivery
    use_commitment_date = fields.Boolean(
        string="Use Commitment Date",
        default=True,
        help="If enabled, the Sales Order commitment date will be sent to Cargoson (either as collection/pick-up date or as delivery date, depending on the option below). "
             "If disabled, collection date is derived from picking scheduled date or order date plus one day, and carrier time window is used."
    )
    commitment_date_for = fields.Selection([
        ('pick_up', 'Pick-up (collection)'),
        ('delivery', 'Delivery'),
    ], string="Commitment Date For",
        default='pick_up',
        help="When using commitment date: use it as the collection/pick-up date and time, or as the requested/expected delivery date."
    )
    delivery_window_time_from = fields.Float(
        string="Delivery Window From",
        help="When commitment date is used for Delivery: start of the delivery time window (delivery_time_from). "
             "The end (delivery_time_to) is two hours after this start on the same day. "
             "The commitment date selects the calendar day only. Used when 'Commitment Date For' is Delivery."
    )

    def _get_cargoson_private_remark(self, sale_order):
        """
        Build the internal remark (private_remark) string sent to Cargoson.
        Format: "Service name - Carrier/Service [ - cost ]" depending on carrier settings.
        """
        self.ensure_one()
        if self.delivery_type != 'cargoson':
            return ''
        parts = [
            (sale_order.service_name or 'No Service'),
            (sale_order.choose_service or 'No Carrier'),
        ]
        if self.include_cost_in_private_remark:
            if self.private_remark_cost_type == 'sale_line':
                delivery_line = sale_order.order_line.filtered(
                    lambda l: l.product_id == self.product_id
                )
                cost = delivery_line[:1].price_unit if delivery_line else 0
            else:
                cost = sale_order.carrier_cost or 0
            parts.append(str(cost))
        return ' - '.join(parts)

    def _get_cargoson_goods_value_payload(self, sale_order):
        """Return optional goods value fields for Cargoson shipment payloads."""
        self.ensure_one()
        if self.delivery_type != 'cargoson' or not self.send_goods_value or not sale_order:
            return {}
        payload = {
            'goods_value': sale_order.amount_total,
        }
        currency = sale_order.currency_id or sale_order.company_id.currency_id
        if currency:
            payload['goods_value_currency'] = currency.name
        return payload

    def _cargoson_get_locked_booking_status(self, reference):
        """Return status when an existing Cargoson booking should not be edited from Odoo."""
        self.ensure_one()
        if not reference:
            return False
        booking_info = cargoson(self)._get_booking_info(reference)
        status = (
            booking_info.get('latest_status')
            or booking_info.get('status')
            or booking_info.get('booking_status')
            or ''
        )
        status = str(status).lower()
        locked_statuses = {'booked', 'confirmed', 'collected', 'delivered', 'completed'}
        return status if status in locked_statuses else False

    def _cargoson_raise_if_booking_locked(self, reference):
        status = self._cargoson_get_locked_booking_status(reference)
        if status:
            raise UserError(_(
                'Cargoson shipment %(reference)s is already %(status)s and cannot be changed from Odoo. '
                'Please make the change in Cargoson, or cancel and recreate the shipment.'
            ) % {
                'reference': reference,
                'status': status,
            })

    def toggle_prod_environment(self):
        for c in self:
            c.prod_environment = not c.prod_environment
            c.cargoson_courier_ids.unlink()

    def _cargoson_format_float_time(self, float_hours):
        """Convert Odoo float_time (hours + fraction of hour) to HH:MM for Cargoson.

        09:30 is stored as 9.5, not 9.30. ``str(9.5).replace('.', ':')`` yields ``9:5``,
        which APIs may read as 09:05.
        """
        if float_hours is None or float_hours is False:
            return None
        t = float_to_time(float(float_hours))
        return t.strftime('%H:%M')

    @api.constrains('collection_time', 'delivery_time')
    def _constrains_collection_time(self):
        for record in self:
            if record.delivery_time < record.collection_time:
                raise ValidationError(_('Delivery Time should be grater than collection time.'))

    def action_get_couriers2(self):
        """Sync available couriers from Cargoson API - manages the available couriers list only"""
        for carrier in self:
            if carrier.delivery_type != 'cargoson':
                continue
                
            sr = cargoson(carrier)
            couriers_list = sr._fetch_cargoson_couriers()
            
            if not couriers_list:
                raise ValidationError(_('Failed to fetch cargoson Couriers(s), Please try again later.'))
            
            # First, clean up ALL existing duplicates for this carrier before processing
            all_existing_couriers = self.env['cargoson.courier'].search([('carrier_id', '=', carrier.id)])
            service_groups = {}
            for courier in all_existing_couriers:
                if courier.service_id not in service_groups:
                    service_groups[courier.service_id] = []
                service_groups[courier.service_id].append(courier)
            
            # Remove ALL duplicates, keeping only the first one
            for service_id, couriers in service_groups.items():
                if len(couriers) > 1:
                    couriers[1:].unlink()
            
            # Get API service IDs
            api_service_ids = [str(courier_data.get('service_id')) for courier_data in couriers_list]
            
            # Find and remove couriers that are no longer in the API response
            couriers_to_delete = self.env['cargoson.courier'].search([
                ('carrier_id', '=', carrier.id),
                ('service_id', 'not in', api_service_ids)
            ])
            couriers_to_delete.unlink()
            
            # Process each courier from API response
            for courier_data in couriers_list:
                service_id_str = str(courier_data.get('service_id'))
                
                # Check if courier already exists for this carrier
                existing_courier = self.env['cargoson.courier'].search([
                    ('service_id', '=', service_id_str),
                    ('carrier_id', '=', carrier.id)
                ], limit=1)
                
                if existing_courier:
                    # Update existing courier
                    existing_courier.write({
                        "cargoson_courier_id": courier_data.get('carrier_id'),
                        'name': courier_data.get('carrier_name'),
                        "carrier_short_name": courier_data.get('carrier_short_name'),
                        "reg_no": courier_data.get('reg_no'),
                        "vat_no": courier_data.get('vat_no'),
                        "service_name": courier_data.get('service_name'),
                        "service_type": courier_data.get('service_type')
                    })
                else:
                    # Create new courier
                    self.env['cargoson.courier'].create({
                        'carrier_id': carrier.id,
                        "cargoson_courier_id": courier_data.get('carrier_id'),
                        'name': courier_data.get('carrier_name'),
                        "carrier_short_name": courier_data.get('carrier_short_name'),
                        "reg_no": courier_data.get('reg_no'),
                        "vat_no": courier_data.get('vat_no'),
                        "service_id": service_id_str,
                        "service_name": courier_data.get('service_name'),
                        "service_type": courier_data.get('service_type')
                    })

    def cargoson_rate_shipment(self, order_or_package_types, order=None, carrier_id=None):
        """
        Returns shipping rate for the order and chosen shipping method.
        Can be called as:
          - cargoson_rate_shipment(order) from delivery.carrier.rate_shipment
          - cargoson_rate_shipment(package_types, order, carrier_id) from Cargoson wizard
        """
        from_rate_shipment = order is None
        if order is None:
            order = order_or_package_types
            package_types = getattr(order, 'package_types', None) or self.env['sale.choose.package.types']
            carrier_id = self
        else:
            package_types = order_or_package_types
            carrier_id = carrier_id or self

        if not package_types:
            return {
                'success': False,
                'price': 0.0,
                'error_message': _('Please add package types in the Cargoson shipping wizard first.'),
                'warning_message': False,
            }

        sr = cargoson(self)
        result = sr._rate_request(package_types, order, carrier_id)

        if not from_rate_shipment:
            # Called from wizard: return raw result (list or error dict)
            if 'error' in result:
                order.write({'error': result['error']})
            return result

        # Called from rate_shipment: return standard format
        if 'error' in result:
            order.write({'error': result['error']})
            return {
                'success': False,
                'price': 0.0,
                'error_message': result['error'] if isinstance(result['error'], str) else str(result['error']),
                'warning_message': False,
            }
        if isinstance(result, list) and result:
            if getattr(order, 'service_id', None):
                match = next((r for r in result if str(r.get('service_id')) == str(order.service_id)), None)
                price = match['price'] if match else result[0]['price']
            else:
                price = result[0]['price']
            return {'success': True, 'price': float(price), 'error_message': False, 'warning_message': False}
        return {'success': False, 'price': 0.0, 'error_message': _('No rates returned.'), 'warning_message': False}

    def _cargoson_sale_order_from_picking(self, picking):
        """Resolve sale.order for Cargoson. Prefer picking.sale_id (sale_stock) so backorders work when origin != SO name."""
        SaleOrder = self.env['sale.order']
        if getattr(picking, 'sale_id', False) and picking.sale_id:
            return picking.sale_id
        if picking.origin:
            return SaleOrder.search([('name', '=', picking.origin)], limit=1)
        return SaleOrder.browse()

    def _cargoson_is_backorder_picking(self, picking):
        return bool(getattr(picking, 'backorder_id', False) and picking.backorder_id)

    def _cargoson_is_followup_cargoson_delivery(self, picking, sale_id):
        """
        True if this transfer must use its own Cargoson query/booking, not the SO's primary one:
        - standard stock backorder (copy of a partially validated delivery), or
        - same operation type already completed on this SO (e.g. first Pack done, second Pack is the
          remainder wave while Deliver may still be open — booking trigger is often before Out), or
        - any picking when another outgoing for the same sale order is already done.
        """
        if not picking or not sale_id:
            return False
        if self._cargoson_is_backorder_picking(picking):
            return True
        ptype = picking.picking_type_id
        if ptype:
            prior_same_type_done = sale_id.picking_ids.filtered(
                lambda p: p.id != picking.id
                and p.picking_type_id == ptype
                and p.state == 'done'
            )
            if prior_same_type_done:
                return True
        prior_done = sale_id.picking_ids.filtered(
            lambda p: p.id != picking.id
            and p.picking_type_id.code == 'outgoing'
            and p.state == 'done'
        )
        return bool(prior_done)

    def _cargoson_ref_is_primary_sale_duplicate(self, picking, sale_id):
        """Picking still shows the first shipment's Cargoson id (parent/SO copy), not a new query."""
        if not sale_id.cargoson_ref or not picking.carrier_tracking_ref:
            return False
        return picking.carrier_tracking_ref == sale_id.cargoson_ref

    def _cargoson_cargoson_document_ref(self, picking, sale_id):
        """
        Cargoson id/reference used for PATCH:
        - Follow-up / backorder: only this picking's own ref; never PATCH the SO primary when
          the picking still duplicates it.
        - First (primary) outgoing: sale order ref.
        """
        if not sale_id:
            return False
        if self._cargoson_is_followup_cargoson_delivery(picking, sale_id):
            ref = picking.carrier_tracking_ref or False
            if not ref:
                return False
            if self._cargoson_ref_is_primary_sale_duplicate(picking, sale_id):
                return False
            return ref
        return sale_id.cargoson_ref or False

    def cargoson_create_draft_query(self, picking):
        """
        POST a new Cargoson draft (query). First delivery stores ref on SO; backorders keep SO ref
        for the first shipment and store this picking's ref on the picking only.
        """
        self.ensure_one()
        sr = cargoson(self)
        sale_id = self._cargoson_sale_order_from_picking(picking)
        if not sale_id:
            raise UserError(_('No sales order linked to this transfer; cannot create a Cargoson draft.'))
        delivery_prices = sale_id.order_line.filtered(
            lambda p: p.product_id.default_code == 'Cargoson'
        )
        if not delivery_prices:
            raise UserError(_(
                'No delivery line with product default code "Cargoson" on the sales order. '
                'Add the delivery product or fix its default code.'
            ))
        shippings = sr._send_shipping(picking, sale_id, delivery_prices[:1].price_unit)
        picking.error = False
        picking.write(shippings)
        if self._cargoson_is_followup_cargoson_delivery(picking, sale_id):
            # Do not overwrite sale.cargoson_ref — that stays tied to the first delivery's draft/booking.
            return
        sale_vals = {
            'cargoson_ref': shippings.get('carrier_tracking_ref'),
            'carrier_id': picking.carrier_id.id,
        }
        for k in ('label_url', 'cmr_url', 'bol_url', 'waybill_url', 'tracking_url'):
            if k in shippings:
                sale_vals[k] = shippings[k]
        sale_id.write(sale_vals)

    def cargoson_update_shipping(self, pickings):
        """
        Update existing Cargoson query/booking via PATCH API.
        Use when cargoson_ref exists and data has changed (e.g. Replan Delivery).
        """
        sr = cargoson(self)
        for picking in pickings:
            sale_id = self._cargoson_sale_order_from_picking(picking)
            if not sale_id:
                continue
            doc_ref = self._cargoson_cargoson_document_ref(picking, sale_id)
            if not doc_ref:
                continue
            delivery_prices = sale_id.order_line.filtered(
                lambda p: p.product_id.default_code == 'Cargoson'
            )
            if not delivery_prices:
                raise ValidationError(_(
                    'Cannot update Cargoson draft: no delivery line with product default code "Cargoson" '
                    'on sale order %s.'
                ) % (sale_id.name,))
            try:
                shippings = sr._patch_shipment(
                    picking, sale_id, delivery_prices[:1].price_unit,
                    doc_ref,
                )
                picking.error = False
                picking.write(shippings)
                is_followup = self._cargoson_is_followup_cargoson_delivery(picking, sale_id)
                sale_vals = {'carrier_id': picking.carrier_id.id}
                if not is_followup and shippings.get('carrier_tracking_ref'):
                    sale_vals['cargoson_ref'] = shippings.get('carrier_tracking_ref')
                for k in ('label_url', 'cmr_url', 'bol_url', 'waybill_url', 'tracking_url'):
                    if k in shippings:
                        sale_vals[k] = shippings[k]
                sale_id.write(sale_vals)
            except Exception as e:
                _logger.error(f"cargoson_update_shipping failed: {e}", exc_info=True)
                raise

    def cargoson_send_shipping(self, pickings):
        sr = cargoson(self)
        res = []

        for picking in pickings:
            sale_id = self._cargoson_sale_order_from_picking(picking)

            if not sale_id:
                # No sale order found - return empty result to avoid IndexError
                _logger.warning(
                    "No sale order found for picking %s (origin=%r, sale_id=%s)",
                    picking.name, picking.origin, getattr(picking, 'sale_id', False) and picking.sale_id.id,
                )
                shipping_data = {
                    'tracking_number': False,
                    'exact_price': 0
                }
                res = res + [shipping_data]
                continue
            
            # Check if this picking type should trigger booking creation
            # For booking method (rate_and_ship), only create booking on configured picking type
            should_create_booking = True  # Default: create booking (for 'rate' mode or when trigger matches)
            
            if self.integration_level == 'rate_and_ship':
                # Check if picking type matches configured trigger type
                should_create_booking = False
                if self.trigger_picking_type_id:
                    if picking.picking_type_id.id == self.trigger_picking_type_id.id:
                        should_create_booking = True
                    else:
                        pass  # Skip booking creation - picking type doesn't match trigger
                else:
                    # Default: only create booking on outgoing pickings
                    if picking.picking_type_id.code == 'outgoing':
                        should_create_booking = True
                    else:
                        pass  # Skip booking creation - not an outgoing picking
                
                # If booking shouldn't be created on this picking, return tracking if any — but never
                # attribute the SO's primary Cargoson ref to a follow-up/backorder transfer (e.g. Pack
                # step when trigger is Delivery): that ref belongs to the first shipment only.
                if not should_create_booking:
                    followup_nt = self._cargoson_is_followup_cargoson_delivery(picking, sale_id)
                    if followup_nt:
                        shipping_data = {
                            'tracking_number': picking.carrier_tracking_ref or False,
                            'exact_price': 0,
                        }
                        res = res + [shipping_data]
                        continue
                    if sale_id.cargoson_ref:
                        shipping_data = {
                            'tracking_number': sale_id.cargoson_ref,
                            'exact_price': 0
                        }
                        res = res + [shipping_data]
                    else:
                        shipping_data = {
                            'tracking_number': False,
                            'exact_price': 0
                        }
                        res = res + [shipping_data]
                    continue
            
            # Create booking when: no SO ref yet, or follow-up delivery still has no own Cargoson id.
            followup = self._cargoson_is_followup_cargoson_delivery(picking, sale_id)
            needs_own_cargoson = followup and (
                not picking.carrier_tracking_ref
                or self._cargoson_ref_is_primary_sale_duplicate(picking, sale_id)
            )
            need_new_booking = should_create_booking and (
                not sale_id.cargoson_ref
                or needs_own_cargoson
            )
            if need_new_booking:
                delivery_prices = sale_id.order_line.filtered(lambda p: p.product_id.default_code == 'Cargoson')
                
                # For rate_and_ship (booking), ensure we have a service_id for direct booking
                if self.integration_level == 'rate_and_ship' and not sale_id.service_id:
                    error_msg = f"No service_id found for booking shipment. Sale: {sale_id.name}"
                    _logger.warning(error_msg)
                    picking.error = error_msg
                    shipping_data = {
                        'tracking_number': False,
                        'exact_price': 0
                    }
                    res = res + [shipping_data]
                    continue
                
                try:
                    shippings = sr._send_shipping(picking, sale_id, delivery_prices[:1].price_unit)
                    # Clear any previous errors on success
                    picking.error = False
                    picking.write(shippings)
                    shipping_data = {
                        'tracking_number': shippings.get('carrier_tracking_ref'),
                        'exact_price': 0
                    }
                    if followup:
                        # Do not overwrite sale.cargoson_ref — first delivery keeps the SO-level ref.
                        pass
                    else:
                        shippings.update({
                            'cargoson_ref': shippings['carrier_tracking_ref'],
                            'carrier_id': picking.carrier_id.id
                        })
                        del shippings['carrier_tracking_ref']
                        del shippings['carrier_tracking_url']
                        sale_id.write(shippings)
                    
                    # Trigger document download if URLs are available and settings allow it
                    # Only download if this picking type matches the trigger type
                    if (self.download_label or self.download_cmr or self.download_bol or self.download_waybill):
                        # Check if this picking type should trigger download
                        if not self.trigger_picking_type_id or picking.picking_type_id.id == self.trigger_picking_type_id.id:
                            # URLs are already in shippings dict, so we can use them directly
                            # But we need to check the picking record to see if attachments already exist
                            picking.invalidate_recordset(['label_attachment_id', 'cmr_attachment_id', 'bol_attachment_id', 'waybill_attachment_id'])
                            picking = picking.env['stock.picking'].browse(picking.id)
                            if picking.label_url or picking.cmr_url or picking.bol_url or picking.waybill_url:
                                sr._download_cargoson_documents(picking)
                    
                    res = res + [shipping_data]
                except Exception as e:
                    # Handle booking creation errors
                    error_message = str(e)
                    cargoson_ref = None
                    cargoson_link = ""
                    
                    # Try to get reference from the API response (stored in _last_response)
                    if hasattr(sr, '_last_response') and sr._last_response:
                        cargoson_ref = sr._last_response.get('reference')
                        # Also check if there's a query_id or similar
                        if not cargoson_ref:
                            cargoson_ref = sr._last_response.get('query_id') or sr._last_response.get('id')
                    
                    # If we have a cargoson_ref, create a clickable link
                    if cargoson_ref:
                        # Determine base URL based on environment
                        if self.prod_environment:
                            base_url = 'https://www.cargoson.com/queries/'
                        else:
                            base_url = 'https://cargoson-staging.herokuapp.com/queries/'
                        
                        # Construct URL (remove first 2 chars from ref if present, as per cargoson_link() method)
                        ref_for_url = cargoson_ref[2:] if len(cargoson_ref) > 2 else cargoson_ref
                        cargoson_url = base_url + ref_for_url
                        cargoson_link = f"\n\nView in Cargoson: {cargoson_url}"
                    
                    # Format error message with link
                    full_error = f"Cargoson Booking Error: {error_message}{cargoson_link}"
                    picking.error = full_error
                    _logger.error(f"Error creating Cargoson booking for picking {picking.name}: {full_error}")
                    
                    # Return empty result to avoid IndexError
                    shipping_data = {
                        'tracking_number': False,
                        'exact_price': 0
                    }
                    res = res + [shipping_data]
            elif sale_id.cargoson_ref or picking.carrier_tracking_ref:
                followup_el = self._cargoson_is_followup_cargoson_delivery(picking, sale_id)
                if followup_el:
                    tracking = picking.carrier_tracking_ref or False
                else:
                    tracking = picking.carrier_tracking_ref or sale_id.cargoson_ref
                shipping_data = {
                    'tracking_number': tracking,
                    'exact_price': 0
                }
                res = res + [shipping_data]
            else:
                # Booking should be created but wasn't (shouldn't happen, but handle gracefully)
                shipping_data = {
                    'tracking_number': False,
                    'exact_price': 0
                }
                res = res + [shipping_data]
        return res
