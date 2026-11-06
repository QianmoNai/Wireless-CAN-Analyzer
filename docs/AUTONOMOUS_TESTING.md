# CAN 模式自主测试台架

2026-11-06曾按用户要求暂停，后续已获指令恢复。暂停时安装Android1.21.10、Desktop1.2.20，物理固件保持1.24.7；已完成查询开关140轮、手机四场景及导入后四场景、桌面三传输整包和两台同时达妙查询九组合。收尾快照为`pause-checkpoint.json`。恢复后已重新核对ADB、两台USB、J-Link及运行镜像；Android Wi-Fi后台租约专项补验通过，Desktop1.2.21实际查询频率显示完成78轮开关、三路整包操作和双通道63轮显示/全屏；无线压力及初始化仍出现转发丢弃，整体无丢弃门槛未通过，本机入口保留1.2.20、候选1.2.21单独保存，详见性能记录。

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
| 桌面文件与操作 | 实际发行包、Qt 原生文件对话框、Niri 焦点 | 1.2.20三种传输配置/上限导出导入、超过10000行CSV、四类手动帧、四档速率及六阶段各20秒通过；1.2.20双实物9组合63段原始流＋63次达妙详情X11全屏、两端同时查询续租与停止确认通过，两台分项无新增；1.2.19另有DJI主动上报矩阵。60秒观察证据为1.2.17 |
| Android 文件与操作 | ADB 原生界面＋实际 WebView | 1.21.10两台BLE/Wi-Fi共24阶段通过，B端Wi-Fi填满10000 RX，暂停DOM不变时CAN继续到达；在线导入被拦截且保持连接，断开后SAF导入16配置及重建信任后24阶段也通过。1.21.7另有A端BLE填满10000证据，旋转/返回/前后台既有证据为1.21.5 |
| Windows | GitHub Actions 原生构建和测试 | 811c036 的主检查及Linux/Windows构建CI通过；没有本机Windows物理台架，不能代替硬件验证 |
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
- `desktop/tests/multichannel_hardware.py`：实际发行包两路实物并存，原生标签切换、隐藏流静默、独有测试帧归属；端口按顺序对应已保存通道1/2及STM32 CAN1/2，写入前核对物理身份与保存身份一致；要求固件1.24.7并比较两台只读三项丢弃。`--detail-fullscreen`在每轮原始流后打开物理DJI反馈详情、核对X11全屏属性及隐藏通道退出，需要xprop及实际X11窗口管理器。追加`--query-dm`会在两通道同时开启独立达妙查询，切换期间检查两端仍续租，全屏选择达妙而非DJI，并在退出前分别等待停止确认。`--query-metrics`配合`--query-dm`检查Desktop1.2.21双通道独立实际频率与详情DOM；`--query-rates A B`可指定两路查询频率（度量专项默认100/500Hz，普通查询默认各100Hz）；隐藏通道按模式3静默，激活后才要求周期统计。专用帧先核对STM32请求/完成序号相等，再递增，避免重跑复用已完成请求或覆盖未知结局。
- `scripts/test-android-can-hardware.py`：Android台架包实际WebView、六阶段CAN显示和清理；`--verify-import-guard`通过真实原生桥接确认在线导入被拒绝且保持连接；`--history-limit 10000 --seconds 20`填满RX历史，并检查暂停DOM不变时CAN仍持续到达。
- `desktop/tests/can_operations_hardware.py`：发行包原生文件操作与实际CAN发送核对；有界保存最近24条查询/错误事件、关闭前后状态及失败位置，超时保留原报告；`--all-bitrates`在速率台架上通过界面应用四档并收取物理CAN；`--monitor-seconds 5..60`加入六阶段参数/曲线观察，`--detail-window 60`扩大可见历史，不修改采样；`--history-limit 10000 --monitor-seconds 20`检查填满RX、暂停及大历史CSV导出逐格核对。

主要物理测试入口共用进程锁，第二个测试会在改变模式前被拒绝，防止两个测试互相干扰。锁在正常结束或进程退出时释放。

Android与四档速率入口可加`--monitor-diagnostics`（要求固件1.24.7），从模式选择前开始记录既有丢弃总数的三个分项，包含退出及恢复USB；无线活动期间通过只读物理USB取样，不取得USB数据会话。新增分项会令测试失败；缺失回复不会当作零值。固件复位后的累计值重新起算，不能跨重启直接比较。

脚本只能在明确的虚拟设备台架上运行。记录“受理”“入队”“无线确认”和“目标CAN实际接收”为不同证据，不把其中一项当作另一项。


### 参数数值的物理反馈核验

`scripts/test-can-feedback-hardware.py`需要显式指定`--fixture stm32h723-dual --elf <运行镜像对应ELF> --port <分析仪by-id路径> --bus 1|2 --channel <已保存身份通道> --surface desktop|android --mode usb|ble|wifi --output <报告>`。桌面另指定`--binary <实际安装包可执行文件>`；Android仅BLE/Wi-Fi，需独立台架包已获ADB授权、配置已保存且CDP端口已转发（默认35013，可用`--cdp`覆盖）。测试不读取/打印配对口令，不重新发出结局未知的CAN指令。

脚本核对USB与保存身份及STM32运行镜像，暂停两路周期模拟，经SWD让STM32在对应物理总线发送已知反馈；Python参考值不调用产品JS解码器。覆盖全部19个预设/自定义/IMU配置、58组有效反馈，分别验证原始接收与详情值，再核对实际DOM数字的显示精度及不可用字段。`--invalid-frames`追加扩展、远程、短帧、错误地址/电机/IMU组号，先证明原始帧确实到达，再检查全部监控值和时间未变化。报告逐组保留载荷、期望、观测和详情显示文本。共用硬件锁，退出恢复配置、断开通道、USB＋CAN及周期模拟；属于模拟设备经实体CAN传输的证据，不代表真实电机/IMU。


### 绘图与窗口恢复

`tests/test_motor_curve.cjs`随主机检查运行，校验电机合并扫描的主/副值边界及完整曲线路径语义。`desktop/tests/test_chart_window.py`模拟合成器延后确认退出全屏、快速跨通道新开全屏、普通窗口基准、隐藏通道请求及关闭/删除，验证不会覆盖原始最大化状态或执行过期恢复。实际Qt用`desktop/tests/fullscreen_tabs.py --switches 50 --settle-ms 250 --output <报告>`复验；该入口使用合成连接状态、不打开硬件。Desktop1.2.19源码在Niri/XWayland的50次切换通过，包含窗口修复的最终包单次实际XCB及离屏smoke均通过；物理CAN功能以独立硬件报告为准。


### 定时查询开关的确认状态

`scripts/test-can-query-toggle-hardware.py --fixture stm32h723-dual --elf <ELF> --surface android|desktop --mode usb|ble|wifi --port <by-id> --channel 1|2 --bus 1|2 --repeat 20 --output <报告>`，桌面另加`--binary`，Android不支持USB Host。需要固件1.24.7诊断及STM32模拟达妙反馈，沿用已保存的身份、Android独立台架包/CDP；身份不符不会执行模式或清理写入。实际点击开关后分别核对等待锁定、最终原生确认、最终DOM及实际反馈；退出恢复配置、USB/周期数据，新增丢弃失败，不把未知结果当作成功或自动重发。
