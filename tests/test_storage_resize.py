"""V5 config migration and manual resize persistence."""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import storage


class SizeSettingsTest(unittest.TestCase):
    def test_settings_persist_dimensions_after_v4_upgrade(self):
        with TemporaryDirectory() as td:
            cfg=Path(td)/'settings.json'
            cfg.write_text(json.dumps({'width':580,'height':855,'manual_height':True,
                                       'theme':'light','visible_providers':{'antigravity':False}}))
            with patch.object(storage,'CONFIG_PATH',cfg), patch.object(storage,'ensure_dir'):
                loaded=storage.load_settings()
            self.assertEqual(loaded['width'],580)
            self.assertEqual(loaded['height'],855)
            self.assertTrue(loaded['manual_height'])
            self.assertEqual(loaded['theme'],'light')
            self.assertFalse(loaded['visible_providers']['antigravity'])

    def test_pre_v5_settings_keep_auto_height(self):
        with TemporaryDirectory() as td:
            cfg=Path(td)/'settings.json'
            cfg.write_text(json.dumps({'theme':'dark','interval':30,'compact':False}))
            with patch.object(storage,'CONFIG_PATH',cfg), patch.object(storage,'ensure_dir'):
                loaded=storage.load_settings()
            self.assertEqual(loaded['width'],440)
            self.assertIsNone(loaded['height'])
            self.assertFalse(loaded['manual_height'])

    def test_invalid_size_does_not_crash(self):
        with TemporaryDirectory() as td:
            cfg=Path(td)/'settings.json'
            cfg.write_text(json.dumps({'width':'oops','height':{},'manual_height':True}))
            with patch.object(storage,'CONFIG_PATH',cfg), patch.object(storage,'ensure_dir'):
                loaded=storage.load_settings()
            self.assertEqual(loaded['width'],440)
            self.assertIsNone(loaded['height'])
            self.assertFalse(loaded['manual_height'])

    def _load(self,payload):
        with TemporaryDirectory() as td:
            cfg=Path(td)/'settings.json'
            cfg.write_text(json.dumps(payload))
            with patch.object(storage,'CONFIG_PATH',cfg), patch.object(storage,'ensure_dir'):
                return storage.load_settings()

    def test_show_chart_defaults_on_and_persists_off(self):
        self.assertIs(self._load({})['show_chart'],True)
        self.assertIs(self._load({'show_chart':False})['show_chart'],False)
        self.assertIs(self._load({'show_chart':True})['show_chart'],True)
        # Garbage must not silently hide the chart.
        self.assertIs(self._load({'show_chart':'no'})['show_chart'],True)

    def test_show_chart_is_saved(self):
        with TemporaryDirectory() as td:
            cfg=Path(td)/'settings.json'
            with patch.object(storage,'CONFIG_PATH',cfg), patch.object(storage,'ensure_dir'):
                storage.save_settings({**storage.DEFAULTS,'show_chart':False})
                self.assertIs(json.loads(cfg.read_text())['show_chart'],False)

if __name__=='__main__':
    unittest.main()


class MinWidthSettingsTest(unittest.TestCase):
    def _load(self, payload):
        with TemporaryDirectory() as td:
            cfg = Path(td)/'settings.json'
            cfg.write_text(json.dumps(payload))
            with patch.object(storage, 'CONFIG_PATH', cfg), patch.object(storage, 'ensure_dir'):
                return storage.load_settings()

    def test_defaults_keep_old_behaviour(self):
        loaded = self._load({})
        self.assertEqual(loaded['min_width'], 390)
        self.assertFalse(loaded['minimal'])
        self.assertEqual(loaded['mini_width'], 190)

    def test_custom_min_width_lowers_the_width_floor(self):
        loaded = self._load({'min_width': 300, 'width': 320})
        self.assertEqual(loaded['min_width'], 300)
        self.assertEqual(loaded['width'], 320)

    def test_width_never_below_min_width_and_values_are_clamped(self):
        loaded = self._load({'min_width': 500, 'width': 420})
        self.assertEqual(loaded['width'], 500)
        loaded = self._load({'min_width': 5, 'mini_width': 3, 'minimal': 'yes'})
        self.assertEqual(loaded['min_width'], 240)
        self.assertEqual(loaded['mini_width'], 110)
        self.assertFalse(loaded['minimal'])
        self.assertTrue(self._load({'minimal': True})['minimal'])

    def test_show_in_taskbar_defaults_off_and_must_be_literal_true(self):
        self.assertFalse(self._load({})['show_in_taskbar'])
        self.assertFalse(self._load({'show_in_taskbar': 'yes'})['show_in_taskbar'])
        self.assertTrue(self._load({'show_in_taskbar': True})['show_in_taskbar'])
