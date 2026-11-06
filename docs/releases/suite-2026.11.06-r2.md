# CAN Scope 完整套件 · 2026.11.06-r2

固件 **1.24.8** / Android **1.21.15（55）** / Desktop **1.2.24（Windows、Linux x64）**。
产品代码及桌面构建提交：`4e40706b8e0f90e007d6dee1203d3ff423b6e5bc`；发布标签：`suite-2026.11.06-r2`。包含 STM32H723 双 CAN 模拟器及微秒压力源源码。

## 相对上一完整套件的更新

- 固件：双机确认会话后缓存派生密钥，保持原线格式；目的 CAN 仅对明确的纯仲裁失败允许最多三次总发送，保留原始 100ms 寿命及会话代次，补齐终端失败/取消丢弃计数。混合、未知和通信失败不自动重放。
- Android：按块读取 TLS 明文并逐帧交付，减少详情批次内重复配置核对及长历史曲线计算；修正空断线原因和确认超时提示。
- 桌面及共享界面：减少长历史详情处理开销，显示实际查询频率；App 丢弃计数变化后立即刷新标签，不再等待设备状态。新增 CUSB CRC 拒绝候选数、丢弃字节数诊断，两者均不等同于 CAN 丢帧数。
- 测试工具：微秒定频 STM32 源，独立核对实际发送速率、唯一序号、目的 CAN、确认与设备/App 丢弃，保留未通过候选和高负载边界。

**已知限制：默认无线 PHY 本次仍出现双机目的 CAN 送达超时。双向各 200 帧/秒的通过成绩来自单独的近距离 6 Mbps 实验镜像，不适用于本附件默认固件。无线高负载长期无损问题尚未解决。**

## 下载与安装

Android：`CAN-Scope-Android-1.21.15-debug.apk`，Android 8.0+，沿用包名及开发签名，升级保留数据；正式 APK 未启用调试。手机支持 BLE/Wi-Fi，尚未实现 USB Host；独立台架调试 APK 不发布。

桌面：Windows 10/11 x64 ZIP、Ubuntu 24.04 构建的 Linux x64 TAR.GZ，含完整依赖，无需 Python。校验 `SHA256SUMS` 后完整解压运行 `CAN-Scope.exe` 或 `CAN-Scope`；Windows 未代码签名。

- [Windows x64 完整包](https://github.com/QianmoNai/Wireless-CAN-Analyzer/releases/download/suite-2026.11.06-r2/CAN-Scope-Desktop-1.2.24-Windows-x64.zip)
- [Linux x64 完整包](https://github.com/QianmoNai/Wireless-CAN-Analyzer/releases/download/suite-2026.11.06-r2/CAN-Scope-Desktop-1.2.24-Linux-x64.tar.gz)

GitHub 提供全部完整附件。Gitee 继续托管固件、APK、源码及校验文件；此前仓库附件已达到 1 GB 配额，桌面包通过同版 GitHub 链接及 `DESKTOP-DOWNLOADS.txt` 下载，保留历史发行。

固件：`BLE-CAN-Scope-ESP32-S3-1.24.8.zip`，仅适用于本项目 ESP32-S3 / 4 MB Flash。含 bootloader、分区表、应用、实际 flash_args、Linux/Windows 烧录脚本、构建信息、SDK 配置及内部校验。默认分段烧录，保留 NVS，不全片擦除；按包内说明选择端口。默认关闭实验双机 6 Mbps、测试回环及 BLE 诊断；CAN TLS 批次 64、UART TLS 批次 16，UART retry=25ms / TTL=1000ms，实验 TCP_NODELAY/关闭省电均 OFF。只支持经典 CAN，不支持 CAN FD。

## 验证及适用范围

- 完整主机检查、161 项本机桌面单测通过；[主机及桌面 CI](https://github.com/QianmoNai/Wireless-CAN-Analyzer/actions/runs/42202394067) 和 [Windows/Linux 打包 CI](https://github.com/QianmoNai/Wireless-CAN-Analyzer/actions/runs/42202396004) 通过，后者包含源码及封装后的 UI 启动检查。发布的两个桌面包直接来自该 CI。
- 本机 Arch/Python 3.14 的 Desktop1.2.24 实包与 Android1.21.15 独立台架包，配合真实 CAN、STM32 模拟反馈及固件1.24.8实验6Mbps镜像完成：桌面 USB/BLE/Wi-Fi 各六阶段20秒，四档 CAN 速率及四类手动帧、10000条历史、详情参数、全屏、暂停/继续及配置/CSV导出；手机 Wi-Fi/BLE 各六阶段20秒及分项诊断。实际 Qt 与手机 WebView 的计数标签检查通过。
- 正式 Android APK 已原位安装并核对摘要/版本及前台活动；手机 DOM 自动化来自代码和资源一致的独立台架包。CI 的 Ubuntu Linux 包与实体台架上的 Arch 包是不同二进制，不将同源码测试写成 CI 包的实体 CAN 验收。Windows 没有实体 CAN 台架验证。
- 固件默认 PHY 和显式6Mbps两个构建已通过编译。实验6Mbps镜像双向100/200/200帧每秒、各60秒，每端6000/12000/12000条完整交付且确认/丢弃通过，另100组同时双向格式检查通过。默认PHY的原版→候选→原版均已握手在线，但首组同时双向目的CAN送达超时；未将实验结果作为默认附件的可靠性资格。实验镜像不在正式附件内。

## 高负载仍需解决的问题与使用取舍

以下原始流基线使用固件1.24.7实验6Mbps构建（该 PHY 开关只影响双机）、Android1.21.14、本机 Desktop1.2.23；不能当作新版所有环境的长期上限：

| 方式 | 已观察结果与取舍 |
| --- | --- |
| USB | 桌面6000帧/秒档两轮各180000条全收；7500档长测仍偶发缺帧。预算、聚合、直达及入队短等候选未证明稳定无损，产品保留原实现。完整原始帧记录优先USB并持续检查丢弃。 |
| Wi-Fi / BLE | 三种接收表面的2000档短测全收，更高档发生转发丢弃；BLE桌面/手机GUI的4000档已缺帧。仅看参数时可显式选择详情限频，保守100Hz；采样不是全总线原始帧记录。 |
| 双机 | 新版实验6Mbps的各200档有上述限定改进；各250档目的CAN虽全收，确认仍过期，默认PHY交付问题仍在。完整高负载转发需减少源端非必要报文或采用足够带宽的有线连接。 |

历史间歇 Wi-Fi/TLS 超时、双 BLE 同时高频的设备丢弃仍待定位。未验证真实电机/IMU、无线距离、长期满负载或手机 USB Host；本轮 CAN 专项不代替完整 UART 硬件回归。页面筛选、暂停和缩小历史不降低链路流量。

详细条件及失败记录见 `docs/PERFORMANCE.md`、`docs/CAN_HIGH_LOAD.md`、`docs/AUTONOMOUS_TESTING.md`。暂停优化状态保持，不为本次发布重新启动硬件压力测试。设备凭据、私有配置、诊断实验候选和展示视频不在附件内。

GitHub：https://github.com/QianmoNai/Wireless-CAN-Analyzer/releases/tag/suite-2026.11.06-r2
Gitee：https://gitee.com/qianmonai/Wireless-CAN-Analyzer/releases/suite-2026.11.06-r2
