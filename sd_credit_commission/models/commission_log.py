# -*- coding: utf-8 -*-
from odoo import fields, models


class SdCommissionLog(models.Model):
    _name = 'sd.commission.log'
    _description = 'Commission Log'

    sale_order_id = fields.Many2one('sale.order', string='Sale Order', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', string='Salesperson', required=True, index=True)
    untaxed_amount = fields.Float(string='Untaxed Amount', digits=(16, 2))
    commission_amount = fields.Float(string='Commission', digits=(16, 2))
    commission_rate = fields.Float(string='Effective Rate %', digits=(16, 4), help='Blended percentage for reporting.')
    confirmed_on = fields.Datetime(required=True, default=fields.Datetime.now)

    company_id = fields.Many2one(
        'res.company',
        related='sale_order_id.company_id',
        store=True,
        readonly=True,
    )

    _sql_constraints = [
        ('commission_non_negative', 'CHECK(commission_amount >= 0)', 'Commission must not be negative.'),
        ('unique_order_commission', 'UNIQUE(sale_order_id)', 'A commission entry already exists for this order.'),
    ]
