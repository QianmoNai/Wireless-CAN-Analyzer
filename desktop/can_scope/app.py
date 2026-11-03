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

from PySide6.QtCore import QObject, Signal, Slot, QTimer, QUrl, QFile, QIODevice, QStandardPaths
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QToolBar, QFileDialog, QMessageBox
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineUrlRequestInterceptor, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from .engine import Channel
from .connection_store import load_connection, save_connection
from . import __version__

SOURCE = Path(__file__).resolve().parent
ASSETS = Path(sys._MEIPASS)/'assets' if hasattr(sys, '_MEIPASS') else SOURCE.parents[1]/'android/app/src/main/assets'


class Runtime(QObject):
    message = Signal(int, str, str)
    def __init__(self, folder):
        super().__init__()
        self.folder = folder
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
            if len(payload) > 2_000_000:
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
                self.window.showFullScreen() if args[0] else self.window.showNormal()
            elif name in ('setMonitorVisible',):
                pass
            elif name in ('connectionMode', 'networkState', 'wifiConfigure', 'connectLan', 'showDetail', 'leaveDetail', 'dashboard', 'scan', 'connect', 'disconnect', 'setPolling', 'send', 'manual', 'delivered', 'reconnect', 'deviceSettings', 'uartSend', 'uartCancel'):
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
    def __init__(self, folder, smoke=False):
        super().__init__()
        self.setWindowTitle(f'CAN Scope Desktop {__version__} · Linux / Windows')
        self.resize(1320, 920)
        self.setMinimumSize(900, 650)
        self.smoke = smoke
        self.js_errors = []
        self.ready = set()
        self.views = {}
        self.profiles = []
        self.folder = folder
        self.runtime = Runtime(folder)
        self.runtime.message.connect(self.deliver)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        toolbar = QToolBar('通道')
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        toolbar.addAction('＋ 添加通道', self.add_channel)
        toolbar.addAction('重新连接', lambda: self.runtime.call(self.tabs.currentIndex()+1, 'reconnect', []))
        toolbar.addAction('断开所有', lambda: [self.runtime.call(slot, 'disconnect', []) for slot in self.views])
        self.statusBar().showMessage('蓝牙 / Wi-Fi · 最多四通道 · 关闭窗口自动断开')
        self.setStyleSheet('QMainWindow,QTabWidget::pane{background:#0a0f19;color:#f2f5fa} QToolBar,QStatusBar{background:#111a28;color:#91a0b7;border:0;padding:7px} QToolButton{background:#172234;border:1px solid #304059;border-radius:6px;color:#e2ecf5;padding:8px 16px} QToolButton:hover{background:#29403e} QTabBar::tab{background:#172234;color:#91a0b7;padding:12px 30px} QTabBar::tab:selected{color:#65e6cb;background:#203333}')
        count = 1
        try:
            count = max(1, min(4, int((folder/'channels.txt').read_text())))
        except (OSError, ValueError):
            pass
        for _ in range(count):
            self.add_channel()

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
        bootstrap += '''
        const desktopQueue=[];let desktopNative=null;
        window.CanAndroid=new Proxy({}, {get:(_,name)=>(...args)=>{if(desktopNative)desktopNative.call(name,JSON.stringify(args));else if(desktopQueue.length<64)desktopQueue.push([name,args]);}});
        new QWebChannel(qt.webChannelTransport, channel=>{desktopNative=channel.objects.desktop;for(const [name,args] of desktopQueue)desktopNative.call(name,JSON.stringify(args));desktopQueue.length=0;});
        '''
        page.scripts().insert(script('native', bootstrap, QWebEngineScript.InjectionPoint.DocumentCreation))
        page.scripts().insert(script('desktop', (SOURCE/'desktop.js').read_text('utf8'), QWebEngineScript.InjectionPoint.DocumentReady))
        self.views[slot] = view
        self.tabs.addTab(view, f'通道 {slot}')
        view.loadStarted.connect(lambda: self.ready.discard(slot))
        view.loadFinished.connect(lambda ok: self.loaded(slot, ok))
        view.setUrl(QUrl.fromLocalFile(str(ASSETS/'index.html')))
        (self.folder/'channels.txt').write_text(str(slot))

    def loaded(self, slot, ok):
        if ok:
            self.ready.add(slot)
            self.runtime.call(slot, 'networkState', [])
            self.runtime.call(slot, 'delivered', [])
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
            self.tabs.setTabText(slot-1, f'通道 {slot}'+(' · 已连接' if value['value'] == 'ready' else ''))
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
        value = json.loads(args[0])
        path, _ = QFileDialog.getSaveFileName(self, '导出当前通道', f'can-scope-channel-{slot}.{extension}', f'*.{extension}')
        if not path:
            return
        if extension == 'json':
            Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf8')
        else:
            with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
                writer = csv.writer(stream)
                writer.writerow(['kind', 'CAN_ID', 'EXT', 'RTR', 'DLC', 'data', 'receivedAt', 'token'])
                for row in value[:20000]:
                    writer.writerow([int(row['kind']), hex(int(row['id'])), int(bool(row['ext'])), int(bool(row['rtr'])), int(row['dlc']), ' '.join(f'{b:02X}' for b in row['data']), int(row.get('receivedAt', 0)), int(row.get('seq', 0))])
        self.statusBar().showMessage('已导出 '+path, 5000)

    def closeEvent(self, event):
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
    window = Window(folder, bool(options.smoke))
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
    return code
