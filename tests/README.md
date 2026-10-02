# 构建与测试

在仓库根目录执行。主机测试不连接 BLE 设备，不发送实际 CAN 帧。

## C / Java / JavaScript 测试

依赖 C 编译器、Python 3、Node.js 和 JDK 17：

```bash
bash scripts/test-host.sh
```

覆盖 CAN 所有者循环、轮询查询、错误计数、速率和估算负载、BLE 数据筛选、ACK 调度、电机/IMU 订阅、蓝牙名称存储、本机按键与屏幕逻辑、App 查询和重连配置。

## 浏览器界面测试

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install playwright
python -m playwright install chromium
python tests/test_home_sort.py
python tests/test_motor_detail.py
python tests/test_imu_ui.py
python tests/test_monitor.py
```

测试采用 Playwright 自带 Chromium 和模拟的 Android 桥接器。Linux 缺少浏览器系统依赖时，参考 Playwright 的安装输出处理。其余 `tests/test_*.py` 为可单独执行的界面回归脚本；`test_query_tx_filter.py` 为主机 C 测试封装。

## 编译与烧录

固件使用 ESP-IDF v6.0.2；App 使用 JDK 17、Android API 35 和 Build Tools 35.0.0。命令见根目录 README。测试通过不代表每一种电机、屏幕或 Android 手机已经通过实物验证。

`render_board_ui.c` 可以在主机绘制本机三页预览（模拟数据）：

```bash
cc -std=c11 -Ifirmware/main tests/render_board_ui.c firmware/main/board_ui_render.c firmware/main/board_ui_model.c -o /tmp/render-board-ui
/tmp/render-board-ui /tmp
```

生成 `/tmp/panel-0.ppm`、`panel-1.ppm`、`panel-2.ppm`。
