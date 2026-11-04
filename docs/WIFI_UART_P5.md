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

## 2026-11-04：旧P0-only可连接，最大供给被CH340错误阻塞

用户批准回烧旧P0-only排除回归。仅将`wifi-uart-ab-p0-0a1f3c1-app.bin`（1129472 B，SHA256 `c533b413e031e2d92321763eb4f9549ca203ae9fb4a1672e04c4cc825fc1b66a`）写入app@0x10000，写后hash校验通过，不擦NVS。设备仍是10.42.0.226经enp3s0/src10.42.0.1，USB QUERY前后均为Wi-Fi/UART/115200。

同一Qt/Channel/LanTransport测量程序完成了旧版TLS认证与QUERY，并连续收到54份P0快照，NODELAY=0。但打开唯一CH340供给口时SerialException/EIO(5)，内核ch341-uart报告failed to read modem status: -32；无同步标记或负载供给，因此没有最大供给60秒结果。供给口设备文件和用户uucp权限仍在，无其他占用。没有sudo或重置USB/驱动；已请用户保持板子独立供电及原生USB连接，重新插拔CH340后继续。

测试程序在port.open失败后，其flush清理又出现PortNotOpenError；已结束自己的测试进程并关闭TLS，保留数字日志。外部测量副本补充失败前安全结果保存及is_open清理保护，不改生产代码、采样频率或串口负载。

新旧network_link.c的差异是宏化批次/抽取收集器及未定义NODELAY时的回退默认值；两份实际构建均明确定义NODELAY=0，收集器在认证后执行。目前尚不能从这些差异定位前置TLS超时因果；没有盲改或交付未经证明的固件修复。旧版空载连接成功不是负载稳定性验收，也不能单独排除时变链路问题。

当前设备保留旧P0-only，两个串口/TLS已关闭。最大连续供给验证和回归修复仍待供给口恢复，32/64未烧录，未推送。完整过程追加于`~/grokbot/wifi-uart-p5-result.md`；证据位于`~/grokbot/wifi-uart-p5-hardware/oldp0-reference/max/`和`wifi-uart-p5-oldp0-*`。

## 2026-11-04：CH340恢复，但目标地址漂移，继续等待原路径

用户撤销Horco指令，恢复CH340。sysfs确认ttyUSB0位于USB3-1.1；没有打开Horco口，没有无线串口负载结果。旧P0-only在此前完整只读app核验中digest匹配，随后没有新烧录；本轮独立USB QUERY前后均Wi-Fi/UART/115200。

原10.42.0.226的路由仍经enp3s0/src10.42.0.1，但6轮UDP无回复、3次ping全丢，原Qt程序connectLan及独立重连均OSError；没有到达CH340打开/同步标记/负载步骤，不是CH340再次EIO，也没有有效60秒数据。

随后只读UDP发现同一ID在192.168.1.221，已有证书固定TLS认证、QUERY和P0均通过：经wlan0/src192.168.1.119，NODELAY=0、Wi-Fi/UART/115200、RSSI=-65 dBm。这证明当前旧地址不可达不能直接归因TLS或硬件故障；实际SSID/BSSID及DHCP网段变化原因未实读，不能单凭IP断言换了AP。没有在备用路径发送负载或改写信任缓存。

原enp3s0匹配路径未恢复，因此暂停最大供给及固件修复，不猜测修改代码。已请用户确认原AP/路由器及接线信息；没有改变电脑/路由器网络或重新配网，也没有读取密码。旧P0-only保留在设备，当前可认证IP为192.168.1.221。所有串口和TLS已关闭，未push。详细证据追加至`~/grokbot/wifi-uart-p5-result.md`；本轮只更新结果文档，未重建固件。

## 2026-11-04：仅离线TLS回归审查，未确认根因

本阶段按用户要求只查离线源码、既有构建产物和纯内存桩；不连接设备、不开串口、不烧录、不改网络。当前设备地址192.168.1.221经wlan0来自上一阶段，本阶段没有实读；不能据地址漂移否认先前10.42/enp3s0下的新失败/旧成功记录。

`0a1f3c1..b852aa9 -- firmware`只有network_link.c、network_batch.h、main/CMakeLists.txt三处。两份实际P0构建都是NODELAY=0，相同sdkconfig/分区/工具链和规范化cflags/specs；新16的宏只增加批次16。收集器与部分批次不凑满、每次取控制优先的行为一致；只在握手、认证、OK01完成后调用。握手到进入数据循环前的生产函数源码逐字相同。目标码1080个对象的可装载节一致，另外两个元数据对象和network_link对象有差异；调用布局/寄存器分配改变不能被主机桩证明无硬件影响。

新增`tests/test_wifi_tls_flow.py`及纯内存C桩，对历史old16和当前16/32/64分别编译实际生产函数，覆盖握手失败不碰队列、选项失败、认证分片、3帧部分批次立即发送且控制优先、首个20 B命令分片、数据读WANT、写WANT让出/1500 ms截止。全部通过。它同时确认两版共有的认证读WANT立即关闭；本机IDF v6.0.2握手WANT包装循环没有显式delay，阻塞读又由socket超时约束。这是共享的潜在脆弱点，不是已证明的新16回归；没有擅自修复。内存桩不模拟真实TLS密码学、lwIP调度、Wi-Fi空口或电源。

离线仍不能确定b852aa9握手超时原因，故没有固件修复diff、没有重建三份“修复”镜像；原b852aa9三份仅作未修复候选保留。优先假设：时变无线/供电/lwIP接收资源；单服务线程被旧会话占用、TCP ESTAB尚未进入应用accept；两版共有握手/认证WANT与超时边界；代码布局/栈/并发潜在缺陷。均未证实，不能把新TCP数据未ACK直接归因批次。

需另获实机阶段授权后做稳定同路径old→new→old无负载重复验证，逐次核对设备身份/IP/经enp3s0/当前AP；客户端只记TCP/TLS/认证阶段及数值时长/错误码，采TCP包头和ACK计数而不保存payload；必要时另做仅安全数值阶段诊断app，记录accept、握手起止、认证、errno、heap/栈水位及事件，不打印证书/凭据。所有未来烧录仍需先展示并按用户确认执行，只app@0x10000且不擦NVS；本阶段没有执行这些计划。

测试：TLS流程四种配置、现有batch/socket/handoff/power五组相关测试及py_compile/diff检查通过；生产代码没有变化，不重复无关回归。完整报告与离线构建审计清单追加到`~/grokbot/wifi-uart-p5-result.md`，仅本地提交，不push。

## 2026-11-04：新enp3s0/DHCP路径空载ABABA，两版均TLS超时

用户自行将enp3s0改为DHCP 192.168.1.131/24，批准在192.168.1.221经enp3s0的新路径做old→new→old→new→old。代理未改网络；默认上网路由保持wlan0，目标MAC为90:da:72:73:72:2c。old为既有P0-only镜像c533b413…（1129472 B），new16为b852aa9镜像11cb9e42…（1129488 B）。

新增`scripts/wifi-tls-check.py`，仍调用生产LanTransport并保留其原有TCP＋TLS总5 s、应用认证5 s截止；只观测asyncio连接/证书验证阶段，用同一monotonic时钟记录开始/结束/异常类型。每例只连接一次，无重试；TCP_INFO/SIOCOUTQ约50 ms采样、ss元数据约250 ms采样，不捕获payload或凭据。脚本不操作串口、网络配置、BLE或UART负载。小单元测试覆盖数字TCP结构、路由门槛及阶段结果。

正式序列前old身份预检成功：证书固定认证、MAC、UDP ID、Wi-Fi/UART/115200与NODELAY=0通过，TCP92.686 ms、TLS712.999 ms、应用认证12.107 ms、QUERY90.500 ms，重传0。此样本单列，不混进正式A/B成功率。

正式五次均仅app@0x10000写入，工具写后hash通过，随后独立verify-flash digest匹配；最后一次verify复位后固定等待30.000 s再调用同一程序，目标路径/身份每次匹配。没有擦NVS、修改固件参数、读取密码或打开CH340。

| 次序 | TCP ms | TLS ms / 结果 | 重传次数/字节 | 发送队列峰值/末值 B |
|---|---:|---|---|---|
| A1 old16 | 95.531 | 4904.957 / TimeoutError | 3/489 | 163/0 |
| B1 new16 | 71.015 | 4929.565 / TimeoutError | 3/489 | 163/0 |
| A2 old16 | 126.192 | 4874.865 / TimeoutError | 3/489 | 163/0 |
| B2 new16 | 128.676 | 4871.616 / TimeoutError | 3/489 | 163/0 |
| A3 old16 | 38.842 | 4961.591 / TimeoutError | 3/489 | 163/163 |

五次均未进入应用认证/首次QUERY，没有对应延迟样本。服务器TCP payload收到量均0；前四次本地数据后期获ACK但仍无TLS回复，最后一次仍未确认；TCP ESTAB不是设备应用accept或握手已完成的证明。

结论为**old16 0/3、新16 0/2，两者都失败**。不支持“仅new16代码回归”，也不是“两者正常、上次已证实偶发”。应优先查复位后共享的链路/射频/供电/接收资源/握手服务状态，具体机制未证实；不能排除两版共有固件问题。初始old预检成功与复位后的失败有时间关联，但不是供电或复位因果证明。按用户要求只做到这里停止，不改超时/缓冲/省电/功率/批次/协议。

第5次A就是最终恢复：old16已写入并独立核验，之后未再写app。最后独立USB QUERY仍Wi-Fi/UART/115200；全部TCP/串口已关闭，CH340始终未开，无sudo或push。正式脚本/阶段原始数字及10份写/核验日志在`~/grokbot/wifi-uart-p5-aba-hardware/`，完整阶段开始/结束/例外、限制和预检说明追加至`~/grokbot/wifi-uart-p5-result.md`。仅本地提交观测脚本、小测试与文档，没有新固件构建。
