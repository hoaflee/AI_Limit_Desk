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

if __name__=='__main__':
    unittest.main()
