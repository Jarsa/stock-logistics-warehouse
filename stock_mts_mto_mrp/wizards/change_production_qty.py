# Copyright 2019, Jarsa Sistemas, S.A. de C.V.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo import api, models


class ChangeProductionQty(models.TransientModel):
    _inherit = 'change.production.qty'

    @api.multi
    def change_prod_qty(self):
        origs = {}
        move_orig = self.mo_id.move_raw_ids[0].move_orig_ids
        for move_raw in self.mo_id.move_raw_ids:
            origs[str(move_raw.product_id.id)] = {
                'move_orig_ids': move_raw.move_orig_ids,
                'old_qty': move_raw.product_uom_qty,
            }
        moves = self.mo_id.mapped('move_raw_ids').filtered(
            lambda m: m.procure_method == 'make_to_order')
        move_lines = moves.mapped('move_line_ids')
        moves.write({
            'state': 'draft',
        })
        move_lines.write({
            'state': 'draft',
        })
        moves.unlink()
        res = super().change_prod_qty()
        # If a MTO move was deleted, the method change_prod_qty creates a new
        # move for the component, but it's in draft state and cannot be
        # reserved, we need to confirm the stock.move
        moves = self.mo_id.move_raw_ids.filtered(
            lambda l: l.state == 'draft')._action_confirm()
        production = self.mo_id
        production.action_assign()
        done_moves = production.move_finished_ids.filtered(
            lambda x: x.state == 'done' and x.product_id ==
            production.product_id)
        qty_produced = production.product_id.uom_id._compute_quantity(
            sum(done_moves.mapped('product_qty')), production.product_uom_id)
        factor = production.product_uom_id._compute_quantity(
            production.product_qty - qty_produced,
            production.bom_id.product_uom_id) / production.bom_id.product_qty
        boms, lines = production.bom_id.explode(
            production.product_id, factor,
            picking_type=production.bom_id.picking_type_id)
        documents = {}
        sm_dict = {}
        picking_obj = self.env['stock.picking']
        for line, line_data in lines:
            move = production.move_raw_ids.filtered(
                lambda x: x.bom_line_id.id == line.id and
                x.state not in ('done', 'cancel'))
            if move:
                move = move[0]
                orig = origs[str(move.product_id.id)]
                move.move_orig_ids = orig['move_orig_ids']
            else:
                move = move_orig
            iterate_key = production._get_document_iterate_key(move)
            warehouse = self.env.ref('stock.warehouse0')
            qual_loc = warehouse.pbm_type_id.default_location_dest_id.id
            pc_move = self.mo_id.picking_ids.mapped(
                'move_ids_without_package').filtered(
                lambda m: m.location_dest_id.id == qual_loc)
            for pcm in pc_move:
                for sm in production.move_raw_ids:
                    if sm.product_id == pcm.product_id:
                        sm_dict.update({pcm: (sm.product_uom_qty,
                                        pcm.product_uom_qty)})
                if move.product_id == pcm.product_id:
                    document = picking_obj._log_activity_get_documents(
                        {move: (line_data['qty'], pcm.product_uom_qty)},
                        iterate_key, 'UP')
                    for key, value in document.items():
                        if documents.get(key):
                            documents[key] += [value]
                        else:
                            documents[key] = [value]
        production._log_downside_manufactured_quantity(sm_dict)
        production._log_manufacture_exception(documents)
        return res
