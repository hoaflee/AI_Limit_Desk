"""GUI smoke tests require an available Tk display (use xvfb-run on Linux)."""
import os
import sys
import unittest
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from providers import Quota, Snapshot
from storage import DEFAULTS
import main


@unittest.skipUnless(os.environ.get('DISPLAY') or sys.platform == 'win32',
                     'No Tk display available')
class WidgetUiTest(unittest.TestCase):
    def setUp(self):
        cfg=deepcopy(DEFAULTS)
        cfg.update({'x':150,'y':120,'opacity':1.0})
        self.patches=[
            patch.object(main,'load_settings',return_value=cfg),
            patch.object(main,'load_cache',return_value={}),
            patch.object(main,'save_settings'),
            patch.object(main.LimitDesk,'refresh',lambda app,manual=False: None),
            patch.object(main.LimitDesk,'fetch_logos',lambda app: None),
        ]
        for p in self.patches:
            p.start()
        self.app=main.LimitDesk()

    def tearDown(self):
        self.app.quit()
        for p in reversed(self.patches):
            p.stop()

    def test_chart_is_inline_and_hidden_service_absent(self):
        app=self.app
        day=date.today().isoformat()
        app.snapshots={
            'codex':Snapshot('codex','Codex','CLI',status='ok',
                    quotas=[Quota('5-hour limit',89),Quota('Weekly limit',67)],
                    daily_tokens={day:300}),
            'claude':Snapshot('claude','Claude Code','CLI',status='ok',
                    quotas=[Quota('Current session',70),Quota('Weekly limit',33)]),
            'antigravity':Snapshot('antigravity','Antigravity','IDE',status='ok',
                    quotas=[Quota('Model 1',99)],daily_tokens={day:9999})}
        app.local_daily={'claude':{day:200},'codex':{day:10}}
        app.draw();app.root.update()
        self.assertEqual([s.id for s in app.get_snapshots()],['codex','claude'])
        self.assertEqual(sum(s[3].get(day,0) for s in app.chart_series()),500)
        self.assertEqual(len(app.chart_bars),7)
        chart_min_y=min(r[1] for r in app.chart_regions)
        last_quota_bottom=max(r[3] for r in app.link_regions)
        self.assertGreater(chart_min_y,last_quota_bottom)
        app.cycle_chart_days()
        self.assertEqual(len(app.chart_bars),14)
        app.shift_chart(-1)
        self.assertNotEqual(app.config['chart_end'],None)
        app.shift_chart(1)
        self.assertEqual(app.config['chart_end'],None)

    def test_full_content_has_no_scroll_or_compact_mode(self):
        app=self.app
        app.work_area=lambda:(0,0,1366,728)
        app.snapshots={
            'codex':Snapshot('codex','Codex','CLI',status='ok',
                quotas=[Quota('5-hour limit',80),Quota('Weekly limit',55)]),
            'claude':Snapshot('claude','Claude Code','CLI',status='ok',
                quotas=[Quota('Current session',75),Quota('Weekly limit',45)]),
        }
        app.local_daily={'claude':{date.today().isoformat():22000}}
        app.draw();app.root.update_idletasks()
        self.assertEqual(app.layout.name,'medium')
        self.assertEqual(app.root.winfo_height(),app.height)
        self.assertEqual(app.body.winfo_height(),app.doc_height)
        self.assertLessEqual(app.body.bbox('all')[3],app.body.winfo_height())
        self.assertEqual(app.body.bind('<MouseWheel>'),'')
        self.assertFalse(hasattr(app,'scroll'))
        self.assertFalse(app.config['compact'])
        app.cycle_chart_days()
        self.assertEqual(len(app.chart_bars),14)
        self.assertLessEqual(app.body.bbox('all')[3],app.body.winfo_height())

    def test_window_resizes_automatically_on_quota_change(self):
        app=self.app
        app.work_area=lambda:(0,0,1920,1040)
        start_height=app.height
        app.snapshots={
            'codex':Snapshot('codex','Codex','CLI',status='ok',
                quotas=[Quota('5-hour limit',80),Quota('Weekly limit',55)]),
            'claude':Snapshot('claude','Claude Code','CLI',status='ok',
                quotas=[Quota('Current session',75),Quota('Weekly limit',45)]),
        }
        app.draw();app.root.update_idletasks()
        self.assertGreater(app.height,start_height)
        self.assertEqual(app.height,774)
        app.config['visible_providers']['claude']=False
        app.draw();app.root.update_idletasks()
        self.assertLess(app.height,774)
        self.assertLessEqual(app.body.bbox('all')[3],app.body.winfo_height())


    def test_drag_bottom_right_resizes_and_keeps_chart_visible(self):
        app=self.app
        app.work_area=lambda:(0,0,1600,1000)
        app.snapshots={
            'codex':Snapshot('codex','Codex','CLI',status='ok',
                quotas=[Quota('5-hour limit',80),Quota('Weekly limit',55)]),
            'claude':Snapshot('claude','Claude Code','CLI',status='ok',
                quotas=[Quota('5-hour session',75),Quota('Weekly \u00b7 all models',45)]),
        }
        app.draw();app.root.update()
        w,h=app.width,app.height
        from types import SimpleNamespace
        mouse=SimpleNamespace(x=w-3,y=h-3,x_root=700,y_root=850)
        app.header_click(mouse)
        app.header_drag(SimpleNamespace(x_root=820,y_root=920))
        app.header_release(SimpleNamespace())
        app.root.update_idletasks()
        self.assertGreater(app.width,w)
        self.assertGreater(app.height,h)
        self.assertEqual(app.config['width'],app.width)
        self.assertEqual(app.config['height'],app.height)
        self.assertTrue(app.config['manual_height'])
        self.assertEqual(app.body.winfo_height(),app.doc_height)
        self.assertLessEqual(app.body.bbox('all')[3],app.body.winfo_height())

    def test_old_claude_cache_is_cleaned_at_startup(self):
        from providers import dedupe_claude_quotas
        vals=[Quota('5-hour session',65),Quota('session',65),
              Quota('Weekly \u00b7 all models',82),Quota('weekly',82),Quota('Weekly \u00b7 Fable',100)]
        self.assertEqual(len(dedupe_claude_quotas(vals)),3)

    def test_codex_logo_uses_bundled_brand_asset_not_chatgpt_cache(self):
        app=self.app
        path=app.logo_path('codex')
        self.assertTrue(path.is_file())
        self.assertEqual(path.name,'openai-blossom-dark.png')
        from PIL import Image
        with Image.open(path) as im:
            self.assertEqual(im.size,(256,256))
        app.toggle_theme()
        self.assertTrue(app.logo_path('codex').is_file())
        self.assertEqual(app.logo_path('codex').name,'openai-blossom-light.png')

    def test_stale_card_shows_age_reason_and_retry_time(self):
        from datetime import timedelta
        from providers import now_utc
        app=self.app
        app.snapshots={
            'claude':Snapshot('claude','Claude Code','CLI',status='stale',
                quotas=[Quota('5-hour session',65),Quota('Weekly · all models',81)],
                message='Bị giới hạn tần suất (HTTP 429)',
                updated=now_utc()-timedelta(minutes=9,seconds=20)),
        }
        app.policy.record_rate_limit('claude',now_utc())
        app.draw();app.root.update_idletasks()
        shown=[app.body.itemcget(i,'text') for i in app.body.find_all()
               if app.body.type(i)=='text']
        line=[t for t in shown if t.startswith('Cũ ')]
        self.assertEqual(len(line),1,shown)
        self.assertIn('9 phút trước',line[0])
        self.assertIn('429',line[0])
        self.assertNotIn('lần cập nhật mới chưa thành công',' '.join(shown))
        # Nothing to redraw until the minute-granular wording changes.
        self.assertEqual(app.stale_signature(),app.stale_signature_drawn)
        app.snapshots['claude'].updated-=timedelta(minutes=1)
        self.assertNotEqual(app.stale_signature(),app.stale_signature_drawn)

    def test_settings_toggle_provider(self):
        app=self.app
        app.show_settings()
        self.assertTrue(app.settings_root.winfo_exists())
        self.assertFalse(app.is_visible('antigravity'))
        app.settings_click(SimpleNamespace(x=303,y=184))
        self.assertTrue(app.is_visible('antigravity'))
        app.settings_click(SimpleNamespace(x=303,y=146))
        self.assertFalse(app.is_visible('claude'))
        self.assertEqual([s.id for s in app.get_snapshots()],['codex','antigravity'])


@unittest.skipUnless(os.environ.get('DISPLAY') or sys.platform == 'win32',
                     'No Tk display available')
class RefreshSchedulingTest(unittest.TestCase):
    """Runs the real refresh()/pump() loop against fake providers."""

    def setUp(self):
        from poll_policy import PollPolicy
        from providers import RateLimitError
        cfg=deepcopy(DEFAULTS)
        cfg.update({'x':150,'y':120,'opacity':1.0})
        self.calls={'codex':0,'claude':0}

        def fake_codex():
            self.calls['codex']+=1
            return Snapshot('codex','Codex','CLI',[Quota('5-hour limit',90)])

        def fake_claude():
            self.calls['claude']+=1
            raise RateLimitError('Bị giới hạn tần suất (HTTP 429)')

        self.patches=[
            patch.object(main,'load_settings',return_value=cfg),
            patch.object(main,'load_cache',return_value={}),
            patch.object(main,'save_settings'),
            patch.object(main,'save_cache'),
            patch.object(main.LimitDesk,'fetch_logos',lambda app: None),
            patch.object(main,'codex',fake_codex),
            patch.object(main,'claude',fake_claude),
            patch.object(main.LocalTokenHistory,'read',lambda *a,**k: {}),
        ]
        for p in self.patches:
            p.start()
        self.app=main.LimitDesk()
        # Same rules as production, but no 30 s floor so the test is instant.
        self.app.policy=PollPolicy(manual_gap=0)
        self.app.snapshots['claude']=Snapshot('claude','Claude Code','CLI',
            quotas=[Quota('5-hour session',65)])

    def tearDown(self):
        # Pending Tk timers of a destroyed root would fire during the next test's update().
        for ident in self.app.root.tk.call('after','info'):
            self.app.root.after_cancel(ident)
        self.app.quit()
        for p in reversed(self.patches):
            p.stop()

    def run_refresh(self,**kw):
        import time
        app=self.app
        app.refresh(**kw)
        deadline=time.monotonic()+10
        while app.busy and time.monotonic()<deadline:
            app.root.update()
            time.sleep(0.02)
        self.assertFalse(app.busy,'refresh did not finish')

    def test_429_keeps_old_data_as_stale_and_stops_polling_until_backoff_ends(self):
        app=self.app
        self.run_refresh(manual=True)
        self.assertEqual(self.calls,{'codex':1,'claude':1})
        claude_snap=app.snapshots['claude']
        self.assertEqual(claude_snap.status,'stale')
        self.assertEqual(claude_snap.quotas[0].left,65)
        self.assertIn('429',claude_snap.message)
        self.assertEqual(app.snapshots['codex'].status,'ok')
        self.assertAlmostEqual(app.policy.retry_in('claude',main.now_utc()),300,delta=5)
        shown=' '.join(app.body.itemcget(i,'text') for i in app.body.find_all()
                       if app.body.type(i)=='text')
        self.assertIn('429',shown)
        self.assertIn('thử lại',shown)
        # Nothing is due yet for a scheduled refresh (Codex polled <30 s ago).
        self.run_refresh()
        self.assertEqual(self.calls,{'codex':1,'claude':1})
        # A manual refresh updates Codex again but leaves the rate-limited Claude alone.
        self.run_refresh(manual=True)
        self.assertEqual(self.calls,{'codex':2,'claude':1})

    def test_claude_is_not_polled_at_the_global_30s_interval(self):
        from datetime import timedelta
        app=self.app
        app.policy=type(app.policy)()      # production defaults, including manual gap
        self.run_refresh()
        self.assertEqual(self.calls,{'codex':1,'claude':1})
        # Pretend 31 s passed and the backoff is over: only codex is due again;
        # claude has a 180 s minimum interval.
        for ident in ('codex','claude'):
            app.policy._last_attempt[ident]-=timedelta(seconds=31)
        app.policy._blocked_until.clear()
        self.run_refresh()
        self.assertEqual(self.calls,{'codex':2,'claude':1})
        # After 180 s claude is polled again.
        for ident in ('codex','claude'):
            app.policy._last_attempt[ident]-=timedelta(seconds=150)
        self.run_refresh()
        self.assertEqual(self.calls,{'codex':3,'claude':2})


if __name__=='__main__':
    unittest.main()
