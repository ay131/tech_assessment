# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    sd_credit_approval_threshold = fields.Float(
        string='Credit Approval Threshold (%)',
        default=90.0,
        help=(
            "When a sales order's credit exposure exceeds this percentage of "
            "the customer credit limit (but stays within the limit), the order "
            "is routed for managerial approval instead of being confirmed directly."
        ),
    )
