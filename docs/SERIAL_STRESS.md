# USB / UART 可重复压力测试

保留原验收：

```bash
desktop/.venv/bin/python scripts/test-serial-hardware.py --output ~/grokbot/baseline-acceptance.json
```

新压力入口（原生 USB `/dev/ttyACM0`，外部 UART `/dev/ttyACM1`）：

```bash
desktop/.venv/bin/python scripts/test-serial-stress.py \
  --bauds 9600 19200 38400 57600 115200 230400 460800 921600 \
  --duration 60 --output ~/grokbot/baseline-stress.json
```

默认四档 115200/230400/460800/921600；可选择全部八档。每档分别运行 60 秒持续全双工和 5 秒突发全双工，分两组：无损组 UART→USB 限至 min(1200 B/s, 半线速)，USB→UART 至多单在途并限至 600 B/s；过载组 UART→USB 提供 98% 的 8N1 线速，USB→UART 单在途尽快发送。限速无损组用于验证内容和会话正确性，不代表最大无损吞吐。持续流以小块匀速发送；突发以最高 4096 字节批量交给适配器，平均速率不变。

每次保存输入/输出字节数、SHA-256 和严格相等结果、type19 包序号缺口/重复/乱序、type20 p50/p95/p99、PING RTT、type21 RX/TX/drop 增量。吞吐分母为负载持续时间，另记包含排空的实耗时；主机实际提供速率可用 input_bytes/duration_s 计算。过载组允许 RX 丢失，但严格相等仍如实为 false，不把过载通过当作无损；发送完成确认、重复和会话泄漏仍必须正确。type21 是双向软件丢弃总数，不能保证覆盖 UART 硬件 FIFO 尚不可知的损失。测试绕过桌面引擎和页面，不能据此宣称桌面吞吐已验证。

最后在真实 UART 输入负载下各两次 BYE、停止心跳和关闭/重开 USB，验证新会话不收到旧 token/旧数据，旧 session 的 UART 命令无物理输出，新会话双向严格相等且无重复尾部。已进入 UART FIFO 的旧字节允许在建立新会话之前排空；完整保存边界清理数据。部分 Linux/USB 驱动在关闭/重开时会触发芯片复位，报告记录观察到的 ROM banner；软件重开不替代物理拔插/断电试验。

设置修改前通过 0x19 查询并保存 initial_settings，最终恢复 USB + UART + 115200，释放两端口。每秒保存当前案例的部分计数，每个完成案例原子更新 JSON；过载导致确认超时会保留 FAIL 与部分数据，另建新会话继续，不重发结果未知的命令。报告同时记录适配器写阻塞和 USB 读取间隔，帮助区分负载工具的停顿；可用 `--patterns burst` 单独复现突发、`--recovery-only` 独立检查会话恢复。最终 BYE、close 异常分别记录，报告仍保存。FakeSerial 失败路径测试：`desktop/.venv/bin/python tests/test_serial_hardware.py`。不连接 CAN 总线时不要传 `--can-tx`。

## Desktop engine delivery load

Run the two ports sequentially with other desktop/serial clients closed:

```sh
desktop/.venv/bin/python desktop/tests/engine_hardware.py \
  --usb /dev/ttyACM0 --peer /dev/ttyACM1 \
  --bauds 115200 460800 921600 --duration 20 --burst-duration 5 \
  --recovery-cycles 2 --ui-delay-ms 25 --output ~/grokbot/desktop-engine-load.json
```

This opt-in path uses physical UART → `UsbTransport.read` → `Channel.receive` →
`Channel.flush` → JSON serialization and delayed delivery receipts. It records
input/output lengths and SHA-256, strict RX/TX equality, sequence gaps,
desktop queue drops/high water, delivery age percentiles, process CPU and sampled
RSS. CPU includes fixture/measurement work; RSS includes test histories. It does
not run a Qt renderer. The age of an aggregate is its oldest packet's age and
is conservatively assigned to every packet in that run. B/s uses offered-load
duration and includes bytes collected during the final two-second drain.
Overload permits measured RX loss; read the equality/drop fields alongside `ok`.

Only contiguous UART sequences are aggregated; gaps, duplicates and sequence wrap
retain their meaning. The desktop keeps 512 data and 64 control packets, spends
at most 16 KiB of wire packets / 4 ms per flush, and allows one data delivery plus
one control delivery awaiting receipts. Receipts carry unique tokens so a late
callback from a disconnected session cannot release a new batch. A stalled data
receipt can still allow a control batch; sufficiently slow consumers cause
bounded, counted drops rather than increasing memory indefinitely. Unit tests
inject 5000/8000 packets/s and 150 ms data receipts.

Each recovery label runs twice by default. Both `bye` and `reopen` use the
public `Channel.disconnect` path (BYE plus close/reopen); `heartbeat` additionally
stops heartbeats for 4.5 seconds first. These check fresh sessions and exact
physical output, not distinct open-port BYE behavior or stale-session injection.
Use `scripts/test-serial-stress.py --recovery-only --recovery-cycles 2` for those
stronger recovery checks under UART input. The tool restores USB + UART + 115200 and closes both ports.
Run `desktop/tests/serial_hardware.py` separately as the desktop user for real Qt
acceptance, including strict 4096-byte HEX/UTF-8 output and completion latency.
Desktop TX stays single-inflight: Python validates physical type20 token, length
and echoed bytes before submitting the next segment. Progress is merged into UI
batches. Cancellation stops subsequent segments, waits for the submitted one,
and never retries an unknown result.
