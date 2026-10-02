# BLE CAN Scope

**蓝牙 CAN 分析仪：ESP32-S3 固件与 Android App 完整源码。**

通过 Bluetooth LE（BLE）将经典 CAN 数据无线传输到手机，支持多通道设备连接、大疆与达妙电机状态监测、达妙 IMU 数据解析、实时曲线和 CAN 报文查看。设备端配备屏幕与按键，可独立查看总线状态。

Bluetooth LE CAN analyzer with ESP32-S3 firmware and Android app source code. Multi-device BLE connectivity, DJI/Damiao motor monitoring, Damiao IMU decoding, live charts, CAN frame inspection, and onboard display support.

## 主要功能

- **无线多设备监控**：App 同时管理多个 BLE CAN 通道，一键重连上次成功连接的设备。
- **电机总览**：卡片显示通道、电机 ID、反馈 CAN ID 和连接状态；左滑删除、右滑配置、长按拖动排序。
- **独立详情**：按电机/IMU 查看反馈参数、动态曲线与对应 CAN 数据流；曲线支持横屏全屏。
- **协议支持**：大疆 C620/M3508、C610/M2006、GM6020、GM3510；达妙多种电机预设及自定义解码范围；DM-IMU-L1 主动上报。
- **达妙状态查询**：每台电机独立设置查询频率，默认 100Hz，可调 1～1000Hz；实际速率取决于总线与无线带宽。
- **报文监控**：CAN ID、RX/TX 筛选、独立保存上限、通信失败原因、设备端速率统计与估算负载。
- **配置管理**：导入/导出通道、电机及重连记录，设备蓝牙名称断电保存。
- **本机显示**：ST7789 总览、最近报文和设置页面，支持按键、背光 PWM、声音提示及 180° 翻转。

App 以状态监测为主，不提供电机运动控制界面。固件仍保留历史原始发帧/受限控制协议，详见协议文档；本机按键不会触发这些控制指令。CAN 接口正常参与 ACK，并非纯监听分析仪。

**支持经典 CAN，不支持 CAN FD。** `LOAD EST.` 按成功收发帧的基础位数估算，不包含位填充、错误帧及失败重试。高频 CAN 到 BLE 的转发不保证逐帧无损。

## 版本与目录

当前固件 **1.19.0**，Android App **1.18.1**（最低 Android 8.0 / API 26）。版本号独立，当前两者配套使用。

```text
firmware/       ESP-IDF 工程、ESP32-S3 固件
android/        Android Java + WebView App 及构建脚本
hardware/       硬件连接说明（不含尚未提供的 PCB 原始文件）
docs/           蓝牙协议、电机/IMU 说明与本机面板说明
tests/          C、Java、JavaScript 与界面回归测试
scripts/        主机测试脚本
```

## 硬件

ESP32-S3 Mini + SN65HVD230 + 1.47 英寸 172×320 ST7789 SPI 屏、三个按键及 3V 有源蜂鸣器。CAN 默认 **1Mbit/s**。

| 功能 | GPIO |
| --- | --- |
| CAN CTX / CRX | 4 / 5 |
| BT1 / BT2 / BT3（另一端接 GND） | 1 / 2 / 7 |
| 背光 MOSFET 栅极 | 6 |
| LCD RESET / D-C / CS | 8 / 9 / 10 |
| LCD MOSI / SCLK | 11 / 12 |
| 有源蜂鸣器 MOSFET 栅极 | 13 |

所有模块与被测设备共地，CANH/CANL 对应连接；终端电阻按总线两端布置。详情见 [硬件接线](hardware/README.md)与[本机面板操作](docs/LOCAL_PANEL.md)。

## 构建 ESP32 固件

已验证工具链：**ESP-IDF v6.0.2**。先安装 ESP-IDF 并激活环境，再执行：

```bash
source /path/to/esp-idf/export.sh
bash firmware/idf.sh build
bash firmware/idf.sh -p /dev/ttyACM0 -b 115200 flash
bash firmware/idf.sh -p /dev/ttyACM0 monitor
```

默认构建目录为 `firmware/build/esp32s3`，不需要 PSRAM。屏幕偏移、色序和本机面板开关可通过 `bash firmware/idf.sh menuconfig` 调整。升级无需全片擦除，保留 NVS 中的蓝牙名称及面板设置。

保留旧 ESP32 目标兼容路径：`CAN_IDF_TARGET=esp32 bash firmware/idf.sh build`；旧目标使用 GPIO21/22，默认不启用本机屏幕。本次发布主要验证 ESP32-S3。

## 构建 Android App

需要 **JDK 17、Android SDK Platform 35、Build Tools 35.0.0、Python 3**。当前项目使用命令行 SDK 构建脚本，不依赖 Gradle。

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
bash android/build.sh
```

产物：`android/dist/CAN-Scope-debug.apk`。脚本在本机生成开发签名，密钥不随源码分发。自行构建 APK 的签名与作者 APK 不同，可能无法直接覆盖安装；如需卸载旧版本，请先在 App 内导出配置。

## 使用

1. 给 CAN 模块、ESP32 与被测设备正确接线、共地并供电。
2. 手机打开蓝牙，允许 App 的蓝牙扫描/连接权限，添加设备建立通道。
3. 设置与总线相同的 CAN 波特率，添加电机或 IMU，填写电机 ID 与反馈 CAN ID。
4. 从首页卡片进入详情页查看参数；达妙电机按需开启持续查询，IMU 使用主动上报模式。
5. 在通道的 CAN 监控页查看报文和速率；配置可导出备份。

## 文档与测试

- [蓝牙通信协议](docs/PROTOCOL.md)
- [电机协议与量程说明](docs/MOTOR_PROTOCOLS.md)
- [支持的电机型号](docs/MOTOR_CATALOG.md)
- [达妙 DM-IMU-L1](docs/DM_IMU_L1.md)
- [本机屏幕、按键与声音](docs/LOCAL_PANEL.md)
- [构建与测试](tests/README.md)
- [外部依赖与协议来源](docs/DEPENDENCIES.md)

![本机屏幕示意，模拟数据](docs/local-panel-preview.png)

固件已在 ESP32-S3 完成烧录与启动检查，并观察到实时 CAN 接收；不代表所有型号、电气工况、手机或面板均已完成实物验证。浏览器测试使用模拟传输，不等同于手机 BLE 实测。

## 许可证

本项目采用 [GNU GPL v3.0](LICENSE)（SPDX：`GPL-3.0-only`）。Copyright (C) 2026 QianmoNai。外部依赖与协议资料按各自许可使用，见 [依赖说明](docs/DEPENDENCIES.md)。
