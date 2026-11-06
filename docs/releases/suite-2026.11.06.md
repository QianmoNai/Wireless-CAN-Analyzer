# CAN Scope 完整套件 · 2026.11.06

固件 **1.24.7** / Android **1.21.10（50）** / Desktop **1.2.20（Windows、Linux x64）**。
源码提交：`345a595d835b14023ed0a183e30b6d1fbbea1263`；包含STM32H723双CAN模拟器源码。

## 本次更新

- 通信：BLE批量通知及MTU协商；Wi-Fi CAN批次和调度、多网卡发现、Android VPN下设备连接与异步TLS关闭；USB握手首命令、取消清理、身份重连和管理回压隔离修复。
- 显示：首页摘要、详情按需订阅和隐藏通道静默；减少报文历史复制及DOM分配，电机曲线合并扫描且保留极值/采样；实际渲染器合成大历史的曲线回调耗时下降约12%～36%，不是屏幕FPS或吞吐提升。
- 操作：查询开关等待最终设备确认时保留目标值并锁定重复操作；修复Android就绪前通知丢弃、在线导入检查、返回键及桌面跨通道全屏恢复。
- 工具：按物理USB身份自主配对、双路独立CAN速率台架、只读丢弃分项及可重复物理反馈/双通道测试入口。

## 下载与安装

Android直接安装`CAN-Scope-Android-1.21.10-debug.apk`，Android 8.0+，沿用正式包名/开发签名，升级保留配置；不包含独立台架调试包。手机支持BLE/Wi-Fi，尚未实现USB Host。

桌面提供Windows 10/11 x64 ZIP和Ubuntu 24.04构建的Linux x64 tar.gz；GitHub直接下载完整压缩包，Gitee下载同平台的全部`.partNN`分卷并按`MERGE-INSTRUCTIONS.txt`合并。使用`SHA256SUMS`核验，再完整解压运行`CAN-Scope.exe`或`CAN-Scope`，无需Python。Windows未代码签名。请勿混用旧版或不同站点的分卷。

固件仅适用于本项目ESP32-S3 / 4MB Flash；ZIP含bootloader、分区表、app、Linux/Windows烧录脚本、构建信息和校验文件，按包内说明选择实际端口，不执行全片擦除。默认固件关闭实验双机6Mbps、测试回环和BLE诊断；CAN TLS批次64、UART TLS批次16，UART retry=25ms / TTL=1000ms，实验TCP_NODELAY/关闭省电均OFF。

## 验证与边界

完整主机检查、157项桌面单测及CI通过。桌面两平台来自成功CI run [42132264994](https://github.com/QianmoNai/Wireless-CAN-Analyzer/actions/runs/42132264994)（构建提交c54b9c7，与发布提交产品源码相同）；CI包含单测、源码及打包后UI smoke。Linux发布包另在本机离屏启动通过；Linux共享资源逐字节匹配源码，Windows仅CRLF/LF差异。它与本机实物验收的Arch/Python3.14包是不同二进制，不将同源码测试称为此Ubuntu包的物理复验。

STM32模拟电机/IMU经真实CAN链路、两台分析仪、Linux本机发行包和Android独立台架包完成：查询开关140轮；手机A/B BLE/Wi-Fi四场景及原生配置导入后四场景；桌面USB/BLE/Wi-Fi四档速率、四类帧、六阶段显示、最大历史及CSV逐格核对；双台九种传输组合、63段原始流和63次达妙原生全屏。多轮另核验19配置、58有效及91异常反馈。正式Android APK已核对签名、安装摘要及原生启动；无线自动化证据来自产品代码/资源一致的独立台架包。最新BLE后台租约停止验证通过，最新Wi-Fi后台专项未执行。

双机高强度矩阵/重启恢复使用未发布的6Mbps实验PHY镜像；默认PHY先前存在间歇发送失败，因此正式固件双机功能仍需按实际环境评估。未验证真实电机/IMU、Windows实体台架、手机USB Host、距离或持续满负载；不承诺无线长期无损。历史间歇Wi-Fi超时及早期BLE建连丢弃记录保留，不能以复测通过认定全部根因解决。实际1000Hz查询请求的USB独立测量约616Hz反馈，未宣称1000Hz送达。

详细记录见源码`docs/PERFORMANCE.md`、`docs/AUTONOMOUS_TESTING.md`。本轮CAN专项不代替完整UART硬件回归。实验候选、私有配置、设备凭据及展示视频不在附件内。

硬件开源：https://oshwhub.com/qianmawa/project_nlqbsqhh
GitHub：https://github.com/QianmoNai/Wireless-CAN-Analyzer/releases/tag/suite-2026.11.06
Gitee：https://gitee.com/qianmonai/Wireless-CAN-Analyzer/releases/suite-2026.11.06
