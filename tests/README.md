# 构建与测试

在仓库根目录执行。主机测试不连接 BLE 设备，不发送实际 CAN 帧。

## C / Java / JavaScript 测试

依赖 C 编译器、Python 3、pyserial 3.5、Node.js 20 以上、JDK 17 和 OpenSSL 开发库。首次运行先安装依赖和锁定的 Node Playwright 及其匹配浏览器：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install pyserial==3.5
npm ci --prefix tests
npm exec --prefix tests -- playwright install --with-deps --only-shell chromium
PATH="$PWD/.venv/bin:$PATH" bash scripts/test-host.sh
```

入口包含 `test_uart_ui.cjs` 与 `test_uart_render.cjs`。独立的 `checks.yml` workflow 对 firmware、scripts、tests、desktop 及共享 App 资源变更运行主机与桌面单测，不创建 Release，也不访问实机串口。

覆盖 CAN 所有者循环、轮询查询、错误计数、速率和估算负载、BLE 数据筛选、ACK 调度、电机/IMU 订阅、蓝牙名称存储、本机按键与屏幕逻辑、App 查询和重连配置，以及有界接收、连接优先级、环形历史、WebView 资源路由和构建脚本保护。

GPIO3 指示灯测试运行真实驱动配合 GPIO 输出模型，覆盖连接基线、RX/TX 活动、持续高流量下 50ms 短灭和至少 150ms 点亮、任务更新延迟、断连重连、计数/时间回绕与 GPIO 失败隔离；同时编译运行关闭配置和旧 ESP32 目标，检查其不操作 GPIO3。该测试不依赖屏幕或无线硬件，不能验证实际 LED 亮度及焊接。

## 浏览器界面测试

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install playwright
python -m playwright install chromium
python tests/test_network_ui.py
python tests/test_home_sort.py
python tests/test_motor_detail.py
python tests/test_imu_ui.py
python tests/test_monitor.py
python tests/test_ui_performance.py
```

测试采用 Playwright 自带 Chromium 和模拟的 Android 桥接器。Linux 缺少浏览器系统依赖时，参考 Playwright 的安装输出处理。其余界面脚本可单独执行；`test_query_tx_filter.py` 为主机 C 测试封装，`test_build_scripts.py` 和 `test_asset_routes.py` 是无需浏览器的主机测试。

## 编译与烧录

固件使用 ESP-IDF v6.0.2；App 使用 JDK 17、Android API 35 和 Build Tools 35.0.0。命令见根目录 README。测试通过不代表每一种电机、屏幕或 Android 手机已经通过实物验证。

`render_board_ui.c` 可以在主机绘制中文总览与设置两页、四种方向和正常/异常状态预览（模拟数据）：

```bash
cc -std=c11 -Ifirmware/main tests/render_board_ui.c firmware/main/board_ui_render.c firmware/main/board_ui_model.c firmware/main/board_ui_font.c -o /tmp/render-board-ui
/tmp/render-board-ui /tmp
```

生成 `/tmp/panel-<角度>-<页码>.ppm`、带 `-status` 后缀的异常状态图、带 `-sound` 后缀的声音选中/关闭预览、带 `-rotation` 后缀的方向编辑图，以及 `-volume-low/medium/high` 三档音量图。安装 Pillow 后运行 `python3 scripts/render-panel-preview.py`，可重新生成文档中的横竖屏、方向、网络及音量预览。

`test_board_ui.c` 验证两页导航、四方向菜单/亮度按键映射、方向编辑固定 BT1 顺时针/BT3 逆时针、中键直接切换声音与音量、导航不改变设置、静音时切换音量、三档音量/四方向/通信模式保存、版本 1～4 迁移、UTF-8 解析、原始帧不进入本机快照、屏幕地址映射及每段绘制边界；也可加 `-fsanitize=address,undefined` 运行。`test_panel_font.py` 检查界面中文全部有字形，主机测试不依赖字体安装或 Pillow。

`test_board_buzzer.c` 将实际蜂鸣器驱动连接到 GPIO/LEDC 模拟接口，验证初始静音、背光通道隔离、低/中 PWM、高档持续电平、停止后恢复 PWM、静音期间切档不出声、无效档位及驱动错误后的关闭与恢复。该测试验证输出控制逻辑，实际响度、杂音和起振仍需用 TMB09A03 实物试听。

`test_board_ui_animation.c` 验证缓动结束、重复目标不重启动画、中途反向、快速重定向、计时回绕和旋转重置。它用真实绘制代码生成新帧，将计划刷新的条带应用到上一帧，再与完整重绘逐像素比较；覆盖四方向、菜单/页面/主题、配对弹层、设置保存提示和遥测变化，检查残影及 DMA 缓冲边界。`python3 scripts/render-panel-animation.py` 生成模拟动画 GIF 和分帧预览；它们不代表设备实测帧率。

`test_panel_dma.py` 编译实际 DMA 完成回调，并模拟 ESP-IDF SPI 驱动忽略返回值的行为，确保等待中的绘制任务会立即请求 ISR 调度；没有高优先级任务被唤醒时不重复调度。

实机页面计时可用 `bash firmware/idf.sh -D CAN_PANEL_BENCHMARK=ON build` 构建，再使用相同选项执行 `app-flash`。测试固件在上电约 2 秒后自动执行两次进入设置、两次返回，打印 `PANEL_ACTION` / `PANEL_FRAME` 串口计时，不发送 CAN 命令，也不改变持久化设置。结束后必须用 `-D CAN_PANEL_BENCHMARK=OFF` 重新构建并烧录；该选项默认关闭，但 CMake 缓存会保留显式启用的值。正式固件不包含自动操作和计时输出。

Wi-Fi 新增：凭据分片/事务超时/重放和擦除测试、Java 本地 TLS 服务端对证书固定与分片重组的集成测试（临时证书由 keytool 创建），以及 App 配网界面回归。局域网测试只访问本机回环地址，不连接用户路由器。

## 双机 ESP-NOW 转发

主机测试额外需要 OpenSSL 3 开发库。`test_dual_protocol.c` 使用实际配对/会话代码验证密码学握手、双端确认和重放隔离；`test_dual_runtime.c` 通过 `dual_runtime_a.c` / `dual_runtime_b.c` 分别编译两份实际运行代码，模拟独立队列、异步无线回调和 NVS，覆盖双向转发、重传去重、积压过期、断线和模式切换、计时回绕及 NVS 失败。现有 CAN owner 和四方向渲染测试同时覆盖双机入口。主机模拟不能证明射频吞吐或实物 CAN 接线正确，硬件验收项见 `docs/DUAL_CAN.md`。

### UART / 声音合并（1.23.0）

`bash scripts/test-host.sh` 包含实际 UART worker 的二进制收发、FIFO 完成应答、模式/连接隔离、输入校验、驱动错误和丢弃统计，以及 CAN owner 在 UART 选择时停止查询/转发、设置 v1–v5 迁移、四档声音和七项菜单滚动。Java 命令过滤器和接收队列测试覆盖 UART 命令与 CAN 订阅切换时保留 UART 数据。

共享 WebView 的串口测试：安装 Playwright 后执行 `node tests/test_uart_ui.cjs`；可用 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指定浏览器。覆盖模式自动切换、HEX/跨包 UTF-8、包序号间隙、12 字节分段、确认早于写完成、token 匹配、取消与断线。截图输出到 `android/dist/preview-uart-*.png`。这些测试使用原生通信桩，不代表实体无线链路测试。

桌面端用 `desktop/.venv/bin/python -m unittest discover -s desktop/tests -v` 检查 UART 命令、模式互斥和原始数据路由。

### USB直连（1.24.0 / Desktop 1.2.0）

主机测试编译实际 USB worker，验证握手、分段、会话隔离、心跳到期、拔线、短写失败、控制优先及队列满；CRC 损坏后重新同步另有独立测试。屏幕测试覆盖 v6 配置迁移、CAN/UART 的 USB 设置持久化与红色主题脏区刷新。桌面测试覆盖握手与旧会话过滤、短写不重放、关闭串口、保留 BLE/Wi-Fi 信息；离线 UI 测试检查 USB 入口和扫描列表。驱动桩与离线界面不代替真实硬件验收。

### STM32H723 CAN 模拟设备实机台架

`scripts/test-can-hardware.py` 是显式启用的硬件测试，不进入普通测试或 CI。先退出占用分析仪的上位机，确认 CAN 总线只接本项目的 STM32H723 电机/IMU 模拟固件。分析仪需通过原生 USB 接电脑；BLE 地址、Wi-Fi 信任信息从指定桌面通道的本机缓存读取，不写入报告。

```bash
desktop/.venv/bin/python scripts/test-can-hardware.py --fixture stm32h723 \
  --port /dev/ttyACM0 --channel 1 --modes usb ble wifi \
  --duration 60 --poll-hz 100 --output dist/can-hardware.json
```

脚本切换分析仪的通信模式与 CAN 数据模式，接收真实总线报文；`--poll-hz 100` 为模拟 ID 1–4 分别启用 100 Hz 的 `0x7FF [ID_L,ID_H,CC,00]` 状态查询，并持续续租。默认 `--poll-hz 0` 不启用周期查询。连接后先确认停止旧查询，测试结束停止租约；退出时尝试恢复 USB + CAN。报告分别记录设备丢弃、CAN 错误、每个 ID/IMU 子类型的最大接收间隔和查询配置确认延迟；配置确认不代表电机反馈成功，仲裁竞争与 CAN 通信错误也需分别解释。失败时保留已收集的数据。可选 `--ble-diagnostics` 只用于启用了诊断特征的测试固件。

该台架验证物理 USB/BLE/Wi-Fi/CAN 链路与模拟协议反馈，不代表真实电机、Windows 蓝牙或 Android 手机已完成测试。

连接、订阅同步及 Wi-Fi 起始诊断不计入流量窗口。报告要求三个 DJI ID 和四类 IMU 反馈持续出现，启用周期查询时还要求四个达妙反馈 ID 持续出现。各流最长主机接收静默不超过 3 秒；台架停止发送也会使验收失败。Wi-Fi 分别在流量窗口前后采集队列和 TLS 诊断。`ok` 同时要求台架存活、无连接错误、设备 CAN 错误和转发丢弃增量为零；不满足时退出码非零，不能仅凭连接成功认定 CAN 测试通过。

Wi-Fi 报告记录实际 socket 源 IPv4；`--wifi-source-ip <本机IPv4>` 可拒绝走不同源地址的连接，适用于同路径 A/B。可同时增加 `--wifi-interface wlan0`，通过真实 UDP 身份回应选择该网卡，并核对 socket 实际绑定。选项仅限次重试连接，不改系统路由或重发 CAN 命令。已有 P0 诊断的队列深度和 TLS 计数适用于 CAN，但 `drop_*`/`lan_reject_*` 字段仅统计 UART 字节，不能用它们的零值推断 CAN 无丢弃。

图形会话内运行以下命令可验证真实 Qt 桌面的监控、DJI/IMU/DM 详情、100 Hz 状态查询、暂停过滤和首页摘要。`--mode` 可选 `usb`、`ble`、`wifi`。使用临时私有通道配置，结束后清理并恢复 USB + CAN。报告保留连接失败、应用/设备丢弃与每阶段截图；首页单独记录切换后一秒的接收基线。绘图耗时仅覆盖 JavaScript/SVG 更新回调，不包含显示合成器的实际绘制。

```bash
desktop/.venv/bin/python desktop/tests/can_render_hardware.py \
  --fixture stm32h723 --mode ble --phase-seconds 20 \
  --output dist/can-desktop-ble.json
```

CAN Qt 实机台架还可增加 `--detail-view stream` 或 `--detail-view fullscreen`，分别测试详情报文列表和全屏曲线。`--exercise-tabs` 会增加一个未连接临时通道，验证原通道后台切为静默、回到原标签后恢复原始流；所有配置仍位于临时目录，不修改用户的通道布局。渲染对照应避免同时编译、打包等 CPU 负载；`--plot-containment` 仅用于测试注入，不改变产品样式。

软件渲染对照可用 `--paint-hz 15|30|60` 临时限制自动详情绘图频率，不限制逐帧接收/记录或手动操作，默认 `0` 保持产品行为。报告分别记录当前进程、包含 Qt 子进程的总 CPU 时间及 renderer CPU 时间，内存为进程树 RSS 相加（共享页可能重复），不能当作独占内存或实际显示帧率。

图形会话内可离线复现跨通道全屏操作，不连接硬件、不读取用户通道配置：

```bash
desktop/.venv/bin/python desktop/tests/fullscreen_tabs.py --switches 100 \
  --settle-ms 100 --output dist/can-fullscreen-tabs.json
```

测试创建两个模拟已连接的 Qt 页面，交替切换通道并打开曲线，要求每次页面可见且原生窗口全屏、最终恢复最大化、隐藏通道的进入请求被忽略、关闭窗口后排队的全屏回调不能重新打开窗口、无 JavaScript 错误。此结果属于真实 Qt 窗口操作验证，不是 CAN 链路验证。

Qt CAN 实机台架每 5 秒记录一次进程树 RSS/CPU，并原子保存阶段报告，方便查看较长运行的资源趋势和保留中断现场；RSS 包含共享页，测试自身的绘图耗时数组也会随阶段收集，不能仅凭 RSS 上升认定内存泄漏。

通信台架的 Wi-Fi 报告还每 5 秒只读采集 Linux `TCP_INFO`：主机 RTT、收取字节、乱序接收、主机重传和窗口等数值，不收集报文内容。仅解码内核实际返回的字段长度，连接关闭后保留此前记录。主机发送侧重传是命令方向，不能当作 ESP32 发送 CAN 数据的重传统计；TCP 指标也不等同于 CAN 端到端延迟。`tests/test_linux_tcp_info.py` 对照本机 Linux 头文件检查偏移并通过实际 loopback 收发验证计数。

显示缩放可离线通过真实 Qt 快捷键和工具栏验证：

```bash
desktop/.venv/bin/python desktop/tests/display_zoom.py --output dist/can-display-zoom.json
```

Qt CAN 台架支持 `--zoom 1.5`、`--detail-window 60` 和 `--freeze-after 45 --phase-seconds 75 --detail-view fullscreen`，验证冻结期间仍持续接收、历史容量有界。阶段超过 60 秒时，按实际保留的 60 秒窗口检查采样数，而非要求历史无限增长。`--recompute-frozen` 仅在测试中关闭冻结图形缓存，供同源码/同台架对照；不会限制接收。资源记录用于观察进程树 CPU 与 RSS，暂停曲线不等于暂停数值或链路。

`tests/bench_frozen_chart.py --output <文件>` 使用 Playwright 做 60000 个可见合成采样、150 次冻结重画的 CPU 对照，支持 `--chromium <浏览器路径>`。强制检查可见样本与非空路径，防止空曲线测量假通过。结果仅覆盖 JS/SVG 回调，不代表 Qt/Android 实机帧率；实际台架另测。

可选 `--failure-observe-seconds 5` 会在 Wi-Fi 测试失败后停止周期心跳、保留接收任务，再被动观察 5 秒，记录 TCP 数值、额外收到的 CAN 帧及最近设备状态，不发送、重试或续租任何命令。原始测试窗口仍止于失败时刻，观察结果另列，结束后仍恢复 USB/CAN；默认 0 不增加等待。

`tests/bench_live_chart.py --output <文件>` 测量 60000 个合成采样的连续重画，分别覆盖 DM 四图、双温度全屏与 IMU 全部 14 图。记录路径与坐标标签的 SHA256，可核对隔离候选是否改变图形；支持 `--chromium <浏览器路径>`、`--detail-script <候选 motor_detail.js>`。所有图同时可见属于合成压力条件，耗时只覆盖 JS/SVG 回调。真实 Qt 台架同样支持 `--detail-script`，只替换 `drawDetailSeries`、`detailPeakPoints`，在报告中记录候选 hash；不替换接收、采样与传输代码，便于同源码的绘图对照。

`desktop/tests/csv_export.py --output <文件>` 通过真实 Qt WebChannel 发起最大 20000 条合成 CAN 历史的 CSV 导出，使用临时目录替代保存对话框。检查全部数据行、UTF-8 BOM、末行边界值与 JS 错误，不连接硬件。`desktop/tests/test_csv_export.py` 另覆盖 Remote/失败帧及非法字段的提前拒绝。

`BleNotificationsTest` 运行实际 Android 纯 Java 拆包器，覆盖旧/批量 UUID 选择、MTU23 单条、2/10/12 条通知、顺序、分拆所有权及整包非法时零投递。实体安卓测试另需手机和授权 ADB，主机测试不能替代手机 GATT 协商与吞吐验证。

`LanSubnetTest` 覆盖 IPv4 非整字节前缀和边界、IPv6/默认路由排除。`LanTransportTest` 使用真实本地 TLS 服务器验证 socket 注入仍执行证书和令牌校验，并检查空闲连接可及时关闭；Android 的 Conscrypt 与主线程网络限制仍须手机检查。

### Android CAN 实机六阶段

`tests/android_can_hardware.py` 要求授权 ADB、已启动的独立 `.bench` 包和该包的 WebView 调试；不允许直接操作正式包。先把分析仪设为对应 BLE/Wi-Fi 和 CAN 模式，释放电脑端连接；Wi-Fi 测试包须已有配对身份。脚本不烧录、不改系统网络、不授予权限、不修改 Wi-Fi 凭据。空台架列表会添加模拟器配置，已有列表不同则退出并保留。

```bash
python tests/android_can_hardware.py --fixture stm32h723 --serial <ADB序列号> \
  --mode wifi --ip <设备IP> --seconds 15 --output /tmp/android-can-wifi.json
```

需安装 Playwright。BLE 使用 `--mode ble --ble-address <设备地址>`。脚本自动分配并清理自己的 CDP 转发端口，依次检查原始流、DJI、IMU、达妙状态查询、暂停筛选及首页摘要。设备丢弃取本轮计数增量；等待查询能力报告后才发起状态查询。最后必须断开且 App PID 保持，才可 `ok=true`，避免数据阶段通过却在关闭 TLS 时闪退的假通过。报告另含安装 APK 摘要和工作区源文件摘要，二者分别标注，源文件摘要本身不是安装包一致性的证明。此脚本没有原生返回键/后台生命周期操作，须另做实机检查。

Linux 上同一手机/测试包加独占锁，第二个测试会在 ADB 操作前退出；SIGINT/SIGTERM 只记录停止请求，分段等待结束后执行断开及转发清理，避免从调试连接的事件循环中直接抛异常。 真实手机四查询就绪后发送 SIGTERM 的验证退出耗时 0.264 秒，连接断开且原进程保留，转发和锁释放；随后 2 秒 DAP 命令增量为零。未取得连接的前置检查失败不会断开已有通道。原始流及暂停过滤阶段还逐项要求三个 DJI ID、四类 IMU 的反馈持续出现，四查询模式额外检查四个达妙反馈 ID。

`--all-dm-queries` 会在连接后开启四个 STM32 虚拟达妙 ID 各 100 Hz 状态查询，并要求六阶段四个查询租约始终有效；不会发送使能或运动。可用 `--seconds 45` 增加每阶段观察时间。 `--raw-seconds 180` 只延长原始流阶段，保留其他阶段原时长，适合连续高吞吐测试；原始流和暂停过滤的每条必需反馈流还要求最长主机静默不超过 3 秒，包含窗口起止边界。原始流、暂停过滤和首页阶段须收到新的实际 CAN 状态，累计错误和总线状态均正常。协议模式 2 的详情主动抑制 type 4，详情没有这类状态不判失败，也不把旧缓存当作新状态；由前后累计计数及独立 DAP 观察补充。连接成功本身不算通过。

Wi-Fi 另加 `--discover-wifi` 会先搜索并要求缓存列表项以外的新 UDP 回应，随后用发现的地址连接；可用独立测试包的过期地址检查恢复，不改变设备或路由器 IP。`tests/test_network_discovery.py` 提取实际固件 UDP handler，覆盖完整 20 字节请求、旧版 19 字节前缀及其他长度/内容的拒绝。

`--observe-rendering` 可选记录每阶段 WebView 的 `onNative`、曲线绘制、详情报文绘制及长任务耗时，以及可见页面的 rAF 间隔。观察器不改变绘图或接收频率，用有界直方图保留计数、平均、最大值和 p95 上界（步长 1/16 ms，1000 ms 以上溢出时 p95 为 null）。阶段切换清空统计，退出恢复原函数并停止观察；测量自身也有少量开销。`--detail-window 60 --seconds 45` 可在真实详情逐渐累积历史后测量长时间窗，默认窗口 1 秒。rAF 间隔不是实体屏幕帧率，回调耗时不包含合成器或屏幕显示，不能用离线结果替代真实手机观察。

Android 实机报告还保留最多 128 个原生连接状态及相对 JS 观察起点的时间，用于区分发现失败与连接后中断；该时间不是物理 CAN 延迟。
