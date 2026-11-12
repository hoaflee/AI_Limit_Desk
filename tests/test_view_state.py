import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import storage
from main import LimitDesk
from providers import Snapshot
from view_state import (HISTORY_DAYS, allowed_chart_days, chart_window,
                        choose_custom, move_window, valid_visible)


class ViewStateTest(unittest.TestCase):
    today = date(2026, 11, 12)

    def test_defaults_hide_antigravity_only(self):
        flags = valid_visible(None)
        self.assertTrue(flags['codex'])
        self.assertTrue(flags['claude'])
        self.assertFalse(flags['antigravity'])
        self.assertTrue(valid_visible({'antigravity': True})['antigravity'])
        self.assertFalse(valid_visible({'custom_acme': False})['custom_acme'])
        self.assertFalse(valid_visible({'claude': 'false'})['antigravity'])

    def test_window_and_navigation(self):
        start,end = chart_window(7,None,self.today)
        self.assertEqual((start,end),(date(2026,11,6),self.today))
        previous = move_window(7,None,-1,self.today)
        self.assertEqual(previous,'2026-11-05')
        self.assertEqual(chart_window(7,previous,self.today),
                         (date(2026,10,30),date(2026,11,5)))
        self.assertIsNone(move_window(7,previous,+1,self.today))
        oldest = (self.today-timedelta(days=HISTORY_DAYS-1)).isoformat()
        start,end = chart_window(30,oldest,self.today)
        self.assertEqual(start.isoformat(),oldest)
        self.assertEqual((end-start).days+1,30)

    def test_select_exact_dates_and_reject_invalid(self):
        self.assertEqual(choose_custom('2026-11-01','2026-11-07',self.today),
                         (7,'2026-11-07'))
        self.assertEqual(choose_custom('2026-11-10','2026-11-12',self.today),
                         (3,None))
        for start,end in [('2026-11-13','2026-11-14'),
                          ('2026-10-01','2026-11-02'),
                          ('2025-01-01','2025-01-07'),
                          ('not a date','2026-11-12')]:
            with self.subTest(start=start),self.assertRaises(ValueError):
                choose_custom(start,end,self.today)
        self.assertEqual(allowed_chart_days('35'),30)

    def test_v2_settings_migrate_without_removing_preferences(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'settings.json').write_text(json.dumps({
                'theme':'light','interval':60,'opacity':0.84,
                'autostart':False,'compact':True,'view':'tokens'}),encoding='utf-8')
            with patch.multiple(storage, APP_DIR=root, CONFIG_PATH=root/'settings.json',
                                CUSTOM_PATH=root/'custom_usage.json',
                                LOGO_DIR=root/'logos'):
                old=storage.load_settings()
                self.assertEqual(old['theme'],'light')
                self.assertEqual(old['interval'],60)
                self.assertFalse(old['visible_providers']['antigravity'])
                self.assertTrue(old['visible_providers']['codex'])
                self.assertEqual(old['chart_days'],7)
                storage.save_settings(old)
                self.assertNotIn('view',json.loads((root/'settings.json').read_text()))
                self.assertEqual(storage.load_settings()['opacity'],0.84)

    def test_hidden_provider_excluded_from_quotas_and_chart(self):
        app=LimitDesk.__new__(LimitDesk)
        app.config={'visible_providers':{'codex':True,'claude':True,'antigravity':False,
                                         'custom_acme':False}}
        app.snapshots={
            'codex':Snapshot('codex','Codex','',status='ok',
                             daily_tokens={'2026-11-11':10}),
            'claude':Snapshot('claude','Claude','',status='ok'),
            'antigravity':Snapshot('antigravity','Antigravity','',status='ok',
                                   daily_tokens={'2026-11-11':1000}),
            'custom_acme':Snapshot('custom_acme','Acme','',status='ok',
                                   daily_tokens={'2026-11-11':2000}),
        }
        app.local_daily={'codex':{'2026-11-11':99},'claude':{'2026-11-11':20}}
        self.assertEqual([x.id for x in app.get_snapshots()],['codex','claude'])
        series=app.chart_series()
        self.assertEqual([x[0] for x in series],['codex','claude'])
        self.assertEqual(sum(row[3].get('2026-11-11',0) for row in series),30)
        app.config['visible_providers']['claude']=False
        self.assertEqual([x[0] for x in app.chart_series()],['codex'])


if __name__=='__main__':
    unittest.main()
