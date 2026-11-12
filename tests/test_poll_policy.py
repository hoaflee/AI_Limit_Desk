"""Polling cadence, 429 backoff and stale-card wording (no GUI, no network)."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import providers
from poll_policy import (PollPolicy, fit_line, format_age, format_wait,
                         stale_line_candidates)

T0 = datetime(2026, 11, 12, 7, 0, 0, tzinfo=timezone.utc)


def after(seconds):
    return T0 + timedelta(seconds=seconds)


class PollPolicyTest(unittest.TestCase):
    def test_claude_polled_slower_than_global_interval(self):
        p = PollPolicy()
        self.assertTrue(p.due('claude', 30, T0))
        p.mark_attempt('claude', T0)
        self.assertFalse(p.due('claude', 30, after(30)))
        self.assertFalse(p.due('claude', 30, after(179)))
        self.assertTrue(p.due('claude', 30, after(180)))

    def test_other_providers_keep_the_global_interval(self):
        p = PollPolicy()
        p.mark_attempt('codex', T0)
        self.assertFalse(p.due('codex', 30, after(29)))
        self.assertTrue(p.due('codex', 30, after(30)))
        # A user-selected 300 s interval is longer than the Claude minimum.
        p.mark_attempt('claude', T0)
        self.assertFalse(p.due('claude', 300, after(299)))
        self.assertTrue(p.due('claude', 300, after(300)))

    def test_rate_limit_backs_off_exponentially_and_is_capped(self):
        p = PollPolicy()
        waits = []
        for _ in range(6):
            until = p.record_rate_limit('claude', T0)
            waits.append((until - T0).total_seconds())
        self.assertEqual(waits, [300, 600, 1200, 1800, 1800, 1800])

    def test_backoff_blocks_even_a_manual_refresh(self):
        p = PollPolicy()
        p.mark_attempt('claude', T0)
        p.record_rate_limit('claude', T0)
        self.assertFalse(p.due('claude', 30, after(299)))
        self.assertFalse(p.due('claude', 30, after(299), manual=True))
        self.assertEqual(p.retry_in('claude', after(240)), 60)
        self.assertTrue(p.due('claude', 30, after(300)))
        self.assertIsNone(p.retry_in('claude', after(300)))

    def test_server_retry_after_is_honoured_when_longer(self):
        p = PollPolicy()
        self.assertEqual((p.record_rate_limit('claude', T0, 900) - T0).total_seconds(), 900)
        p = PollPolicy()
        self.assertEqual((p.record_rate_limit('claude', T0, 30) - T0).total_seconds(), 300)
        p = PollPolicy()
        self.assertEqual((p.record_rate_limit('claude', T0, 99999) - T0).total_seconds(), 3600)

    def test_success_resets_the_backoff(self):
        p = PollPolicy()
        p.record_rate_limit('claude', T0)
        p.record_rate_limit('claude', after(300))
        p.record_success('claude')
        self.assertIsNone(p.retry_in('claude', after(301)))
        self.assertEqual((p.record_rate_limit('claude', T0) - T0).total_seconds(), 300)

    def test_manual_refresh_skips_interval_but_not_the_minimum_gap(self):
        p = PollPolicy()
        p.mark_attempt('claude', T0)
        self.assertFalse(p.due('claude', 30, after(10), manual=True))
        self.assertTrue(p.due('claude', 30, after(30), manual=True))
        self.assertFalse(p.due('claude', 30, after(30)))

    def test_backoff_is_per_provider(self):
        p = PollPolicy()
        p.record_rate_limit('claude', T0)
        self.assertTrue(p.due('codex', 30, after(1)))


class StaleWordingTest(unittest.TestCase):
    def test_age_and_wait_formats(self):
        self.assertEqual(format_age(5), 'dưới 1 phút trước')
        self.assertEqual(format_age(9 * 60 + 20), '9 phút trước')
        self.assertEqual(format_age(3600), '1 giờ trước')
        self.assertEqual(format_age(3600 * 2 + 600), '2 giờ 10 phút trước')
        self.assertEqual(format_age(86400 * 3), '3 ngày trước')
        self.assertEqual(format_wait(1), '1 phút')
        self.assertEqual(format_wait(61), '2 phút')
        self.assertEqual(format_wait(300, compact=True), '5p')
        self.assertEqual(format_wait(3600), '1 giờ')

    def test_candidates_include_reason_age_and_retry(self):
        lines = stale_line_candidates('Bị giới hạn tần suất (HTTP 429)',
                                      T0 - timedelta(minutes=9), T0, retry_in=290)
        self.assertEqual(lines[0], 'Cũ 9 phút trước · Bị giới hạn tần suất (HTTP 429) · thử lại sau 5 phút')
        self.assertEqual(lines[1], 'Cũ 9 phút trước · Bị giới hạn tần suất (HTTP 429) · thử lại 5p')
        self.assertEqual(lines[-1], 'Cũ 9 phút trước · Bị giới hạn tần suất (HTTP 429)')

    def test_candidates_without_retry_or_timestamp(self):
        self.assertEqual(stale_line_candidates('Lỗi mạng', None, T0), ['Cũ · Lỗi mạng'])
        self.assertEqual(stale_line_candidates('', T0, T0), ['Cũ dưới 1 phút trước'])

    def test_fit_line_prefers_most_informative_that_fits_then_truncates(self):
        lines = ['aaaa bbbb cccc', 'aaaa bbbb', 'aaaa']
        self.assertEqual(fit_line(lines, len, 20), 'aaaa bbbb cccc')
        self.assertEqual(fit_line(lines, len, 10), 'aaaa bbbb')
        self.assertEqual(fit_line(['xxxxxxxxxxxx'], len, 6), 'xxxxx…')
        self.assertLessEqual(len(fit_line(['xxxxxxxxxxxx'], len, 1)), 1)


class ClaudeRateLimitTest(unittest.TestCase):
    class Response:
        def __init__(self, status, headers=None):
            self.status_code = status
            self.headers = headers or {}
            self.ok = status < 400

    def call(self, response):
        with patch.object(providers, 'requests') as fake, \
             patch.object(providers.Path, 'is_file', return_value=True), \
             patch.object(providers.Path, 'read_text',
                          return_value='{"claudeAiOauth":{"accessToken":"t"}}'):
            fake.RequestException = Exception
            fake.get.return_value = response
            return providers.claude()

    def test_http_429_raises_rate_limit_error_with_retry_after(self):
        with self.assertRaises(providers.RateLimitError) as ctx:
            self.call(self.Response(429, {'Retry-After': '120'}))
        self.assertEqual(ctx.exception.retry_after, 120)
        self.assertIsInstance(ctx.exception, providers.ProviderError)
        self.assertIn('429', str(ctx.exception))

    def test_retry_after_zero_or_garbage_means_unknown(self):
        for value in ('0', '', 'soon', None):
            with self.assertRaises(providers.RateLimitError) as ctx:
                self.call(self.Response(429, {'Retry-After': value} if value is not None else {}))
            self.assertIsNone(ctx.exception.retry_after, value)

    def test_other_http_errors_are_not_rate_limits(self):
        with self.assertRaises(providers.ProviderError) as ctx:
            self.call(self.Response(500))
        self.assertNotIsInstance(ctx.exception, providers.RateLimitError)


if __name__ == '__main__':
    unittest.main()
