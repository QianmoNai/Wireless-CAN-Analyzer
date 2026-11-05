# STM32H723ZGT6 CAN 电机模拟器（八种配置）

支持大疆 C620/M3508、C610/M2006、GM6020 电压控制协议，以及达妙 MIT、位置速度、速度、力位混控四种模式的控制帧和反馈帧。一个固件只启用一个配置；七种单电机配置之外，另有“大疆周期反馈＋达妙问询反馈”的混合测试配置。所有配置均使用经典 CAN、1 Mbit/s。

## 一个标志选择当前模式

编辑 `Core/Inc/motor_sim_config.h`：

```c
#define SIM_PROFILE SIM_ALL_FEEDBACK
```

可选值如下。表中电机 ID 均默认为 1，达妙 Master ID 默认为 0：

| SIM_PROFILE 的值 | 模拟对象 | 控制 ID | 反馈 ID |
| --- | --- | --- | --- |
| `SIM_DJI_C620` | C620 / M3508 | 0x200 | 0x201 |
| `SIM_DJI_C610` | C610 / M2006 | 0x200 | 0x201 |
| `SIM_DJI_GM6020` | GM6020 电压控制 | 0x1FF | 0x205 |
| `SIM_DM_MIT` | 达妙 MIT | 0x001 | 0x000 |
| `SIM_DM_POS_VEL` | 达妙位置速度 | 0x101 | 0x000 |
| `SIM_DM_VEL` | 达妙速度 | 0x201 | 0x000 |
| `SIM_DM_POS_FORCE` | 达妙力位混控 | 0x301 | 0x000 |
| `SIM_ALL_FEEDBACK` | 全类型变化反馈（DJI 周期＋DM 问询） | 0x7FF 状态查询；兼容低 8 位 ID 匹配 | 见下表 |

修改后重新编译并烧录才会生效，不是运行时变量。屏幕标题显示所选配置。

也可不改代码，用独立构建目录覆盖：

```sh
cmake -S . -B build-dm-vel -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi.cmake -DSIM_PROFILE=SIM_DM_VEL
cmake --build build-dm-vel -j4
```

CMake 的覆盖值会缓存并优先于头文件；使用 `-DSIM_PROFILE=` 清除覆盖，恢复按头文件选择。当前 `build` 目录按头文件选择 `SIM_ALL_FEEDBACK`。

## 全类型变化反馈：大疆周期、达妙问询

选择 `SIM_ALL_FEEDBACK`，大疆三路上电即周期发送，达妙四路只在收到问询帧后回复。数据保持随时间变化，不需使能；本混合测试模式不执行输入中的控制动作，达妙反馈状态固定使能。单电机配置原有控制功能保留。

支持达妙专用状态查询：标准数据帧 ID `0x7FF`，DLC 4，数据为 `电机ID低字节 电机ID高字节 CC 00`；也兼容末尾补四个零的 DLC 8 版本。按数据中的完整 16 位电机 ID 匹配当前节点 1～4，一条有效查询产生一条对应 Master ID 的 8 字节反馈。未知节点、错误长度、错误命令或非零保留/填充字节不回复。

同时保留原来的查询方式：CAN 接收过滤器接受低 8 位为 1～4 的标准数据帧，高 3 位忽略；软件检查完整标准 ID 范围和 DLC 0～8。任意数据内容均可触发对应达妙节点一次反馈，包含 DLC=0。两种方式均拒绝扩展帧和远程帧；这里的“问询”是标准数据帧，不是 RTR。

屏幕显示 `DJI AUTO DM QUERY`、反馈 ID、TX Q、QUERY 接收数和 DROP 回复丢弃数。

| 虚拟节点 | 电机 ID | 反馈 CAN ID |
| --- | --- | --- |
| C620/M3508 | 1 | 0x201 |
| C610/M2006 | 2 | 0x202 |
| GM6020 | 1 | 0x205 |
| 达妙 MIT | 1 | 0x011 |
| 达妙位置速度 | 2 | 0x012 |
| 达妙速度 | 3 | 0x013 |
| 达妙力位混控 | 4 | 0x014 |

此模式固定使用以上不冲突的 ID，不使用单电机模式的 `SIM_DM_MASTER_ID` 或 `SIM_DJI_FIRST_ID`。达妙四模式的反馈格式相同，报文里没有“控制模式”字段；这里的模式对应关系是测试约定，用四个独立电机 ID 和 Master ID 区分。

`SIM_BROADCAST_PERIOD_MS` 默认 10 ms，仅控制大疆三路：每路 100 帧/秒、共约 300 帧/秒。达妙反馈次数由外部问询频率决定，不主动定时发送。四路达妙各以 100 Hz 问询时，总反馈约 700 帧/秒。总线仍为经典 CAN 1 Mbit/s；实际发送受仲裁、ACK 和队列影响。

数据按 8 秒周期往返，各路错开 700 ms 相位：

- 大疆角度变化覆盖 0～8191，速度为与角度轨迹对应的正负 RPM（约 ±22 RPM）；C620/GM6020 电流原始值约 ±6000，C610 约 ±3000。
- 达妙位置约 ±6.25 rad，速度约 ±4.69 rad/s，力矩约 ±4 N·m（默认映射量程下）。状态固定为使能，数据正常量化。
- C620/GM6020 温度在 30～40 ℃ 变化；达妙 MOS 温度 30～40 ℃、线圈温度 35～45 ℃；C610 保留字节仍为 0。

数值来自平滑周期测试轨迹，用于观察解码、符号、量程和曲线；不是四种控制环在受控运行，也不表示真实电机的热力学行为。

发送受阻时大疆只保留每路最新状态；达妙按查询时刻生成回复快照并排队，队列最多 32 帧，满后丢弃新回复并增加 DROP。两类轮流调度，避免一方长期占用队列。仍需正常模式的接收节点提供 ACK；单独一块板或只听节点不能保证发出反馈。不要与反馈 ID 相同的真实电机同时接入。

仅电机部分，主机端 `candump can0` 默认看到三路大疆反馈（启用新增 IMU 时还会有 `0x060`）；达妙需要另一个终端发送状态查询：

```sh
cansend can0 7FF#0100CC00  # 电机 ID1 -> 011
cansend can0 7FF#0200CC00  # 电机 ID2 -> 012
cansend can0 7FF#0300CC00  # 电机 ID3 -> 013
cansend can0 7FF#0400CC00  # 电机 ID4 -> 014
cansend can0 7FF#0100CC0000000000  # 兼容 8 字节补零版本 -> 011
```

查询数据填写基本电机 ID，不加控制模式的 0x100/0x200/0x300 偏移。`7FF#2301CC00` 指向 0x123，当前没有配置该节点，所以不回复。可按每台 10 ms 查询一次；停止查询后不会继续定时发送达妙反馈。

原有无负载问询仍可用于本模拟器测试：

```sh
cansend can0 001#  # 达妙 ID1 / MIT 测试节点 -> 011
cansend can0 102#  # 达妙 ID2 / 位置速度测试节点 -> 012
cansend can0 203#  # 达妙 ID3 / 速度测试节点 -> 013
cansend can0 304#  # 达妙 ID4 / 力位测试节点 -> 014
```

重复问询即可看到变化的回复；停止问询后，已排队的回复发送完毕便不再发送达妙反馈。反馈 CAN ID 011～014 不是查询 ID。

按低字节匹配也意味着外部发来的 `0x201/0x202` 会触发达妙节点 1/2；若开启内部回环，本板自己的两路大疆反馈也会触发对应问询。正常模式不会接收自身发送，默认 `CAN_LOOPBACK=OFF`。真实多设备总线需规划 ID 以避免此类交叉触发。

主机测试覆盖无问询无达妙反馈、一问一答、全部高位组合/DLC、八秒轨迹正负变化、队列溢出/顺序/公平性和 tick 回绕，原七种单电机测试同时通过。

## 达妙 DM-IMU-L1 CAN 模拟（新增）

按达妙 DM-IMU-L1 V1.2 手册实现传感器报文。默认在 `SIM_ALL_FEEDBACK` 混合配置中同时启用，不替换原来的七路电机模拟。2026-10-02 已烧录包含 IMU 的混合模拟固件，21,060 字节 Flash 校验通过（含对齐填充实际写入 21,088 字节），已复位运行。

在 `Core/Inc/imu_sim.h` 中修改：

| 标志 | 默认值 | 含义 |
| --- | --- | --- |
| `SIM_IMU_ENABLED` | `SIM_IS_BROADCAST` | 混合配置启用；设 0 关闭，设 1 可与任一电机配置同时启用 |
| `SIM_IMU_CAN_ID` | `0x050` | IMU 接收命令 ID（模拟器选择值） |
| `SIM_IMU_MASTER_ID` | `0x060` | IMU 所有数据及寄存器应答的反馈 ID |
| `SIM_IMU_ACTIVE` | `1` | 上电主动发送；设 0 则只响应查询 |
| `SIM_IMU_PERIOD_MS` | `10` | 每组四帧周期，默认每类 100 Hz |

全部为经典 CAN、标准数据帧、DLC 8，总线仍为 1 Mbps。默认 `0x060` 每秒约 400 帧，通过首字节区分：

| DATA[0] | 内容 | DATA[1] | DATA[2..7] |
| --- | --- | --- | --- |
| `01` | 加速度（m/s²） | 温度原始字节固定 0 | X/Y/Z，各 16 位小端无符号映射，±235.2 |
| `02` | 角速度（rad/s） | 0 | X/Y/Z，各 16 位小端无符号映射，±34.88 |
| `03` | 欧拉角（度） | 0 | **Pitch/Yaw/Roll**，各 16 位小端无符号映射，分别 ±90/±180/±180 |
| `04` | 四元数 | 与后六字节共同打包 | W/X/Y/Z，每项 14 位，映射范围 ±1 |

16 位映射解码为 `raw / 65535 * (max-min) + min`，不是有符号 int16。四元数按手册 56 位布局：`W[13:6]`、`W[5:0]/X[13:12]`、`X[11:4]`、`X[3:0]/Y[13:10]`、`Y[9:2]`、`Y[1:0]/Z[13:8]`、`Z[7:0]`。官方示例 W 解码掩码存在笔误，应为 `(data[1]<<6) | (data[2]>>2)`；此实现以手册位域表为准。

数据按 8 秒周期摆动，Roll 约 ±20°、Pitch 约 ±14°、Yaw 约 ±40°。角速度由同一姿态轨迹导出，四元数与欧拉角一致；加速度模拟重力在机体三轴上的投影，模长约 9.8 m/s²，不含平移运动和温度模型。

查询无需关闭主动模式，每次读取相应数据返回一帧：

```sh
cansend can0 050#CC0100DD00000000  # 加速度 -> 060，首字节 01
cansend can0 050#CC0200DD00000000  # 角速度 -> 060，首字节 02
cansend can0 050#CC0300DD00000000  # 欧拉角 -> 060，首字节 03
cansend can0 050#CC0400DD00000000  # 四元数 -> 060，首字节 04
cansend can0 050#CC0B01DD00000000  # 关闭主动发送，仅查询
cansend can0 050#CC0B01DD01000000  # 恢复主动发送
cansend can0 050#CC0A01DD14000000  # 周期改为 20 ms，每类 50 Hz
cansend can0 050#CC0A00DD00000000  # 读取周期
```

寄存器应答为 `CC RID DD ACK VALUE_LE32`，反馈 ID 同为 `0x060`。实现 0x0A 周期（模拟器允许 1～60000 ms）、0x0B 主动/查询（0/1）的读写，以及 0x0C 波特率序号、0x0D CAN ID、0x0E Master ID 的只读查询。设置仅保存在 RAM，复位恢复头文件配置。未实现改 ID/波特率、校准、保存、重启等命令，返回操作失败 03；未知寄存器返回 01，周期/模式取值错误返回 02，成功为 00。错误帧头、长度、扩展帧或不匹配的地址不响应。

也兼容该手册附录的查询方式：ID `0x6FF`，DLC 4，数据 `ID_L ID_H RID CC`，例如 `6FF#500003CC` 查询欧拉角。这与达妙电机的 `0x7FF` 查询不同。

默认加入 IMU 后，不查询达妙电机时总发送量约 **700 帧/秒**（大疆 300 + IMU 400）；四台达妙电机各查询 100 Hz 时约 **1100 帧/秒**，不含外部请求帧。IMU 的额外查询会再增加反馈。电机与 IMU 轮流入队；IMU 周期数据只保留最新一组，查询/设置应答队列最多 16 帧，满后丢弃新应答并累加 `imu_sim.dropped`。

屏幕每 5 秒切换电机页和 IMU 页，显示 IMU 命令/反馈 ID、主动或查询模式、周期、发送入队数和丢弃数。`imu_sim`、`imu_tx_queued` 可供调试器观察。

主机测试覆盖 8 秒四类数据变化、四元数归一化及欧拉角一致性、重力模长、三轴角速度、两种查询、模式/周期设置、错误输入、队列上限/重试/公平性、tick 回绕、关闭 IMU、自定义 ID。已烧录并检查运行状态：约 1 秒内总 TX 入队增加 700 帧，其中 IMU 增加 400 帧；fatal_error、CAN 错误、Bus-Off、TX BUSY、RX 丢失、LCD 错误均为 0，lcd_ready=1。尚未使用外部 CAN 接收器验证 IMU 数据解码。

## 硬件

| 项目 | 配置 |
| --- | --- |
| MCU | STM32H723ZGT6，1 MB Flash，LDO |
| CAN TX / RX | PD1 / PD0，AF9，需经过 CAN 收发器 |
| 总线 | 经典 CAN 1 Mbit/s，非 CAN FD |
| CAN 时钟 | 外部 25 MHz HSE；分频 1、Seg1=19、Seg2=5、SJW=4，80% 采样点 |
| CPU | 内部 HSI 64 MHz |
| 屏幕 | 沿用 test_h7 的 240×240 初始化序列 |
| SPI6 SCK / MOSI | PG13 / PG14，模式 0、8 MHz |
| CS / DC / BL | PG8 / PG15 / PG12，背光高有效 |

CANH/CANL 对接、两端共地，两端各 120 Ω；对端正常模式提供 ACK。屏幕为软件复位，不控制独立 RST。25 MHz 晶振和面板配置需与实际板卡一致。

## 大疆协议差异

三类均使用 8 字节标准控制帧，每两个字节为一台电机的大端 int16 指令。

| 配置 | ID 范围 | ID 1～4 控制组 | ID 5～末号控制组 | 输入限幅 | 反馈基址 |
| --- | --- | --- | --- | --- | --- |
| C620 | 1～8 | 0x200 | 0x1FF | ±16384，对应 ±20 A | 0x200 + ID |
| C610 | 1～8 | 0x200 | 0x1FF | ±10000，对应 ±10 A | 0x200 + ID |
| GM6020 | 1～7 | 0x1FF | 0x2FF | ±30000 电压指令 | 0x204 + ID |

控制槽位为 `(ID-1)%4`。默认一台；可用 `SIM_DJI_FIRST_ID` 和 `SIM_DJI_COUNT` 配置连续同类型节点，不允许跨越上述范围。

反馈为大端角度 uint16（0～8191）、转速 int16（RPM）、电流/转矩反馈原始值 int16；C620/GM6020 字节 6 为温度，字节 7 保留为 0；C610 字节 6、7 均为保留 0。反馈标称周期 1 ms，可改 `SIM_DJI_FEEDBACK_MS`。

模拟器用一阶惯性计算转速并积分角度。C620/C610 电流反馈跟随钳位后指令，GM6020 由电压和速度差估算模拟电流，**不把电压指令原样冒充电流反馈**。GM6020 电流原始单位与真实电流的标定不作保证。温度固定 30 ℃。满输入的模型目标转速默认为 C620/C610 9000 RPM、GM6020 320 RPM，时间常数 150 ms，均为模拟参数。

## 达妙四种控制报文

`SIM_DM_ID` 为基本电机 ID（1～15），控制 ID 按模式加 0x000/0x100/0x200/0x300，特殊命令也发给该模式的控制 ID。

| 模式 | DLC | 控制数据 |
| --- | --- | --- |
| MIT | 8 | P16/V12/Kp12/Kd12/T12 位域 |
| 位置速度 | 8 | 小端 IEEE754 float32 位置 rad + float32 最大速度 rad/s |
| 速度 | **4** | 小端 IEEE754 float32 目标速度 rad/s，可正可负 |
| 力位混控 | 8 | 小端 float32 位置 rad + uint16 速度×100 + uint16 电流比例×10000 |

位置速度的限速须非负。力位混控两个 uint16 值按手册限制到 0～10000，再转换为 0～100 rad/s 和 0～1 电流比例；模拟模型最终仍受 `SIM_DM_V_MAX` 限制。NaN/Infinity、错误 ID/DLC 和扩展帧均拒绝，不刷新指令看门狗。

反馈四种模式相同：DLC 8，ID=`SIM_DM_MASTER_ID`（默认 0）；字节 0 为状态高四位、电机 ID 低四位，随后为 P16/V12/T12、MOS 温度、线圈温度。默认映射 **P ±12.5 rad、V ±30 rad/s、T ±10 N·m**，外部控制器需一致。Kp 0～500、Kd 0～5。采用截断量化，零点反馈可能有半个 LSB 的偏差。

全部模式支持 8 字节特殊指令：前七字节 FF，末字节 FC 使能、FD 失能、FE 设零、FB 清错。上电失能，每条有效指令回复一次当前状态。设零仅保存在 RAM，不跨复位；清错后失能。速度模式的特殊指令仍是 **8 字节**，普通速度指令是 **4 字节**。

四种达妙单电机模式均支持上述 `0x7FF + CC 00` 状态查询：数据填写 `SIM_DM_ID`，回复 ID 为 `SIM_DM_MASTER_ID`，默认 `7FF#0100CC00` → `0x000`。无需先使能；失能或超时状态也能查询。查询不改变控制目标、不计为控制指令，也不刷新本模拟器的 500 ms 控制超时。三种大疆单电机模式不响应此达妙查询。

例如默认 ID 1：

```text
MIT：使能 001#FFFFFFFFFFFFFFFC；+1 Nm 001#7FFF7FF0000008CC
位置速度：使能 101#FFFFFFFFFFFFFFFC；1 rad、1 rad/s 101#0000803F0000803F
速度：使能 201#FFFFFFFFFFFFFFFC；+1 rad/s 201#0000803F
力位混控：使能 301#FFFFFFFFFFFFFFFC；1 rad、1 rad/s、25%电流 301#0000803F6400C409
```

需周期发送普通控制帧。500 ms 无有效指令时，达妙进入状态 0xD、力矩清零，需 FB 清错、FC 使能恢复；大疆输入清零并减速。超时为模拟器配置，不代表所有真机默认设置。

MIT 使用 PD＋前馈；位置速度/力位使用模拟位置和速度闭环，力位按电流比例限制模型力矩；速度模式用模拟速度闭环。模型使用 1 ms 积分，惯量 0.02、阻尼 0.10，在 ±P_MAX 设置模拟机械限位。它们用于上层协议与逻辑联调，不是对真实驱动控制器及机械动力学的精确复现。

## 构建、测试与外部联调

依赖 ARM GCC/newlib、CMake、本机 STM32CubeH7 V1.13.0 HAL/CMSIS：

```sh
cmake -S . -B build -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi.cmake -DSIM_PROFILE=
cmake --build build -j4
bash tests/run.sh
```

产物为 `can_h723.elf/.hex/.bin`，BIN 起始地址 0x08000000。HAL 路径可通过 `STM32_CUBE_H7` 覆盖；手写工程无 .ioc。newlib 的 stdio 未实现警告不影响构建，本工程无 printf 串口日志。

本次七种配置均已交叉编译，在 `build-SIM_...` 中生成独立固件。主机 ASan/UBSan 测试覆盖七种协议、大小端、长度、状态切换、错误输入、超时、模式隔离、上半组大疆 ID、每种配置 20,000 帧随机输入。**2026-09-30 已烧录包含 `0x7FF + CC` 状态查询的混合模式固件，16,288 字节 Flash 校验通过并复位运行。运行检查 fatal_error、CAN 错误、Bus-Off、RX 丢失、TX BUSY、LCD 错误均为 0，lcd_ready=1；约 1 秒内 TX 入队增加 303 帧。尚未使用外部控制器验证达妙查询回复。**

2026-09-30 新增并已烧录 `0x7FF + CC` 查询支持。新增主机测试覆盖 DLC 4/8、四节点映射、完整 16 位地址匹配、只读语义、控制超时、100 Hz 变化反馈、队列溢出，以及四种模式下自定义电机 ID/Master ID。

Linux SocketCAN 接口配置为 1 Mbit/s 后，运行与已烧录配置匹配的命令；脚本只支持默认电机 ID 和量程：

```sh
python3 tools/motor_client.py c620 --current 4096
python3 tools/motor_client.py c610 --current 3000
python3 tools/motor_client.py gm6020 --current 10000  # 此参数实际为电压原始指令
python3 tools/motor_client.py mit --torque 1
python3 tools/motor_client.py pos-vel --position 1 --velocity 1
python3 tools/motor_client.py vel --velocity -1
python3 tools/motor_client.py pos-force --position 1 --velocity 1 --ratio 0.25
```

脚本 100 Hz 发送，解码反馈，默认 3 秒后发送零输入/失能。不需 python-can。对端应观察到所选反馈 ID 的状态变化；未选中的协议不回复。

## 屏幕、队列与边界

屏幕每 5 秒切换电机页/CAN 页，只显示所选电机状态，每 500 ms 更新快照，逐扫描线传输。TX Q 为硬件入队数，BUSY 为队列满重试次数，RX 为匹配控制 ID 的接收数，DROP 为达妙软件回复队列溢出数，LOST 为硬件溢出标志记录次数。

硬件 TX 8 帧、RX 32 帧，达妙软件回复队列 32 帧；无 ACK 会满队列。大疆反馈合并为最新状态，达妙满队列丢弃新回复并计数。Bus-Off 后修复总线并复位。调试变量：`motor_sim`、`can_*`、`lcd_ready`、`lcd_errors`、`fatal_error`。屏幕 MCU 侧无错误不等于面板显示已被验证。

未实现寄存器读写、参数持久化、固件升级、GM6020 其他固件版本的额外控制协议或真实热模型。所有 API 由单个前台执行，不支持中断/任务并发。需用主机测试或外部控制器验证协议；混合模式的内部回环注意上述低字节交叉触发。

## 协议依据

- [C620 官方手册](https://cdn-hz.robomaster.com/robomasters/public/document/RoboMaster%20C620%20Brushless%20DC%20Motor%20Speed%20Controller%20V1.0.pdf)
- [C610 官方手册](https://cdn-hz.robomaster.com/tem/RM%20C610无刷电机调速器使用说明%20发布版.pdf)
- [GM6020 V1.2 官方手册](https://rm-static.djicdn.com/tem/17348/RoboMaster%20GM6020直流无刷电机使用说明.pdf)
- [达妙 DM-J4310-2EC 官方文档](https://github.com/dmBots/DM-J4310-2EC)：V1.2 电机使用说明书 V1.4，2026-09-14，控制模式及 CAN 章节。
- [达妙官方 C++ 状态查询实现](https://github.com/dmBots/motor-control-routine/blob/master/orin载板can控制达妙电机例程/dm_hw/src/hardware_interface/damiao.cpp)：`refresh_motor_status` 使用 ID 0x7FF、4 字节 `ID_L ID_H CC 00`。
- [达妙官方 Python SDK](https://github.com/dmBots/motor-sdk/blob/main/Python例程/u2can/DM_CAN.py)：`refresh_motor_status` 使用同样内容并补零到 8 字节。

- [达妙 DM-IMU 官方手册及示例](https://github.com/dmBots/DM-IMU)：`manual/达妙科技DM-IMU-L1六轴IMU模块使用说明书V1.2.pdf` 第 8～10 页、附录二/三；`examples/mc02-can-transmit-receive-example/DM-IMU/`。
