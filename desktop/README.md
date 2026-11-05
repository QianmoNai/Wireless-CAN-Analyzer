# CAN Scope Desktop 1.2.14

1.2.14 修复 Linux 打开原生 USB 串口时 DTR/RTS 分步清除造成的 ESP32-S3 意外复位。两条控制线在一次 ioctl 中同时清除，设置脚本复用同一入口；其他系统保留串口库原有行为。实体台架上原路径打开时启动编号改变，修复后打开、持有和关闭均保持编号，详细范围见性能记录。

1.2.13 将共享配对说明改为系统配对窗口和系统通知，适配电脑与手机；Android 原生进度仍给出手机通知栏入口。页面内版本号统一使用程序提供的版本，避免窗口与页面显示不同版本。

1.2.12 将 Wi-Fi 操作状态移至按钮附近，读取、保存或输入验证时滚动显示提示，避免结果位于长表单另一端。手机首次安全配对提示补充通知栏入口；实际系统配对仍需用户完成。

1.2.11 将同一次手动发送的等待、成功、失败或结果未知更新到同一行，最多保留 40 次请求。成功后不再残留该请求的旧等待行；未确认仍明确显示未知，不自动重发。

1.2.10 增加 Escape 关闭当前设备搜索窗口或电机编辑窗口，保持当前通道与连接；再次操作回到原页面。隐藏页面中的编辑器不会拦截按键，未保存的草稿不会自动添加到列表。

1.2.9 修复最大历史（RX/TX 各 10000 条）的 CSV 导出被 2 MB 界面参数限制拒绝的问题；CSV 单独允许 4 MB 参数，并在打开文件前校验最多 20000 行及 CAN 字段。保持 UTF-8 BOM 和原列格式。长曲线减少每像素桶临时数组，IMU 的有效点筛选与范围统计合并；保留首尾、峰值和时间断点，采样接收与保存上限不变。离线与实体台架结果见性能报告，不换算固定帧率提升。

1.2.8 增加通道独立的 75–200% 显示缩放：工具栏放大、缩小、百分比复位，也支持 Ctrl++ / Ctrl+=、Ctrl+-、Ctrl+0；当前会话各通道分别保留，重启后恢复 100%。缩放不修改 CAN 采样频率或报文历史。暂停曲线复用冻结图形，时间窗、所选曲线、尺寸或可见区域变化时重新绘制；后台数据接收和当前数值继续更新。

同时保留 1.2.7 的通道全屏修复：原生全屏跟随当前通道，后台页面的迟到请求不会关闭当前曲线，关闭后恢复进入前的窗口状态。

CAN 模式的数据传输与显示优化：首页使用在线/温度摘要，非活动标签页切为静默，详情只订阅当前设备；USB 与 Wi-Fi 按块解析，蓝牙在固件 1.24.3 上自动使用批量通知，旧固件保留原有通知。电机卡片显示实际反馈 ID 和过期状态，C610 等无温度字段的型号保持空值；IMU 仅绘制可见曲线，详情直接切入监控时避免中间订阅，并收起连接配置栏以扩大参数视口。Wi-Fi 连接阶段限次重试且可取消；详情报文列表复用行与字节元素，避免每次刷新重建 40 行；列表自动刷新最高 10 次/秒，接收记录不降采样，手动操作立即刷新；Linux 多网卡环境并行搜索并合并重复结果，再按设备回应选择连接接口，保持证书固定校验，不修改系统路由。采样控制与频率在桌面横排，退出曲线全屏恢复原窗口状态。原生通道标签与状态栏显示连接方式。实机与模拟测试的范围分别记录在验证报告中。

1.2.4 配合固件 1.24.2 提供可自动调用的远程设置接口，查询/保存通信模式、CAN/UART 与八档串口波特率。无需实体按键进入 USB + UART：先关闭占用串口的桌面/监视器，在仓库根目录运行 `desktop/.venv/bin/python scripts/serial-settings.py --port /dev/ttyACM0 --mode usb --data uart --baud 115200`，再正常打开桌面连接。只查询时省略设置参数，不改变设备模式。桌面桥接 API `deviceSettings` 和显式 `prepare_usb` Python API 使用相同校验；现有 USB 连接保留 HELLO 兼容路径。详见 [USB 协议与自动测试](../docs/USB.md)。

1.2.3 在 Linux 默认使用软件渲染，绕开 NVIDIA/Wayland 下观察到的 Qt WebEngine 图形合成阻塞。CAN 列表复用固定行元素并缓存时间文本，隐藏时停止重绘；Linux 默认将每个渲染进程的 V8 老生代预算设为 256 MiB，促使连续接收时及时回收临时对象（并非整个应用的内存上限），不改变记录保留数量。串口接收区保留最近 64 KiB，每 250ms 按小文本块更新；隐藏或暂停时不重绘，仍继续接收。软件渲染和更及时的内存回收可能增加 CPU 使用量；排查时可设置 `CAN_SCOPE_HARDWARE_RENDERING=1` 恢复原硬件渲染策略。Windows 图形后端不变。

1.2.2 在 Linux 应用内筛选 CAN Scope 服务，避开 BlueZ 5.87 UUID 扫描过滤器崩溃；Windows 保留系统服务筛选，USB 握手修复保留。

1.2.1 修复 USB 打开后设备尚在启动时的握手超时：在 4 秒内每 0.5 秒重试同一会话的握手，连接成功后停止；不重发 CAN / UART 命令。

Linux / Windows 桌面上位机，连接本项目 ESP32-S3 固件。Qt 桌面窗口复用 Android 的电机解析、曲线、配置与监控资源，BLE 使用系统 BlueZ / WinRT，局域网使用证书指纹绑定的 TLS 1.2。设备双机 A/B 模式不接受上位机连接。

## 功能

- 新增 [USB直连](../docs/USB.md)：CAN / UART 共用原生 USB 接口，免配网，需 ESP32-S3 1.24.0；设备红色界面。

- 新增 UART 无线串口：通过蓝牙或 Wi-Fi 双向收发 HEX / UTF-8，设备屏幕选择 CAN 或 UART；支持波特率状态、丢弃统计、分段完成确认。需配套 ESP32-S3 1.23.0。

- 最多四个独立通道，每通道可选蓝牙、Wi-Fi 或 USB直连，独立保存电机配置与帧记录上限。
- DJI / 达妙电机、DM IMU 状态、温度、实时曲线、全屏曲线及采样频率配置。
- CAN 接收/成功发送/失败记录、ID 筛选、总线负载、实际帧率与失败统计。
- 标准/扩展、数据/远程经典 CAN 帧单次发送，DLC 0–8。通过非零 token 匹配实际 CAN 完成事件；提交完成不表示 CAN 成功，超时/断开显示结果未知且不自动重发。
- 达妙设备端定时反馈查询，ACK 确认后显示启用；续租失败停止续租，固件租约最长 3 秒。查询开启状态不保存、不自动恢复。
- 蓝牙安全配网、已绑定设备局域网发现、手动 IPv4 连接；修改 CAN 波特率、设备名称。
- 与 Android 兼容的 JSON 通道配置导入/导出；多个通道的备份导入时选择其中一个到当前标签页。CSV 导出当前保留的帧记录。

此版本使用每通道的设备卡片页，不提供 Android 的跨通道汇总首页。没有新增电机自动使能、周期运动指令或 CAN FD 功能。

## 界面预览

以下为 Linux 桌面程序实际渲染，使用模拟数据。

![设备与温度](preview-overview.png)

![电机曲线](preview-detail.png)

![CAN 单次发送](preview-live.png)

## 从源码运行

建议 Python 3.12（64 位）；Linux 需 BlueZ 5.55+ 和可用蓝牙适配器，Windows 需支持 BLE 的 Windows 10/11 64 位系统及驱动。使用正常桌面用户运行，Linux 蓝牙权限由系统 BlueZ / D-Bus 管理，无需以 root 运行。

Linux：在仓库根目录执行 `bash desktop/start.sh`。

Windows：安装 Python 3.12 并勾选添加 PATH，然后双击 `desktop/start.bat`。

启动脚本首次创建独立虚拟环境并联网安装依赖。安装中断后，可在 desktop 目录执行以下命令修复：

```bash
# Linux
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

```powershell
# Windows
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

Linux Qt WebEngine 需要桌面图形库、NSS、音频基础库和中文字体。Ubuntu 24.04 可安装 `libegl1 libopengl0 libxkbcommon-x11-0 libxcb-cursor0 libnss3 libasound2t64 fonts-noto-cjk`。

## 连接

USB：设备屏幕通信模式选择 USB直连，连接原生 USB 口，电脑端选择 USB直连后扫描并选择串口。外接单片机继续接 TX/RX 引脚。 默认只列出带 USB 信息的端口，并优先显示 ESP32 原生 USB 候选；需要时勾选“显示系统串口 / 缺少 USB 描述的端口”。扫描只读取端口信息，不打开串口，候选仍需连接验证。

1. ESP32 屏幕选择蓝牙模式；桌面程序选蓝牙，点击连接设备、扫描并连接。每台设备同一时间只连接一个上位机，请先断开手机 App。
2. 首次 Wi-Fi 使用：在电脑通过蓝牙连接，进入设备设置读取网络状态或配置 Wi-Fi。系统配对弹窗输入 ESP32 屏幕上的六位码；Linux 需桌面蓝牙管理器的配对代理运行。
3. 完成配网并保存设备凭据后，在 ESP32 屏幕切换 Wi-Fi 模式，桌面通道也切换 Wi-Fi；电脑与设备处于同一局域网。可搜索或填写设备屏幕 IP。UDP 32323 发现失败时仍可手动连接 TCP 32323。
4. Android 配置导出不携带 TLS 令牌；电脑首次使用仍需自己通过蓝牙认证。切换到另一台蓝牙设备后清除当前通道原有的局域网凭据，避免连错设备。

Linux 终端也可由用户本人运行 `desktop/.venv/bin/python scripts/wifi-provision.py`，为目标 `90da7273722c` 完成一次 BLE 认证、隐藏输入密码、配网、切换 Wi-Fi、保存通道 1 信任缓存和一次 TLS/QUERY 验证。脚本内置临时 BlueZ 配对代理，无需 bluetoothctl 或桌面配对管理器；等本终端出现“ESP32 六位配对码”提示后输入屏幕六位码，不回显。**脚本会配对所选设备并修改设备的 Wi-Fi 设置。** `--help` / `--dry-run` 不访问设备或缓存；运行条件、排查与 USB 恢复命令见 [终端 Wi-Fi 配网](../docs/WIFI_PROVISION.md)。

电脑最小化继续监控、续租；关闭整个窗口断开所有通道。电机查询及手动发送需要主动操作；重新连接不会自动恢复发送。

## 数据与边界

配置位于 Qt 的 `AppLocalDataLocation/Qianmo/CAN Scope Desktop` 对应用户目录（具体顺序由系统决定），每通道的 WebEngine profile 隔离 localStorage。连接信息保存在 `connection-N.json`，包括通过认证蓝牙获取的证书指纹、令牌；Linux 目录 0700、文件 0600，Windows 使用用户 AppData 目录的继承权限。这是本机文件存储，未做磁盘加密。JSON 配置/CSV 导出不包含这些连接凭据。

只加载仓库打包的本地页面；禁止页面联网、跳转和下载。后端使用命令白名单、独立命令队列、512 帧数据缓冲与单批界面回执，避免 UI 卡顿导致无限积压。CSV 是当前保留记录，不是无损全量记录。

## 打包

在各自目标系统、desktop 目录安装 `requirements-build.txt` 后执行 `python build.py`。产物为 `desktop/dist/CAN-Scope/`；Windows 内有 `CAN-Scope.exe`，Linux 内有 `CAN-Scope`。构建同时生成 Linux `.tar.gz` 或 Windows `.zip`，推荐分发压缩包，解压后运行。整个目录一起分发，不要仅复制可执行文件。Linux 若项目所在文件系统不保留可执行权限，请把 tar.gz 解压到支持 Unix 权限的本地目录。

PyInstaller 按当前系统构建；Linux 不能直接产出 Windows exe。仓库提供 `.github/workflows/desktop.yml`，在 Ubuntu 24.04 与 Windows Server 2022 各自测试、打包。主分支推送或手动运行时，安装包及 SHA-256 上传到对应运行的 Release 草稿，不自动正式发布；PR 只构建和测试。Actions 仅保存 3 天的测试截图、报告，这些诊断附件上传失败不会影响已验证安装包的交付。Linux 构建会依赖构建机的 glibc 版本；分发到其他发行版建议使用 CI 的 Ubuntu 构建或从源码启动。本地 Arch 构建不能视为所有 Linux 发行版通用包。

打包使用 Qt / PySide6、Bleak 等第三方组件，分发时保留其随包许可证和本项目 GPL-3.0 许可证。Windows 产物未代码签名。

## 验证

```bash
python -m unittest discover -s desktop/tests -v
python desktop/main.py --smoke desktop/build/smoke.png
```

在无显示器 Linux 环境可设 `QT_QPA_PLATFORM=offscreen`、`QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`。测试使用临时用户数据，不连接真实 BLE 或 CAN 设备。TLS 测试通过本地回环模拟服务器验证先核对证书再发送令牌、拆包/粘包；其证书由测试时调用 openssl 生成。

本次交付的验证范围以任务说明为准：自动测试和 Linux 界面启动不等于 Windows 蓝牙硬件或真实电机总线已经联调。

参考：[Qt 桌面部署](https://doc.qt.io/qtforpython-6/deployment/index.html)、[Bleak 支持平台](https://bleak.readthedocs.io/en/stable/)。

### 无线串口（固件 1.23.0）

最新源码共用 App 的 UART 页面。设备屏幕切到 UART 后，通过原有蓝牙或 Wi-Fi 连接即可接收/发送 HEX 或 UTF-8 字节，屏幕切回 CAN 后恢复 CAN 页面。TX=GPIO43、RX=GPIO44，默认 115200 / 8N1，接线和吞吐边界见 [无线串口说明](../docs/UART.md)。既有 Desktop 1.0.0 Release 安装文件不包含此更新，需从本次源码运行或重新打包。

## 1.1.0 更新包

Windows：解压 `CAN-Scope-Desktop-1.1.0-Windows-x64.zip`，运行目录中的 `CAN-Scope.exe`。Linux：解压 `CAN-Scope-Desktop-1.1.0-Linux-x64.tar.gz`，运行 `CAN-Scope/CAN-Scope`。完整目录一起保留，内含运行库，无需安装 Python。先退出旧版再启动新版；用户配置仍从原位置读取。SHA-256 校验文件与压缩包一起提供。
