# -*- coding: utf-8 -*-
import logging
import time
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SdCreditExposureLogCron(models.Model):
    _inherit = 'sd.credit.exposure.log'

    @api.model
    def cron_reconcile_blocked_exposure(self):
        """Review blocked logs from the last 7 days; mark resolved when exposure fits the limit."""
        start_time = time.perf_counter()
        slot = fields.Datetime.now() - timedelta(days=7)

        blocked = self.search([
            ('status', '=', 'blocked'),
            ('checked_on', '>=', slot),
        ])
        reviewed = len(blocked)
        resolved = 0
        still_blocked = 0

        for log in blocked:
            order = log.sale_order_id
            if not order or not order.partner_id:
                still_blocked += 1
                continue
            try:
                exposure, limit, _msg = order._sd_compute_credit_exposure()
            except Exception as exc:  # noqa: BLE001 — cron must not abort the batch
                _logger.exception('SD credit cron: failed to recompute for log %s: %s', log.id, exc)
                still_blocked += 1
                continue

            if limit <= 0 or exposure <= limit:
                extra = '\n[Nightly reconciliation] Exposure %.2f now within limit %.2f.' % (exposure, limit)
                log.write({
                    'status': 'resolved',
                    'resolved_on': fields.Datetime.now(),
                    'message': (log.message or '') + extra,
                    'exposure_amount': exposure,
                })
                resolved += 1
            else:
                still_blocked += 1

        elapsed = time.perf_counter() - start_time
        summary = (
            'SD Credit Reconciliation — Blocked reviewed: %(rev)s — Resolved: %(ok)s — Still blocked: %(ko)s — '
            'Seconds: %(sec).2f'
        ) % {'rev': reviewed, 'ok': resolved, 'ko': still_blocked, 'sec': elapsed}
        _logger.info(summary)
        return True
