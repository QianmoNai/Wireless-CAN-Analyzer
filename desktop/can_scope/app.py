"""Qt shell. Web UI only loads packaged files; native access is allowlisted."""
import asyncio
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from .rendering import configure
configure()
from shiboken6 import delete, isValid

from PySide6.QtCore import QObject, Signal, Slot, QTimer, QUrl, QFile, QIODevice, QStandardPaths, QEvent, Qt
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QToolBar, QFileDialog, QMessageBox
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineUrlRequestInterceptor, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from .engine import Channel
from .connection_store import load_connection, save_connection
from . import __version__
from . import csv_export

SOURCE = Path(__file__).resolve().parent
ASSETS = Path(sys._MEIPASS)/'assets' if hasattr(sys, '_MEIPASS') else SOURCE.parents[1]/'android/app/src/main/assets'


class Runtime(QObject):
    message = Signal(int, str, str)
    def __init__(self, folder):
        super().__init__()
        self.folder = folder
        self.diagnostic_log = None
        self.channels = {}
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name='CAN transports', daemon=True)
        self.thread.start()

    def add(self, slot):
        path = self.folder/f'connection-{slot}.json'
        try:
            saved = load_connection(path)
        except (OSError, ValueError):
            saved = {}
        def save(value):
            save_connection(path, value)
        async def create():
            channel = Channel(lambda kind, data: self.message.emit(slot, kind, json.dumps(data, ensure_ascii=False)), saved, save)
            if self.diagnostic_log is not None:
                channel.enable_wifi_diagnostics(self.diagnostic_log, slot)
            self.channels[slot] = channel
            channel.start()
        asyncio.run_coroutine_threadsafe(create(), self.loop).result(3)

    def call(self, slot, name, args):
        def execute():
            try:
                self.channels[slot].submit(name, args)
            except Exception as e:
                self.message.emit(slot, 'error', json.dumps(dict(message=str(e))))
        self.loop.call_soon_threadsafe(execute)

    def stop(self):
        async def shutdown():
            await asyncio.gather(*(c.close() for c in self.channels.values()), return_exceptions=True)
        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(8)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(2)


class LocalOnly(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        url = info.requestUrl()
        allowed = url.scheme() == 'file' and Path(url.toLocalFile()).resolve().parent == ASSETS.resolve()
        info.block(not allowed)


class Page(QWebEnginePage):
    def acceptNavigationRequest(self, url, kind, main):
        return url.scheme() == 'file' and Path(url.toLocalFile()).resolve() == (ASSETS/'index.html').resolve()

    def javaScriptConsoleMessage(self, level, message, line, source):
        # No payloads or credentials are printed to the terminal.
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.parent().js_errors.append((Path(source).name, line, message))


class Bridge(QObject):
    def __init__(self, window, slot):
        super().__init__(window)
        self.window, self.slot = window, slot

    @Slot(str, str)
    def call(self, name, payload):
        try:
            limit = csv_export.MAX_BRIDGE_PAYLOAD if name == 'exportCsv' else 2_000_000
            if len(payload) > limit:
                raise ValueError('操作数据过大')
            args = json.loads(payload)
            if not isinstance(args, list):
                raise ValueError('参数格式错误')
            if name in ('exportConfig', 'importConfig', 'exportCsv'):
                self.window.file_action(self.slot, name, args)
            elif name == 'home':
                self.window.views[self.slot].page().runJavaScript("showTab('motor')")
                self.window.runtime.call(self.slot, 'leaveDetail', [])
            elif name == 'setChartFullscreen':
                if len(args) != 1 or type(args[0]) is not bool:
                    raise ValueError('全屏状态无效')
                self.window.set_chart_fullscreen(self.slot, args[0])
            elif name in ('setMonitorVisible', 'connectionMode', 'networkState', 'wifiConfigure', 'connectLan', 'showDetail', 'leaveDetail', 'dashboard', 'scan', 'connect', 'disconnect', 'setPolling', 'send', 'manual', 'delivered', 'reconnect', 'deviceSettings', 'uartSend', 'uartCancel'):
                self.window.runtime.call(self.slot, name, args)
        except Exception as e:
            self.window.deliver(self.slot, 'error', json.dumps(dict(message=str(e))))


def script(name, source, point):
    item = QWebEngineScript()
    item.setName(name)
    item.setInjectionPoint(point)
    item.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    item.setSourceCode(source)
    return item


class Window(QMainWindow):
    def __init__(self, folder, smoke=False, diagnostic_log=None):
        super().__init__()
        self.setWindowTitle(f'CAN Scope Desktop {__version__} · Linux / Windows')
        self.resize(1320, 920)
        self.setMinimumSize(900, 650)
        self.smoke = smoke
        self.js_errors = []
        self.ready = set()
        self.channel_states = {}
        self.chart_window_state = None
        self.chart_window_slot = None
        self.chart_restore_generation = 0
        self.chart_restore_pending = None
        self.views = {}
        self.profiles = []
        self.folder = folder
        self.runtime = Runtime(folder)
        self.runtime.diagnostic_log = diagnostic_log
        self.runtime.message.connect(self.deliver)
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self.channel_changed)
        self.setCentralWidget(self.tabs)
        toolbar = QToolBar('通道')
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        toolbar.addAction('＋ 添加通道', self.add_channel)
        toolbar.addAction('重新连接', lambda: self.runtime.call(self.tabs.currentIndex()+1, 'reconnect', []))
        toolbar.addAction('断开所有', lambda: [self.runtime.call(slot, 'disconnect', []) for slot in self.views])
        toolbar.addSeparator()
        smaller = toolbar.addAction('缩小', lambda: self.adjust_zoom(-1))
        smaller.setShortcuts([QKeySequence('Ctrl+-')])
        smaller.setToolTip('缩小当前通道显示（Ctrl+-）')
        self.zoom_action = toolbar.addAction('100%', lambda: self.set_active_zoom(1.0))
        self.zoom_action.setShortcuts([QKeySequence('Ctrl+0')])
        self.zoom_action.setToolTip('恢复当前通道 100% 显示（Ctrl+0）')
        larger = toolbar.addAction('放大', lambda: self.adjust_zoom(1))
        larger.setShortcuts([QKeySequence('Ctrl++'), QKeySequence('Ctrl+=')])
        larger.setToolTip('放大当前通道显示（Ctrl++ 或 Ctrl+=）')
        self.statusBar().showMessage('USB / 蓝牙 / Wi-Fi · 最多四通道 · 关闭窗口自动断开')
        self.setStyleSheet('QMainWindow,QTabWidget::pane{background:#0a0f19;color:#f2f5fa} QToolBar,QStatusBar{background:#111a28;color:#91a0b7;border:0;padding:7px} QToolButton{background:#172234;border:1px solid #304059;border-radius:6px;color:#e2ecf5;padding:8px 16px} QToolButton:hover{background:#29403e} QTabBar::tab{background:#172234;color:#91a0b7;padding:12px 30px} QTabBar::tab:selected{color:#65e6cb;background:#203333}')
        count = 1
        try:
            count = max(1, min(4, int((folder/'channels.txt').read_text())))
        except (OSError, ValueError):
            pass
        for _ in range(count):
            self.add_channel()

    def set_chart_fullscreen(self, slot, enabled):
        if enabled:
            if slot != self.tabs.currentIndex()+1:
                return
            if self.chart_window_state is None or (self.chart_window_slot is None and
                    self.chart_restore_pending is None and not self.isFullScreen()):
                self.chart_window_state = self.windowState()
            self.chart_restore_generation += 1
            self.chart_restore_pending = None
            self.chart_window_slot = slot
            self.showFullScreen()
            # A tab switch can restore and enter in the same event turn.
            # Reconcile after Qt processes the preceding window-state change.
            QTimer.singleShot(0, lambda: self.showFullScreen()
                              if self.chart_window_slot == slot and self.tabs.currentIndex()+1 == slot else None)
        elif self.chart_window_slot == slot and self.chart_window_state is not None:
            # Preserve this baseline across Qt's asynchronous restore. A new
            # tab can enter while windowState still reports the old fullscreen.
            state = self.chart_window_state
            self.chart_window_slot = None
            self.chart_restore_generation += 1
            generation = self.chart_restore_generation
            self.chart_restore_pending = generation
            self.setWindowState(state)
            self.show()
            # XWayland may acknowledge leaving fullscreen by clearing the
            # simultaneous maximize request. Preserve the baseline while this
            # restore is pending, including a quick handoff to another chart.
            def finish_restore():
                if (isValid(self) and self.isVisible() and
                        self.chart_restore_generation == generation and
                        self.chart_window_slot is None):
                    if (state & Qt.WindowState.WindowMaximized and
                            not self.isFullScreen() and not self.isMaximized()):
                        self.showMaximized()
                    self.chart_restore_pending = None
            QTimer.singleShot(100, finish_restore)

    def changeEvent(self, event):
        super().changeEvent(event)
        slot = getattr(self, 'chart_window_slot', None)
        if event.type() == QEvent.Type.WindowStateChange and slot is not None and not self.isFullScreen():
            QTimer.singleShot(0, lambda: self.showFullScreen()
                              if self.chart_window_slot == slot and self.tabs.currentIndex()+1 == slot else None)

    def channel_changed(self, index):
        previous = self.chart_window_slot
        if previous is not None and previous != index+1:
            self.set_chart_fullscreen(previous, False)
            # The native tab change owns this transition. A hidden page's
            # later visibility callback cannot close another tab's fullscreen.
            self.views[previous].page().runJavaScript('closeDetailChartFullscreen(false)')
        for slot in self.views:
            self.runtime.call(slot, 'setChannelVisible', [slot == index+1])
        self.refresh_connection_status()
        self.refresh_zoom()

    def refresh_zoom(self):
        view = self.views.get(self.tabs.currentIndex()+1)
        if view is not None:
            self.zoom_action.setText(f'{round(view.zoomFactor()*100)}%')

    def set_active_zoom(self, factor):
        view = self.views.get(self.tabs.currentIndex()+1)
        if view is not None:
            view.setZoomFactor(max(.75, min(2.0, factor)))
            self.refresh_zoom()

    def adjust_zoom(self, direction):
        view = self.views.get(self.tabs.currentIndex()+1)
        if view is None:
            return
        current = view.zoomFactor()
        steps = (.75, .9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0)
        choices = [step for step in steps if step > current+.001] if direction > 0 else [step for step in reversed(steps) if step < current-.001]
        if choices:
            self.set_active_zoom(choices[0])

    def refresh_connection_status(self):
        slot = self.tabs.currentIndex()+1
        state = self.channel_states.get(slot)
        if state:
            mode = {'usb':'USB直连', 'ble':'蓝牙', 'wifi':'Wi-Fi'}.get(state.get('transport'), '')
            self.statusBar().showMessage(f'通道 {slot} · {mode} · '+state.get('message', ''))

    def add_channel(self):
        if len(self.views) >= 4:
            return
        slot = len(self.views)+1
        self.runtime.add(slot)
        profile = QWebEngineProfile(f'can-scope-{slot}', self)
        profile.setPersistentStoragePath(str(self.folder/f'web-{slot}'))
        profile.setCachePath(str(self.folder/f'cache-{slot}'))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        interceptor = LocalOnly(profile)
        profile.setUrlRequestInterceptor(interceptor)
        profile.interceptor = interceptor
        profile.downloadRequested.connect(lambda download: download.cancel())
        self.profiles.append(profile)
        view = QWebEngineView(self)
        page = Page(profile, self)
        view.setPage(page)
        page.setBackgroundColor(QColor('#0a0f19'))
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        channel = QWebChannel(page)
        bridge = Bridge(self, slot)
        channel.registerObject('desktop', bridge)
        page.setWebChannel(channel)
        view.channel, view.bridge = channel, bridge
        resource = QFile(':/qtwebchannel/qwebchannel.js')
        resource.open(QIODevice.OpenModeFlag.ReadOnly)
        bootstrap = bytes(resource.readAll()).decode()
        bootstrap += '\nwindow.CAN_SCOPE_VERSION=' + json.dumps(__version__) + ';\n'
        bootstrap += '''
        const desktopQueue=[];let desktopNative=null;
        window.CanAndroid=new Proxy({}, {get:(_,name)=>(...args)=>{if(desktopNative)desktopNative.call(name,JSON.stringify(args));else if(desktopQueue.length<64)desktopQueue.push([name,args]);}});
        new QWebChannel(qt.webChannelTransport, channel=>{desktopNative=channel.objects.desktop;for(const [name,args] of desktopQueue)desktopNative.call(name,JSON.stringify(args));desktopQueue.length=0;});
        '''
        page.scripts().insert(script('native', bootstrap, QWebEngineScript.InjectionPoint.DocumentCreation))
        page.scripts().insert(script('desktop', (SOURCE/'desktop.js').read_text('utf8'), QWebEngineScript.InjectionPoint.DocumentReady))
        self.views[slot] = view
        self.tabs.addTab(view, f'通道 {slot}')
        self.runtime.call(slot, 'setChannelVisible', [slot == self.tabs.currentIndex()+1])
        view.loadStarted.connect(lambda: self.ready.discard(slot))
        view.loadFinished.connect(lambda ok: self.loaded(slot, ok))
        view.setUrl(QUrl.fromLocalFile(str(ASSETS/'index.html')))
        (self.folder/'channels.txt').write_text(str(slot))

    def loaded(self, slot, ok):
        if ok:
            self.ready.add(slot)
            self.runtime.call(slot, 'networkState', [])
            self.runtime.call(slot, 'delivered', [])
            self.views[slot].page().runJavaScript("CanAndroid.setMonitorVisible(!$('view-live').hidden||!$('view-uart').hidden)")
        else:
            self.statusBar().showMessage('界面加载失败，请检查资源文件是否完整')

    @Slot(int, str, str)
    def deliver(self, slot, kind, payload):
        if slot not in self.ready:
            return
        expression = f'window.onNative&&window.onNative({json.dumps(kind)},{payload})'
        self.views[slot].page().runJavaScript(expression, lambda _: self.runtime.call(slot, 'delivered', [json.loads(payload)['delivery']]) if kind in ('packets', 'motorSamples') else None)
        if kind == 'state':
            value = json.loads(payload)
            self.channel_states[slot] = value
            mode = {'usb':'USB', 'ble':'蓝牙', 'wifi':'Wi-Fi'}.get(value.get('transport'), '')
            self.tabs.setTabText(slot-1, f'通道 {slot}'+(f' · {mode} · 已连接' if value['value'] == 'ready' else ''))
            self.refresh_connection_status()
        elif kind == 'error':
            self.statusBar().showMessage(json.loads(payload)['message'], 8000)

    def file_action(self, slot, name, args):
        if name == 'importConfig':
            path, _ = QFileDialog.getOpenFileName(self, '导入当前通道配置', '', 'CAN Scope 配置 (*.json)')
            if path:
                if Path(path).stat().st_size > 1048576:
                    raise ValueError('配置文件超过 1 MB')
                text = Path(path).read_text('utf-8-sig')
                self.views[slot].page().runJavaScript('desktopImport('+json.dumps(text)+')')
            return
        extension = 'csv' if name == 'exportCsv' else 'json'
        if len(args) != 1 or type(args[0]) is not str:
            raise ValueError('导出参数无效')
        value = csv_export.rows(args[0]) if extension == 'csv' else json.loads(args[0])
        path, _ = QFileDialog.getSaveFileName(self, '导出当前通道', f'can-scope-channel-{slot}.{extension}', f'*.{extension}')
        if not path:
            return
        if extension == 'json':
            Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf8')
        else:
            with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
                writer = csv.writer(stream)
                writer.writerow(csv_export.HEADER)
                writer.writerows(value)
        self.statusBar().showMessage('已导出 '+path, 5000)

    def closeEvent(self, event):
        # Any queued window-state reconciliation must stop with this window.
        self.chart_window_slot = None
        self.chart_restore_generation += 1
        self.chart_restore_pending = None
        # Stop WebChannel callbacks before joining workers or destroying Chromium.
        for view in self.views.values():
            view.page().setWebChannel(None)
            view.stop()
        self.runtime.message.disconnect(self.deliver)
        try:
            self.runtime.stop()
        except Exception:
            pass
        event.accept()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke', type=Path, help='offline UI smoke test; writes PNG and report')
    parser.add_argument('--wifi-diag-log', type=Path, help='Linux only: private numeric-only per-second UART/Wi-Fi diagnostics JSONL')
    options = parser.parse_args()
    if options.smoke and sys.stderr is not None:
        import faulthandler
        faulthandler.enable()
        faulthandler.dump_traceback_later(30, repeat=True)
    app = QApplication(sys.argv[:1])
    app.setOrganizationName('Qianmo')
    app.setApplicationName('CAN Scope Desktop')
    temp = tempfile.TemporaryDirectory(prefix='can-desktop-smoke-', ignore_cleanup_errors=True) if options.smoke else None
    folder = Path(temp.name) if temp else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        folder.chmod(0o700)
    diagnostic_log = None
    if options.wifi_diag_log:
        if sys.platform != 'linux' or options.smoke:
            parser.error('--wifi-diag-log 仅用于 Linux 真实 Wi-Fi 会话')
        from .wifi_diagnostics import NumericLog
        diagnostic_log = NumericLog(options.wifi_diag_log)
    window = Window(folder, bool(options.smoke), diagnostic_log)
    window.show()
    if options.smoke:
        options.smoke.parent.mkdir(parents=True, exist_ok=True)
        from .smoke import run
        run(app, window, options.smoke)
    code = app.exec()
    if options.smoke:
        print("Smoke event loop exited", flush=True)
    # Destroy pages before profiles so Chromium releases its storage handles.
    for view in window.views.values():
        page = view.page()
        if isValid(page):
            delete(page)
    for profile in window.profiles:
        if isValid(profile):
            delete(profile)
    if temp:
        temp.cleanup()
    if diagnostic_log:
        diagnostic_log.close()
    return code
