"""Physical USB read encoding/decoding only; fake exchange, no hardware."""
import struct
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from usb_management import LocalUsb


class MonitorDropRead(unittest.TestCase):
    def device(self,version=1,reserved=0):
        device=object.__new__(LocalUsb)
        self.requests=[]
        def exchange(request,event):
            self.requests.append((bytes(request),event))
            return bytes((1,27,version,reserved))+struct.pack('<III',17,23,0xffffffff)+request[16:20]
        device.exchange=exchange
        return device

    def test_read_correlated_versioned_components(self):
        self.assertEqual(self.device().monitor_drops(),dict(bridge_delivery_dropped=17,can_rx_queue_dropped=23,dual_dropped=0xffffffff))
        self.assertEqual(len(self.requests),1)
        request,event=self.requests[0]
        self.assertEqual(request[:16],bytes((1,0x1c))+bytes(14))
        self.assertEqual(len(request),20)
        self.assertNotEqual(request[16:],bytes(4))
        self.assertEqual(event,27)

    def test_unknown_reply_never_becomes_zero_counters(self):
        for version,reserved in [(0,0),(2,0),(1,1)]:
            with self.subTest(version=version,reserved=reserved),self.assertRaises(ValueError):
                self.device(version,reserved).monitor_drops()
            self.assertEqual(len(self.requests),1)

    def test_missing_response_is_not_replayed(self):
        device=self.device()
        def missing(request,event):
            self.requests.append((bytes(request),event))
            raise TimeoutError('Outcome unknown')
        device.exchange=missing
        with self.assertRaises(TimeoutError):device.monitor_drops()
        self.assertEqual(len(self.requests),1)


if __name__=='__main__':unittest.main()
