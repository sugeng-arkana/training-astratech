import json
import logging
import base64
import time
import requests
from dateutil import tz

from datetime import datetime, timedelta, timezone, date as dt_date

from odoo import fields, _
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)


def _utc_to_local(dt, to_zone):
    """Convert datetime to target zone. Odoo datetimes are stored UTC and read as naive; treat as UTC then convert."""
    if dt is None:
        return None
    if to_zone is None:
        to_zone = tz.gettz('UTC')
    if getattr(dt, 'tzinfo', None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(to_zone)


CARGOSON_DELIVERY_WINDOW_HOURS = 2.0


def _cargoson_hm_add_hours(hm_str, hours):
    """Return HH:MM = hm_str + hours, same calendar day, capped at 23:59."""
    if not hm_str:
        return None
    try:
        parts = hm_str.strip().split(':')
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        total = h * 60 + m + int(round(float(hours) * 60))
        total = max(0, min(total, 24 * 60 - 1))
        return f'{total // 60:02d}:{total % 60:02d}'
    except (ValueError, IndexError, TypeError):
        return hm_str


def _cargoson_roll_past_slot_to_working_day(date_str, time_from_str, time_to_str, to_zone):
    """
    If the slot end (date + time_to) is before *now* in ``to_zone``, move **date** forward day
    by day until Mon–Fri where the slot end is still in the future.

    Used when Odoo commitment (or computed collection date) is already in the past so Cargoson
    gets a valid next working day; Sat/Sun are skipped.
    """
    if not date_str or not to_zone:
        return date_str
    now = datetime.now(to_zone)
    y, mo, d = map(int, date_str.split('-'))
    cur = dt_date(y, mo, d)
    if not time_from_str:
        for _ in range(370):
            while cur.weekday() >= 5:
                cur += timedelta(days=1)
            if cur >= now.date():
                return cur.strftime('%Y-%m-%d')
            cur += timedelta(days=1)
        return date_str
    hf, mf = map(int, time_from_str.split(':'))
    if time_to_str:
        ht, mt = map(int, time_to_str.split(':'))
    else:
        ht, mt = 23, 59
    for _ in range(370):
        while cur.weekday() >= 5:
            cur += timedelta(days=1)
        start_dt = datetime(cur.year, cur.month, cur.day, hf, mf, tzinfo=to_zone)
        end_same_day = datetime(cur.year, cur.month, cur.day, ht, mt, tzinfo=to_zone)
        if end_same_day <= start_dt:
            effective_end = start_dt
        else:
            effective_end = end_same_day
        if now < effective_end:
            rolled = cur.strftime('%Y-%m-%d')
            if rolled != date_str:
                _logger.info(
                    'Cargoson: rolled past collection/delivery date %s -> %s (timezone %s)',
                    date_str, rolled, to_zone,
                )
            return rolled
        cur += timedelta(days=1)
    return date_str


def _cargoson_ensure_collection_end_not_in_past(parcel_dict, to_zone):
    """
    Cargoson returns 422 when collection_time_to on collection_date is not after \"now\"
    in local time (\"Collection time to can't be in the past\").
    Advance collection_date (Mon–Fri) until the same-day slot end is in the future.
    """
    if not to_zone:
        return
    date_str = parcel_dict.get('collection_date')
    t_from = parcel_dict.get('collection_time_from')
    t_to = parcel_dict.get('collection_time_to')
    if not date_str or not t_from:
        return
    if not t_to:
        try:
            parts = t_from.strip().split(':')
            hf = int(parts[0])
            mf = int(parts[1]) if len(parts) > 1 else 0
            mins = min(hf * 60 + mf + 60, 24 * 60 - 1)
            t_to = f'{mins // 60:02d}:{mins % 60:02d}'
            parcel_dict['collection_time_to'] = t_to
        except (ValueError, IndexError):
            return
    _cargoson_fix_collection_time_window(parcel_dict)
    t_from = parcel_dict.get('collection_time_from')
    t_to = parcel_dict.get('collection_time_to')
    if not t_from or not t_to:
        return
    try:
        now = datetime.now(to_zone)
        cur = dt_date(*map(int, date_str.split('-')))
        hf, mf = map(int, t_from.split(':'))
        ht, mt = map(int, t_to.split(':'))
        orig = date_str
        for _ in range(370):
            while cur.weekday() >= 5:
                cur += timedelta(days=1)
            start_same = datetime(cur.year, cur.month, cur.day, hf, mf, tzinfo=to_zone)
            end_same = datetime(cur.year, cur.month, cur.day, ht, mt, tzinfo=to_zone)
            if end_same <= start_same:
                end_same = start_same + timedelta(hours=1)
            if now < end_same:
                rolled = cur.strftime('%Y-%m-%d')
                if rolled != orig:
                    _logger.info(
                        'Cargoson: collection end was in the past; collection_date %s -> %s (%s)',
                        orig, rolled, to_zone,
                    )
                parcel_dict['collection_date'] = rolled
                return
            cur += timedelta(days=1)
    except (ValueError, TypeError, IndexError):
        return


def _cargoson_ensure_delivery_not_before_pickup(delivery_date_str, collection_date_str):
    """
    When commitment is used as **delivery** date, collection may fall on a later calendar day
    (e.g. scheduled date + 1). Ensure delivery_date is not before collection_date; if so,
    align to collection date (skipping Sat/Sun to next weekday).
    """
    if not delivery_date_str or not collection_date_str:
        return delivery_date_str
    try:
        d_del = dt_date(*map(int, delivery_date_str.split('-')))
        d_col = dt_date(*map(int, collection_date_str.split('-')))
    except (ValueError, TypeError):
        return delivery_date_str
    if d_del >= d_col:
        return delivery_date_str
    cur = d_col
    for _ in range(14):
        if cur.weekday() < 5:
            out = cur.strftime('%Y-%m-%d')
            if out != delivery_date_str:
                _logger.info(
                    'Cargoson: delivery date %s before collection %s; adjusted to %s',
                    delivery_date_str, collection_date_str, out,
                )
            return out
        cur += timedelta(days=1)
    return delivery_date_str


def _cargoson_bump_delivery_time_after_collection(parcel_dict, collection_date_str, delivery_date_str):
    """
    Cargoson rejects same-day queries when delivery_time_from is before collection starts
    (422: "Delivery time from can't be before earliest collection time").
    When collection and delivery share the same calendar day, ensure delivery starts at least
    one hour after collection_time_from; widen delivery_time_to if it would end before the new start.
    """
    if not collection_date_str or not delivery_date_str or collection_date_str != delivery_date_str:
        return
    col_from = parcel_dict.get('collection_time_from')
    d_from = parcel_dict.get('delivery_time_from')
    if not col_from or not d_from:
        return
    try:

        def _hm_to_minutes(hm):
            parts = hm.strip().split(':')
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            return h * 60 + m

        def _minutes_to_hm(mins):
            mins = max(0, min(int(mins), 24 * 60 - 1))
            return f'{mins // 60:02d}:{mins % 60:02d}'

        cmin = _hm_to_minutes(col_from)
        dmin = _hm_to_minutes(d_from)
        min_delivery = cmin + 60  # at least one hour after collection opens
        if min_delivery >= 24 * 60:
            min_delivery = 24 * 60 - 1  # 23:59 — edge case: collection late same day
        if dmin >= min_delivery:
            return
        old_from, old_to = d_from, parcel_dict.get('delivery_time_to')
        parcel_dict['delivery_time_from'] = _minutes_to_hm(min_delivery)
        _logger.info(
            'Cargoson: delivery_time_from %s before collection %s + 1h; adjusted to %s',
            old_from, col_from, parcel_dict['delivery_time_from'],
        )
        if old_to:
            tmin = _hm_to_minutes(old_to)
            if tmin <= min_delivery:
                new_to = min(min_delivery + 60, 24 * 60 - 1)
                parcel_dict['delivery_time_to'] = _minutes_to_hm(new_to)
                _logger.info(
                    'Cargoson: delivery_time_to %s not after new delivery window start; adjusted to %s',
                    old_to, parcel_dict['delivery_time_to'],
                )
    except (ValueError, TypeError, AttributeError):
        return


def _cargoson_fix_collection_time_window(parcel_dict):
    """
    Pick-up window must end after it starts. Commitment sets *from*; carrier *delivery_time*
    sets the window *end*. If commitment is later than that end, Cargoson returns 422.
    Bump *to* to at least one hour after *from* (cap 23:59).
    """
    t_from = parcel_dict.get('collection_time_from')
    t_to = parcel_dict.get('collection_time_to')
    if not t_from or not t_to:
        return

    def _hm_to_minutes(hm):
        parts = (hm or '').strip().split(':')
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return h * 60 + m

    def _minutes_to_hm(mins):
        mins = max(0, min(int(mins), 24 * 60 - 1))
        return f'{mins // 60:02d}:{mins % 60:02d}'

    try:
        fmin = _hm_to_minutes(t_from)
        tmin = _hm_to_minutes(t_to)
        if tmin > fmin:
            return
        new_end = min(fmin + 60, 24 * 60 - 1)
        old_to = t_to
        parcel_dict['collection_time_to'] = _minutes_to_hm(new_end)
        _logger.info(
            'Cargoson: collection window invalid (to=%s not after from=%s); adjusted collection_time_to to %s',
            old_to, t_from, parcel_dict['collection_time_to'],
        )
    except (ValueError, TypeError, IndexError):
        return


def cargoson_format_api_error(json_data):
    """
    Turn Cargoson API JSON into a user-visible string.
    Do not pass the raw response dict to ValidationError — the web client shows '[object Object]'.
    """
    if not json_data or not isinstance(json_data, dict):
        return _('Cargoson returned an empty or invalid response.')
    errors = json_data.get('errors', {})
    payload = json_data.get('payload') or {}
    if not isinstance(payload, dict):
        payload = {}
    message = ''

    if errors:
        if isinstance(errors, list):
            error_list = []
            for error_item in errors:
                if isinstance(error_item, str):
                    error_list.append(error_item)
                elif isinstance(error_item, dict):
                    error_list.extend(str(v) for v in error_item.values())
                else:
                    error_list.append(str(error_item))
            message = '; '.join(error_list) if error_list else ''
        elif isinstance(errors, dict):
            error_list = []
            for key, value in errors.items():
                if isinstance(value, list):
                    error_list.extend(
                        f'{key}: {v}' if key else str(v) for v in value
                    )
                else:
                    error_list.append(f'{key}: {value}' if key else str(value))
            message = '; '.join(error_list) if error_list else ''
        else:
            message = str(errors)

    if not message:
        if json_data.get('message'):
            message = _('Cargoson: %s') % json_data['message']
        elif payload.get('error_message'):
            message = _('Cargoson: %s') % payload['error_message']
        elif payload.get('awb_assign_error'):
            message = _('Cargoson: %s') % payload['awb_assign_error']
        elif not json_data.get('label_created') and json_data.get('response') is not None:
            resp = json_data['response']
            message = (
                _('Cargoson: %s') % resp
                if isinstance(resp, str)
                else _('Cargoson: %s') % json.dumps(resp)
            )

    if not message:
        st = json_data.get('status')
        qs = json_data.get('query_status')
        if st is not None or qs is not None:
            message = _('Cargoson error (HTTP %(status)s, query_status=%(qs)s).') % {
                'status': st if st is not None else '?',
                'qs': qs if qs is not None else '?',
            }
    return message


class cargoson:

    def _get_delivery_partner_info(self, order):
        """
        Get the correct delivery partner and determine if it's a private person.
        
        Logic:
        1. If delivery address (partner_shipping_id) exists, use it
        2. Otherwise, use main contact (partner_id)
        3. Check if the selected partner is a private person:
           - If it's a company (is_company=True): Always private_person=False
           - If it's not a company: Check if it has a parent company
        
        Returns:
            tuple: (delivery_partner, is_private_person)
        """
        # Step 1: Determine which partner to use for delivery
        if hasattr(order, 'partner_shipping_id') and order.partner_shipping_id:
            delivery_partner = order.partner_shipping_id
        else:
            delivery_partner = order.partner_id
        
        # Step 2: Check if it's a private person
        if delivery_partner.is_company:
            # Companies are never private persons
            is_private_person = False
        else:
            # For non-companies, check if they have a parent company
            # A partner is private if it's the same as its commercial partner (no parent company)
            is_private_person = delivery_partner.commercial_partner_id == delivery_partner
        
        return delivery_partner, is_private_person

    def __init__(self, carrier):
        """
        Initial parameters for making api requests.
        """
        if carrier.prod_environment:
            self.url = carrier.production_url
        else:
            self.url = 'https://cargoson-staging.herokuapp.com/api/'
        self.session = requests.Session()
        self.carrier = carrier
        self.env = carrier.env  # Store environment for creating attachments
        self._last_response = None  # Store last API response for error handling

    def _make_api_request(self, endpoint, header_data, method='GET', data=None, token=None):
        """
        make an api call, return response for multiple api requests of cargoson
        """
        headers = header_data
        if token:
            headers['Authorization'] = 'Token {}'.format(token)
        access_url = self.url + endpoint
        
        _logger.info("=== CARGOSON API REQUEST DEBUG ===")
        _logger.info(f"Request URL: {access_url}")
        _logger.info(f"Request Method: {method}")
        _logger.info(f"Request Headers: {headers}")
        _logger.info(f"Request Data: {data}")
        _logger.info(f"Request Timeout: 30 seconds")
        
        try:
            response = self.session.request(method, access_url, data=data, headers=headers, timeout=30)
            
            _logger.info(f"Response Status Code: {response.status_code}")
            _logger.info(f"Response Headers: {dict(response.headers)}")
            _logger.info(f"Response Content Length: {len(response.content)} bytes")
            _logger.info(f"Response Content (first 500 chars): {response.text[:500]}")
            
            json_data = json.loads(response.content)
            response_json = response.json()
            
            _logger.info(f"Parsed JSON Response: {response_json}")
            _logger.info(f"Response JSON Type: {type(response_json)}")
            
            return response_json
        except requests.exceptions.ConnectionError as error:
            _logger.error('Connection Error: %s with the given URL: %s', error, access_url)
            return {'errors': {'timeout': "Cannot reach the server. Please try again later."}}
        except json.decoder.JSONDecodeError as error:
            _logger.error('JSONDecodeError: %s', error)
            _logger.error('Raw response content: %s', response.text if 'response' in locals() else 'No response')
            return {'errors': {'JSONDecodeError': str(error)}}
        except Exception as error:
            _logger.error('Unexpected error in API request: %s', error)
            return {'errors': {'unexpected': str(error)}}

    def _get_token(self):
        if self.carrier.prod_environment:
            if self.carrier.production_token:
                return self.carrier.production_token
        return self.carrier.cargoson_access_token

    def _cargoson_get_error_message(self, json_data):
        """Return error message(s) from cargoson requests (see :func:`cargoson_format_api_error`)."""
        return cargoson_format_api_error(json_data)

    def _get_booking_info(self, reference):
        """Return booking details when the reference belongs to an existing booking."""
        if not reference:
            return {}
        header_data = {
            'Content-Type': 'application/json',
            'Accept': 'application/vnd.api.v1',
        }
        booking_info = self._make_api_request(
            'bookings/%s' % reference,
            header_data=header_data,
            method='GET',
            token=self._get_token(),
        )
        if isinstance(booking_info, dict) and not booking_info.get('errors'):
            return booking_info
        return {}

    def _fetch_cargoson_couriers(self):
        header_data = {
            'Content-Type': 'application/json'
        }
        _logger.info("=== CARGOSON COURIER FETCH DEBUG ===")
        _logger.info(f"API URL: {self.url}")
        _logger.info(f"Endpoint: services/list")
        _logger.info(f"Headers: {header_data}")
        _logger.info(f"Using token: {self._get_token()[:10]}..." if self._get_token() else "No token")
        
        carriers_json = self._make_api_request('services/list', header_data=header_data, token=self._get_token())
        
        _logger.info(f"Raw API Response: {carriers_json}")
        _logger.info(f"Response type: {type(carriers_json)}")
        _logger.info(f"Response keys: {list(carriers_json.keys()) if isinstance(carriers_json, dict) else 'Not a dict'}")
        
        if 'services' in carriers_json:
            services_list = carriers_json['services']
            _logger.info(f"Services found: {len(services_list)} couriers")
            _logger.info(f"Services type: {type(services_list)}")
            if services_list:
                _logger.info(f"First courier sample: {services_list[0] if services_list else 'Empty list'}")
                _logger.info(f"Service IDs: {[s.get('service_id') for s in services_list[:5]]}")
            return services_list
        else:
            _logger.error(f"No 'services' key in response. Available keys: {list(carriers_json.keys()) if isinstance(carriers_json, dict) else 'Not a dict'}")
            raise ValidationError(_('Failed to fetch cargoson Couriers(s), Please try again later.'))

    def prepare_shipping_rate(self, order, carrier_id):
        # Get correct delivery partner and private person status
        delivery_partner, is_private_person = self._get_delivery_partner_info(order)
        
        data = {
            "collection_date": order.date_order.strftime('%Y-%m-%d'),
            "collection_postcode": order.warehouse_id.partner_id.zip,
            "collection_country": order.warehouse_id.partner_id.country_id.code,
            "collection_with_tail_lift": carrier_id.collection_with_tail_lift,
            "collection_prenotification": True,
            "delivery_postcode": delivery_partner.zip,
            "delivery_country": delivery_partner.country_id.code,
            "delivery_with_tail_lift": carrier_id.delivery_with_tail_lift,
            "delivery_prenotification": True,
            "delivery_return_document": True,
            "delivery_to_private_person": is_private_person,
            "frigo": False,
            "adr": False,
            "request_external_partners": carrier_id.request_carrier_api_prices,
            "carrier_id": ""
        }
        rows_attributes = []
        rows_attributes_dict = {}
        for product_line in order.order_line.filtered(lambda p: p.product_id.type in ['consu', 'product']):
            rows_attributes_dict.update({
                "quantity": product_line.product_uom_qty,
                "package_type": carrier_id.cargoson_default_package_type_id.shipper_package_code,
                "weight": carrier_id._weight_to_api(product_line.product_id.weight),
                "description": product_line.product_id.name
            })
            rows_attributes.append(rows_attributes_dict)
        data.update({
            "rows_attributes": rows_attributes
        })
        return data

    def _get_rate(self, package_types, order, carrier_id):
        price_list = []
        if order._name == 'sale.order':
            # Get correct delivery partner and private person status
            delivery_partner, is_private_person = self._get_delivery_partner_info(order)
            
            data = {
                "collection_date": order.date_order.strftime('%Y-%m-%d'),
                "collection_postcode": order.warehouse_id.partner_id.zip,
                "collection_country": order.warehouse_id.partner_id.country_id.code,
                "collection_with_tail_lift": carrier_id.collection_with_tail_lift,
                "collection_prenotification": True,
                "delivery_postcode": delivery_partner.zip,
                "delivery_country": delivery_partner.country_id.code,
                "delivery_with_tail_lift": carrier_id.delivery_with_tail_lift,
                "delivery_prenotification": True,
                "delivery_return_document": True,
                "delivery_to_private_person": is_private_person,
                "frigo": False,
                "adr": False,
                "request_external_partners": carrier_id.request_carrier_api_prices,
                "carrier_id": ""
            }
        if order._name == 'purchase.order':
            # Get correct delivery partner and private person status
            delivery_partner, is_private_person = self._get_delivery_partner_info(order)
            
            data = {
                "collection_date": order.date_order.strftime('%Y-%m-%d'),
                "collection_postcode": order.picking_type_id.warehouse_id.partner_id.zip,
                "collection_country": order.picking_type_id.warehouse_id.partner_id.country_id.code,
                "collection_with_tail_lift": carrier_id.collection_with_tail_lift,
                "collection_prenotification": True,
                "delivery_postcode": delivery_partner.zip,
                "delivery_country": delivery_partner.country_id.code,
                "delivery_with_tail_lift": carrier_id.delivery_with_tail_lift,
                "delivery_prenotification": True,
                "delivery_return_document": True,
                "delivery_to_private_person": is_private_person,
                "frigo": False,
                "adr": False,
                "request_external_partners": carrier_id.request_carrier_api_prices,
                "carrier_id": ""
            }
        rows_attributes = []
        for product_line in package_types:
            # Use total_weight (product weight + base_weight * qty) and include dimensions
            # Convert to API units (metric: cm/kg, imperial: in/lb)
            rows_attributes_dict = {
                "quantity": product_line.qty,
                "package_type": product_line.package_type.shipper_package_code,
                "weight": carrier_id._weight_to_api(product_line.total_weight),
                "description": product_line.package_type.default_product_id.name
            }
            # Add dimensions if available (internal: cm)
            if hasattr(product_line, 'height') and product_line.height:
                rows_attributes_dict['height'] = carrier_id._dimension_to_api(product_line.height)
            if hasattr(product_line, 'width') and product_line.width:
                rows_attributes_dict['width'] = carrier_id._dimension_to_api(product_line.width)
            if hasattr(product_line, 'depth') and product_line.depth:
                rows_attributes_dict['length'] = carrier_id._dimension_to_api(product_line.depth)
            rows_attributes.append(rows_attributes_dict)
        data.update({
            "rows_attributes": rows_attributes
        })
        header_data = {
            'Accept': 'application/vnd.api.v1',
            'Content-Type': 'application/json'
        }
        rate_json = self._make_api_request(
            'freightPrices/list', header_data=header_data, data=json.dumps(data), method='POST', token=self._get_token())
        required_carrier = carrier_id.cargoson_courier_ids.mapped('service_name')
        if 'errors' not in rate_json:
            if rate_json and rate_json.get('object'):
                available_couriers = rate_json['object'].get('prices')
                for available_courier in available_couriers:
                    if available_courier.get('service') in required_carrier:
                        res = {
                            'courier_name': available_courier.get('carrier'),
                            'price': available_courier.get('price'),
                            'service': available_courier.get('service'),
                            'service_id': available_courier.get('service_id'),
                            'estimated_delivery_date': available_courier.get('estimated_delivery_date')
                        }
                        price_list.append(res)
            return price_list
        else:
            return {'error': rate_json['errors']}

    def _rate_request(self, package_types, order=False, carrier_id=False):
        if not order:
            raise UserError(_('Sale Order or Picking is required to get rate.'))
        rate_dict = self._get_rate(package_types, order, carrier_id)
        return rate_dict

    def _prepare_parcel(self, picking, package, courier_code=False, ship_charges=0.00):
        parcel_data = {}
        if picking.carrier_id:
            if picking.package_type.use_product_dimensions:
                parcel_data.update({
                    'length': package.product_id.length,
                    'width': package.product_id.width,
                    'height': package.product_id.height,
                    'quantity': package.quantity,
                    'package_type': picking.package_type.shipper_package_code,
                    'description': package.product_id.name,
                    'weight': package.product_id.weight,
                })
            elif picking.package_type.is_crate:
                parcel_data.update({
                    'length': picking.depth,
                    'width': picking.width,
                    'height': picking.height,
                    'quantity': picking.package_qty,
                    'package_type': picking.package_type.shipper_package_code,
                    'description': package.product_id.name,
                    'weight': package.product_id.weight,
                })
            else:
                parcel_data.update({
                    'length': picking.depth,
                    'width': picking.width,
                    'height': picking.height,
                    'quantity': package.quantity,
                    'package_type': picking.package_type.shipper_package_code,
                    'description': package.product_id.name,
                    'weight': package.product_id.weight,
                })
        return parcel_data

    def _get_shipping_params(self, picking, sale, delivery_prices, draft_only=False):
        """
        Returns the shipping data from picking for create an cargoson order.
        When draft_only=True, omit direct_booking_service_id so Cargoson keeps it as a draft (no booking).
        """
        parcel_dict = {}

        # Use order's salesperson TZ then current user TZ; Odoo datetimes are UTC (res.company has no tz)
        tz_str = (sale.user_id and sale.user_id.tz) or self.env.user.tz or 'UTC'
        to_zone = tz.gettz(tz_str) or tz.gettz('UTC')
        carrier = picking.carrier_id
        use_commitment = getattr(carrier, 'use_commitment_date', True) and sale.commitment_date
        commitment_for_pick_up = use_commitment and getattr(carrier, 'commitment_date_for', 'pick_up') == 'pick_up'
        commitment_for_delivery = use_commitment and getattr(carrier, 'commitment_date_for', 'pick_up') == 'delivery'

        if commitment_for_pick_up:
            collection_date = sale.commitment_date.strftime('%Y-%m-%d')
            if carrier.collection_time and carrier.delivery_time:
                parcel_dict.update({
                    "collection_time_from": carrier._cargoson_format_float_time(carrier.collection_time),
                    "collection_time_to": carrier._cargoson_format_float_time(carrier.delivery_time),
                })
            else:
                str_collection_time = _utc_to_local(sale.commitment_date, to_zone)
                time_from = str_collection_time.strftime("%H:%M")
                parcel_dict.update({
                    "collection_time_from": time_from,
                })
                if carrier.delivery_time:
                    parcel_dict.update({
                        "collection_time_to": carrier._cargoson_format_float_time(carrier.delivery_time),
                    })
            _cargoson_fix_collection_time_window(parcel_dict)
            tf = parcel_dict.get('collection_time_from')
            tt = parcel_dict.get('collection_time_to')
            collection_date = _cargoson_roll_past_slot_to_working_day(
                collection_date, tf, tt, to_zone)
        else:
            collection_date = (picking.scheduled_date + timedelta(days=1)).strftime('%Y-%m-%d')
            collection_date_obj = (picking.scheduled_date + timedelta(days=1))
            if carrier.delivery_time and carrier.collection_time:
                str_collection_time = _utc_to_local(collection_date_obj, to_zone)
                str_collection_time_from = carrier._cargoson_format_float_time(carrier.collection_time)
                str_collection_time_to = carrier._cargoson_format_float_time(carrier.delivery_time)
                parcel_dict.update({
                    "collection_time_from": str_collection_time_from,
                    "collection_time_to": str_collection_time_to,
                })
                _cargoson_fix_collection_time_window(parcel_dict)
                tf = parcel_dict.get('collection_time_from')
                tt = parcel_dict.get('collection_time_to')
                collection_date = _cargoson_roll_past_slot_to_working_day(
                    collection_date, tf, tt, to_zone)

        if commitment_for_delivery:
            d_date = sale.commitment_date.strftime('%Y-%m-%d')
            from_time = carrier.delivery_window_time_from or 8.0
            d_from = carrier._cargoson_format_float_time(from_time)
            d_to = _cargoson_hm_add_hours(d_from, CARGOSON_DELIVERY_WINDOW_HOURS)
            parcel_dict["delivery_time_from"] = d_from
            parcel_dict["delivery_time_to"] = d_to
            parcel_dict["delivery_date"] = _cargoson_roll_past_slot_to_working_day(
                d_date, d_from, d_to, to_zone)
            parcel_dict["delivery_date"] = _cargoson_ensure_delivery_not_before_pickup(
                parcel_dict["delivery_date"], collection_date)
            _cargoson_bump_delivery_time_after_collection(
                parcel_dict, collection_date, parcel_dict["delivery_date"])

        if sale.client_order_ref:
            parcel_dict.update({
                "customer_remark": sale.client_order_ref + ' ' + sale.name + ' ' + (picking.comment or ''),
            })
        else:
            parcel_dict.update({
                "customer_remark": sale.name + ' ' + (picking.comment or ''),
            })
        warehouse_id = picking.location_id.warehouse_id
        
        # Get correct delivery partner and private person status
        delivery_partner, is_private_person = self._get_delivery_partner_info(sale)
        
        # Determine collection contact - use carrier's collection_contact_id if set, otherwise use warehouse partner
        collection_contact = picking.carrier_id.collection_contact_id if picking.carrier_id.collection_contact_id else warehouse_id.partner_id
        
        # Build delivery address: US format (street+street2 → row_1, state code → row_2) or standard
        delivery_addr_row_1 = picking.partner_id.street or ""
        delivery_addr_row_2 = None
        if picking.carrier_id.us_address:
            delivery_addr_row_1 = ' '.join(filter(None, [
                (picking.partner_id.street or '').strip(),
                (picking.partner_id.street2 or '').strip()
            ])).strip() or ""
            delivery_addr_row_2 = picking.partner_id.state_id.code if picking.partner_id.state_id else ""
        
        # Build collection address: same US format logic
        collection_addr_row_1 = warehouse_id.partner_id.street or ""
        collection_addr_row_2 = None
        if picking.carrier_id.us_address:
            collection_addr_row_1 = ' '.join(filter(None, [
                (warehouse_id.partner_id.street or '').strip(),
                (warehouse_id.partner_id.street2 or '').strip()
            ])).strip() or ""
            collection_addr_row_2 = warehouse_id.partner_id.state_id.code if warehouse_id.partner_id.state_id else ""
        
        parcel_dict.update({
            "delivery_to_private_person": is_private_person,
            "customer_reference": sale.name,
            "incoterm_code": sale.incoterm.code if sale.incoterm else None,
            "collection_date": collection_date,
            "collection_country": warehouse_id.partner_id.country_id.code,
            "collection_postcode": warehouse_id.partner_id.zip,
            "collection_address_row_1": collection_addr_row_1,
            "collection_contact_email": collection_contact.email,
            "collection_city": warehouse_id.partner_id.city,
            "collection_company_name": warehouse_id.partner_id.name,
            "collection_contact_name": collection_contact.name,
            "collection_contact_phone": getattr(collection_contact, 'mobile', None) or getattr(collection_contact, 'phone', None) or "",
            "collection_with_tail_lift": picking.carrier_id.collection_with_tail_lift,
            "delivery_country": picking.partner_id.country_id.code,
            "delivery_postcode": picking.partner_id.zip,
            "delivery_address_row_1": delivery_addr_row_1,
            "delivery_city": picking.partner_id.city,
        })
        if delivery_addr_row_2 is not None:
            parcel_dict["delivery_address_row_2"] = delivery_addr_row_2
        if collection_addr_row_2 is not None:
            parcel_dict["collection_address_row_2"] = collection_addr_row_2
        options = {
            "delivery_sms_notification": picking.carrier_id.delivery_sms_notification
        }
        if not draft_only and sale.service_id:
            options["direct_booking_service_id"] = sale.service_id
        parcel_dict.update({
            "delivery_company_name": picking.partner_id.commercial_partner_id.name,
            "delivery_contact_email": picking.partner_id.email,
            "delivery_contact_name": picking.partner_id.name,
            "delivery_contact_phone": getattr(picking.partner_id, 'mobile', None) or getattr(picking.partner_id, 'phone', None) or "",
            "delivery_with_tail_lift": picking.carrier_id.delivery_with_tail_lift,
            "delivery_prenotification": True,
            "options": options
        })
        if picking.carrier_id.delivery_type == 'cargoson':
            parcel_dict["private_remark"] = picking.carrier_id._get_cargoson_private_remark(sale)
            parcel_dict.update(picking.carrier_id._get_cargoson_goods_value_payload(sale))
        
        # Add freight payer information if enabled
        if picking.carrier_id.use_freight_payer:
            if picking.carrier_id.freight_payer_type == 'sender':
                # Use collection contact details (sender)
                freight_payer_partner = collection_contact
                freight_payer_address = warehouse_id.partner_id
            else:
                # Use delivery partner details (receiver)
                freight_payer_partner = delivery_partner
                freight_payer_address = delivery_partner
            
            fp_row_1 = freight_payer_address.street or ""
            fp_row_2 = freight_payer_address.street2 or ""
            if picking.carrier_id.us_address:
                fp_row_1 = ' '.join(filter(None, [
                    (freight_payer_address.street or '').strip(),
                    (freight_payer_address.street2 or '').strip()
                ])).strip() or ""
                fp_row_2 = freight_payer_address.state_id.code if freight_payer_address.state_id else ""
            parcel_dict.update({
                "freight_payer_company_name": freight_payer_address.name or "",
                "freight_payer_address_row_1": fp_row_1,
                "freight_payer_address_row_2": fp_row_2,
                "freight_payer_postcode": freight_payer_address.zip or "",
                "freight_payer_city": freight_payer_address.city or "",
                "freight_payer_country": freight_payer_address.country_id.code or "",
                "freight_payer_contact_name": freight_payer_partner.name or "",
                "freight_payer_contact_phone": getattr(freight_payer_partner, 'mobile', None) or getattr(freight_payer_partner, 'phone', None) or "",
                "freight_payer_contact_email": freight_payer_partner.email or "",
        })
        carrier = picking.carrier_id
        product_data = []
        # Prefer picking package lines; fall back to SO (e.g. if sync to picking was skipped by edge case)
        package_lines = picking.package_types
        if not package_lines and sale and getattr(sale, 'package_types', None):
            package_lines = sale.package_types
        for package_line in package_lines:
            product_data.append({
                'length': carrier._dimension_to_api(package_line.depth),
                'width': carrier._dimension_to_api(package_line.width),
                'height': carrier._dimension_to_api(package_line.height),
                'quantity': package_line.qty,
                'package_type': package_line.package_type.shipper_package_code,
                'description': package_line.package_type.default_product_id.name,
                'weight': carrier._weight_to_api(package_line.total_weight),
            })
        parcel_dict['rows_attributes'] = product_data

        _cargoson_ensure_collection_end_not_in_past(parcel_dict, to_zone)
        if parcel_dict.get('delivery_date') and parcel_dict.get('collection_date'):
            parcel_dict['delivery_date'] = _cargoson_ensure_delivery_not_before_pickup(
                parcel_dict['delivery_date'], parcel_dict['collection_date'])
            _cargoson_bump_delivery_time_after_collection(
                parcel_dict, parcel_dict['collection_date'], parcel_dict['delivery_date'])
        
        # Log delivery_sms_notification value before returning
        _logger.info(f"_get_shipping_params - delivery_sms_notification: {parcel_dict.get('delivery_sms_notification')}, carrier setting: {picking.carrier_id.delivery_sms_notification}")
        
        return json.dumps(parcel_dict)

    def _patch_shipment(self, picking, sale, delivery_prices, ref):
        """
        Update an existing query or booking in Cargoson via PATCH.
        Tries PATCH /queries/{id} first; if 403 (booking exists), uses PATCH /bookings/{reference}.
        When integration_level is 'rate' (draft mode), omits direct_booking_service_id to keep as draft.
        """
        products = picking.move_line_ids.product_id
        self._check_required_value(
            picking.partner_id,
            picking.picking_type_id.warehouse_id.partner_id or picking.company_id.partner_id,
            products and products.filtered(lambda p: p.type in ['consu', 'product'])
        )
        # Draft mode: update query only, do not create booking
        draft_only = picking.carrier_id.integration_level == 'rate'
        res = {}
        params = self._get_shipping_params(picking, sale, delivery_prices, draft_only=draft_only)
        header_data = {
            'Content-Type': 'application/json',
            'Accept': 'application/vnd.api.v1'
        }
        if self._get_token():
            header_data['Authorization'] = 'Token {}'.format(self._get_token())

        # Try PATCH /queries/{id} first (for drafts with no booking)
        access_url = self.url + 'queries/' + str(ref)
        _logger.info(f"_patch_shipment - PATCH query: {access_url}")
        try:
            response = self.session.request('PATCH', access_url, data=params, headers=header_data, timeout=30)
            self._last_response = response.json() if response.content else {}
        except Exception as e:
            _logger.error(f"PATCH query failed: {e}")
            raise ValidationError(str(e))

        if response.status_code in (200, 201):
            order_response = response.json()
            self._last_response = order_response
            if order_response.get('label_url'):
                res['label_url'] = order_response.get('label_url')
            if order_response.get('cmr_url'):
                res['cmr_url'] = order_response.get('cmr_url')
            bol_url = (
                order_response.get('bol_url') or
                order_response.get('bill_of_lading_url') or
                order_response.get('bol_document_url')
            )
            if bol_url:
                res['bol_url'] = bol_url
            if order_response.get('waybill_url'):
                res['waybill_url'] = order_response.get('waybill_url')
            if order_response.get('tracking_url'):
                res['tracking_url'] = order_response.get('tracking_url')
                res['carrier_tracking_url'] = order_response.get('tracking_url')
            if order_response.get('reference'):
                res['carrier_tracking_ref'] = order_response.get('reference')
            picking.message_post(
                body=_('Shipment updated with Ref: %s') % (order_response.get('reference', ref)))
            return res

        if response.status_code == 403:
            # Booking already exists - try PATCH /bookings/{reference}
            access_url = self.url + 'bookings/' + str(ref)
            _logger.info(f"_patch_shipment - PATCH booking (query returned 403): {access_url}")
            try:
                response = self.session.request('PATCH', access_url, data=params, headers=header_data, timeout=30)
                self._last_response = response.json() if response.content else {}
            except Exception as e:
                _logger.error(f"PATCH booking failed: {e}")
                raise ValidationError(str(e))

        if response.status_code not in (200, 201):
            error_msg = self._cargoson_get_error_message(
                response.json() if response.content else {}
            )
            if not error_msg:
                error_msg = _('Cargoson API error (status %s)') % response.status_code
            raise ValidationError(error_msg)

        order_response = response.json()
        self._last_response = order_response
        if order_response.get('label_url'):
            res['label_url'] = order_response.get('label_url')
        if order_response.get('cmr_url'):
            res['cmr_url'] = order_response.get('cmr_url')
        bol_url = (
            order_response.get('bol_url') or
            order_response.get('bill_of_lading_url') or
            order_response.get('bol_document_url')
        )
        if bol_url:
            res['bol_url'] = bol_url
        if order_response.get('waybill_url'):
            res['waybill_url'] = order_response.get('waybill_url')
        if order_response.get('tracking_url'):
            res['tracking_url'] = order_response.get('tracking_url')
            res['carrier_tracking_url'] = order_response.get('tracking_url')
        if order_response.get('reference'):
            res['carrier_tracking_ref'] = order_response.get('reference')
        picking.message_post(
            body=_('Shipment updated with Ref: %s') % (order_response.get('reference', ref)))
        return res

    def _send_shipping(self, picking, sale, delivery_prices):
        products = picking.move_line_ids.product_id
        self._check_required_value(
            picking.partner_id,
            picking.picking_type_id.warehouse_id.partner_id or picking.company_id.partner_id,
            products and products.filtered(lambda p: p.type in ['consu', 'product'])
        )
        res = {}
        # Match draft vs booking: omit direct_booking_service_id when carrier is in "Get rate" (draft/query) mode
        draft_only = picking.carrier_id.integration_level == 'rate'
        params = self._get_shipping_params(picking, sale, delivery_prices, draft_only=draft_only)
        # Log the full payload to verify delivery_sms_notification is included
        params_dict = json.loads(params)
        _logger.info(f"_send_shipping - Creating shipment. delivery_sms_notification in payload: {params_dict.get('delivery_sms_notification')}, carrier setting: {picking.carrier_id.delivery_sms_notification}")
        _logger.info(f"_send_shipping - Full payload keys: {list(params_dict.keys())}")
        
        header_data = {
            'Content-Type': 'application/json',
            'Accept': 'application/vnd.api.v1'
        }
        order_response = self._make_api_request(
            'queries',
            header_data,
            'POST',
            params,
            token=self._get_token()
        )
        # Store response for error handling (to extract reference even on errors)
        self._last_response = order_response
        
        if order_response.get('query_status') != 'created':
            error_msg = cargoson_format_api_error(order_response)
            if not (error_msg and str(error_msg).strip()):
                error_msg = _('Could not create the Cargoson shipment.')
            order_response['_error_message'] = error_msg
            raise ValidationError(error_msg)
        if order_response.get('label_url'):
            res['label_url'] = order_response.get('label_url')
        if order_response.get('cmr_url'):
            res['cmr_url'] = order_response.get('cmr_url')
        # BOL: try bol_url, bill_of_lading_url, bol_document_url (carrier-specific keys)
        bol_url = (
            order_response.get('bol_url') or
            order_response.get('bill_of_lading_url') or
            order_response.get('bol_document_url')
        )
        if bol_url:
            res['bol_url'] = bol_url
        if order_response.get('waybill_url'):
            res['waybill_url'] = order_response.get('waybill_url')
        if order_response.get('tracking_url'):
            res['tracking_url'] = order_response.get('tracking_url')
            res['carrier_tracking_url'] = order_response.get('tracking_url')
        if order_response.get('reference'):
            res['carrier_tracking_ref'] = order_response.get('reference')
            picking.message_post(
                body=_('Shipment Created with Ref: %s') % (order_response.get('reference')))
        return res

    def _download_pdf_as_attachment(self, url, filename, res_model, res_id, document_type='', delay_seconds=0, max_retries=0):
        """
        Download PDF from URL and create Odoo attachment with retry logic.
        
        Args:
            url: URL to download PDF from
            filename: Name for the attachment
            res_model: Model name (e.g., 'stock.picking', 'sale.order')
            res_id: Record ID
            document_type: Type of document ('label', 'cmr', 'waybill')
            delay_seconds: Delay in seconds before first attempt (and between retries)
            max_retries: Maximum number of retry attempts (0 = no retries, just initial delay)
        
        Returns:
            ir.attachment record or False
        """
        if not url:
            return False
        
        # Initial delay before first attempt
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        
        # Prepare headers - may need authentication token
        headers = {}
        token = self._get_token()
        if token:
            headers['Authorization'] = 'Token {}'.format(token)
        
        # Try downloading with retries
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                # Download PDF content
                response = self.session.get(url, headers=headers, timeout=30, stream=True)
                response.raise_for_status()
                
                # Check if response is actually a PDF
                content_type = response.headers.get('Content-Type', '')
                if 'application/pdf' not in content_type and 'application/octet-stream' not in content_type:
                    _logger.warning(f"Unexpected content type for {document_type}: {content_type}")
                    # Still try to process it, might be PDF with wrong content-type
                
                pdf_content = response.content
                
                # Verify it's actually PDF by checking magic bytes
                if not pdf_content.startswith(b'%PDF'):
                    if attempt < max_retries:
                        _logger.warning(f"Downloaded content is not a valid PDF for {document_type} (attempt {attempt + 1}/{max_retries + 1}), retrying...")
                        if delay_seconds > 0:
                            time.sleep(delay_seconds)
                        continue
                    else:
                        _logger.error(f"Downloaded content is not a valid PDF for {document_type} after {max_retries + 1} attempts")
                        return False
                
                # Encode to base64 for Odoo attachment
                pdf_base64 = base64.b64encode(pdf_content).decode('utf-8')
                
                # Create attachment
                attachment = self.env['ir.attachment'].create({
                    'name': filename,
                    'type': 'binary',
                    'datas': pdf_base64,
                    'res_model': res_model,
                    'res_id': res_id,
                    'mimetype': 'application/pdf',
                })
                
                if attempt > 0:
                    _logger.info(f"Successfully downloaded {document_type} PDF after {attempt + 1} attempts")
                
                return attachment
                
            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < max_retries:
                    _logger.warning(f"Error downloading {document_type} PDF (attempt {attempt + 1}/{max_retries + 1}): {e}, retrying...")
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)
                else:
                    _logger.error(f"Error downloading {document_type} PDF from {url} after {max_retries + 1} attempts: {e}")
            except Exception as e:
                last_error = e
                _logger.error(f"Unexpected error creating {document_type} attachment: {e}")
                # Don't retry on unexpected errors
                break
        
        return False

    def _download_cargoson_documents(self, picking):
        """
        Download Cargoson documents (Label, CMR, Waybill) based on carrier settings.
        This method is called when picking is validated.
        
        We store ONE PDF per document type and link it to the picking so it appears in the attachment widget.
        The same attachment is also referenced by sale order and purchase order via Many2one fields.
        This avoids duplicate storage while ensuring documents are visible in the picking's attachment list.
        
        Args:
            picking: stock.picking record
        """
        if not picking.carrier_id or picking.carrier_id.delivery_type != 'cargoson':
            return
        
        carrier = picking.carrier_id
        attachments_created = {}
        
        # Get delay and retry settings from carrier
        delay_seconds = carrier.download_delay_seconds or 3
        max_retries = carrier.download_max_retries or 2
        
        # Initial delay before starting downloads (gives Cargoson time to generate documents)
        if delay_seconds > 0:
            _logger.info(f"Waiting {delay_seconds} seconds before downloading Cargoson documents for {picking.name}")
            time.sleep(delay_seconds)
        
        # Determine record name for filename
        record_name = picking.name or picking.origin or 'Unknown'
        
        # Link attachments to picking so they show up in the attachment widget
        # We'll also reference them from sale order and purchase order via Many2one fields
        link_model = 'stock.picking'
        link_id = picking.id
        
        # Download Label if enabled and URL exists
        if carrier.download_label and picking.label_url and not picking.label_attachment_id:
            filename = f'Cargoson_Label_{record_name}.pdf'
            attachment = self._download_pdf_as_attachment(
                picking.label_url,
                filename,
                link_model,
                link_id,
                'label',
                delay_seconds=0,  # Already waited initially, no additional delay needed
                max_retries=max_retries
            )
            if attachment:
                # Link to picking (this makes it appear in attachment widget)
                picking.label_attachment_id = attachment.id
                attachments_created['label'] = attachment
                # Also link to sale order if exists
                if picking.sale_id:
                    picking.sale_id.label_attachment_id = attachment.id
        
        # Download CMR if enabled and URL exists
        if carrier.download_cmr and picking.cmr_url and not picking.cmr_attachment_id:
            filename = f'Cargoson_CMR_{record_name}.pdf'
            attachment = self._download_pdf_as_attachment(
                picking.cmr_url,
                filename,
                link_model,
                link_id,
                'cmr',
                delay_seconds=0,  # Already waited initially, no additional delay needed
                max_retries=max_retries
            )
            if attachment:
                # Link to picking (this makes it appear in attachment widget)
                picking.cmr_attachment_id = attachment.id
                attachments_created['cmr'] = attachment
                # Also link to sale order if exists
                if picking.sale_id:
                    picking.sale_id.cmr_attachment_id = attachment.id
        
        # Download BOL if enabled and URL exists
        if carrier.download_bol and picking.bol_url and not picking.bol_attachment_id:
            filename = f'Cargoson_BOL_{record_name}.pdf'
            attachment = self._download_pdf_as_attachment(
                picking.bol_url,
                filename,
                link_model,
                link_id,
                'bol',
                delay_seconds=0,  # Already waited initially, no additional delay needed
                max_retries=max_retries
            )
            if attachment:
                # Link to picking (this makes it appear in attachment widget)
                picking.bol_attachment_id = attachment.id
                attachments_created['bol'] = attachment
                # Also link to sale order if exists
                if picking.sale_id:
                    picking.sale_id.bol_attachment_id = attachment.id
        
        # Download Waybill if enabled and URL exists
        if carrier.download_waybill and picking.waybill_url and not picking.waybill_attachment_id:
            filename = f'Cargoson_Waybill_{record_name}.pdf'
            attachment = self._download_pdf_as_attachment(
                picking.waybill_url,
                filename,
                link_model,
                link_id,
                'waybill',
                delay_seconds=0,  # Already waited initially, no additional delay needed
                max_retries=max_retries
            )
            if attachment:
                # Link to picking (this makes it appear in attachment widget)
                picking.waybill_attachment_id = attachment.id
                attachments_created['waybill'] = attachment
                # Also link to sale order if exists
                if picking.sale_id:
                    picking.sale_id.waybill_attachment_id = attachment.id
        
        # Also update purchase order if it exists (any PO sequence prefix)
        if picking.origin:
            purchase = self.env['purchase.order'].search([('name', '=', picking.origin)], limit=1)
            if purchase:
                if 'label' in attachments_created:
                    purchase.label_attachment_id = attachments_created['label'].id
                if 'cmr' in attachments_created:
                    purchase.cmr_attachment_id = attachments_created['cmr'].id
                if 'bol' in attachments_created and hasattr(purchase, 'bol_attachment_id'):
                    purchase.bol_attachment_id = attachments_created['bol'].id
                if 'waybill' in attachments_created:
                    purchase.waybill_attachment_id = attachments_created['waybill'].id
        
        # Documents downloaded and linked to picking

    def _check_required_value(self, recipient, shipper, products):
        return True
