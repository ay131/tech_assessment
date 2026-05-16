# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SdCreditExposureLog(models.Model):
    _name = 'sd.credit.exposure.log'
    _description = 'Credit Exposure Log'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'checked_on desc, id desc'

    name = fields.Char(required=True, readonly=True, copy=False, default='New')
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, index=True,
        tracking=True,
    )
    sale_order_id = fields.Many2one('sale.order', string='Sale Order', ondelete='set null', index=True)
    exposure_amount = fields.Float(string='Exposure', digits=(16, 2))
    credit_limit = fields.Float(string='Credit Limit', digits=(16, 2))
    status = fields.Selection(
        [('success', 'Success'), ('blocked', 'Blocked'), ('resolved', 'Resolved')],
        required=True,
        tracking=True,
    )
    message = fields.Text()
    checked_on = fields.Datetime(required=True, default=fields.Datetime.now)
    resolved_on = fields.Datetime()
    user_id = fields.Many2one('res.users', string='Responsible', default=lambda self: self.env.user, readonly=True)

    company_id = fields.Many2one(
        'res.company',
        related='sale_order_id.company_id',
        store=True,
        readonly=True,
    )

    _sql_constraints = [
        ('exposure_positive', 'CHECK(exposure_amount >= 0)', 'Exposure amount must not be negative.'),
        ('unique_sale_order_id', 'UNIQUE(sale_order_id)', 'Exposure Amount Waiting to be Resolved.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('sd.credit.exposure.log') or 'New'
        return super().create(vals_list)
