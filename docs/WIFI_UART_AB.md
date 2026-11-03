# Wi-Fi UART P0-only / P0＋P1 对照

本次仅准备可比较的镜像，恢复路径和烧录均有执行前检查点。不能将构建或离线测试记作实体负载结果。

## 唯一实验变量

`CAN_WIFI_TCP_NODELAY` 是 CMake 构建选项，默认 ON，保持9ab2277的设备行为。OFF 明确对接受的设备socket设置 TCP_NODELAY=0；ON 设置1。两种情况均保留相同的P0、认证和错误处理，快照 `tcp_nodelay` 回报对应设置值。设置失败仍关闭socket并记 options 错误，不继续测量。

P0-only不是旧版无诊断固件。不能在一组关闭观测、另一组开启观测；客户端asyncio原有NODELAY也保持不变。发射功率请求32 quarter-dBm、16帧批次、1500 ms截止、所有队列/池/reserve、BLE v1/CUSB/session/type20及不重传策略不变。

两份镜像应从同一提交、同一份既有sdkconfig构建到独立目录，显式传入ON/OFF，避免修改普通构建缓存。示例（先激活现有ESP-IDF v6.0.2，`<dir>.sdkconfig` 为同一既有配置的副本）：

```bash
CAN_IDF_BUILD_DIR=<p0-dir> bash firmware/idf.sh -D CAN_WIFI_TCP_NODELAY=OFF build
CAN_IDF_BUILD_DIR=<p1-dir> bash firmware/idf.sh -D CAN_WIFI_TCP_NODELAY=ON build
```

核对两份sdkconfig、分区表相同，目标ESP32-S3、DIO/4MB/40m、app@0x10000且大小不超过现有2 MiB app分区。镜像不提交git。确切路径、SHA256和待确认的仅app烧录命令写入 `~/grokbot/wifi-uart-ab-result.md`。构建完成不意味着已批准执行烧录。

## 路径门槛

旧基线走 `10.42.0.0/24 dev enp3s0 src 10.42.0.1`；当时没有csap0，CANScope-Test脚本使用的是10.77.0.0/24，二者不能替代。2026-11-04只读检查：enp3s0原有shared连接仍在、链路1000 Mb/s，wlan0仍在5 GHz信道149；没有创建、重载或修改网络。

只有原目标ID在10.42.0.x被发现、原证书固定认证通过、路由确实经enp3s0，且无其他同设备会话时才开始。未知AP物理连接需用户说明；不推测凭据，不自动开10.77热点，不在当前5 GHz wlan0上强开2.4 GHz AP。恢复路径若需sudo，交用户执行并停止；不切换电脑连接或改路由器其他设置。

## 统一测量

每个候选各测8,000 B/s×60秒与最大连续供给×60秒。UART为115200/8N1、120 B分块；同一路径、相同随机种子、同一桌面和页面、同一观测调度（每秒P0/每秒收包日志、约每0.5秒只读设置QUERY）。忙时按同一规则跳过或等待，日志记录实际间隔，不凭计划频率宣称实际频率。

每档：先120 B标记端到端校验，静止前快照（核对boot_id、模式、波特率和 `tcp_nodelay`），启动唯一UART供给者，收尾等待线缆/驱动排空及桌面交接，后快照。跨烧录boot_id不同，计数只在各自前后求差。全部保留每块输入/到达时间线、接收字节和哈希；失败也保存这些信息，再独立认证读保留计数。

比较：输入/设备读取/LAN/Qt字节、各层drop与不可定位差额、FIFO/ring事件、队列高水位、TLS失败/超时/WANT和等待累计/最大值、控制RTT、UART→LAN/Qt p50/p99。峰值为启动后累计值，不能直接相减当作每档峰值；注明其最后变更时间，必要时以同档采样和事件关联判断。

样本延迟从serial.write开始计，到完整120 B进入LAN/Qt；包括USB/UART串行和调度，不是纯空口延迟。早断就标未完成，p99只代表截断的完整接收样本，不能拼接重连凑满60秒或判作改善。末尾缺失不一定产生序号缺口；桌面断开清理的尾部单列，不把 overflow=0 解释成零丢失。

本次主机测试覆盖默认/显式ON/OFF的socket参数及快照值、设置失败分类和认证边界。实体对照需先满足网络与烧录检查点；设备最终保留哪份镜像应按用户后续确认执行，不自动回滚、不擦NVS、不推送。
