<p align="center">
  <img src="docs/assets/hero.svg" alt="BLE CAN Scope — ESP32-S3 蓝牙 CAN 分析仪与 Android App" width="100%">
</p>

<h1 align="center">BLE CAN Scope</h1>

<p align="center">
  <strong>把 CAN 总线状态，带到你的手机上。</strong><br>
  ESP32-S3 固件 × Android App · 蓝牙 / 局域网无线监控 · 电机与 IMU · 本机屏幕
</p>

<p align="center">
  <a href="https://github.com/QianmoNai/BLE-CAN-Scope/releases/tag/v1.19.0"><img src="https://img.shields.io/badge/Firmware-1.19.0-65e6cb?style=flat-square&amp;labelColor=17212f" alt="Firmware 1.19.0"></a>
  <a href="https://github.com/QianmoNai/BLE-CAN-Scope/releases/tag/v1.19.0"><img src="https://img.shields.io/badge/Android_App-1.18.1-88baff?style=flat-square&amp;labelColor=17212f" alt="Android App 1.18.1"></a>
  <img src="https://img.shields.io/badge/Transport-Bluetooth_LE-65e6cb?style=flat-square&amp;labelColor=17212f" alt="Bluetooth LE">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-GPL--3.0-ccd6e0?style=flat-square&amp;labelColor=17212f" alt="GPL-3.0-only"></a>
</p>

<p align="center">
  <a href="#download"><strong>下载安装</strong></a> ·
  <a href="#preview">界面预览</a> ·
  <a href="#quickstart">快速上手</a> ·
  <a href="#hardware">硬件接线</a> ·
  <a href="#build">源码构建</a> ·
  <a href="#docs">文档</a>
</p>

---

**BLE CAN Scope** 是一套包含 ESP32-S3 固件和 Android App 完整源码的蓝牙 CAN 监控工具。通过 BLE 同时连接多个 CAN 通道，在手机上查看电机状态、IMU 参数、实时曲线和原始报文；也可以通过设备上的屏幕与按键查看总线状态。

<sub>Bluetooth LE CAN analyzer with ESP32-S3 firmware and an Android app. Multi-device monitoring, motor and IMU decoding, live charts, and an onboard display.</sub>

当前源码固件 **1.21.0** / App **1.20.0** 支持蓝牙安全配网、局域网通信选择，以及实体按键切换蓝牙蓝色 / Wi-Fi 紫色界面。固件提供蜂鸣器低 / 中 / 高三档试验音量、页面滑动、菜单选中框移动和蓝紫主题渐变；1.20.3 修正整屏切换时的 DMA 调度等待，并柔化页面滑动起步；1.20.4 将四个方向的亮度加减对调，统一为上方或左侧按键增加、下方或右侧按键减少，详见 [本机面板操作](docs/LOCAL_PANEL.md)。原有蓝牙保留，使用步骤见 [Wi-Fi 配网与通信说明](docs/WIFI.md)。上方徽章仍指向已发布版本。

<a id="preview"></a>

## 从设备总览，到每一帧反馈

<table>
  <tr>
    <td align="center" width="50%"><strong>多通道设备总览</strong><br><sub>通道、设备 ID 与连接状态，一屏查看</sub></td>
    <td align="center" width="50%"><strong>单设备参数曲线</strong><br><sub>独立详情、实时反馈、点击曲线全屏查看</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="docs/assets/app-overview.png" width="330" alt="App 首页：多通道电机卡片，使用模拟数据"></td>
    <td align="center"><img src="docs/assets/app-detail.png" width="330" alt="App 电机详情：参数曲线，页面向下滚动，使用模拟数据"></td>
  </tr>
</table>

<sub>以上为实际 App WebView 页面在浏览器中的渲染，使用模拟 CAN 数据，不是手机实测截图。详情图展示向下滚动后的曲线区域。</sub>

### 离开手机，也能查看总线

<p align="center">
  <img src="docs/local-panel-preview.png" alt="ST7789 中文总览与设置两页，横竖屏模拟数据" width="728">
</p>

<sub>本机页面由固件实际绘制代码生成，使用模拟数据。中文总览与设置两页，支持背光亮度、声音开关、三档音量、通信模式与 0°/90°/180°/270° 四方向，设置断电保存；本机不显示报文数据流。</sub>

## 为机器人调试准备

| 能力 | 可以做什么 |
| --- | --- |
| **蓝牙多通道** | 同时连接多台 ESP32，按通道管理设备，一键重连上次成功连接的设备 |
| **电机状态卡片** | 显示来源通道、电机 ID、反馈 CAN ID；左滑删除、右滑配置、长按拖动排序 |
| **实时参数曲线** | 独立查看电机或 IMU 参数，点击曲线进入横屏全屏，查看对应 CAN 数据流 |
| **达妙持续查询** | 每台电机独立开关与频率，默认 100Hz，可调 1～1000Hz |
| **CAN 报文监控** | CAN ID 与 RX/TX 筛选、记录保存上限、发送失败原因、设备端帧率和估算负载 |
| **配置备份** | 导入/导出通道、电机及重连记录，设备蓝牙名称断电保存 |
| **本机屏幕** | 中文总览与设置两页，四方向布局，三个按键、PWM 背光及蜂鸣器提示 |

### 支持的设备

| 类别 | 型号 / 协议 | 说明 |
| --- | --- | --- |
| 大疆电机 | C620 / M3508、C610 / M2006、GM6020、GM3510 | 按型号解析反馈；GM3510 部分参数保留原始值 |
| 达妙电机 | DM4310 及更多型号预设、自定义解码范围 | 独立 ID / Master ID 配置，支持状态查询 |
| 达妙 IMU | DM-IMU-L1 | 主动 CAN 上报，加速度、角速度、姿态角、四元数等 |

具体型号、量程与协议来源见 [电机型号表](docs/MOTOR_CATALOG.md) 和 [DM-IMU-L1 说明](docs/DM_IMU_L1.md)。

<a id="download"></a>

## 下载与安装

**[前往 Release 下载 →](https://github.com/QianmoNai/BLE-CAN-Scope/releases/tag/v1.19.0)**

| 下载内容 | 版本 | 适用环境 |
| --- | --- | --- |
| [Android APK](https://github.com/QianmoNai/BLE-CAN-Scope/releases/download/v1.19.0/BLE-CAN-Scope-Android-1.18.1-debug.apk) | **1.18.1** | Android 8.0 / API 26 及以上，支持 BLE |
| [ESP32-S3 烧录包](https://github.com/QianmoNai/BLE-CAN-Scope/releases/download/v1.19.0/BLE-CAN-Scope-ESP32-S3-1.19.0.zip) | **1.19.0** | ESP32-S3 Mini，4MB Flash；包含三个镜像与烧录说明 |
| [SHA-256 校验文件](https://github.com/QianmoNai/BLE-CAN-Scope/releases/download/v1.19.0/SHA256SUMS) | 本次发行 | 核对下载附件完整性 |

> 固件与 App 独立编号，以上版本配套使用。仓库当前为私有，下载需要登录具有访问权限的 GitHub 账号。APK 使用开发签名；更换签名需要卸载时，请先导出配置。

<a id="quickstart"></a>

## 快速上手

1. **接线与供电** — 连接 SN65HVD230 与 ESP32，CANH/CANL 对应接入总线，所有模块及被测设备共地。
2. **烧录与安装** — 下载固件包，按包内 `README.txt` 烧录；安装 Android APK。
3. **蓝牙连接** — 开启手机蓝牙，授予扫描与连接权限，在 App 添加设备建立通道。
4. **配置设备** — 设置与总线一致的 CAN 波特率，添加电机或 IMU，填写设备 ID 和反馈 CAN ID。
5. **开始监控** — 点击首页卡片查看曲线；达妙电机按需开启持续查询，IMU 使用主动上报。在通道页查看原始 CAN 报文。

```text
电机 / IMU ── CAN 总线 ── SN65HVD230 ── ESP32-S3 ── BLE ── Android App
                                         │
                                  ST7789 屏幕 + 按键
```

每台 ESP32 对应一条 CAN 总线；App 可以同时管理多个 BLE 通道。详情页按当前设备订阅所需数据，减少无关蓝牙传输。

<a id="hardware"></a>

## 硬件接线

**ESP32-S3 Mini + SN65HVD230 + 1.47 英寸 ST7789（172×320）**，配三个按键和 3V 有源蜂鸣器。CAN 默认 **1Mbit/s**。

| 功能 | GPIO | 连接 |
| --- | --- | --- |
| CAN 发送 / 接收 | **4 / 5** | SN65HVD230 CTX / CRX |
| BT1 / BT2 / BT3 | **1 / 2 / 7** | 按键另一端接 GND，内部上拉 |
| 背光 PWM | **6** | 背光 MOSFET 栅极 |
| LCD RESET / D-C / CS | **8 / 9 / 10** | 屏幕复位、命令、片选 |
| LCD MOSI / SCLK | **11 / 12** | SPI 数据与时钟 |
| 有源蜂鸣器 | **13** | 蜂鸣器 MOSFET 栅极 |

CAN 终端电阻按总线两端布置。完整供电、插座引脚顺序及面板适配说明见 [硬件文档](hardware/README.md) 和 [本机面板操作](docs/LOCAL_PANEL.md)。

<a id="build"></a>

## 从源码构建

在仓库根目录执行下面的命令。

### ESP32-S3 固件

当前固件源码版本 **1.21.0** 提供中文界面、切换动画和四方向设置，菜单选择随屏幕方向映射；亮度编辑统一为上方或左侧按键增加、下方或右侧按键减少；方向编辑固定 BT1 顺时针、BT3 逆时针；声音、音量和通信模式选中后中键直接切换。本机报文页已移除，上方下载表仍指向已发布版本。

新增屏幕按键配对的“双机A端”（绿色）和“双机B端”（黄色），两端各接独立 CAN 总线，通过 ESP-NOW 双向转发，无需 App。配对步骤、1 Mbit/s 默认 CAN 配置和测试范围见 [双机 CAN 转发](docs/DUAL_CAN.md)。

已验证工具链：**ESP-IDF v6.0.2**。先安装 ESP-IDF 并激活环境，再执行：

```bash
source /path/to/esp-idf/export.sh
bash firmware/idf.sh build
bash firmware/idf.sh -p /dev/ttyACM0 -b 115200 flash
bash firmware/idf.sh -p /dev/ttyACM0 monitor
```

默认构建目录位于 `${XDG_CACHE_HOME:-$HOME/.cache}/can-scope-firmware/<项目路径哈希>/esp32s3`；缓存实际路径含非 ASCII 字符时自动改用 `/tmp/can-scope-$UID`，避免工具链路径解析错误。可用 `CAN_IDF_BUILD_DIR` 指定目录。首次迁移会保留旧 `firmware/build/esp32s3.sdkconfig`；同一构建目录拒绝并发运行。不需要 PSRAM。屏幕偏移、色序和本机面板开关可通过 `bash firmware/idf.sh menuconfig` 调整。升级无需全片擦除，保留 NVS 中的蓝牙名称及面板设置。

**VS Code ESP-IDF 扩展**：直接打开 `firmware` 文件夹时，扩展默认使用 `firmware/build`，不会经过上述脚本。若项目的真实路径包含中文，请在工作区设置中将 `idf.buildPath` 指向纯英文绝对路径，例如 `/home/<用户名>/.cache/can-scope-vscode/esp32s3`，并将 `clangd.arguments` 中的 `--compile-commands-dir` 指向同一目录；确认该目录经符号链接解析后的真实路径也不含中文。继续使用现有 `firmware/sdkconfig`，重新构建即可。`cannot read spec file .../picolibc.specs` 且错误路径中出现换行或 `\x0a` 时，应先检查构建路径。

固件默认关闭应用、NimBLE 和 Bootloader 日志，CAN 接收任务不格式化串口日志；App 和本机屏幕仍正常显示报文及统计。芯片 ROM 的启动信息仍可能输出。已有构建配置不会被 `sdkconfig.defaults` 覆盖，需在 `menuconfig` 中将应用、NimBLE 和 Bootloader 的日志级别设为 `None`，或使用新的构建目录。

保留旧 ESP32 目标兼容路径：`CAN_IDF_TARGET=esp32 bash firmware/idf.sh build`；旧目标使用 GPIO21/22，默认不启用本机屏幕。本次发布主要验证 ESP32-S3。

### Android App

需要 **JDK 17、Android SDK Platform 35、Build Tools 35.0.0、Python 3**。当前项目使用命令行 SDK 构建脚本，不依赖 Gradle。

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
bash android/build.sh
```

产物：`android/dist/CAN-Scope-debug.apk`。脚本在本机生成开发签名，密钥不随源码分发。自行构建 APK 的签名与作者 APK 不同，可能无法直接覆盖安装；如需卸载旧版本，请先在 App 内导出配置。

Android 默认缓存为 `${XDG_CACHE_HOME:-$HOME/.cache}/can-scope-android/<项目路径哈希>`，可用 `CAN_ANDROID_BUILD_DIR` 指定目录。同一目录拒绝并发构建；每次清理旧 class/dex，签名验证通过后原子替换 APK。当前源码 App 版本为 **1.18.2**，包含[采样、绘图和首页更新优化](docs/PERFORMANCE.md)。上方下载表仍指向已发布版本。

<details>
<summary><strong>目录结构</strong></summary>

```text
BLE-CAN-Scope/
├── firmware/     ESP-IDF 工程与 ESP32-S3 固件
├── android/      Android Java + WebView App
├── hardware/     接线说明（尚未纳入 PCB 原始工程）
├── docs/         协议、型号、面板说明与预览素材
├── tests/        C / Java / JavaScript / 浏览器测试
└── scripts/      主机测试脚本
```

</details>

<a id="docs"></a>

## 文档导航

| 我想了解… | 文档 |
| --- | --- |
| 蓝牙数据包、页面订阅与统计协议 | [BLE 通信协议](docs/PROTOCOL.md) |
| 电机帧格式、型号和参数量程 | [电机协议](docs/MOTOR_PROTOCOLS.md) · [型号目录](docs/MOTOR_CATALOG.md) |
| 达妙 IMU 的 CAN 数据解析 | [DM-IMU-L1](docs/DM_IMU_L1.md) |
| 屏幕、按键、亮度与声音设置 | [本机面板](docs/LOCAL_PANEL.md) |
| 编译检查、测试方法与验证结果 | [测试指南](tests/README.md) · [发布验证](docs/VALIDATION.md) |
| 外部框架和协议资料来源 | [依赖与来源](docs/DEPENDENCIES.md) |

## 使用范围

- **经典 CAN**：不支持 CAN FD。接口正常参与 ACK，并非纯监听分析仪。
- **状态监测**：App 不提供电机运动控制界面；固件保留历史原始发帧及受限控制协议，本机按键不会触发这些控制指令。
- **频率与带宽**：查询和采样配置上限不代表实际吞吐，高频 CAN 经 BLE 转发不保证逐帧无损。
- **估算负载**：`LOAD EST.` 使用成功收发帧的基础位数，不包含位填充、错误帧或失败尝试，不是精确物理占用率。
- **验证范围**：已完成构建、相关回归及 ESP32-S3 启动/CAN 接收检查；实体屏幕、全部电机型号和手机尚未逐项实测。Release 中重新构建的镜像验证范围以发布说明为准。

## 参与开发

运行 `bash scripts/test-host.sh` 检查主机测试。界面改动可按 [测试指南](tests/README.md) 执行 Playwright 回归；反馈问题时附上固件/App 版本、设备型号、CAN 波特率与复现步骤。

## 许可证

[GNU GPL v3.0](LICENSE) · `GPL-3.0-only` · Copyright © 2026 QianmoNai。

嵌入的中文点阵字形子集采用 [SIL OFL 1.1](firmware/fonts/OFL-SourceHanSans.txt)，来源及生成说明见 [字库说明](firmware/fonts/README.md)。

外部依赖和协议资料遵守各自许可。本项目不隶属于大疆、达妙或乐鑫，品牌与型号用于说明兼容设备。

---

<p align="center">
  <a href="https://github.com/QianmoNai/BLE-CAN-Scope">GitHub</a> ·
  <a href="https://gitee.com/qianmonai/BLE-CAN-Scope">Gitee</a> ·
  <a href="https://github.com/QianmoNai/BLE-CAN-Scope/releases/tag/v1.19.0">Release</a>
</p>
