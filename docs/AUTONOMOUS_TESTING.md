# CAN 模式自主测试台架

当前台架为两台原生 USB ESP32-S3、J-Link/STM32H723 独立双 CAN、ADB Android 手机和 Linux 桌面。测试只对虚拟电机/IMU及专用测试 ID 操作。原始报告保存于本机 `dist/can-go-20261106/autonomy-audit/`；构建、模拟与物理结果分别记录在 [性能记录](PERFORMANCE.md)。

| 功能 | 自主操作方式 | 当前证据或待办 |
| --- | --- | --- |
| USB/BLE/Wi-Fi 模式切换 | 物理 USB 设置，提交一次后读取状态 | 两台实物可自动切换和恢复 USB＋CAN |
| 蓝牙首次认证 | 核对 USB 身份，读取正在显示的临时码，处理系统配对 | 两台Android及第二台Linux首次认证通过，临时码不记录 |
| Wi-Fi 配网与发现 | 读取已授权本机网络配置，认证 BLE 配网，生产 UDP 发现 | 第二台同网配置、桌面和手机实物通过，不保存明文凭据日志 |
| CAN 帧与虚拟参数 | STM32 模拟器、真实 CAN、GUI/CDP | 标准/扩展、数据/远程、DLC 边界；DJI/IMU/达妙显示、查询、暂停筛选和首页静默通过 |
| 双机配对 | 指定两个 USB 身份、已知 MAC、双方独立校验后分别确认 | 实物通过，失败保留旧绑定，不选择不明设备 |
| 双机传输与重启 | SWD 双控制器逐帧核对，USB ROM/看门狗复位 | 1.24.6候选5000组及离线帧不补发通过；默认速率失败与候选结果分别保留 |
| USB 编号变化后重连 | VID/PID/序列号解析，握手后复核 | Desktop 1.2.16 源码跨 ACM1/ACM2 重连并收到 CAN；发行包页面重启重连通过，该轮编号未变 |
| USB管理回压与无线隔离 | 临时接管指定USB CDC接口，暂停读回复，实际无线静默监控 | 1.24.7修复前蓝牙出现1750条意外原始帧；修复后两台BLE/Wi-Fi四场景均为零，恢复监控与USB通过 |
| 桌面文件与操作 | 实际发行包、Qt 原生文件对话框、Niri 焦点 | 1.2.18三种传输配置/上限导出导入、超过10000行CSV、四类手动帧、四档速率及六阶段各20秒通过；60秒观察证据为1.2.17，双实物9组合63段及50次离线Qt全屏证据为1.2.16 |
| Android 文件与操作 | ADB 原生界面＋实际 WebView | 1.21.8两台BLE/Wi-Fi共24阶段通过，B端Wi-Fi填满10000 RX，暂停DOM不变时CAN继续到达；在线导入被拦截且保持连接。1.21.7另有A端BLE填满10000证据。断开后SAF导入16配置证据为1.21.6，旋转/返回/前后台为1.21.5 |
| Windows | GitHub Actions 原生构建和测试 | 92563bd 的CI通过；没有本机Windows物理台架，不能代替硬件验证 |
| CAN四档速率 | SWD停控制器，协议改分析仪速率，再启动同速率 | 1.24.6两台各12组合、四种帧双向唯一ID/载荷复验通过，CAN错误零；桌面三种连接下实际界面四档切换、双机16种独立速率组合800帧通过 |
| 持续高负载、距离 | 需要扩展可控台架或测试场景 | 双机逐帧成功不等于满负载和距离通过 |

## 可重复入口

- `scripts/test-can-hardware.py`：USB/BLE/Wi-Fi、虚拟电机查询及设备计数。
- `scripts/test-can-bitrates-hardware.py`：四档速率的停发协调和双向物理帧核对。
- `scripts/test-usb-command-admission-hardware.py`：不加延时的USB握手后首条设置命令受理与执行结果核对。
- `scripts/test-usb-management-isolation-hardware.py`：需要本机PyUSB/libusb权限，按物理身份临时接管该分析仪CDC数据接口；512条只读查询期间暂停bulk IN读取，核对无线静默模式不被重置，随后恢复监控、内核CDC驱动及USB＋CAN。只在明确STM32模拟器台架运行，不重放超时请求。
- `scripts/dual-can-pair.py`：身份限定的物理 USB 双端配对。
- `scripts/test-dual-can-hardware.py`：独立 CAN1/CAN2 真实送达；`--repeat`扩大矩阵，`--simultaneous`使两端先发后等，`--connect-stored`在同一互斥锁内先关闭周期发送、再切换双机并核对保存绑定。进入双机前必须先通过SWD关闭两路周期模拟数据，避免转发模拟器互相响应形成业务环路。`--rates A B`配合`--connect-stored`协调两端独立CAN速率，结束恢复1Mbps；常规测试结束先恢复两台USB，再恢复周期模拟数据。
- `scripts/test-dual-can-recovery.py`：保存绑定的重启恢复、离线帧不补发。
- `scripts/test-usb-reconnect-hardware.py`：桌面按 USB 身份跨复位重连；必须指定模拟器台架、已核对设备端口及esptool路径。
- `scripts/test-android-config-import-hardware.py`：独立台架包的原生SAF键盘导入；核对ADB/CDP进程、已有数字文件名备份及断开状态，只确认一次。导入清除网络信任，随后`test-android-can-hardware.py --mode ble --restore-wifi-trust`从实际设置按钮重新认证；首次配对由`android_pairing.py`读取身份核对的物理USB当前临时码并操作原生通知/确认，仅内存处理。
- `desktop/tests/multichannel_hardware.py`：实际发行包两路实物并存，原生标签切换、隐藏流静默、独有测试帧归属；端口按顺序对应已保存通道1/2及STM32 CAN1/2。
- `scripts/test-android-can-hardware.py`：Android台架包实际WebView、六阶段CAN显示和清理；`--verify-import-guard`通过真实原生桥接确认在线导入被拒绝且保持连接；`--history-limit 10000 --seconds 20`填满RX历史，并检查暂停DOM不变时CAN仍持续到达。
- `desktop/tests/can_operations_hardware.py`：发行包原生文件操作与实际CAN发送核对；`--all-bitrates`在速率台架上通过界面应用四档并收取物理CAN；`--monitor-seconds 5..60`加入六阶段参数/曲线观察，`--detail-window 60`扩大可见历史，不修改采样；`--history-limit 10000 --monitor-seconds 20`检查填满RX、暂停及大历史CSV导出逐格核对。

主要物理测试入口共用进程锁，第二个测试会在改变模式前被拒绝，防止两个测试互相干扰。锁在正常结束或进程退出时释放。

Android与四档速率入口可加`--monitor-diagnostics`（要求固件1.24.7），从模式选择前开始记录既有丢弃总数的三个分项，包含退出及恢复USB；无线活动期间通过只读物理USB取样，不取得USB数据会话。新增分项会令测试失败；缺失回复不会当作零值。固件复位后的累计值重新起算，不能跨重启直接比较。

脚本只能在明确的虚拟设备台架上运行。记录“受理”“入队”“无线确认”和“目标CAN实际接收”为不同证据，不把其中一项当作另一项。
