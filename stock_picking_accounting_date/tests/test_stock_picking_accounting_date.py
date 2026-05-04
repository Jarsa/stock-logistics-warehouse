# Copyright 2023 Quartile Limited
# Copyright 2023 Jarsa
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date, timedelta

from odoo.tests import TransactionCase


class TestStockPickingAccountingDate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier_location = cls.env.ref("stock.stock_location_suppliers")
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.uom_unit = cls.env.ref("uom.product_uom_unit")
        cls.partner = cls.env["res.partner"].create({"name": "Test Supplier"})

        # account_asset (enterprise) adds create_asset as NOT NULL without a DB
        # default. When it is installed in the DB but not loaded in the ORM (e.g.
        # missing enterprise license in the test run), any INSERT into account_account
        # fails. Set a temporary DB default so the column is satisfied.
        cls.env.cr.execute(
            """
            SELECT column_default FROM information_schema.columns
            WHERE table_name = 'account_account' AND column_name = 'create_asset'
            """
        )
        row = cls.env.cr.fetchone()
        if row is not None and row[0] is None:
            cls.env.cr.execute(
                "ALTER TABLE account_account "
                "ALTER COLUMN create_asset SET DEFAULT 'no'"
            )

        cls.env.user.groups_id += cls.env.ref(
            "stock_account.group_stock_accounting_automatic"
        )

        stock_journal = cls.env["account.journal"].create(
            {"name": "Stock Journal Test", "code": "STJTST", "type": "general"}
        )
        account_vals = {"account_type": "asset_current", "reconcile": True}
        if "create_asset" in cls.env["account.account"]._fields:
            account_vals["create_asset"] = "no"
        stock_input_account = cls.env["account.account"].create(
            {**account_vals, "name": "Stock Input Test", "code": "StockInTst"}
        )
        stock_output_account = cls.env["account.account"].create(
            {**account_vals, "name": "Stock Output Test", "code": "StockOutTst"}
        )
        stock_valuation_account = cls.env["account.account"].create(
            {
                **account_vals,
                "name": "Stock Valuation Test",
                "code": "StockValTst",
            }
        )

        categ = cls.env["product.category"].create({"name": "Test Category"})
        categ.write(
            {
                "property_cost_method": "fifo",
                "property_valuation": "real_time",
                "property_stock_account_input_categ_id": stock_input_account.id,
                "property_stock_account_output_categ_id": stock_output_account.id,
                "property_stock_valuation_account_id": stock_valuation_account.id,
                "property_stock_journal": stock_journal.id,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Test Product",
                "is_storable": True,
                "categ_id": categ.id,
            }
        )

    def test_stock_picking_accounting_date(self):
        accounting_date = date.today() + timedelta(days=1)
        receipt = self.env["stock.picking"].create(
            {
                "location_id": self.supplier_location.id,
                "location_dest_id": self.stock_location.id,
                "partner_id": self.partner.id,
                "picking_type_id": self.env.ref("stock.picking_type_in").id,
                "accounting_date": accounting_date,
            }
        )
        move = self.env["stock.move"].create(
            {
                "picking_id": receipt.id,
                "name": "10 in",
                "location_id": self.supplier_location.id,
                "location_dest_id": self.stock_location.id,
                "product_id": self.product.id,
                "product_uom": self.uom_unit.id,
                "product_uom_qty": 10.0,
                "price_unit": 10,
            }
        )
        move._action_confirm()
        move.move_line_ids.quantity = 10.0
        move.picked = True
        move._action_done()
        self.assertEqual(
            move.stock_valuation_layer_ids.account_move_id.date, accounting_date
        )
