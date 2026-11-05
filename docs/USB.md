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

Linux 1.2.14 的原生 USB 打开入口将 DTR/RTS 在一次 ioctl 中同时清除。实体 ESP32-S3 台架曾在分步清除两线时报告 USB UART reset；仅设置两个布尔值为 false 不足以避免过渡状态。设置脚本与桌面传输复用 `can_scope.serial_port.open_native_port`；不需要修改系统网络或设备保存的配置。

桌面 1.2.15 在取消 USB 设置或数据写入时等待当前有超时限制的 OS 写入结束，再关闭串口或释放写锁；重复取消不提前释放，也不重发设置或 CAN 命令。取消后已提交的设置结果仍可能未知，应重新读取确认。

桌面 1.2.16 连接后保存端口 VID/PID/序列号；重连按这一身份寻找唯一端口，兼容设备复位后的 `/dev/ttyACM*` 或 COM 编号变化。找不到、同身份有多个端口或握手期间身份改变时拒绝连接，要求检查或重新扫描。没有序列号的设备及旧缓存沿用原端口逻辑；重新选择连接后更新身份。该机制不替代 USB 的物理信任边界，也不重发 CAN 命令。

## 物理 USB 管理（固件 1.24.4）

以下入口仅由原生 USB CUSB 解析器处理，在蓝牙、Wi-Fi、双机和 USB 模式均可使用，无需取得 CAN 数据会话。存在活动 USB 会话时仍排除其他 session ID。每个请求有非零 token，返回 type18 准入结果；状态非零时不返回事件。不会通过 BLE 或 TLS 开放这两个入口。

双机命令 `0x1a`：byte0=1，byte1=`0x1a`，byte2 操作，byte3 角色，byte4–9 目标 MAC，byte10–11=0，u32 校验码在12，u32 token在16。

| 操作 | 含义与约束 |
| --- | --- |
| 0 | 读当前状态；角色、MAC、校验码字段须为零 |
| 1 | 开始一次本机 USB 配对授权；角色须匹配正在运行的 A=2/B=3，MAC 须为指定另一端的单播地址，校验码为零 |
| 2 | A 选择指定 MAC 的新鲜广播候选；使用本次开始时的 token 和同一 MAC |
| 3 | 确认当前已派生校验码；token、角色、MAC、校验码均须匹配，且配对窗口未过期 |
| 4 | 取消本次 USB 配对；token、角色、MAC 须匹配，保留旧绑定 |
| 5 | 读本机 STA MAC；请求其余字段与操作0相同 |
| 6 | 读双机累计统计；请求其余字段与操作0相同 |
| 7 | 固件1.24.5：读 CAN 出队过期、待发过期、重试耗尽累计帧数 |
| 8 | 固件1.24.5：读无线 MAC 发送失败次数、提交拒绝次数、CAN 输出队列过期帧数 |
| 9 | 固件1.24.5：读无线接收队列满次数、CAN 发出队列满帧数、接入时已过期帧数 |

事件23：byte2=结果，byte3=`dual_view.h` 状态；byte4–9=对端 MAC（操作5为本机 MAC），byte10 标记 bit0已绑定/bit1本机已确认/bit2配对活动/bit3 UART，byte11角色，u32校验码在12，原token在16。操作6改为 u32 sent/received/dropped 在4/8/12。配对密钥不导出。开始授权后仅接受指定 MAC 的配对报文；手动重开、取消、模式退出及超时都会使旧 USB 确认失效。

操作7～9请求其余字段与操作0相同；事件23在4/8/12放置表中三个u32累计数，覆盖这些位置原有的状态字段。计数为启动以来的诊断观察，不清零，不改变重试或100ms寿命。MAC失败只表示底层没有确认送达，不能单独证明应用或物理CAN是否收到；须结合双端统计和总线观测。

本机蓝牙配对显示命令 `0x1b`：byte0=1、byte1=`0x1b`、byte2–15全零，非零token在16。事件24：byte2表示是否有活动六位码，byte3为本机网络显示状态，u32配对码在4（无活动码时为 `0xffffffff`），byte8–15零，原token在16。该入口仅把当前本机屏幕的临时蓝牙配对码提供给物理 USB 主机，以便电脑自动完成系统配对对话框；不提供 Wi-Fi 密码、TLS私钥或令牌。调用方不得记录配对码。无线配网依然要求 LE Secure Connections、加密和 MITM 认证。

电脑配对示例见 [双机指南](DUAL_CAN.md#电脑通过-usb-配对固件-1244)。
