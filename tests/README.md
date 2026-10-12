# 构建与测试

在仓库根目录执行。主机测试不连接 BLE 设备，不发送实际 CAN 帧。

## C / Java / JavaScript 测试

依赖 C 编译器、Python 3、Node.js 和 JDK 17：

```bash
bash scripts/test-host.sh
```

覆盖 CAN 所有者循环、轮询查询、错误计数、速率和估算负载、BLE 数据筛选、ACK 调度、电机/IMU 订阅、蓝牙名称存储、本机按键与屏幕逻辑、App 查询和重连配置，以及有界接收、连接优先级、环形历史、WebView 资源路由和构建脚本保护。

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
