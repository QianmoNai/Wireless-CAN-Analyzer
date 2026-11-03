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
