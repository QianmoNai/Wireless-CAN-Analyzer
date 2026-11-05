"""Open native USB without a transient DTR/RTS bootloader reset."""
import os
import sys
import serial


def _clear_modem_lines(fd):
    import fcntl
    import struct
    import termios
    fcntl.ioctl(fd, termios.TIOCMBIC,
                struct.pack('I', termios.TIOCM_DTR | termios.TIOCM_RTS))


class NativeUsbSerial(serial.Serial):
    # pyserial normally clears DTR, then RTS. The intermediate state resets
    # ESP32-S3 USB Serial/JTAG. Clear both in one ioctl on Linux instead.
    def _update_dtr_state(self):
        if sys.platform == 'linux' and not self._dtr_state and not self._rts_state:
            _clear_modem_lines(self.fd)
        else:
            super()._update_dtr_state()

    def _update_rts_state(self):
        if sys.platform == 'linux' and not self._dtr_state and not self._rts_state:
            _clear_modem_lines(self.fd)
        else:
            super()._update_rts_state()


def open_native_port(device, *, timeout=0, write_timeout=.5):
    options = {'exclusive': True} if os.name == 'posix' else {}
    port = NativeUsbSerial(port=None, baudrate=115200, timeout=timeout,
                   write_timeout=write_timeout, **options)
    try:
        port.dtr = port.rts = False
        port.port = device
        port.open()
        port.reset_input_buffer()
        return port
    except BaseException:
        port.close()
        raise
