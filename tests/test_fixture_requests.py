"""Offline request ownership: no SWD, USB, or live fixture is opened."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'desktop')]
spec=importlib.util.spec_from_file_location('fixture_requests',ROOT/'scripts/test-dual-can-hardware.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
import motor_feedback_reference as feedback


class MemoryFixture(module.Fixture):
    def __init__(self):
        self.symbols={'can_bench':0x1000,'can_bitrate_bench':0x2000}
        self.memory={};self.writes=[]

    def read(self,address,count=1):
        return [self.memory.get(address+i*4,0)for i in range(count)]

    def write(self,address,values):
        self.writes.append((address,list(values)))
        for i,value in enumerate(values):self.memory[address+i*4]=value
        for bus in range(2):
            base=self.symbols['can_bench']+bus*52
            if address==base+16:
                self.memory[base+20]=values[0];self.memory[base+24]=0
                dest=self.symbols['can_bench']+(1-bus)*52
                for i in range(4):self.memory[dest+32+i*4]=self.memory[base+i*4]
                self.memory[dest+48]=self.memory.get(dest+48,0)+1
            rate=self.symbols['can_bitrate_bench']+bus*24
            if address==rate:
                self.memory.update({rate+8:values[1],rate+12:0,rate+16:values[0],rate+20:int(values[0]==0)})

    def handshake(self,base,request,completed,rate=False):
        offset=4 if rate else 16
        self.memory[base+offset]=request;self.memory[base+offset+4]=completed


class Requests(unittest.TestCase):
    def test_pending_single_preserves_payload_and_request(self):
        f=MemoryFixture();f.handshake(0x1000,7,6);before=dict(f.memory)
        with self.assertRaisesRegex(RuntimeError,'confirmed outcome'):
            f.transfer(0,0x6a0,8,0,7)
        self.assertEqual(f.writes,[]);self.assertEqual(f.memory,before)

    def test_pending_second_bus_prevents_both_frame_writes(self):
        f=MemoryFixture();f.handshake(0x1000,7,7);f.handshake(0x1034,20,19);before=dict(f.memory)
        with self.assertRaisesRegex(RuntimeError,'confirmed outcome'):
            f.transfer_pair(0x6a0,8,0,8)
        self.assertEqual(f.writes,[]);self.assertEqual(f.memory,before)

    def test_pending_rate_preserves_rate_and_request(self):
        f=MemoryFixture();f.handshake(0x2000,7,6,rate=True);before=dict(f.memory)
        with self.assertRaisesRegex(RuntimeError,'confirmed outcome'):
            f.set_rate(0,1000000)
        self.assertEqual(f.writes,[]);self.assertEqual(f.memory,before)

    def test_reused_payload_sequence_still_sends_new_requests(self):
        f=MemoryFixture();f.handshake(0x1000,0xffffffff,0xffffffff)
        for expected in [1,2]:
            row=f.transfer(0,0x6a0,8,0,88)
            self.assertTrue(row['exact_frame']);self.assertEqual(f.memory[0x1010],expected)
        self.assertEqual(f.memory[0x1034+48],2)

    def test_simultaneous_independent_generations(self):
        f=MemoryFixture();f.handshake(0x1000,7,7);f.handshake(0x1034,29,29)
        rows=f.transfer_pair(0x1abc000,8,1,8)
        self.assertEqual(len(rows),2);self.assertTrue(all(r['exact_frame']for r in rows))
        self.assertEqual(f.memory[0x1010],8);self.assertEqual(f.memory[0x1034+16],30)
        self.assertEqual([f.memory[base+48]for base in [0x1000,0x1034]],[1,1])

    def test_rate_wrap_and_consecutive_transition(self):
        f=MemoryFixture();f.handshake(0x2018,0xffffffff,0xffffffff,rate=True)
        f.set_rate(1,0);self.assertEqual(f.read(0x2018,6),[0,1,1,0,0,1])
        f.set_rate(1,1000000);self.assertEqual(f.read(0x2018,6),[1000000,2,2,0,1000000,0])

    def test_feedback_injection_preserves_an_unconfirmed_frame(self):
        f=MemoryFixture();f.handshake(0x1034,20,19);before=dict(f.memory)
        with self.assertRaisesRegex(RuntimeError,'confirmed outcome'):
            feedback.inject(f,1,0x680,bytes([1,73,0,0,0,128,255,255]))
        self.assertEqual(f.writes,[]);self.assertEqual(f.memory,before)

    def test_feedback_injection_uses_new_requests_across_wrap(self):
        f=MemoryFixture();f.handshake(0x1034,0xffffffff,0xffffffff)
        payload=bytes([1,73,0,0,0,128,255,255])
        for expected in [1,2]:
            feedback.inject(f,1,0x680,payload)
            self.assertEqual(f.memory[0x1034+16],expected)
        self.assertEqual(f.memory[0x1000+48],2)


if __name__=='__main__':unittest.main()
