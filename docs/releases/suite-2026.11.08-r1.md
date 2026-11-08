# CAN Scope 完整套件 · 2026.11.08-r1

固件 **1.24.9** / Android **1.21.16（56）** / Desktop **1.2.24**，对应源码提交 `bacd5a62a97264d59fc18f3472a9d1714bb31cc3`。

## 相对 suite-2026.11.08 的更新

- Android 与桌面共享界面新增 UART 无线串口 VOFA+ 波形显示，支持 FireWater 文本和 JustFloat 二进制协议。
- 波形支持 BLE、Wi‑Fi、USB 数据分包重组、暂停/恢复、通道选择、显示窗口和采样率显示。
- 波形采样使用有界 4096 点环形缓存，避免长时间运行导致内存持续增长。
- 波形协议、通道数、显示窗口偏好保存到本地，重启或页面重载后恢复。
- 修正 Android WebView 对波形资源的访问白名单，并补充跨端回归测试。

## 验证范围

- 主机、固件、桌面单元测试及 GitHub Linux/Windows CI 通过。
- 安卓 APK 已在 Redmi K20 Pro（ADB 设备 `72eaf0fc`）安装并启动，未发现崩溃或 WebView 错误。
- STM32 正式镜像已恢复，烧录前后 SHA-256 一致。
- 本轮实体高负载测试的无损实测边界：USB 至 7500 Hz；BLE 3500 Hz，4000 Hz 首次失败；Wi‑Fi 2500 Hz，3000 Hz 首次失败。该数据是当前台架的实测边界，不是理论最大值。
- 双机协议和配对测试通过；实体 CAN2→CAN1 仍有目标帧无法到达的问题，需继续检查收发器、接线、终端和 ACK 链路。

## 下载

先校验 `SHA256SUMS`，再完整解压或安装。固件按包内 `flash_args` 分段烧录并保留 NVS，不要全片擦除。Gitee 因附件配额继续通过 `DESKTOP-DOWNLOADS.txt` 提供同版 GitHub 桌面包链接。
