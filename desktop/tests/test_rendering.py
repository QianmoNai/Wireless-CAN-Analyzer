import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from can_scope.rendering import configure
class RenderingTests(unittest.TestCase):
    def test_linux_keeps_other_flags_and_uses_software(self):
        with patch('sys.platform','linux'), patch.dict(os.environ, {'QTWEBENGINE_CHROMIUM_FLAGS':'--disable-logging'}, clear=True):
            configure();configure()
            self.assertEqual(os.environ['QTWEBENGINE_CHROMIUM_FLAGS'],'--disable-logging --disable-gpu --js-flags=--max-old-space-size=256')
            self.assertEqual(os.environ['QT_QUICK_BACKEND'],'software')
    def test_explicit_js_flags_are_preserved(self):
        with patch('sys.platform','linux'), patch.dict(os.environ, {'QTWEBENGINE_CHROMIUM_FLAGS':'--js-flags="--max-old-space-size=512 --trace-gc"'}, clear=True):
            configure();configure()
            self.assertEqual(os.environ['QTWEBENGINE_CHROMIUM_FLAGS'],'--js-flags="--max-old-space-size=512 --trace-gc" --disable-gpu')
    def test_other_platform_and_explicit_opt_out(self):
        for platform,opt in [('win32',''),('linux','1')]:
            with patch('sys.platform',platform), patch.dict(os.environ, {'CAN_SCOPE_HARDWARE_RENDERING':opt}, clear=True):
                configure()
                self.assertNotIn('QTWEBENGINE_CHROMIUM_FLAGS',os.environ)
                self.assertNotIn('QT_QUICK_BACKEND',os.environ)
