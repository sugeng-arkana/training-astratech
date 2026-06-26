# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
from odoo import fields, models, api, _
from dateutil import tz
import json
import logging
from odoo.exceptions import ValidationError
from ..models.cargoson_request import (
    cargoson,
    cargoson_format_api_error,
    _utc_to_local,
    _cargoson_roll_past_slot_to_working_day,
    _cargoson_ensure_delivery_not_before_pickup,
    _cargoson_bump_delivery_time_after_collection,
    _cargoson_fix_collection_time_window,
    _cargoson_ensure_collection_end_not_in_past,
    _cargoson_hm_add_hours,
    CARGOSON_DELIVERY_WINDOW_HOURS,
)

_logger = logging.getLogger(__name__)


class CarrierRates(models.TransientModel):
    _name = 'carrier.rates'
    _description = 'Carrier Rates'

    name = fields.Char('Name')
    price = fields.Char('Rate')
    service = fields.Char('Service')
    service_id = fields.Char('Service ID')
    estimated_delivery_date = fields.Char('Estimated Delivery Date')
    wizard_id = fields.Many2one('choose.delivery.carrier', string='Sale Wizard')
    choose = fields.Boolean('Select')
    carrier_id = fields.Many2one('delivery.carrier', string='Carrier')



class ChoosePackageTypes(models.TransientModel):
    _name = 'choose.package.types'
    _description = 'choose package types'

    wizard_id = fields.Many2one('choose.delivery.carrier', string='Sale Wizard')
    package_type = fields.Many2one('stock.package.type', string='Package Type')
    height = fields.Float('Height')
    width = fields.Float('Width')
    depth = fields.Float('Depth')
    qty = fields.Integer('QTY')
    weight = fields.Float('Weight')
    total_weight = fields.Float('Total Weight')
    is_height_edit = fields.Boolean(compute='check_edit', string='Edit Height')
    is_length_edit = fields.Boolean(compute='check_edit', string='Edit Length')
    is_width_edit = fields.Boolean(compute='check_edit', string='Edit Width')

    @api.depends('package_type')
    def check_edit(self):
        for record in self:
            record.is_height_edit = record.package_type.required_height
            record.is_length_edit = record.package_type.required_length
            record.is_width_edit = record.package_type.required_width

    def _get_crate_weight_for_package_line(self):
        """Get crate weight for this package line when crate shipping is used."""
        if not self.package_type or not self.package_type.is_crate:
            return 0.0
        
        if not self.wizard_id or not self.wizard_id.order_id:
            return 0.0
        
        # Get crate products from the order
        crate_lines = self.wizard_id.order_id.order_line.filtered(
            lambda p: p.product_id.is_crate is True and p.product_id.type != 'service'
        )
        
        if not crate_lines:
            return 0.0
        
        # Calculate total crate weight
        # If there's only one crate product, use its weight directly
        # If multiple, we'll use the total (this is a simplification - ideally we'd match the specific crate)
        total_crate_weight = sum(
            line.product_template_id.weight * line.product_uom_qty
            for line in crate_lines
        )
        
        # If there's only one crate line, use its weight directly
        # Otherwise, we need to distribute proportionally, but for simplicity, use total
        # (The initial creation in one_line_all handles proper distribution)
        if len(crate_lines) == 1:
            return total_crate_weight
        else:
            # For multiple crates, we'd need to match this package line to a specific crate
            # For now, return 0 and let the initial creation handle it
            # The onchange will preserve the existing total_weight if it was set correctly
            return 0.0

    @api.onchange('package_type')
    def onchange_package_type(self):
        if self.package_type:
            self.height = self.package_type.height
            self.width = self.package_type.width
            self.depth = self.package_type.packaging_length
            self.qty = self.package_type.default_qty
            # Use wizard total_weight if available, otherwise use 0
            # weight = product weight only (without package base weight)
            wizard_weight = getattr(self.wizard_id, 'total_weight', 0) or 0
            _logger.info(f"Package type onchange: wizard_weight={wizard_weight}, base_weight={self.package_type.base_weight}, qty={self.qty}")
            self.weight = wizard_weight
            # total_weight = product weight + crate weight (if crate shipping) + (package base_weight * qty)
            package_qty = self.qty or 1
            crate_weight = self._get_crate_weight_for_package_line() if self.package_type.is_crate else 0.0
            base_weight = (self.package_type.base_weight * package_qty) if self.package_type.base_weight else 0.0
            self.total_weight = wizard_weight + crate_weight + base_weight
            _logger.info(f"Package type onchange result: weight={self.weight}, crate_weight={crate_weight}, base_weight={base_weight}, total_weight={self.total_weight}")

    @api.onchange('weight')
    def onchange_weight(self):
        if self.package_type:
            # weight = product weight (without base_weight and without crate weight for crate shipping)
            # total_weight = product weight + crate weight (if crate shipping) + (package base_weight * qty)
            product_weight = self.weight or 0
            package_qty = self.qty or 1
            crate_weight = self._get_crate_weight_for_package_line() if self.package_type.is_crate else 0.0
            package_base_weight = (self.package_type.base_weight * package_qty) if self.package_type.base_weight else 0
            new_total_weight = product_weight + crate_weight + package_base_weight
            _logger.info(f"Weight onchange: weight={product_weight}, crate_weight={crate_weight}, base_weight={package_base_weight}, qty={package_qty}, total_weight={new_total_weight}")
            self.total_weight = new_total_weight

    @api.onchange('qty')
    def onchange_qty(self):
        if self.package_type:
            # When quantity changes, recalculate total_weight
            # total_weight = product weight + crate weight (if crate shipping) + (package base_weight * qty)
            product_weight = self.weight or 0
            package_qty = self.qty or 1
            crate_weight = self._get_crate_weight_for_package_line() if self.package_type.is_crate else 0.0
            package_base_weight = (self.package_type.base_weight * package_qty) if self.package_type.base_weight else 0
            new_total_weight = product_weight + crate_weight + package_base_weight
            _logger.info(f"Qty onchange: weight={product_weight}, crate_weight={crate_weight}, base_weight={package_base_weight}, qty={package_qty}, total_weight={new_total_weight}")
            self.total_weight = new_total_weight


class ChooseDeliveryCarrier(models.TransientModel):
    _inherit = 'choose.delivery.carrier'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if (record.carrier_id and
                    record.carrier_id.delivery_type == 'cargoson' and
                    not record.carrier_rates):
                try:
                    record.button_set_package()
                except Exception:
                    pass
        return records

    def _get_field_value_safe(self, recordset, field_name):
        """
        Safely get field value from recordset using mapped().
        Returns the first value if available, otherwise 0.0.
        """
        if not field_name or not recordset:
            return 0.0
        try:
            mapped_result = recordset.mapped(field_name)
            return mapped_result[0] if mapped_result else 0.0
        except (AttributeError, IndexError, KeyError):
            return 0.0

    def _get_product_dimensions_cm(self, product_template):
        """
        Get product dimensions in cm. Uses cargo_height, cargo_width, cargo_length
        from delivery_cargoson_dimension when available, with cargo_dimension_uom_id for conversion.
        Returns (height_cm, width_cm, depth_cm) or None if dimensions not available.
        """
        if not product_template:
            return None
        if not (hasattr(product_template, 'cargo_height') and hasattr(product_template, 'cargo_width') and hasattr(product_template, 'cargo_length')):
            return None
        h = getattr(product_template, 'cargo_height', None) or 0
        w = getattr(product_template, 'cargo_width', None) or 0
        d = getattr(product_template, 'cargo_length', None) or 0
        if not (h or w or d):
            return (0.0, 0.0, 0.0)
        cm_unit = self.env.ref('uom.product_uom_cm', raise_if_not_found=False) or self.env['uom.uom'].search([('name', '=', 'cm')], limit=1)
        uom = getattr(product_template, 'cargo_dimension_uom_id', None) if hasattr(product_template, 'cargo_dimension_uom_id') else None
        try:
            if uom and cm_unit:
                h = float(uom._compute_quantity(h, cm_unit)) if h else 0.0
                w = float(uom._compute_quantity(w, cm_unit)) if w else 0.0
                d = float(uom._compute_quantity(d, cm_unit)) if d else 0.0
            else:
                h, w, d = float(h or 0), float(w or 0), float(d or 0)
        except Exception:
            h, w, d = float(h or 0), float(w or 0), float(d or 0)
        return (round(h, 2), round(w, 2), round(d, 2))
    
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

    carrier_rates = fields.One2many('carrier.rates', 'wizard_id', string='Carrier Rates')
    package_types = fields.One2many('choose.package.types', 'wizard_id', string='Package Types')
    use_for = fields.Selection(related='carrier_id.use_for', string='Use For')
    integration_level = fields.Selection(related='carrier_id.integration_level', string='Integration Level')
    package_type = fields.Many2one('stock.package.type', string='Package Type')
    package_qty = fields.Integer('Package Qty', default=1)
    height = fields.Float('Height')
    width = fields.Float('Width')
    depth = fields.Float('Depth')
    comment = fields.Char('Comment')
    incoterms_id = fields.Many2one('account.incoterms', string='Incoterms')
    required_length = fields.Boolean(related='package_type.required_length', string='Required Length')
    required_width = fields.Boolean(related='package_type.required_width', string='Required Width')
    required_height = fields.Boolean(related='package_type.required_height', string='Required Height')
    qty_editable = fields.Boolean(related='package_type.qty_editable', string='Edit Quantity')
    total_planned_weight = fields.Float(compute='get_total_planned_weight', string='Total Distributed')
    remain_weight = fields.Float(compute='get_total_planned_weight', string='Difference')
    cargoson_weight_uom = fields.Selection(related='carrier_id.cargoson_weight_uom', string='Weight Unit')
    cargoson_length_uom = fields.Selection(related='carrier_id.cargoson_length_uom', string='Length Unit')
    has_selected_rate = fields.Boolean(compute='_compute_has_selected_rate', string='Has Selected Rate')
    # Do not redefine available_carrier_ids: core Odoo computes it via _compute_available_carrier
    # (company domain + available_carriers / tags / weight / volume). Replacing that logic hid other
    # integrations' carriers from the domain on carrier_id, which also hid Cost / Get rate.

    @api.depends(
        'partner_id',
        'order_id',
        'order_id.partner_shipping_id',
        'order_id.partner_shipping_id.country_id',
        'order_id.company_id',
    )
    def _compute_available_carrier(self):
        super()._compute_available_carrier()
        for rec in self:
            if not rec.order_id:
                continue
            partner = rec.order_id.partner_shipping_id or rec.order_id.partner_id

            def cargoson_extra_allowed(carrier):
                uf = getattr(carrier, 'use_for', None)
                if uf and uf not in ('sale', 'picking'):
                    return False
                if getattr(carrier, 'use_destination_filtering', False):
                    if (
                        carrier.country_ids
                        and partner
                        and partner.country_id
                        and partner.country_id not in carrier.country_ids
                    ):
                        return False
                return True

            rec.available_carrier_ids = rec.available_carrier_ids.filtered(cargoson_extra_allowed)

    @api.depends('carrier_rates.choose')
    def _compute_has_selected_rate(self):
        for record in self:
            record.has_selected_rate = bool(record.carrier_rates.filtered(lambda r: r.choose))

    @api.depends('package_types.weight')
    def get_total_planned_weight(self):
        for record in self:
            if self.package_types:
                record.total_planned_weight = sum(package_type.weight for package_type in self.package_types)
            else:
                record.total_planned_weight = 0.0
            record.remain_weight = record.total_weight - record.total_planned_weight

    @api.onchange('carrier_id', 'total_weight')
    def _onchange_carrier_id(self):
        res = super()._onchange_carrier_id()
        if self.carrier_id:
            self.package_type = self.carrier_id.cargoson_default_package_type_id.id
        return res

    @api.onchange('order_id')
    def _onchange_order_id(self):
        if self.carrier_id and self.carrier_id.delivery_type == 'cargoson':
            self.delivery_message = False
            self.display_price = 0.0
            self.delivery_price = 0.0
            return
        return super()._onchange_order_id()

    def button_set_package(self):
        order_id = self.env['sale.order'].browse(self.env.context.get('default_order_id'))
        result = []
        package_type = self.carrier_id.cargoson_default_package_type_id
        
        # Calculate estimated products weight (excluding crates when crate shipping is used)
        estimated_products_weight = self.order_id._get_estimated_weight()
        
        # Check if shipping with crate
        if package_type.is_crate:
            # Only process crate products
            crate_lines = order_id.order_line.filtered(lambda p: p.product_id.is_crate is True and p.product_id.type != 'service')
            if not crate_lines:
                raise ValidationError(_("No crate products found in the order. Please add crate products to use crate shipping."))
            # Calculate total non-crate products weight
            non_crate_lines = order_id.order_line.filtered(
                lambda p: p.product_id.is_crate is False and p.product_id.type in ['consu', 'product'] and p.product_id.type != 'service'
            )
            total_non_crate_weight = sum(
                line.product_template_id.weight * line.product_uom_qty 
                for line in non_crate_lines
            )
            # Use non-crate weight as estimated products weight when shipping with crates
            estimated_products_weight = total_non_crate_weight
            
            # Calculate crate volumes based on dimensions (for proportional weight distribution)
            # Weight will be distributed by volume: bigger crates get more weight proportionally
            crate_data = []
            total_crate_volume = 0.0
            
            for crate_line in crate_lines:
                dims = self._get_product_dimensions_cm(crate_line.product_template_id)
                if dims is None:
                    raise ValidationError(_(
                        "Crate product '%s' has no dimensions. Install delivery_cargoson_dimension and set cargo length, width, height on the product."
                    ) % crate_line.product_template_id.display_name)
                crate_height, crate_width, crate_depth = dims
                
                # Calculate volume per unit crate: height × width × depth (in cm³)
                crate_volume_per_unit = crate_height * crate_width * crate_depth
                
                # Calculate total volume for this crate line: volume per unit × quantity
                crate_total_volume = crate_volume_per_unit * crate_line.product_uom_qty
                
                # Calculate total weight for this crate line (crate's own weight)
                crate_line_weight = crate_line.product_template_id.weight * crate_line.product_uom_qty
                
                crate_data.append({
                    'line': crate_line,
                    'height': crate_height,
                    'width': crate_width,
                    'depth': crate_depth,
                    'qty': crate_line.product_uom_qty,
                    'volume_per_unit': crate_volume_per_unit,
                    'total_volume': crate_total_volume,
                    'crate_weight': crate_line_weight,
                })
                
                # Sum total volume of all crates (needed for proportional weight distribution)
                total_crate_volume += crate_total_volume
            
            # Create package lines for each crate product line with weight distributed by volume
            for crate_info in crate_data:
                # Distribute non-crate products weight proportionally based on crate volume
                # Formula: (crate_total_volume / total_crate_volume) × total_non_crate_weight
                # This ensures bigger crates (larger volume) get proportionally more weight
                if total_crate_volume > 0:
                    # Calculate volume ratio: this crate's total volume / total volume of all crates
                    volume_ratio = crate_info['total_volume'] / total_crate_volume
                    # Distribute non-crate products weight proportionally
                    # Example: If crate has 40% of total volume, it gets 40% of non-crate weight
                    distributed_weight = total_non_crate_weight * volume_ratio
                else:
                    # Fallback: if no volume data available, distribute equally between all crate lines
                    distributed_weight = total_non_crate_weight / len(crate_data) if crate_data else 0.0
                
                # weight = distributed non-crate products weight only (crate weight is NOT included in product weight)
                # total_weight = distributed weight + crate weight + (package base_weight * qty)
                # Note: Crate weight needs to be included in total_weight for shipping purposes
                
                # Calculate total weight: distributed products + crate weight + package base weight
                crate_weight = crate_info['crate_weight']
                base_weight = (package_type.base_weight * crate_info['qty']) if package_type.base_weight else 0.0
                total_weight_for_crate = distributed_weight + crate_weight + base_weight
                
                result.append((0, 0, {
                    'package_type': package_type.id,
                    'weight': distributed_weight,  # Only distributed non-crate products weight (no crate weight)
                    'height': crate_info['height'],
                    'width': crate_info['width'],
                    'depth': crate_info['depth'],
                    'qty': crate_info['qty'],
                    'total_weight': total_weight_for_crate  # Includes: distributed products + crate weight + package base weight
                }))
        
        elif package_type.one_line_all:
            # Sum product weights for all non-service products (including crates when not using crate shipping)
            product_weight = sum(
                line.product_template_id.weight * line.product_uom_qty
                for line in order_id.order_line.filtered(
                    lambda p: p.product_id.type in ['consu', 'product'] and p.product_id.type != 'service'
                )
            )
            result.append((0, 0, {
                'package_type': package_type.id,
                'weight': product_weight,
                'height': package_type.height,
                'width': package_type.width,
                'depth': package_type.packaging_length,
                'qty': package_type.default_qty,
                'total_weight': product_weight + (package_type.base_weight * package_type.default_qty) if package_type.base_weight else product_weight
            }))
        else:
            # All non-service lines (including crates when not using crate shipping)
            for line in order_id.order_line.filtered(lambda p: p.product_id.type != 'service'):
                # Calculate product weight (without package base weight)
                product_weight = line.product_template_id.weight * line.product_uom_qty
                package_qty = package_type.default_qty if package_type.use_default_qty else line.product_uom_qty
                
                if package_type.use_product_dimensions:
                    dims = self._get_product_dimensions_cm(line.product_template_id)
                    if dims is not None:
                        height, width, depth = dims
                    else:
                        height = package_type.height
                        width = package_type.width
                        depth = package_type.packaging_length
                    
                    result.append((0, 0, {
                        'package_type': package_type.id,
                        'weight': product_weight,  # Product weight only (without base_weight)
                        'height': height,  # Converted to cm
                        'width': width,  # Converted to cm
                        'depth': depth,  # Converted to cm
                        'qty': package_qty,
                        'total_weight': product_weight + (package_type.base_weight * package_qty) if package_type.base_weight else product_weight
                    }))
                else:
                    result.append((0, 0, {
                        'package_type': package_type.id,
                        'weight': product_weight,  # Product weight only (without base_weight)
                        'height': package_type.height,
                        'width': package_type.width,
                        'depth': package_type.packaging_length,
                        'qty': package_qty,
                        'total_weight': product_weight + (package_type.base_weight * package_qty) if package_type.base_weight else product_weight

                    }))
                
        view_id = self.env.ref('delivery.choose_delivery_carrier_view_form').id
        return_dict = {
            'name': 'Carrier Rates',
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'choose.delivery.carrier',
            'view_id': view_id,
            'views': [(view_id, 'form')],
            'target': 'new',
            'context': {
                'default_order_id': self.order_id.id,
                'default_carrier_id': self.carrier_id.id,
                'default_total_weight': estimated_products_weight,
                'default_comment': self.comment,
                'default_package_type': self.package_type.id,
                'default_incoterms_id': self.carrier_id.incoterms_id.id,
                'default_package_types': result
            }
        }
        return return_dict

    def button_get_size(self):
        # Calculate estimated products weight (excluding crates when crate shipping is used)
        estimated_products_weight = self.order_id._get_estimated_weight()
        if self.package_type and self.package_type.is_crate:
            # Exclude crate products from estimated weight when shipping with crates
            non_crate_lines = self.order_id.order_line.filtered(
                lambda p: p.product_id.is_crate is False and p.product_id.type in ['consu', 'product']
            )
            estimated_products_weight = sum(
                line.product_template_id.weight * line.product_uom_qty 
                for line in non_crate_lines
            )
        
        context = {
            'default_order_id': self.order_id.id,
            'default_carrier_id': self.carrier_id.id,
            'default_total_weight': estimated_products_weight,
            'default_package_qty': self.package_type.default_qty,
            'default_comment': self.comment,
            'default_package_type': self.package_type.id,
            'default_incoterms_id': self.carrier_id.incoterms_id.id,
        }
        if self.package_type:
            if self.package_type.is_crate:
                crate_product = self.order_id.order_line.filtered(lambda p: p.product_id.is_crate is True)
                dims = self._get_product_dimensions_cm(crate_product.product_template_id) if crate_product else None
                if dims is None:
                    raise ValidationError(_("Crate product has no dimensions. Install delivery_cargoson_dimension and set cargo length, width, height."))
                context.update({
                    'default_height': dims[0],
                    'default_width': dims[1],
                    'default_depth': dims[2]
                })
            elif self.package_type.use_product_dimensions:
                height = 0.0
                width = 0.0
                length = 0.0
                sum_qty = 0.0
                if self.package_type.default_product_id:
                    count_qty = sum(product_line.product_uom_qty for product_line in self.order_id.order_line.filtered(
                            lambda p: p.product_id.type in ['consu', 'product']))
                    height = self.package_type.height
                    width = self.package_type.width
                    length = self.package_type.packaging_length
                    sum_qty = count_qty
                else:
                    for product_line in self.order_id.order_line.filtered(
                        lambda p: p.product_id.type in ['consu', 'product'] and p.product_id.is_crate is False):
                        dims = self._get_product_dimensions_cm(product_line.product_template_id)
                        if dims is not None:
                            height += dims[0]
                            width += dims[1]
                            length += dims[2]
                        else:
                            height += self.package_type.height
                            width += self.package_type.width
                            length += self.package_type.packaging_length
                        sum_qty += product_line.product_uom_qty
                context.update({
                    'default_package_qty': sum_qty,
                    'default_height': height,
                    'default_width': width,
                    'default_depth': length
                })
            else:
                if self.package_type.default_qty <= 0.0:
                    sum_qty = sum(self.order_id.order_line.filtered(
                        lambda p: p.product_id.type in ['consu', 'product'] and p.product_id.is_crate is False).mapped('product_uom_qty'))
                    context.update({
                        'default_package_qty': sum_qty,
                    })
                context.update({
                    'default_height': self.package_type.height,
                    'default_width': self.package_type.width,
                    'default_depth': self.package_type.packaging_length
                })
            view_id = self.env.ref('delivery.choose_delivery_carrier_view_form').id
            return_dict = {
                'name': 'Carrier Rates',
                'type': 'ir.actions.act_window',
                'view_mode': 'form',
                'res_model': 'choose.delivery.carrier',
                'view_id': view_id,
                'views': [(view_id, 'form')],
                'target': 'new',
                'context': context
            }
            if self.comment:
                return_dict['context'].update({'default_comment': str(self.comment)})
            return return_dict

    def get_prices(self):
        if self.carrier_id.prod_environment:
            if not self.carrier_id.production_url or not self.carrier_id.production_token:
                raise ValidationError(_('Production URL and Token is required! please check shipping method.'))
        else:
            if not self.carrier_id.cargoson_access_token:
                raise ValidationError(_('Test Token is required! please check shipping method.'))
        
        order_id = self.env['sale.order'].browse(self.env.context.get('default_order_id'))
        
        # Ensure package_types data is saved and total_weight is properly set
        # Use the total_weight from the wizard pop-up (what user sees/edits) instead of recalculating from product_weight
        # Only recalculate if total_weight is missing or zero, ensuring crate weight is included
        # CRITICAL: Read total_weight values BEFORE any operations to capture UI values
        package_weights = {}  # Store weights by line ID to preserve UI values
        for package_line in self.package_types:
            # Read the total_weight value directly - this should capture UI-modified values
            package_weights[package_line.id] = package_line.total_weight or 0
            _logger.info(f"Initial read - Package line {package_line.id}: total_weight={package_weights[package_line.id]}")
        
        # Now process each line and ensure total_weight is set correctly
        # Also update package_weights dict with calculated values for validation
        for package_line in self.package_types:
            # Get existing total_weight from our initial read (preserves UI values)
            existing_total_weight = package_weights.get(package_line.id, 0) or 0
            
            if existing_total_weight > 0:
                # Total weight is already set in wizard - use it as-is
                # The onchange methods already handle qty changes and update total_weight correctly
                # Don't recalculate - trust the value from the wizard
                # Ensure the value is set on the record for validation
                package_line.total_weight = existing_total_weight
                # Keep the value in our dict for validation
                package_weights[package_line.id] = existing_total_weight
                _logger.info(f"Package line {package_line.id}: Using existing total_weight={existing_total_weight} (from UI)")
            else:
                # Total weight is not set or is zero - calculate it properly including crate weight
                product_weight = package_line.weight or 0
                package_qty = package_line.qty or 1
                crate_weight = package_line._get_crate_weight_for_package_line() if package_line.package_type.is_crate else 0.0
                base_weight = (package_line.package_type.base_weight * package_qty) if (package_line.package_type and package_line.package_type.base_weight) else 0.0
                calculated_total_weight = product_weight + crate_weight + base_weight
                package_line.total_weight = calculated_total_weight
                # Update our dict with the calculated value for validation
                package_weights[package_line.id] = calculated_total_weight
                _logger.info(f"Package line {package_line.id}: Calculated total_weight={calculated_total_weight} (product={product_weight}, crate={crate_weight}, base={base_weight})")
        
        # For crate shipping, check the sum of package line total_weights instead of self.total_weight
        # because self.total_weight only includes non-crate products weight (which can be 0 if only crates)
        # but package lines include crate weight + distributed weight + base weight
        if self.package_type and self.package_type.is_crate and self.package_types:
            # Sum all package line total_weights for crate shipping
            # Use package_weights dict which has the correct values (from UI or calculated)
            total_weight_from_packages = sum(package_weights.values())
            _logger.info(f"Crate shipping validation: total_weight_from_packages={total_weight_from_packages}, package_weights_dict={package_weights}, package_lines_from_recordset={[(l.id, l.total_weight) for l in self.package_types]}")
            if total_weight_from_packages <= 0.0:
                raise ValidationError(_('Total weight cant be zero! Please ensure package weights are set correctly.'))
        else:
            # For non-crate shipping, check package line weights if package_types exist
            # Otherwise, use self.total_weight (for backward compatibility)
            if self.package_types and package_weights:
                # Use package_weights dict which has the correct values (from UI or calculated)
                total_weight_from_packages = sum(package_weights.values())
                _logger.info(f"Non-crate shipping validation (with package_types): total_weight_from_packages={total_weight_from_packages}, package_weights_dict={package_weights}, self.total_weight={self.total_weight}")
                if total_weight_from_packages <= 0.0:
                    raise ValidationError(_('Total weight cant be zero! Please ensure package weights are set correctly.'))
            else:
                # For non-crate shipping without package_types, use self.total_weight as before
                _logger.info(f"Non-crate shipping validation (no package_types): self.total_weight={self.total_weight}")
                if self.total_weight <= 0.0:
                    raise ValidationError(_('Total weight cant be zero!'))
        
        # Refresh to ensure we have the latest data
        self.package_types.invalidate_recordset()
        
        data = self.carrier_id.cargoson_rate_shipment(self.package_types, order_id, self.carrier_id)
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
                        'estimated_delivery_date': line.get('estimated_delivery_date')
                    }))
                    new.append(line.get('service_id'))
            extra_lines = self.carrier_id.cargoson_courier_ids.filtered(lambda x: int(x.service_id) not in new)
            for extra_line in extra_lines:
                result.append((0, 0, {
                    'name': extra_line.name,
                    'price': 0.0,
                    'service': extra_line.service_name,
                    'service_id': extra_line.service_id,
                }))
            package_types = []
            for line in self.package_types:

                package_types.append((0, 0, {
                    'package_type': line.package_type.id,
                    'total_weight': line.total_weight,
                    'weight': line.weight,
                    'height': line.height,
                    'width': line.width,
                    'depth': line.depth,
                    'qty': line.qty
                }))


            view_id = self.env.ref('delivery.choose_delivery_carrier_view_form').id
            return_dict = {
                'name': 'Carrier Rates',
                'type': 'ir.actions.act_window',
                'view_mode': 'form',
                'res_model': 'choose.delivery.carrier',
                'view_id': view_id,
                'views': [(view_id, 'form')],
                'target': 'new',
                'context': {
                    'default_order_id': self.order_id.id,
                    'default_carrier_id': self.carrier_id.id,
                    'default_total_weight': self.total_weight,
                    'default_carrier_rates': result,
                    'default_package_qty': self.package_qty,
                    'default_comment': self.comment,
                    'default_package_type': self.package_type.id,
                    'default_incoterms_id': self.incoterms_id.id,
                    'default_height':  self.height,
                    'default_width':  self.width,
                    'default_depth': self.depth,
                    'default_package_types': package_types
                }
            }
            if self.comment:
                return_dict['context'].update({'default_comment': str(self.comment)})
            return return_dict

    def button_confirm(self):
        if not self.carrier_id:
            raise ValidationError("Please select a carrier first!")
        
        # Cargoson-specific validations
        if self.carrier_id.delivery_type == 'cargoson':
            # Check if user has actually selected a carrier rate (not just default carrier)
            choose_rate = self.carrier_rates.filtered(lambda p: p.choose is True)
            if not choose_rate:
                raise ValidationError("Please select a carrier rate first!")
            if len(choose_rate) > 1:
                raise ValidationError("You can choose only single option")
            if not self.incoterms_id:
                raise ValidationError("Incoterms is required!")
            if self.env.context.get('carrier_recompute'):
                self._ensure_cargoson_recompute_can_change()
            if self.env.context.get('carrier_recompute') and self.carrier_id.integration_level == 'rate':
                return self.create_cargoson_order()
            # Pass raw cost to set_delivery_line; margin will be applied in _prepare_delivery_line_vals
            self.order_id.set_delivery_line(self.carrier_id, float(choose_rate.price))
        else:
            # For non-Cargoson carriers, use standard behavior
            self.order_id.set_delivery_line(self.carrier_id, 0.0)
        
        # Prepare order_dict with basic fields
        order_dict = {
            'recompute_delivery_price': False,
            'carrier_id': self.carrier_id.id
        }
        
        # Add Cargoson-specific fields only for Cargoson carriers
        if self.carrier_id.delivery_type == 'cargoson':
            choose_rate = self.carrier_rates.filtered(lambda p: p.choose is True)
            order_dict.update({
                'delivery_message': self.delivery_message,
                'service_id': choose_rate.service_id if choose_rate else False,
                'package_type': self.package_type.id if self.package_type else False,
                'package_qty': self.package_qty,
                'height': self.height,
                'width': self.width,
                'depth': self.depth,
                'incoterm': self.incoterms_id.id if self.incoterms_id else False,
            })
            
            package_types = [(5, 0, 0)]  # Clear all existing package types first
            for line in self.package_types:
                _logger.info(f"Button confirm - Package weight: weight={line.weight}, total_weight={line.total_weight}")
                package_types.append((0, 0, {
                    'package_type': line.package_type.id,
                    'total_weight': line.total_weight,
                    'weight': line.weight,
                    'height': line.height,
                    'width': line.width,
                    'depth': line.depth,
                    'qty': line.qty
                }))
            order_dict.update({
                'package_types': package_types,
                'service_name': choose_rate.name if choose_rate else False,
                'choose_service': choose_rate.service if choose_rate else False,
                'carrier_cost': choose_rate.price if choose_rate else 0.0
            })
        
        if self.comment:
            order_dict.update({'comment': str(self.comment)})
        
        self.order_id.write(order_dict)
        
        # Sync package information to picking after updating the sale order
        if self.carrier_id.delivery_type == 'cargoson' and self.order_id.package_types:
            self.order_id.update_picking()
        if self.env.context.get('carrier_recompute'):
            self._sync_cargoson_recompute_to_existing_shipment()

    def _sync_cargoson_recompute_to_existing_shipment(self):
        if not self.carrier_id or self.carrier_id.delivery_type != 'cargoson':
            return
        if self.carrier_id.integration_level == 'rate':
            return
        pickings = self._get_cargoson_recompute_pickings()
        if not pickings:
            return

        self.env.flush_all()
        carrier = self.carrier_id
        order = self.order_id
        for picking in pickings:
            picking.invalidate_recordset()
            picking = picking.browse(picking.id)
            doc_ref = carrier._cargoson_cargoson_document_ref(picking, order)
            if carrier._cargoson_is_followup_cargoson_delivery(picking, order):
                picking.write({'carrier_tracking_ref': False})
            elif doc_ref:
                carrier.cargoson_update_shipping(picking)

    def _ensure_cargoson_recompute_can_change(self):
        if not self.carrier_id or self.carrier_id.delivery_type != 'cargoson':
            return
        carrier = self.carrier_id
        order = self.order_id
        if order.cargoson_ref:
            carrier._cargoson_raise_if_booking_locked(order.cargoson_ref)
        for picking in self._get_cargoson_recompute_pickings():
            doc_ref = carrier._cargoson_cargoson_document_ref(picking, order)
            if doc_ref:
                carrier._cargoson_raise_if_booking_locked(doc_ref)

    def _get_cargoson_recompute_pickings(self):
        order = self.order_id
        pickings = getattr(order, 'picking_ids', self.env['stock.picking'])
        if not pickings:
            pickings = self.env['stock.picking'].search([('origin', '=', order.name)])
        pickings = pickings.filtered(
            lambda p: p.carrier_id == self.carrier_id and p.state != 'cancel'
        )
        trigger_pickings = pickings.filtered(
            lambda p: hasattr(p, '_should_trigger_cargoson_action') and p._should_trigger_cargoson_action()
        )
        return trigger_pickings or pickings[:1]

    def _prepare_parcel(self, order_line, carrier_id):
        order_dict = {}
        if self.package_type.is_crate:
            order_dict.update({
                'length': self.depth,
                'width': self.width,
                'height': self.height,
                'quantity': self.package_qty,
                'package_type': self.package_type.shipper_package_code,
                'description': order_line.product_id.name,
                'weight': order_line.product_id.weight,
            })
        elif self.package_type.use_product_dimensions:
            order_dict.update({
                'length': self.depth,
                'width': self.width,
                'height': self.height,
                'quantity': order_line.product_uom_qty,
                'package_type': self.package_type.shipper_package_code,
                'description': order_line.product_id.name,
                'weight': order_line.product_id.weight,
            })
        else:
            # if self.package_type.default_qty <= 0.0:
            order_dict.update({
                'quantity': order_line.product_uom_qty
            })
            order_dict.update({
                'package_type': self.package_type.shipper_package_code,
                'description': order_line.product_id.name,
                'weight': order_line.product_id.weight,
            })
        return order_dict

    def is_between(times, time_range):
        if time_range[1] < time_range[0]:
            return times >= time_range[0] or times <= time_range[1]

    def create_cargoson_draft(self):
        """Create draft order for rate integration level"""
        if not self.carrier_id:
            raise ValidationError("Please select a carrier first!")
        return self.create_cargoson_order()
    
    def create_cargoson_booking(self):
        """Create booking for rate_and_ship integration level"""
        if not self.carrier_id:
            raise ValidationError("Please select a carrier first!")
        # For booking, we need to ensure a rate is selected
        choose_rate = self.carrier_rates.filtered(lambda p: p.choose is True)
        if not choose_rate:
            raise ValidationError("Please select a carrier rate before creating a booking.")
        
        # Store the data in sale order but don't send to Cargoson yet
        # The actual booking will be sent when the picking is validated
        return self.create_cargoson_order(send_to_cargoson=False)



    def create_cargoson_order(self, send_to_cargoson=True):
        choose_rate = self.carrier_rates.filtered(lambda p: p.choose is True)
        if len(choose_rate) > 1:
            raise ValidationError("You can choose only single option")
        if not self.incoterms_id:
            raise ValidationError("Incoterms is required!")
        
        # Handle case when no carrier is selected
        if not self.carrier_id:
            raise ValidationError("Please select a carrier before setting the delivery line!")
        if not choose_rate:
            price = 0.0
            self.order_id.set_delivery_line(self.carrier_id, 0.0)
        else:
            price = float(choose_rate.price) * (1.0 + self.carrier_id.margin) + self.carrier_id.fixed_margin
            self.order_id.set_delivery_line(self.carrier_id, float(choose_rate.price))
        order_dict = {
            'recompute_delivery_price': False,
            'delivery_message': self.delivery_message,
            'incoterm': self.incoterms_id.id,
            'carrier_cost': choose_rate.price if choose_rate else 0.0,
            'service_name': choose_rate.name if choose_rate else False,
            'choose_service': choose_rate.service if choose_rate else False,
            'service_id': choose_rate.service_id if choose_rate else False
        }
        package_types_list = [(5, 0, 0)]  # Clear all existing package types first
        for package_line in self.package_types:
            # Use total_weight directly from the wizard (already calculated correctly)
            # weight field = product weight (without base_weight)
            # total_weight field = product weight + (base_weight * qty)
            _logger.info(f"Package weight: weight={package_line.weight} (product), total_weight={package_line.total_weight} (product + base_weight * qty)")
            package_types_list.append((0, 0, {
                'sale_id': self.order_id.id,
                'depth': package_line.depth,
                'width': package_line.width,
                'height': package_line.height,
                'qty': package_line.qty,
                'package_type': package_line.package_type.id,
                'total_weight': package_line.total_weight,  # Use total_weight from wizard
                'weight': package_line.weight  # Product weight only
            }))
            
        order_dict.update({'package_types': package_types_list})
        if self.comment:
            order_dict.update({'comment': str(self.comment)})
        self.order_id.write(order_dict)
        
        # Sync package information to picking after updating the sale order
        if self.carrier_id.delivery_type == 'cargoson' and self.order_id.package_types:
            self.order_id.update_picking()

        sr = cargoson(self.carrier_id)
        header_data = {
            'Content-Type': 'application/json',
            'Accept': 'application/vnd.api.v1'
        }
        products_list = []
        cargoson_parcel = {}
        if self.carrier_id:
            for package_line in self.package_types:
                products_list.append({
                    'length': package_line.depth,
                    'width': package_line.width,
                    'height': package_line.height,
                    'quantity': package_line.qty,
                    'package_type': package_line.package_type.shipper_package_code,
                    'description': package_line.package_type.default_product_id.name,
                    'weight': package_line.total_weight,  # Use total_weight (includes base_weight * qty)
                })
        else:
            raise ValidationError("Carrier_id Required!")
        
        # Use order's salesperson TZ then current user TZ; Odoo datetimes are UTC (res.company has no tz)
        tz_str = (self.order_id.user_id and self.order_id.user_id.tz) or self.env.user.tz or 'UTC'
        to_zone = tz.gettz(tz_str) or tz.gettz('UTC')
        use_commitment = self.carrier_id.use_commitment_date and self.order_id.commitment_date
        commitment_for_pick_up = use_commitment and self.carrier_id.commitment_date_for == 'pick_up'
        commitment_for_delivery = use_commitment and self.carrier_id.commitment_date_for == 'delivery'

        if commitment_for_pick_up:
            collection_date = self.order_id.commitment_date.strftime('%Y-%m-%d')
            if self.carrier_id.collection_time and self.carrier_id.delivery_time:
                cargoson_parcel.update({
                    "collection_time_from": self.carrier_id._cargoson_format_float_time(
                        self.carrier_id.collection_time),
                    "collection_time_to": self.carrier_id._cargoson_format_float_time(
                        self.carrier_id.delivery_time),
                })
            else:
                str_collection_time = _utc_to_local(self.order_id.commitment_date, to_zone)
                time_from = str_collection_time.strftime("%H:%M")
                cargoson_parcel.update({
                    "collection_time_from": time_from,
                })
                if self.carrier_id.delivery_time:
                    cargoson_parcel.update({
                        "collection_time_to": self.carrier_id._cargoson_format_float_time(
                            self.carrier_id.delivery_time),
                    })
            _cargoson_fix_collection_time_window(cargoson_parcel)
            tf = cargoson_parcel.get('collection_time_from')
            tt = cargoson_parcel.get('collection_time_to')
            collection_date = _cargoson_roll_past_slot_to_working_day(
                collection_date, tf, tt, to_zone)
        else:
            collection_date = (self.order_id.date_order + timedelta(days=1)).strftime('%Y-%m-%d')
            collection_date_obj = (self.order_id.date_order + timedelta(days=1))
            if self.carrier_id.delivery_time and self.carrier_id.collection_time:
                str_collection_time = _utc_to_local(collection_date_obj, to_zone)
                str_collection_time_from = self.carrier_id._cargoson_format_float_time(self.carrier_id.collection_time)
                str_collection_time_to = self.carrier_id._cargoson_format_float_time(self.carrier_id.delivery_time)
                cargoson_parcel.update({
                    "collection_time_from": str_collection_time_from,
                    "collection_time_to": str_collection_time_to,
                })
                _cargoson_fix_collection_time_window(cargoson_parcel)
                tf = cargoson_parcel.get('collection_time_from')
                tt = cargoson_parcel.get('collection_time_to')
                collection_date = _cargoson_roll_past_slot_to_working_day(
                    collection_date, tf, tt, to_zone)

        if commitment_for_delivery:
            d_date = self.order_id.commitment_date.strftime('%Y-%m-%d')
            from_time = self.carrier_id.delivery_window_time_from or 8.0
            d_from = self.carrier_id._cargoson_format_float_time(from_time)
            d_to = _cargoson_hm_add_hours(d_from, CARGOSON_DELIVERY_WINDOW_HOURS)
            cargoson_parcel["delivery_time_from"] = d_from
            cargoson_parcel["delivery_time_to"] = d_to
            cargoson_parcel["delivery_date"] = _cargoson_roll_past_slot_to_working_day(
                d_date, d_from, d_to, to_zone)
            cargoson_parcel["delivery_date"] = _cargoson_ensure_delivery_not_before_pickup(
                cargoson_parcel["delivery_date"], collection_date)
            _cargoson_bump_delivery_time_after_collection(
                cargoson_parcel, collection_date, cargoson_parcel["delivery_date"])

        if self.order_id.client_order_ref:
            cargoson_parcel.update({
                "customer_remark": self.order_id.client_order_ref + ' ' + self.order_id.name + ' ' + (self.order_id.comment or ''),
            })
        else:
            cargoson_parcel.update({
                "customer_remark": self.order_id.name + ' ' + (self.order_id.comment or ''),
            })

        # Get correct delivery partner and private person status
        delivery_partner, is_private_person = self._get_delivery_partner_info(self.order_id)
        
        # Determine collection contact - use carrier's collection_contact_id if set, otherwise use warehouse partner
        collection_contact = self.carrier_id.collection_contact_id if self.carrier_id.collection_contact_id else self.order_id.warehouse_id.partner_id
        
        # Build collection address: US format (street+street2 → row_1, state code → row_2) or standard
        wh_partner = self.order_id.warehouse_id.partner_id
        collection_addr_row_1 = wh_partner.street or ""
        collection_addr_row_2 = None
        if self.carrier_id.us_address:
            collection_addr_row_1 = ' '.join(filter(None, [
                (wh_partner.street or '').strip(),
                (wh_partner.street2 or '').strip()
            ])).strip() or ""
            collection_addr_row_2 = wh_partner.state_id.code if wh_partner.state_id else ""
        
        # Build delivery address from partner_id (base)
        dp = self.order_id.partner_id
        delivery_addr_row_1 = dp.street or ""
        delivery_addr_row_2 = None
        if self.carrier_id.us_address:
            delivery_addr_row_1 = ' '.join(filter(None, [
                (dp.street or '').strip(),
                (dp.street2 or '').strip()
            ])).strip() or ""
            delivery_addr_row_2 = dp.state_id.code if dp.state_id else ""
        
        cargoson_parcel.update({
            "delivery_to_private_person": is_private_person,
            "customer_reference": self.order_id.name,
            "collection_date": collection_date,
            "collection_contact_email": collection_contact.email,
            "collection_country": wh_partner.country_id.code,
            "collection_postcode": wh_partner.zip,
            "collection_address_row_1": collection_addr_row_1,
            "collection_city": wh_partner.city,
            "collection_company_name": wh_partner.name,
            "collection_contact_name": collection_contact.name,
            "collection_contact_phone": getattr(collection_contact, 'mobile', None) or getattr(collection_contact, 'phone', None) or "",
            "collection_with_tail_lift": self.carrier_id.collection_with_tail_lift,
            "delivery_country": dp.country_id.code,
            "delivery_postcode": dp.zip,
            "delivery_address_row_1": delivery_addr_row_1,
            "delivery_city": dp.city,
            "delivery_company_name": dp.commercial_partner_id.name,
            "delivery_contact_name": self.order_id.partner_id.name,
            "delivery_contact_email": self.order_id.partner_id.email,
            "delivery_contact_phone": getattr(self.order_id.partner_id, 'mobile', None) or getattr(self.order_id.partner_id, 'phone', None) or "",
            "delivery_with_tail_lift": self.carrier_id.delivery_with_tail_lift,
            "delivery_prenotification": True,
            "rows_attributes": products_list,
            "incoterm_code": self.order_id.incoterm.code if self.order_id.incoterm else None,
            "private_remark": self.carrier_id._get_cargoson_private_remark(self.order_id),
            "options": {
                "delivery_sms_notification": self.carrier_id.delivery_sms_notification
            }
        })
        cargoson_parcel.update(self.carrier_id._get_cargoson_goods_value_payload(self.order_id))
        if delivery_addr_row_2 is not None:
            cargoson_parcel["delivery_address_row_2"] = delivery_addr_row_2
        if collection_addr_row_2 is not None:
            cargoson_parcel["collection_address_row_2"] = collection_addr_row_2

        if self.order_id.partner_shipping_id:
            # Preserve existing options if they exist, otherwise create new
            existing_options = cargoson_parcel.get('options', {})
            existing_options['delivery_sms_notification'] = self.carrier_id.delivery_sms_notification
            
            pship = self.order_id.partner_shipping_id
            ship_addr_row_1 = pship.street or ""
            ship_addr_row_2 = None
            if self.carrier_id.us_address:
                ship_addr_row_1 = ' '.join(filter(None, [
                    (pship.street or '').strip(),
                    (pship.street2 or '').strip()
                ])).strip() or ""
                ship_addr_row_2 = pship.state_id.code if pship.state_id else ""
            ship_update = {
                "delivery_country": pship.country_id.code,
                "delivery_postcode": pship.zip,
                "delivery_address_row_1": ship_addr_row_1,
                "delivery_city": pship.city,
                "delivery_company_name": pship.commercial_partner_id.name,
                "delivery_contact_name": pship.name,
                "delivery_contact_email": pship.email,
                "delivery_contact_phone": getattr(pship, 'mobile', None) or getattr(pship, 'phone', None) or "",
                "options": existing_options
            }
            if ship_addr_row_2 is not None:
                ship_update["delivery_address_row_2"] = ship_addr_row_2
            cargoson_parcel.update(ship_update)
        
        # Add freight payer information if enabled
        if self.carrier_id.use_freight_payer:
            if self.carrier_id.freight_payer_type == 'sender':
                # Use collection contact details (sender)
                freight_payer_partner = collection_contact
                freight_payer_address = self.order_id.warehouse_id.partner_id
            else:
                # Use delivery partner details (receiver)
                if self.order_id.partner_shipping_id:
                    freight_payer_partner = self.order_id.partner_shipping_id
                    freight_payer_address = self.order_id.partner_shipping_id
                else:
                    freight_payer_partner = delivery_partner
                    freight_payer_address = delivery_partner
            
            fp_row_1 = freight_payer_address.street or ""
            fp_row_2 = freight_payer_address.street2 or ""
            if self.carrier_id.us_address:
                fp_row_1 = ' '.join(filter(None, [
                    (freight_payer_address.street or '').strip(),
                    (freight_payer_address.street2 or '').strip()
                ])).strip() or ""
                fp_row_2 = freight_payer_address.state_id.code if freight_payer_address.state_id else ""
            cargoson_parcel.update({
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
        
        _cargoson_ensure_collection_end_not_in_past(cargoson_parcel, to_zone)
        collection_date = cargoson_parcel.get('collection_date', collection_date)
        if cargoson_parcel.get('delivery_date') and cargoson_parcel.get('collection_date'):
            cargoson_parcel['delivery_date'] = _cargoson_ensure_delivery_not_before_pickup(
                cargoson_parcel['delivery_date'], cargoson_parcel['collection_date'])
            _cargoson_bump_delivery_time_after_collection(
                cargoson_parcel, cargoson_parcel['collection_date'], cargoson_parcel['delivery_date'])

        # Log delivery_sms_notification value before sending
        _logger.info(f"Sending to Cargoson - delivery_sms_notification: {cargoson_parcel.get('delivery_sms_notification')}, carrier setting: {self.carrier_id.delivery_sms_notification}")
        
        if send_to_cargoson:
            # Send or update Cargoson immediately (for draft orders)
            endpoint = 'queries'
            method = 'POST'
            if self.env.context.get('carrier_recompute') and self.order_id.cargoson_ref:
                endpoint = 'queries/%s' % self.order_id.cargoson_ref
                method = 'PATCH'
            order_response = sr._make_api_request(
                endpoint,
                header_data,
                method,
                json.dumps(cargoson_parcel),
                token=sr._get_token()
            )
            if (
                    (method == 'POST' and order_response.get('query_status') != 'created')
                    or (method == 'PATCH' and order_response.get('errors'))):
                error_msg = cargoson_format_api_error(order_response)
                if not (error_msg and str(error_msg).strip()):
                    error_msg = _('Could not create the Cargoson shipment.')
                raise ValidationError(error_msg)
            order_data = {}
            if order_response.get('label_url'):
                order_data.update({'label_url': order_response.get('label_url')})
            if order_response.get('cmr_url'):
                order_data.update({'cmr_url': order_response.get('cmr_url')})
            # BOL: try bol_url, bill_of_lading_url, bol_document_url (carrier-specific keys)
            bol_url = (
                order_response.get('bol_url') or
                order_response.get('bill_of_lading_url') or
                order_response.get('bol_document_url')
            )
            if bol_url:
                order_data.update({'bol_url': bol_url})
            if order_response.get('waybill_url'):
                order_data.update({'waybill_url': order_response.get('waybill_url')})
            if order_response.get('tracking_url'):
                order_data.update({'tracking_url': order_response.get('tracking_url')})
            if order_response.get('reference'):
                order_data.update({'cargoson_ref': order_response.get('reference')})
                order_data.update({
                    'carrier_id': self.carrier_id.id if self.carrier_id else False,
                    'carrier_cost': price,
                    'service_name': choose_rate.name if choose_rate else False,
                    'service_type': choose_rate.service if choose_rate else False,
                    'service_id': choose_rate.service_id if choose_rate else False
                })
            self.order_id.write(order_data)
        else:
            # For booking orders, just store the data without sending to Cargoson
            # The actual booking will be sent when the picking is validated
            order_data = {
                'carrier_id': self.carrier_id.id if self.carrier_id else False,
                'carrier_cost': price,
                'service_name': choose_rate.name if choose_rate else False,
                'service_type': choose_rate.service if choose_rate else False,
                'service_id': choose_rate.service_id if choose_rate else False
            }
            self.order_id.write(order_data)
