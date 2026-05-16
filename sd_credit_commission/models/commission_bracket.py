# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SdCommissionBracket(models.Model):
    _name = 'sd.commission.bracket'
    _description = 'Commission Bracket'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10, help='Determines bracket ordering (lowest first).')
    name = fields.Char(compute='_compute_name', store=True)
    amount_from = fields.Float(
        string='From Amount',
        digits=(16, 2),
        required=True,
        help='Lower bound of this bracket (inclusive).',
    )
    amount_to = fields.Float(
        string='To Amount',
        digits=(16, 2),
        help='Upper bound of this bracket (exclusive). Leave 0 for unlimited.',
    )
    rate = fields.Float(
        string='Commission Rate (%)',
        digits=(16, 4),
        required=True,
        help='Commission rate as a percentage (e.g. 3.0 means 3%).',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        index=True,
        help='Leave empty for a global bracket.',
    )

    _sql_constraints = [
        ('rate_positive', 'CHECK(rate >= 0)', 'Commission rate must not be negative.'),
    ]

    @api.depends('amount_from', 'amount_to', 'rate')
    def _compute_name(self):
        for rec in self:
            to_label = f'{rec.amount_to:,.2f}' if rec.amount_to else '∞'
            rec.name = f'{rec.amount_from:,.2f} – {to_label} @ {rec.rate}%'

    @api.constrains('amount_from', 'amount_to')
    def _check_bracket_range(self):
        for rec in self:
            if rec.amount_to and rec.amount_to <= rec.amount_from:
                raise ValidationError(
                    _('Bracket "To Amount" (%(to)s) must be greater than "From Amount" (%(from)s) or set to 0 for unlimited.')
                    % {'to': rec.amount_to, 'from': rec.amount_from}
                )

    @api.model
    def _get_brackets(self, company):
        """Return ordered list of (slice_width, rate_decimal) tuples for progressive calculation."""
        brackets = self.search([
            '|',
            ('company_id', '=', company.id),
            ('company_id', '=', False),
        ], order='sequence, id')
        if not brackets:
            return [
                (1000.0, 0.03),
                (4000.0, 0.05),
                (5000.0, 0.07),
                (0.0, 0.10),
            ]
        result = []
        for b in brackets:
            width = (b.amount_to - b.amount_from) if b.amount_to else 0.0
            result.append((width, b.rate / 100.0))
        return result

    @api.model
    def compute_progressive_commission(self, amount, company):
        """Compute progressive commission for *amount* using the configured brackets."""
        if amount <= 0:
            return 0.0
        brackets = self._get_brackets(company)
        total = 0.0
        consumed = 0.0
        for width, rate in brackets:
            if width > 0:
                part = min(max(amount - consumed, 0), width)
            else:
                part = max(amount - consumed, 0)
            total += part * rate
            consumed += part
            if consumed >= amount:
                break
        return total
