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
