# Copyright 2019 Jarsa Sistemas, www.vauxoo.com
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools import float_is_zero, float_round


class ChangeProductionQty(models.TransientModel):
    _inherit = 'change.production.qty'

    @api.multi
    def _make_to_order_adjust_qty(self, wizard, lines):
        documents = {}
        production = wizard.mo_id
        for line, line_data in lines:
            move, old_qty, new_qty = production._update_raw_move(
                line, line_data)
            iterate_key = production._get_document_iterate_key(move)
            if iterate_key:
                document = (
                    self.env['stock.picking'].
                    _log_activity_get_documents({
                        move: (new_qty, old_qty)}, iterate_key, 'UP'))
                for key, value in document.items():
                    if documents.get(key):
                        documents[key] += [value]
                    else:
                        documents[key] = [value]
            moves_mto = production.move_raw_ids.filtered(
                lambda m: m.procure_method == 'make_to_order')
            for move_mto in moves_mto:
                if line.product_id.id == move_mto.product_id.id:
                    move_mto._do_unreserve()
                    move_mts = production.move_raw_ids.filtered(
                        lambda n: n.procure_method == 'make_to_stock' and
                        n.product_id.id == line.product_id.id)
                    new_qty = line_data['qty'] - move_mts.product_uom_qty
                    move_mto.write({'product_uom_qty': new_qty})
        moves_with_need = production.move_raw_ids.filtered(
            lambda m: m.procure_method == 'make_to_order' and
            m.product_uom_qty > 0
            and m.reserved_availability < m.product_uom_qty)
        procure_obj = self.env['procurement.group']
        for mw in moves_with_need:
            values = mw._prepare_procurement_values()
            procure_obj.run(
                mw.product_id, mw.product_uom_qty, mw.product_uom,
                mw.location_id, 'hola', production.name, values)
        return documents

    @api.multi
    def change_prod_qty(self):
        precision = self.env['decimal.precision'].precision_get(
            'Product Unit of Measure')
        for wizard in self:
            production = wizard.mo_id
            produced = sum(production.move_finished_ids.filtered(
                lambda m: m.product_id == production.product_id).mapped(
                'quantity_done'))
            if wizard.product_qty < produced:
                format_qty = '%.{precision}f'.format(precision=precision)
                raise UserError(
                    _("You have already processed %s. Please input a quantity "
                        "higher than %s ") % (
                        format_qty % produced, format_qty % produced))
            old_production_qty = production.product_qty
            production.write({'product_qty': wizard.product_qty})
            done_moves = production.move_finished_ids.filtered(
                lambda x: x.state == 'done' and
                x.product_id == production.product_id)
            qty_produced = production.product_id.uom_id._compute_quantity(
                sum(done_moves.mapped('product_qty')),
                production.product_uom_id)
            factor = production.product_uom_id._compute_quantity(
                production.product_qty - qty_produced,
                production.bom_id.product_uom_id) / (
                production.bom_id.product_qty)
            boms, lines = production.bom_id.explode(
                production.product_id, factor,
                picking_type=production.bom_id.picking_type_id)
            documents = self._make_to_order_adjust_qty(wizard, lines)
            production._log_manufacture_exception(documents)
            operation_bom_qty = {}
            for bom, bom_data in boms:
                for operation in bom.routing_id.operation_ids:
                    operation_bom_qty[operation.id] = bom_data['qty']
            finished_moves_modification = self._update_product_to_produce(
                production, production.product_qty - qty_produced,
                old_production_qty)
            production._log_downside_manufactured_quantity(
                finished_moves_modification)
            moves = production.move_raw_ids.filtered(
                lambda x: x.state not in ('done', 'cancel'))
            moves._action_assign()
            for wo in production.workorder_ids:
                operation = wo.operation_id
                if operation_bom_qty.get(operation.id):
                    cycle_number = float_round(
                        operation_bom_qty[operation.id] / (
                            operation.workcenter_id.capacity),
                        precision_digits=0, rounding_method='UP')
                    wo.duration_expected = (
                        operation.workcenter_id.time_start +
                        operation.workcenter_id.time_stop +
                        cycle_number * operation.time_cycle * 100.0 /
                        operation.workcenter_id.time_efficiency)
                quantity = wo.qty_production - wo.qty_produced
                if production.product_id.tracking == 'serial':
                    quantity = 1.0 if not float_is_zero(
                        quantity, precision_digits=precision) else 0.0
                else:
                    quantity = quantity if (quantity > 0) else 0
                if float_is_zero(quantity, precision_digits=precision):
                    wo.final_lot_id = False
                    wo.active_move_line_ids.unlink()
                wo.qty_producing = quantity
                if wo.qty_produced < wo.qty_production and wo.state == 'done':
                    wo.state = 'progress'
                if (wo.qty_produced == wo.qty_production and
                        wo.state == 'progress'):
                    wo.state = 'done'
                # assign moves; last operation receive all unassigned moves
                # TODO: following could be put in a function as it is similar
                # as code in _workorders_create
                # TODO: only needed when creating new moves
                moves_raw = production.move_raw_ids.filtered(
                    lambda move: move.operation_id == operation and move.state
                    not in ('done', 'cancel'))
                if wo == production.workorder_ids[-1]:
                    moves_raw |= production.move_raw_ids.filtered(
                        lambda move: not move.operation_id)
                moves_finished = production.move_finished_ids.filtered(
                    lambda move: move.operation_id == operation)
                # TODO: code does nothing, unless maybe by_products?
                moves_raw.mapped('move_line_ids').write({
                    'workorder_id': wo.id})
                (moves_finished + moves_raw).write({'workorder_id': wo.id})
                if quantity > 0 and (
                        wo.move_raw_ids.filtered(
                            lambda x: x.product_id.tracking != 'none') and
                        not wo.active_move_line_ids):
                    wo._generate_lot_ids()
        return {}
