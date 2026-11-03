# Wi-Fi UART P5 单因素对照

当前源码默认关闭设备端 `CAN_WIFI_TCP_NODELAY`，C源未定义选项的回退也为0。P0完整保留；客户端asyncio原有NODELAY不改。旧构建缓存的ON需显式关闭，或使用新的独立构建目录。

`CAN_WIFI_TLS_BATCH` 是最大一次TLS批次的20 B帧数，只允许16、32、64，默认16。收集器每次取帧都先检查control再检查raw；无帧立即返回，不增加凑满批次的等待或新超时。上限不等于每次实际批次，未新增实际批次直方图。

三份候选应从同一提交、相同sdkconfig在不同目录构建，NODELAY全部为0，唯一实验变量是批次上限：

```bash
CAN_IDF_BUILD_DIR=<b16-dir> bash firmware/idf.sh build
CAN_IDF_BUILD_DIR=<b32-dir> bash firmware/idf.sh -D CAN_WIFI_TLS_BATCH=32 build
CAN_IDF_BUILD_DIR=<b64-dir> bash firmware/idf.sh -D CAN_WIFI_TLS_BATCH=64 build
```

b16的全新CMake缓存用于验证两个默认值；b32/b64也必须核对NODELAY=OFF。没有修改TCP发送缓冲、BLE池/reserve、任何队列容量、功率请求32 quarter-dBm、省电、任务优先级/核、FIFO/DMA、BLE v1/CUSB/session/type20或不重传策略。P0配网JSON与QUERY字段保持原样。

默认16的收集顺序和数量保持原行为。32/64的局部batch数组由320 B增到640/1280 B，64比16多960 B；server任务仍为12288 B，不扩大栈或默认池。更大的在途写入可能增加控制等待或TLS阻塞，必须一起观察QUERY尾延迟，不预设更大批次更好。

阶段与用户检查点：

1. 先交付b16的路径、大小、SHA256、仅app@0x10000命令，停下给用户看。确认后才烧录，校验成功后核对10.42.0.226经enp3s0、已有固定证书认证、Wi-Fi/UART/115200与NODELAY=0，再测最大连续供给60秒。与此前P0-only结果比较，未达恢复水平则先查明，不混入批次实验。
2. 再交付b32，同样先停下确认；之后各测8,000 B/s和最大连续供给60秒。
3. 再交付b64，同样先停下确认；之后重复两档。

UART115200/8N1、120 B随机块、相同种子、同一电脑页面和10.42/enp3s0路径。沿用每秒P0/RX与前次QUERY完成后约0.5秒间隔；记录实际频率，繁忙时不追赶请求。不重连拼60秒。每次烧录后boot_id不同，计数只在各次前后求差；累积峰值结合最后变更时间/序号解释。

比较输入/设备读取/LAN/Qt字节、各级拒收与清理drop、TLS失败/超时/WANT累计和最大等待、UART→Qt p50/p99、QUERY p99及最大值。只对完整收到的120 B块统计延迟，同时报告覆盖率与丢失；早断不能给出有效完整60秒p99。FIFO精确丢字节、无线重传和内核发送占用仍unknown，不推造。

离线简化测试直接执行生产收集器，覆盖默认及16/32/64边界、控制优先、收集中途到达的控制、部分批次立即返回与外侧guard；非法批次编译拒绝。实体结果须另行实测，构建成功不代表已完成烧录或负载。

完整执行状态、镜像、批准前命令与后续结果写入 `~/grokbot/wifi-uart-p5-result.md`。仅本地提交，不推送，不提交镜像或测试数据；所有烧录只允许app@0x10000，不擦NVS。

## 2026-11-04：连续执行授权后，16帧前置TLS检查失败

用户已一次批准16→32→64连续执行，不再逐份等待；仍要求16帧恢复门槛明显偏离即停止，任何烧录或校验失败即停止。先16帧最大供给60秒，再各批次两档60秒；最后保留最优镜像。此授权更新前文逐次确认的执行安排，没有扩大到网络设置或配网。

仅烧录 `wifi-uart-p5-b16-b852aa9-app.bin`（1129488 B，SHA256 `11cb9e42655bd8f98a154c4cce96343e00efc6e24641a0b4c4eb91d220bf9374`）到app@0x10000。esptool退出0，写后hash校验通过；没有擦NVS、写其他分区、修改网络或配网。已烧16帧及NODELAY=0由构建定义和镜像校验证明；在线快照未取得，不能冒称NODELAY已实时读回。

独立USB QUERY前后均为result=0/network_mode=1/data_mode=1/baudrate=115200。到10.42.0.226的路由仍是enp3s0/src10.42.0.1；启动初期两次UDP发现无回复，之后发现目标；5次ping全部回复，RTT min/avg/max=33.362/81.800/153.588 ms。检查程序随后10次TLS认证阶段超时。一次只存在一个目标TLS客户端，所有检查顺序执行。

沿用真实Qt/Channel/LanTransport的恢复门槛程序也在连接阶段TimeoutError，独立恢复连接同样超时。没有到达UART打开、120 B同步标记或FixtureWriter供给步骤；cases为空、无P0快照、JS错误数0。不是“最大供给跑了0秒丢0字节”，也不是一个完整60秒负载结果。按恢复门槛停止，未烧32/64。

| 项目 | 16帧恢复门槛 | 16/32/64正式两档矩阵 |
|---|---|---|
| 负载完成时间 | 未开始 | 未开始 |
| 各层drop、TLS WANT、UART→Qt p50/p99、QUERY p99 | 未取得 | 未测 |
| 早断 | 负载前连接超时，不能列作负载早断 | 未测 |

不能据此确定TLS超时根因或推荐32/64。暂保留16帧作为后续恢复起点，不称其已通过P0-only稳定性验收或三者中表现最好；不在前置失败时继续烧新变量。最后USB独立QUERY正常，测试进程/TLS/串口已关闭，设备保留该16帧镜像与Wi-Fi/UART/115200。源码参数、32 quarter-dBm功率上限、队列/池/reserve及协议语义未改。本轮只追加结果文档，未推送。完整失败证据见 `~/grokbot/wifi-uart-p5-result.md` 和 `~/grokbot/wifi-uart-p5-hardware/b16/recovery-max/`。
