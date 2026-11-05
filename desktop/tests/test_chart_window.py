"""Reproduce asynchronous compositor acknowledgments without opening a window."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from can_scope import app


class ChartWindowTests(unittest.TestCase):
    def window(self,maximized=True):
        state=app.Qt.WindowState
        window=SimpleNamespace(chart_window_state=None,chart_window_slot=None,
            chart_restore_generation=0,chart_restore_pending=None,visible=True,
            current=state.WindowMaximized if maximized else state.WindowNoState,
            maximizations=0,tabs=SimpleNamespace(index=0))
        window.tabs.currentIndex=lambda:window.tabs.index
        window.windowState=lambda:window.current
        window.isFullScreen=lambda:bool(window.current&state.WindowFullScreen)
        window.isMaximized=lambda:bool(window.current&state.WindowMaximized)
        window.isVisible=lambda:window.visible
        window.show=lambda:None
        window.setWindowState=lambda value:setattr(window,'current',value)
        window.showFullScreen=lambda:setattr(window,'current',state.WindowFullScreen)
        def maximize():
            window.maximizations+=1
            window.current=state.WindowMaximized
        window.showMaximized=maximize
        return window

    def test_maximized_baseline_survives_transient_normal_during_chart_handoff(self):
        window=self.window();callbacks=[]
        with patch.object(app.QTimer,'singleShot',side_effect=lambda delay,fn:callbacks.append((delay,fn))),patch.object(app,'isValid',return_value=True):
            app.Window.set_chart_fullscreen(window,1,True)
            app.Window.set_chart_fullscreen(window,1,False)
            # Compositor acknowledges leaving fullscreen as normal before the
            # deferred maximize request can complete. Open another chart now.
            window.current=app.Qt.WindowState.WindowNoState
            window.tabs.index=1
            app.Window.set_chart_fullscreen(window,2,True)
            for _,callback in callbacks[:]:callback()
            self.assertTrue(window.isFullScreen())
            self.assertEqual(window.chart_window_state,app.Qt.WindowState.WindowMaximized)
            self.assertEqual(window.maximizations,0)
            callbacks.clear()
            app.Window.set_chart_fullscreen(window,2,False)
            window.current=app.Qt.WindowState.WindowNoState
            for _,callback in callbacks:callback()
            self.assertTrue(window.isMaximized())
            self.assertEqual(window.maximizations,1)
            self.assertIsNone(window.chart_restore_pending)

    def test_normal_baseline_stays_normal_and_hidden_request_does_not_cancel_restore(self):
        window=self.window(False);callbacks=[]
        with patch.object(app.QTimer,'singleShot',side_effect=lambda delay,fn:callbacks.append((delay,fn))),patch.object(app,'isValid',return_value=True):
            app.Window.set_chart_fullscreen(window,1,True)
            app.Window.set_chart_fullscreen(window,1,False)
            generation=window.chart_restore_generation
            app.Window.set_chart_fullscreen(window,2,True)
            self.assertEqual(window.chart_restore_generation,generation)
            for _,callback in callbacks:callback()
            self.assertEqual(window.current,app.Qt.WindowState.WindowNoState)
            self.assertEqual(window.maximizations,0)
            self.assertIsNone(window.chart_restore_pending)

    def test_closed_or_deleted_window_is_not_reopened(self):
        for valid in [True,False]:
            window=self.window();callbacks=[]
            with patch.object(app.QTimer,'singleShot',side_effect=lambda delay,fn:callbacks.append((delay,fn))),patch.object(app,'isValid',return_value=valid):
                app.Window.set_chart_fullscreen(window,1,True)
                app.Window.set_chart_fullscreen(window,1,False)
                window.current=app.Qt.WindowState.WindowNoState
                window.visible=False
                window.chart_restore_generation+=1
                window.chart_restore_pending=None
                for _,callback in callbacks:callback()
                self.assertEqual(window.maximizations,0)


if __name__=='__main__':unittest.main()
