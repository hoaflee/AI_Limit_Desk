import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from providers import (Quota, Snapshot, dt_parse, friendly_window, load_custom,
                       parse_antigravity, parse_claude, parse_codex)


class CollectorParsersTest(unittest.TestCase):
    def test_codex_two_windows(self):
        d = {"rateLimits": {"primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": 1780000000},
                            "secondary": {"usedPercent": 79, "windowDurationMins": 10080, "resetsAt": 1780400000}}}
        q = parse_codex(d)
        self.assertEqual([(x.label, x.left) for x in q], [("5-hour limit", 100), ("Weekly limit", 21)])
        self.assertIsNotNone(q[0].reset_at)

    def test_codex_multi_bucket(self):
        d = {"rateLimits": {"primary": {"usedPercent": 25, "windowDurationMins": 300}},
             "rateLimitsByLimitId": {
                 "codex": {"primary": {"usedPercent": 10, "windowDurationMins": 300}},
                 "codex_other": {"primary": {"usedPercent": 70, "windowDurationMins": 60}}
             }}
        q = parse_codex(d)
        self.assertEqual(len(q), 2)
        self.assertTrue(any(v.left == 30 for v in q))

    def test_claude_standard_and_scoped(self):
        d = {"five_hour": {"utilization": 17, "resets_at": "2026-11-12T10:00:00Z"},
             "seven_day": {"utilization": 16, "resets_at": "2026-11-18T09:00:00+07:00"},
             "seven_day_opus": None,
             "limits": [{"group": "weekly", "percent": 49, "scope": {"model": {"display_name": "Fable"}}}]}
        q = parse_claude(d)
        self.assertEqual([(v.label, v.left) for v in q],
                         [("5-hour session", 83), ("Weekly · all models", 84), ("Weekly · Fable", 51)])


    def test_claude_aliases_not_counted_twice(self):
        # Reproduces user report: 5-hour session + session, weekly + weekly.
        payload = {
            'five_hour': {'utilization':35,'resets_at':'2026-11-12T10:10:00Z'},
            'seven_day': {'utilization':18,'resets_at':'2026-11-18T09:00:00Z'},
            'limits': [
                {'group':'session','percent':35,'resets_at':'2026-11-12T10:10:00Z'},
                {'group':'weekly','percent':18,'resets_at':'2026-11-18T09:00:00Z'},
                {'group':'weekly','scope':{'model':{'display_name':'Fable'}},'percent':0},
            ],
        }
        quotas = parse_claude(payload)
        self.assertEqual([(q.label,q.left) for q in quotas],
                         [('5-hour session',65),('Weekly \u00b7 all models',82),
                          ('Weekly \u00b7 Fable',100)])

    def test_v4_stale_cache_claude_aliases_deduped(self):
        from providers import dedupe_claude_quotas
        old = [Quota('5-hour session',65),Quota('Weekly \u00b7 all models',82),
               Quota('session',65),Quota('weekly',82),Quota('Weekly \u00b7 Fable',100)]
        self.assertEqual([q.label for q in dedupe_claude_quotas(old)],
                         ['5-hour session','Weekly \u00b7 all models','Weekly \u00b7 Fable'])

    def test_distinct_claude_models_are_not_merged(self):
        payload = {'five_hour': {'utilization':5}, 'seven_day':{'utilization':12},
                   'seven_day_fable':{'utilization':14},
                   'seven_day_sonnet':{'utilization':55},
                   'limits':[{'group':'weekly','scope':{'model':{'display_name':'Sonnet'}},'percent':55}]}
        quotas = parse_claude(payload)
        self.assertEqual(len(quotas),4)

    def test_antigravity(self):
        q, plan = parse_antigravity({"userStatus": {
            "planStatus": {"planInfo": {"planName": "Pro"}},
            "cascadeModelConfigData": {"clientModelConfigs": [
                {"label": "Gemini", "quotaInfo": {"remainingFraction": 0.385, "resetTime": "2026-11-12T14:00:00Z"}},
                {"label": "Claude", "quotaInfo": {"remainingFraction": 0}},
                {"label": "Internal model"}
            ]}}})
        self.assertEqual(plan, "Pro")
        self.assertEqual([(v.label, v.left) for v in q], [("Gemini", 38.5), ("Claude", 0.0)])

    def test_cache_roundtrip(self):
        snap = Snapshot("x", "Test", "Manual", [Quota("Daily", 76, datetime.now(timezone.utc))])
        restored = Snapshot.from_dict(snap.to_dict())
        self.assertEqual(restored.status, "stale")
        self.assertEqual(restored.quotas[0].left, 76)

    def test_custom_feed(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom.json"
            path.write_text(json.dumps({"providers": [{"name": "Cursor", "windows": [
                {"label": "Daily", "remaining_percent": 31, "reset_at": "2026-11-13T00:00:00+07:00"},
                {"label": "Weekly", "used_percent": 72},
            ]}]}), encoding="utf-8")
            result = load_custom(path)
            self.assertEqual(result[0].quotas[1].left, 28)

    def test_dates(self):
        self.assertIsNone(dt_parse("bad-date"))
        self.assertEqual(friendly_window(10080), "Weekly limit")


if __name__ == "__main__":
    unittest.main()


class LocalIntegrationTest(unittest.TestCase):
    @unittest.skipIf(__import__("sys").platform == "win32", "Fake script has POSIX shebang")
    def test_codex_app_server_handshake(self):
        import os
        import stat
        from unittest.mock import patch
        from providers import codex
        with TemporaryDirectory() as temp:
            file = Path(temp) / "fake_codex"
            file.write_text('''#!/usr/bin/env python3
import json,sys
for line in sys.stdin:
 m=json.loads(line)
 if m.get("method")=="initialize":
  print(json.dumps({"id":m["id"],"result":{"userAgent":"test"}}),flush=True)
 if m.get("method")=="account/rateLimits/read":
  print(json.dumps({"id":m["id"],"result":{"rateLimits":{"primary":{"usedPercent":40,"windowDurationMins":300}}}}),flush=True)
 if m.get("method")=="account/usage/read":
  print(json.dumps({"id":m["id"],"result":{"dailyUsageBuckets":[{"startDate":"2026-11-12","tokens":1234}]}}),flush=True)
''', encoding="utf-8")
            file.chmod(file.stat().st_mode | stat.S_IEXEC)
            with patch.dict(os.environ, {"AI_LIMIT_CODEX_PATH": str(file)}):
                snapshot = codex()
        self.assertEqual(snapshot.id, "codex")
        self.assertEqual(snapshot.quotas[0].left, 60.0)
        self.assertEqual(snapshot.daily_tokens.get('2026-11-12'), 1234)

    def test_antigravity_only_contacts_loopback(self):
        from unittest.mock import patch
        import providers
        class FakeResponse:
            ok = True
            def json(self):
                return {"userStatus": {"cascadeModelConfigData": {
                    "clientModelConfigs": [{"label": "Gemini", "quotaInfo": {"remainingFraction": 0.42}}]
                }}}
        with patch.object(providers, "_ls_candidates", return_value=[(1234, "sensitive-csrf", [31415])]), \
             patch.object(providers.requests, "post", return_value=FakeResponse()) as request:
            result = providers.antigravity()
            self.assertEqual(result.quotas[0].left, 42)
            self.assertIn("127.0.0.1:31415", request.call_args[0][0])
            self.assertEqual(request.call_args[1]["headers"]["x-codeium-csrf-token"], "sensitive-csrf")
