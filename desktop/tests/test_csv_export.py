"""Export maximum CAN histories without truncating or accepting malformed data."""
import csv
import io
import json
import unittest
from can_scope.csv_export import HEADER, MAX_BRIDGE_PAYLOAD, MAX_ROWS, rows


class CsvExportTests(unittest.TestCase):
    def frame(self, **changes):
        result = dict(kind=1, id=0x1fffffff, flags=1, ext=True, rtr=False,
                      dlc=8, data=[255]*8, receivedAt=2**53-1, seq=0xffffffff)
        result.update(changes)
        return result

    def test_maximum_history_fits_bridge_and_exports_every_row(self):
        payload = json.dumps([self.frame()]*MAX_ROWS, separators=(',', ':'))
        bridge = json.dumps([payload], separators=(',', ':'))
        self.assertGreater(len(bridge), 2_000_000)  # Previous bridge rejected it.
        self.assertLessEqual(len(bridge), MAX_BRIDGE_PAYLOAD)
        stream = io.StringIO(newline='')
        writer = csv.writer(stream); writer.writerow(HEADER); writer.writerows(rows(payload))
        stream.seek(0); exported = list(csv.reader(stream))
        self.assertEqual(len(exported), MAX_ROWS+1)
        self.assertEqual(exported[0], HEADER)
        self.assertEqual(exported[-1], ['1', '0x1fffffff', '1', '0', '8',
                                      'FF FF FF FF FF FF FF FF', str(2**53-1), str(0xffffffff)])

    def test_remote_and_failure_frames_preserve_fields(self):
        remote = self.frame(kind=2, id=0x7ff, ext=False, rtr=True, dlc=8, data=[])
        failed = self.frame(kind=3, id=0, ext=False, dlc=0, data=[])
        result = rows(json.dumps([remote, failed]))
        self.assertEqual(result[0][:6], [2, '0x7ff', 0, 1, 8, ''])
        self.assertEqual(result[1][:6], [3, '0x0', 0, 0, 0, ''])
        self.assertEqual(rows('[]'), [])

    def test_oversize_and_malformed_histories_fail(self):
        invalid = [None, '{}', json.dumps([self.frame()]*(MAX_ROWS+1)),
                   '[null]', json.dumps([self.frame(id=0x800, ext=False)]),
                   json.dumps([self.frame(data=[256]*8)]),
                   json.dumps([self.frame(data=[True]*8)]),
                   json.dumps([self.frame(data=[])]),
                   json.dumps([self.frame(dlc=9)]),
                   json.dumps([self.frame(seq=-1)]),
                   json.dumps([self.frame(receivedAt='=formula')]),
                   json.dumps([self.frame(ext=1)])]
        for payload in invalid:
            with self.subTest(payload_type=type(payload).__name__):
                with self.assertRaises(ValueError): rows(payload)
