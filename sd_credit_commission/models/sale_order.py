# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
class SaleOrder(models.Model):
    _inherit = 'sale.order'

    state = fields.Selection(
        selection_add=[('waiting_approval', 'Waiting Approval')],
        ondelete={'waiting_approval': 'set default'},
    )
    sd_exposure_amount = fields.Float(
        string='Credit Exposure',
        compute='_compute_sd_credit_display',
        digits=(16, 2),
        help='Receivable + other confirmed uninvoiced orders + this order (company currency).',
    )
    sd_credit_limit_amount = fields.Float(
        string='Credit Limit',
        compute='_compute_sd_credit_display',
        digits=(16, 2),
    )
    sd_credit_utilization_pct = fields.Float(
        string='Limit Utilization %',
        compute='_compute_sd_credit_display',
        digits=(16, 2),
    )
    sd_commission_preview = fields.Monetary(
        string='Commission (preview)',
        compute='_compute_sd_commission_preview',
        currency_field='currency_id',
    )
    sd_commission_rate_preview = fields.Float(
        string='Effective Commission %',
        compute='_compute_sd_commission_preview',
        digits=(16, 4),
    )
    sd_needs_manager_approval = fields.Boolean(
        string='In approval band',
        compute='_compute_sd_credit_display',
    )
    sd_credit_log_count = fields.Integer(compute='_compute_sd_smart_counts')
    sd_commission_log_count = fields.Integer(compute='_compute_sd_smart_counts')

    @api.depends('partner_id', 'amount_total', 'amount_untaxed', 'currency_id', 'company_id', 'order_line', 'state')
    def _compute_sd_credit_display(self):
        for order in self:
            if not order.partner_id:
                order.sd_exposure_amount = 0.0
                order.sd_credit_limit_amount = 0.0
                order.sd_credit_utilization_pct = 0.0
                order.sd_needs_manager_approval = False
                continue
            exposure, limit, utilization, in_band = order._sd_get_credit_numbers()
            order.sd_exposure_amount = exposure
            order.sd_credit_limit_amount = limit
            order.sd_credit_utilization_pct = utilization
            order.sd_needs_manager_approval = in_band

    @api.depends('amount_untaxed', 'currency_id', 'company_id')
    def _compute_sd_commission_preview(self):
        CommBracket = self.env['sd.commission.bracket']
        for order in self:
            untaxed_ccy = order._sd_convert_to_company_currency(order.amount_untaxed or 0.0, order.currency_id)
            commission_ccy = CommBracket.compute_progressive_commission(untaxed_ccy, order.company_id)
            order.sd_commission_rate_preview = (commission_ccy / untaxed_ccy * 100.0) if untaxed_ccy else 0.0
            if order.currency_id and order.company_id.currency_id:
                order.sd_commission_preview = order.company_id.currency_id._convert(
                    commission_ccy,
                    order.currency_id,
                    order.company_id,
                    fields.Date.context_today(order),
                )
            else:
                order.sd_commission_preview = commission_ccy

    def _compute_sd_smart_counts(self):
        credit = self.env['sd.credit.exposure.log']
        commission = self.env['sd.commission.log']
        for order in self:
            order.sd_credit_log_count = credit.search_count([('sale_order_id', '=', order.id)])
            order.sd_commission_log_count = commission.search_count([('sale_order_id', '=', order.id)])

    def _sd_get_approval_threshold(self):
        """Return the approval threshold as a decimal (e.g. 0.9 for 90%)."""
        self.ensure_one()
        pct = self.company_id.sd_credit_approval_threshold
        return (pct / 100.0) if pct else 0.9

    def _sd_convert_to_company_currency(self, amount, from_currency):
        self.ensure_one()
        if not from_currency or not self.company_id:
            return amount
        return from_currency._convert(
            amount, self.company_id.currency_id, self.company_id, fields.Date.context_today(self)
        )

    def _sd_compute_credit_exposure(self):
        """Return exposure, credit limit (0 = unset / unlimited), and a short message."""
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id.with_company(self.company_id).sudo()
        limit = partner.credit_limit or 0.0
        receivable = partner.credit or 0.0

        SaleOrder = self.env['sale.order']
        domain = [
            ('id', '!=', self.id),
            ('partner_id.commercial_partner_id', '=', partner.id),
            ('state', '=', 'sale'),
            ('company_id', '=', self.company_id.id),
        ]
        others = SaleOrder.search(domain)
        uninvoiced = sum(
            self._sd_convert_to_company_currency(so.amount_to_invoice or 0.0, so.currency_id) for so in others
        )
        current_total = self._sd_convert_to_company_currency(self.amount_total or 0.0, self.currency_id)
        exposure = receivable + uninvoiced + current_total
        msg = _('Receivable: %(r)s + Uninvoiced (others): %(u)s + This order: %(c)s') % {
            'r': receivable,
            'u': uninvoiced,
            'c': current_total,
        }
        return exposure, limit, msg

    def _sd_get_credit_numbers(self):
        self.ensure_one()
        exposure, limit, _msg = self._sd_compute_credit_exposure()
        threshold = self._sd_get_approval_threshold()
        if limit > 0:
            utilization = (exposure / limit) * 100.0
            in_band = exposure > threshold * limit and exposure <= limit
        else:
            utilization = 0.0
            in_band = False
        return exposure, limit, utilization, in_band

    def _sd_evaluate_credit_on_confirm(self):
        """Return 'ok', 'approval', or 'block' for current order (draft/sent)."""
        self.ensure_one()
        exposure, limit, msg = self._sd_compute_credit_exposure()
        threshold = self._sd_get_approval_threshold()
        if limit <= 0:
            return 'ok', exposure, limit, msg
        if exposure > limit:
            return 'block', exposure, limit, msg
        if exposure > threshold * limit:
            return 'approval', exposure, limit, msg
        return 'ok', exposure, limit, msg

    def _sd_persist_blocked_credit_log(self, exposure, limit, message):
        """Persist using a dedicated cursor so data survives the rollback caused by ValidationError.

        If a blocked log for this sale order already exists, update it instead of
        creating a duplicate (respects UNIQUE constraint on sale_order_id).
        """
        partner = self.partner_id.commercial_partner_id
        vals = {
            'partner_id': partner.id,
            'sale_order_id': self.id,
            'exposure_amount': exposure,
            'credit_limit': limit,
            'status': 'blocked',
            'message': message,
            'checked_on': fields.Datetime.now(),
            'user_id': self.env.uid,
        }
        registry = self.env.registry
        with registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            CreditLog = env['sd.credit.exposure.log']
            existing = CreditLog.search([('sale_order_id', '=', self.id)], limit=1)
            if existing:
                existing.write({
                    'exposure_amount': exposure,
                    'credit_limit': limit,
                    'status': 'blocked',
                    'message': message,
                    'checked_on': fields.Datetime.now(),
                })
            else:
                CreditLog.create(vals)
            cr.commit()

    def _sd_create_credit_success_log(self, exposure, limit, detail_message):
        existing = self.env['sd.credit.exposure.log'].search([('sale_order_id', '=', self.id)], limit=1)
        vals = {
            'partner_id': self.partner_id.commercial_partner_id.id,
            'sale_order_id': self.id,
            'exposure_amount': exposure,
            'credit_limit': limit,
            'status': 'success',
            'message': detail_message,
            'checked_on': fields.Datetime.now(),
            'user_id': self.env.uid,
        }
        if existing:
            existing.write(vals)
        else:
            self.env['sd.credit.exposure.log'].create(vals)

    def _sd_compute_commission(self, untaxed_in_company_currency):
        """Compute commission using configurable brackets for this order's company."""
        return self.env['sd.commission.bracket'].compute_progressive_commission(
            untaxed_in_company_currency, self.company_id
        )

    def _sd_create_commission_log(self):
        self.ensure_one()
        if self.env['sd.commission.log'].search_count([('sale_order_id', '=', self.id)]):
            return
        untaxed_ccy = self._sd_convert_to_company_currency(self.amount_untaxed or 0.0, self.currency_id)
        commission_ccy = self._sd_compute_commission(untaxed_ccy)
        rate = (commission_ccy / untaxed_ccy * 100.0) if untaxed_ccy else 0.0
        self.env['sd.commission.log'].create({
            'sale_order_id': self.id,
            'user_id': (self.user_id or self.env.user).id,
            'untaxed_amount': untaxed_ccy,
            'commission_amount': commission_ccy,
            'commission_rate': rate,
            'confirmed_on': fields.Datetime.now(),
        })

    def _confirmation_error_message(self):
        if self.env.context.get('sd_credit_force_confirm') and self.state == 'waiting_approval':
            if any(
                not line.display_type
                and not line.is_downpayment
                and not line.product_id
                for line in self.order_line
            ):
                return _("Some order lines are missing a product, you need to correct them before going further.")
            return False
        return super()._confirmation_error_message()

    def action_confirm(self):
        force = self.env.context.get('sd_credit_force_confirm')
        to_super = self.env['sale.order']
        for order in self:
            if order.state == 'waiting_approval':
                if not force:
                    raise UserError(
                        _('Order %(name)s is waiting for credit approval. Use Approve or Reject.')
                        % {'name': order.name}
                    )
                to_super |= order
                continue
            if order.state in ('draft', 'sent'):
                err = order._confirmation_error_message()
                if err:
                    raise UserError(err)
                decision, exposure, limit, msg = order._sd_evaluate_credit_on_confirm()
                if decision == 'block':
                    block_message = _('Credit exposure %(exp)s exceeds limit %(lim)s.') % {'exp': exposure, 'lim': limit}
                    order._sd_persist_blocked_credit_log(exposure, limit, f'{msg}\n{block_message}')
                    raise ValidationError(block_message)
                if decision == 'approval':
                    order.write({'state': 'waiting_approval'})
                    continue
                to_super |= order
                continue
            to_super |= order

        if not to_super:
            return True
        res = super(SaleOrder, to_super).action_confirm()
        for order in to_super:
            expo, lim, detail = order._sd_compute_credit_exposure()
            order._sd_create_credit_success_log(expo, lim, _('Confirmed: %(detail)s') % {'detail': detail})
            order._sd_create_commission_log()
        return res

    def action_sd_credit_approve(self):
        if not self.env.user.has_group('sales_team.group_sale_manager'):
            raise AccessError(_("Only Sales Managers can approve credit for this workflow."))
        if any(o.state != 'waiting_approval' for o in self):
            raise UserError(_("Only orders in 'Waiting Approval' can be approved."))
        return self.with_context(sd_credit_force_confirm=True).action_confirm()

    def action_sd_credit_reject(self):
        if not self.env.user.has_group('sales_team.group_sale_manager'):
            raise AccessError(_("Only Sales Managers can reject these orders."))
        if any(o.state != 'waiting_approval' for o in self):
            raise UserError(_("Only orders in 'Waiting Approval' can be rejected."))
        self.write({'state': 'cancel'})
        return True

    def action_view_sd_credit_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Credit Logs'),
            'res_model': 'sd.credit.exposure.log',
            'view_mode': 'list,form',
            'domain': [('sale_order_id', '=', self.id)],
            'context': {'default_sale_order_id': self.id, 'default_partner_id': self.partner_id.id},
        }

    def action_view_sd_commission_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Commission Logs'),
            'res_model': 'sd.commission.log',
            'view_mode': 'list,form',
            'domain': [('sale_order_id', '=', self.id)],
        }
