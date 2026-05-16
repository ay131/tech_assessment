# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase



# ──────────────────────────────────────────────────────────────────────────────
# 1. Pure-function commission math (no DB required for brackets)
# ──────────────────────────────────────────────────────────────────────────────

class TestSdCommissionMath(TransactionCase):
    """Test the standalone fallback commission function."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Bracket = cls.env['sd.commission.bracket']
        cls.company = cls.env.company
        cls.Bracket.search([]).unlink()

    def test_progressive_example_13000(self):
        # 1000*3% + 4000*5% + 5000*7% + 3000*10% = 30+200+350+300 = 880
        self.assertAlmostEqual(self.Bracket.compute_progressive_commission(13000, self.company), 880.0, places=2)

    def test_progressive_zero(self):
        self.assertEqual(self.Bracket.compute_progressive_commission(0, self.company), 0.0)

    def test_progressive_negative(self):
        self.assertEqual(self.Bracket.compute_progressive_commission(-500, self.company), 0.0)

    def test_progressive_small_amount(self):
        # 500 falls entirely within the first bracket: 500 * 3% = 15
        self.assertAlmostEqual(self.Bracket.compute_progressive_commission(500, self.company), 15.0, places=2)

    def test_progressive_exactly_1000(self):
        # 1000 * 3% = 30
        self.assertAlmostEqual(self.Bracket.compute_progressive_commission(1000, self.company), 30.0, places=2)

    def test_progressive_mid_bracket(self):
        # 3000: 1000*3% + 2000*5% = 30 + 100 = 130
        self.assertAlmostEqual(self.Bracket.compute_progressive_commission(3000, self.company), 130.0, places=2)

    def test_progressive_boundary_5000(self):
        # 5000: 1000*3% + 4000*5% = 30 + 200 = 230
        self.assertAlmostEqual(self.Bracket.compute_progressive_commission(5000, self.company), 230.0, places=2)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Configurable bracket model
# ──────────────────────────────────────────────────────────────────────────────

class TestSdCommissionBracket(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Bracket = cls.env['sd.commission.bracket']
        cls.company = cls.env.company

    def test_fallback_when_no_brackets(self):
        """With no bracket records the hardcoded fallback should kick in."""
        self.Bracket.search([]).unlink()
        result = self.Bracket.compute_progressive_commission(13000, self.company)
        self.assertAlmostEqual(result, 880.0, places=2)

    def test_custom_brackets(self):
        """Create custom flat-rate bracket and verify it is used."""
        self.Bracket.search([]).unlink()
        self.Bracket.create({
            'sequence': 10,
            'amount_from': 0,
            'amount_to': 0,   # unlimited
            'rate': 5.0,
            'company_id': self.company.id,
        })
        # 1000 * 5% = 50
        result = self.Bracket.compute_progressive_commission(1000, self.company)
        self.assertAlmostEqual(result, 50.0, places=2)

    def test_bracket_validation(self):
        """amount_to must be > amount_from when it is non-zero."""
        with self.assertRaises(ValidationError):
            self.Bracket.create({
                'sequence': 10,
                'amount_from': 500,
                'amount_to': 200,
                'rate': 3.0,
            })


# ──────────────────────────────────────────────────────────────────────────────
# 3. Credit decision logic
# ──────────────────────────────────────────────────────────────────────────────

class TestSdCreditDecision(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'SD Credit Tester',
            'credit_limit': 1000.0,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'SD Test Widget',
            'list_price': 100.0,
            'sale_ok': True,
            'type': 'consu',
        })

    def _create_order(self, qty=1):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': qty})],
        })

    def test_no_limit_skips_wall(self):
        self.partner.credit_limit = 0.0
        order = self._create_order(1)
        decision, _u, limit, _m = order._sd_evaluate_credit_on_confirm()
        self.assertEqual(decision, 'ok')
        self.assertEqual(limit, 0.0)

    def test_credit_limit_block(self):
        """Order exceeding credit limit must be blocked with a ValidationError."""
        order = self._create_order(11)  # 11 * 100 = 1100 > 1000
        decision, exposure, limit, _m = order._sd_evaluate_credit_on_confirm()
        self.assertEqual(decision, 'block')
        self.assertGreater(exposure, limit)

        with self.assertRaises(ValidationError):
            order.action_confirm()

        log = self.env['sd.credit.exposure.log'].search([('sale_order_id', '=', order.id)])
        self.assertEqual(len(log), 1)
        self.assertEqual(log.status, 'blocked')

    def test_credit_approval_band(self):
        """Order in 90%-100% of limit routes to waiting_approval."""
        order = self._create_order(9.5)  # 950, in [900..1000]
        decision, exposure, limit, _m = order._sd_evaluate_credit_on_confirm()
        self.assertEqual(decision, 'approval')

        order.action_confirm()
        self.assertEqual(order.state, 'waiting_approval')

    def test_successful_confirmation(self):
        """Order within limit confirms normally and generates logs."""
        order = self._create_order(5)  # 500
        decision, exposure, limit, _m = order._sd_evaluate_credit_on_confirm()
        self.assertEqual(decision, 'ok')

        order.action_confirm()
        self.assertEqual(order.state, 'sale')

        credit_log = self.env['sd.credit.exposure.log'].search([('sale_order_id', '=', order.id)])
        self.assertEqual(len(credit_log), 1)
        self.assertEqual(credit_log.status, 'success')

        commission_log = self.env['sd.commission.log'].search([('sale_order_id', '=', order.id)])
        self.assertEqual(len(commission_log), 1)
        self.assertGreater(commission_log.commission_amount, 0)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Configurable approval threshold
# ──────────────────────────────────────────────────────────────────────────────

class TestSdApprovalThreshold(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Threshold Tester',
            'credit_limit': 1000.0,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Threshold Widget',
            'list_price': 100.0,
            'sale_ok': True,
            'type': 'consu',
        })

    def _create_order(self, qty=1):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': qty})],
        })

    def test_custom_threshold_50_pct(self):
        """Lowering threshold to 50% should trigger approval for a 600 order (60% of 1000)."""
        self.env.company.sd_credit_approval_threshold = 50.0
        order = self._create_order(6)  # 600 → 60% of 1000
        decision, _e, _l, _m = order._sd_evaluate_credit_on_confirm()
        self.assertEqual(decision, 'approval')

    def test_default_threshold_90_pct(self):
        """With default 90%, a 600 order (60%) should pass directly."""
        self.env.company.sd_credit_approval_threshold = 90.0
        order = self._create_order(6)  # 600 → 60% of 1000
        decision, _e, _l, _m = order._sd_evaluate_credit_on_confirm()
        self.assertEqual(decision, 'ok')


# ──────────────────────────────────────────────────────────────────────────────
# 5. Approve / reject workflow
# ──────────────────────────────────────────────────────────────────────────────

class TestSdApproveReject(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Approval Tester',
            'credit_limit': 1000.0,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Approval Widget',
            'list_price': 100.0,
            'sale_ok': True,
            'type': 'consu',
        })
        # Create a Sales Manager user
        cls.manager_user = cls.env['res.users'].create({
            'name': 'Test Sales Manager',
            'login': 'sd_test_manager',
            'groups_id': [(6, 0, [
                cls.env.ref('sales_team.group_sale_manager').id,
                cls.env.ref('base.group_user').id,
            ])],
        })

    def _create_order(self, qty=1):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': qty})],
        })

    def test_reject_cancels_order(self):
        """Rejecting a waiting_approval order should cancel it."""
        order = self._create_order(9.5)  # 950 → approval band
        order.action_confirm()
        self.assertEqual(order.state, 'waiting_approval')

        order_as_manager = order.with_user(self.manager_user)
        order_as_manager.action_sd_credit_reject()
        self.assertEqual(order.state, 'cancel')

    def test_non_manager_cannot_approve(self):
        """A plain sales user should not be able to approve."""
        salesman_user = self.env['res.users'].create({
            'name': 'Test Salesman',
            'login': 'sd_test_salesman',
            'groups_id': [(6, 0, [
                self.env.ref('sales_team.group_sale_salesman').id,
                self.env.ref('base.group_user').id,
            ])],
        })
        order = self._create_order(9.5)
        order.action_confirm()
        self.assertEqual(order.state, 'waiting_approval')

        with self.assertRaises(AccessError):
            order.with_user(salesman_user).action_sd_credit_approve()


# ──────────────────────────────────────────────────────────────────────────────
# 6. Cron reconciliation
# ──────────────────────────────────────────────────────────────────────────────

class TestSdCronReconcile(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cron Tester',
            'credit_limit': 1000.0,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Cron Widget',
            'list_price': 100.0,
            'sale_ok': True,
            'type': 'consu',
        })

    def test_cron_resolves_when_limit_raised(self):
        """If customer limit is raised, the nightly cron should resolve the blocked log."""
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 11})],
        })
        # This should block
        with self.assertRaises(ValidationError):
            order.action_confirm()

        log = self.env['sd.credit.exposure.log'].search([('sale_order_id', '=', order.id)])
        self.assertEqual(log.status, 'blocked')

        # Raise the credit limit so exposure is now acceptable
        self.partner.credit_limit = 5000.0

        # Run the cron
        self.env['sd.credit.exposure.log'].cron_reconcile_blocked_exposure()

        log.invalidate_recordset()
        self.assertEqual(log.status, 'resolved')
        self.assertTrue(log.resolved_on)
