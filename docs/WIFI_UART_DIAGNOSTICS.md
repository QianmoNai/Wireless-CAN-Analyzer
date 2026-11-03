# Wi-Fi UART 分层诊断（P0）与设备 TCP_NODELAY（P1）

仅固件与 Linux 桌面。TCP/TLS 认证、原有 20 B 帧、BLE v1、CUSB/session、type20 物理发送完成、不重传、控制优先和池/reserve 均保持。数据批次仍为 16 帧，发射功率请求仍为 32 quarter-dBm；不改变省电、核分配、优先级、FIFO 阈值或 DMA。

## 使用

普通用户运行，先关闭其他占用同一设备的桌面/测试连接：

```bash
desktop/.venv/bin/python desktop/main.py --wifi-diag-log "$HOME/grokbot/wifi-uart-diag-session.jsonl"
```

在桌面选择局域网并连接屏幕上的当前 IP。正常认证代码复用既有信任；没有蓝牙操作、配网或口令读取提示。日志必须是当前用户所有的普通文件，权限 0600；禁止符号链接，已有宽权限文件会拒绝。此参数仅 Linux 可用，默认不开启。

每秒记录 `rx_second`：实际间隔、接收字节、B/s、序号缺口/重复/乱序、桌面队列丢弃、最近设备快照序号及其年龄。按原有串行控制 owner 调度只读诊断，繁忙时不会额外堆积请求；实际间隔可能大于一秒。每份成功快照记录为 `device_snapshot`。失败只记异常类型和收到的字段数量，不记录异常 args、frame hex 或任意设备 JSON。

断开记录最后快照，不把过期快照冒充断开后的读数。设备侧计数不因 TCP 断开而清零；重新连接后可读后续快照，比较 boot_id 确认是否重启。旧固件拒绝此请求后停止该会话的诊断轮询，不自动配对、重发数据或升级固件。

## 读取协议

现有**已认证 TLS** QUERY 通道扩展：`[1,0x19,2,0,...,request_id_le32]`，byte3–15 必须全零，请求编号非零。没有 SET 或清零操作。原设置 QUERY op0 和 SET op1 不变；BLE 与 USB 仍只接受原有设置操作，拒绝 op2。

响应 type26，每帧仍为20 B。首帧 byte2=255，其余字段为版本1、公开 boot_id、snapshot serial；后续 byte2 为严格递增字段索引，byte3 为固定字段总数56。byte4–15 为 `value / last_change_uptime_ms / last_change_UART_sequence` 三个 uint32，byte16–19 回显请求编号。一次快照57帧、1140 B，由独立有界数组发送，不经过 raw/control 队列，不修改配网512 B JSON；既有最坏511 B测试继续有效。

值和时间戳在同一个短临界区更新，快照一次复制。32位累计计数按模2^32比较；微秒累计约71.6分钟回绕，毫秒时钟约49.7天回绕；重启用 boot_id/serial 关联。boot_id 是公开随机启动标记，不是访问凭据。计数只在 Wi-Fi 模式增长；退出模式后的旧队列清理存在模式交接边界，应以静止前后快照对账。

## 字段与边界

完整有序字段见 `firmware/main/wifi_uart_diag.h` 与 `desktop/can_scope/wifi_diagnostics.py`；离线测试校验两者顺序一致。

| 层 | 指标 | 解释 |
|---|---|---|
| UART | FIFO-overrun、ring-full事件，ring高水位、读取错误、读取字节、总drop、配置flush、未连接输入、TX丢弃 | ring高水位是每轮读取前 `uart_get_buffered_data_len` 的采样最大值，不含ISR/FIFO/stash的精确峰值；不 flush 错误事件后的有效数据 |
| 打包/共享notify | raw/control/ack深度与采样高水位；队列满、reset、generation、inactive、suppression、route失败的UART字节 | 只统计合法type19的1–12 B payload，绝不把type20当作RX丢弃 |
| Wi-Fi发送队列 | raw/control深度与采样高水位、拒收满/离线、reset丢弃 | `lan_reject_*` 是 `drop_route` 的原因子集，不可再次相加到总drop |
| TCP/TLS | 写调用/失败/超时/WANT次数，WANT等待累计和单次写内等待最大值，写函数累计/最大耗时，应用待写块高水位 | WANT等待可包含调度时间；写函数耗时还含加密/系统调用，不能当作纯空口耗时。应用待写块包括诊断1140 B响应，不是内核TCP缓冲占用 |
| 断链 | TCP close次数及分类、读失败、最后TLS数字错误/errno、握手失败；Wi-Fi断开次数和reason | TCP分类：1模式/离线，2写失败，3 EOF，4读错，5心跳超时，6诊断响应失败，7 NODELAY设置失败；Wi-Fi reason为驱动原始数字 |
| 无线 | 读取时RSSI、协商PHY模式 | PHY是制式枚举，不是当前Mbps；读取失败/未提供是 unavailable |

**不能精确获取的字段必须保持 unavailable**：

- UART FIFO/ring溢出事件没有提供实际丢失字节数，`ring_lost_bytes` 为未知，绝不拿主机输入减输出制造溢出字节。
- ESP-IDF公开STA API没有提供此处可用的当前实际TX/RX速率与空口重传累计，`wifi_rate_kbps`、`wifi_retries` 为未知。不会打开promiscuous或调用私有调速API。
- BSD socket公开接口没有可移植的当前未确认发送队列精确占用，`tcp_kernel_send_high_bytes` 未知。不能用SO_SNDBUF配置容量冒充高水位。
- 未知值在JSON日志中为 `value:null, available:false`；有符号RSSI/TLS返回码按int32解码。

读出后的RX软件丢弃台账可分解为 `drop_notify_full + drop_notify_reset + drop_generation + drop_inactive + drop_suppressed + drop_route + drop_lan_reset + drop_tls`。LAN拒收观察是route的子集。总UART drop另含配置flush、未连接输入、TX丢弃等；只能在无并发修改、边界排空后核对，不把会话截断处的未采集尾部误认成已确认硬件丢字节。

## P1及验证

设备对接受的TCP socket显式设置 `TCP_NODELAY=0`，保留P0；默认及未定义构建选项的回退均为0。P1实体A/B失败后，`CAN_WIFI_TCP_NODELAY=ON` 仅作为显式实验选项保留。旧CMake缓存的ON需显式关闭或使用新目录；不会因修改默认值而自动覆盖已有缓存。设置失败时记录errno和分类并关闭该socket；不修改全局网络设置。Linux asyncio原本已启用客户端NODELAY，未重复改变客户端设置。

测试覆盖：真实UART worker、真实下游计数所有权与TLS部分写/WANT/超时，时间戳/序号与未知值，严格分片/版本校验，旧请求隔离，私有日志不包含测试payload，BLE/CUSB不接受诊断扩展，NODELAY成功/失败。主机模拟不能替代实体Wi-Fi证据。

构建后仍需用户确认才能仅烧app@0x10000。实体复测只做8 KB/s×60 s和一次同负载断连复现尝试，保留失败；不人为断网制造“自然复现”。断开后停供给、等线程退出、关闭唯一串口，再重连读同boot_id后的快照。没有复现时应如实写未复现。
