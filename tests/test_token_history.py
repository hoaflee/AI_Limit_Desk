import json
import os
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from token_history import (LocalTokenHistory, parse_claude_file, parse_codex_file,
                           parse_codex_server_daily, _total_claude)


class TokenHistoryTest(unittest.TestCase):
    def write_rows(self, file: Path, rows: list[dict]):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('\n'.join(json.dumps(x) for x in rows) + '\n', encoding='utf-8')

    def test_codex_cumulative_only_positive_delta(self):
        with TemporaryDirectory() as folder:
            file = Path(folder) / 'rollout-123.jsonl'
            mk = lambda ts, tokens: {'timestamp': ts, 'type': 'event_msg', 'payload': {
                'type':'token_count','info': {'last_token_usage': {'total_tokens': 12345},
                                             'total_token_usage': {'total_tokens': tokens}}}}
            self.write_rows(file, [mk('2026-11-10T14:20:00+07:00', 100),
                                   mk('2026-11-10T14:21:00+07:00', 100),
                                   mk('2026-11-11T10:00:00+07:00', 280)])
            self.assertEqual(parse_codex_file(file), {'2026-11-10':100,'2026-11-11':180})

    def test_claude_cache_and_stream_dedup(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'example.jsonl'
            def mk(u):
                return {'type':'assistant','timestamp':'2026-11-12T09:00:00+07:00',
                        'message':{'id':'msg_42','usage':u}}
            self.write_rows(path, [mk({'input_tokens':10,'output_tokens':20,'cache_read_input_tokens':40,
                                        'cache_creation_input_tokens':30}),
                                   mk({'input_tokens':10,'output_tokens':25,'cache_read_input_tokens':40,
                                        'cache_creation_input_tokens':30}),
                                   {'type':'user','message':{'content':'Never store actual prompt content'}}])
            self.assertEqual(parse_claude_file(path),{'2026-11-12':105})

    def test_server_days(self):
        self.assertEqual(parse_codex_server_daily({'dailyUsageBuckets': [
            {'startDate':'2026-11-10','tokens':120}, {'startDate':'bad','tokens':100},
            {'startDate':'2026-11-10','tokens':30}]}), {'2026-11-10':150})

    def test_history_cache_and_missing_source(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            path = root/'.codex'/'sessions'/'2026'/'11'/'12'/'rollout-x.jsonl'
            path.parent.mkdir(parents=True)
            self.write_rows(path,[{'type':'event_msg','timestamp':'2026-11-12T10:00:00+07:00',
                                  'payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':100}}}}])
            with patch.dict(os.environ,{'CODEX_HOME':str(root/'.codex'),
                                       'CLAUDE_CONFIG_DIR':str(root/'.claude')}):
                history=LocalTokenHistory(root)
                self.assertEqual(history.read(today=date(2026,11,12))['codex']['2026-11-12'],100)
                self.assertEqual(history.read(today=date(2026,11,12))['claude'],{})
                self.assertEqual(len(history._file_cache),1)


if __name__=='__main__':
    unittest.main()
