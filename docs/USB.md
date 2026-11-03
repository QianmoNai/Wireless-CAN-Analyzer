# USB 直连（ESP32-S3 1.24.2 / Desktop 1.2.4）

1. 设备屏幕进入设置，数据选择选 `CAN` 或 `UART`。
2. 通信模式选择 `USB直连`，界面变为红色，设置自动保存。固件 1.24.1 起顺序为：蓝牙 → Wi-Fi → USB直连 → 双机A端 → 双机B端。
3. 用可传数据的 USB 线连接 ESP32-S3 **原生 USB** 接口与电脑。
4. Windows / Linux 上位机选择 `USB直连`，点击连接设备、扫描，选择对应 COM 端口或 `/dev/ttyACM*`。
5. 握手完成后屏幕显示 `USB已连接`。CAN 页面保留帧监视、电机曲线、查询和单次发送；UART 页面收发 HEX / UTF-8。

USB 使用原生 USB Serial/JTAG，USB D−/D+ 为 GPIO19/GPIO20。开发板 TX/RX（GPIO43/GPIO44）仍用于外接单片机的 3.3 V UART，双方共地、TX 接对方 RX；原生 USB 与该 UART 独立。若开发板有两个 USB 插座，应使用接至 GPIO19/20 的插座，USB 转 UART 芯片对应插座不能用于本协议。

Windows 使用系统串口设备，Linux 使用 CDC ACM 串口；扫描会列出可用串口，握手可以排除不支持本协议的设备。端口被其他串口工具占用时先关闭工具。Linux 若提示权限不足，按发行版配置串口用户组或 udev 权限，例如 Ubuntu 的 dialout、Arch 的 uucp，并重新登录。不要以 root 身份运行整个图形程序。

USB 串口的 115200 参数用于打开端口，不决定 USB 链路速度；UART 页面设置的波特率才决定 TX/RX 与外部单片机的速率。高持续数据率受 MCU 处理、缓冲和 UI 刷新限制，满队列仍可能丢弃数据。

选 USB 模式时停止无线连接，切回蓝牙、Wi-Fi 或双机模式后恢复对应功能。USB 不要求配网或蓝牙配对，同一台设备只供一个上位机通道使用。关闭窗口、拔线或心跳超时会清理会话和待执行命令，不自动重发发送结果未知的命令。USB 直连不提供加密认证，适用于物理连接的本机。

应用控制台、应用日志及二级引导日志关闭；USB 应用数据流只使用以下协议。芯片 ROM 在复位早期可能输出启动字节，解析器会丢弃这些字节，不改写不可逆的 eFuse。

## 无需实体按键的远程设置（固件 1.24.2）

20 字节命令 `0x19` 可查询或持久化修改通信模式、CAN/UART 选择和 UART 波特率，复用屏幕的 `local_panel/settings` NVS。屏幕继续显示和修改同一份设置。完整字段与校验见 [PROTOCOL.md](PROTOCOL.md#persistent-remote-settings-firmware-1242)。

原生 USB 即使当前处于蓝牙/Wi-Fi/双机模式、尚未 HELLO，也接受 CUSB 封装中的设置查询，以及明确选择 `network=4` 的设置命令；先检查 CRC、非零会话号、非零 token、全部字段与保留零字节。此时不能发送 CAN/UART 数据，也不能用该路径选择其他通信模式。成功后再按原协议 HELLO 建立数据会话；已有会话仍隔离其他会话号。没有增加外接 UART AT 解析器，GPIO43/44 始终承载用户字节流。

从仓库根目录可自动进入 USB + UART：

```bash
desktop/.venv/bin/python scripts/serial-settings.py --port /dev/ttyACM0 --mode usb --data uart --baud 115200
```

省略 `--mode/--data/--baud` 只查询，不切换模式。USB 会话中还可只设置 `--data can`、`--data uart` 或 `--baud 230400`；处于其他通信模式时，原生 USB 设置必须显式包含 `--mode usb`。请先关闭占用该端口的桌面和监视器；命令不会自动重发 SET。

type18 的状态0表示受理，type22 的 result1 才表示 NVS 已保存；驱动由设置任务随后应用，UART 是否就绪仍看 type21。查询返回当前共享配置，可能包含屏幕刚修改但仍等待延迟保存的值，不会强制写入或重新读取 NVS。切换通信模式可能使旧会话的应答来不及送达，应重新连接查询；恢复 USB 后仍需 HELLO。启动初期设置尚未初始化时可能返回 type18 状态17，可稍后重试只读查询。相同的已持久化设置不会反复写 Flash。

## 帧格式

固定 32 字节，小端序：`CUSB`（4）+ 非零会话号（4）+ 原有桥接报文（20）+ CRC32 IEEE（4，覆盖前 28 字节）。字节流支持分段、粘包和损坏后重新同步。USB 封装不改变 CAN/UART 的 20 字节命令和状态报文。

- HELLO：20 字节 `01 7E 00 00` + ASCII `CANUSB1` + 9 个零；电脑每次连接随机生成非零 32 位会话号。设备仅在 USB 模式接受，回复将报文偏移 2 改为 `01`。上述设置 bootstrap 是建立数据会话前的唯一命令扩展。
- PING：`01 7F` + 18 个零；电脑每秒发送、设备原样响应，4 秒无心跳失效。
- BYE：`01 7D` + 18 个零；清理当前会话。断线无法送达时由租约兜底。
- 其余报文须匹配当前会话。命令受理响应类型 18，偏移 2 为受理状态，偏移 3 为命令类型，偏移 4–7 为原命令 token。CAN 完成/失败、UART 完成分别使用原有类型 2/3、20；受理成功不等于总线发送成功。
- USB 侧有独立、有限的控制与原始数据队列；控制优先。输出失败使连接失效，旧会话数据由会话号过滤。

## 固件 USB 调度

ESP32-S3 worker 非阻塞读取最多 256 字节，再发送最多 64 帧或 2 ms 的出站批次；每帧重新优先检查控制队列，批次之间重新处理输入。单次驱动写入仍保留原有 20 ms 超时，时间预算在帧边界检查，不截断或重发帧。

出站入队和 USB RX 中断通过任务通知唤醒 worker，空闲时最多阻塞一 tick，以继续检查断线和租约。worker 累计处理时间达到 10 ms 后阻塞一 tick，让低优先级及 idle 任务运行；等待通知的时间不计入处理预算，等待超时或已等待至少 2 ms 时恢复预算，避免刚唤醒又强制休眠。当前 FreeRTOS 为 100 Hz，一 tick 为 10 ms。链路独占 USB Serial/JTAG 驱动的 select 通知回调，不能再为此驱动注册另一套 VFS select 回调。

## 可重复实机测试

已有 `desktop/.venv` 后，在仓库根目录执行以下命令（测试会持久化 USB + UART / 115200，关闭占用端口的程序后运行）：

```bash
desktop/.venv/bin/python scripts/test-serial-hardware.py --output "$HOME/grokbot/serial-test-hardware.json"
desktop/.venv/bin/python scripts/test-serial-hardware.py --check-only --output "$HOME/grokbot/serial-test-persistence.json"
XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland desktop/.venv/bin/python desktop/tests/serial_hardware.py --usb /dev/ttyACM0 --peer /dev/ttyACM1 --output "$HOME/grokbot/serial-test-desktop-hardware.json"
```

最后一行是本机普通用户 Wayland 会话示例，显示环境随机器调整。原始测试默认不发送 CAN，只有在确认可测试的总线上才加 `--can-tx`，它会发送一帧 STD ID `0x7fe`、数据 `00`。UART 测试在正式数据前发送同步标记并记录标记前 ROM/适配器缓存字节；之后 HEX、UTF-8 的全部字节严格比对。测试包含设置校验、两档 UART 速率、type19/20/21、BYE、旧会话拒绝、租约失效与重连；`--check-only` 不修改设置，供外部复位后检查 NVS。Qt 测试操作真实页面和真实串口，使用临时桌面配置，输出 JSON 与截图。

编译、主机协议测试、桌面界面测试与物理收发证据分别记录。本次 ESP32-S3 双串口实机验证已覆盖 USB 会话及 UART 115200/230400 的 HEX、UTF-8 双向字节比对；CAN 单次发送得到 type3 / ACK error，未观察到 CAN RX，因此没有证明真实 CAN 总线双向通信成功。断线恢复与桌面验证的具体覆盖范围以交付测试日志、结果文件为准。
