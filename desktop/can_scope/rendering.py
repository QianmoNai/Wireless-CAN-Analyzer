"""Choose the graphics backend before importing Qt."""
import os
import sys


def configure():
    if sys.platform != 'linux' or os.environ.get('CAN_SCOPE_HARDWARE_RENDERING') == '1':
        return
    # Avoid the Qt/Chromium GPU compositor lock observed with NVIDIA/Wayland.
    flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
    if '--disable-gpu' not in flags.split():
        flags = (flags + ' --disable-gpu').strip()
    # Bounded histories need little live JS memory. A multi-GB default heap
    # delays reclamation of short-lived packet/DOM objects during continuous RX.
    # Respect explicit diagnostic/runtime flags; never force a synchronous GC.
    if '--js-flags' not in flags:
        flags += ' --js-flags=--max-old-space-size=256'
    os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = flags
    os.environ['QT_QUICK_BACKEND'] = 'software'
