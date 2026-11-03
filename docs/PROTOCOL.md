# CAN Scope BLE protocol v1

Default peripheral name: `ESP32-CAN` (persistently configurable in App 1.6 firmware). Only one central connects at a time.

| GATT object | UUID | Properties |
| --- | --- | --- |
| Service | `c0de0001-1212-efde-1523-785feabcd123` | Primary |
| Command | `c0de0002-1212-efde-1523-785feabcd123` | Write with response |
| Events | `c0de0003-1212-efde-1523-785feabcd123` | Notify (enable CCCD) |

Each packet is exactly 20 bytes. No MTU negotiation or fragmentation is required (ATT MTU 23). Integers are unsigned little-endian. Firmware rejects invalid version, type, length, flags, ID or DLC with ATT errors; a full command queue returns insufficient resources. ATT write success means **queued**, not CAN TX success.

## Persistent remote settings (firmware 1.24.2)

Command `0x19` queries or updates the shared configuration used by the screen, with updates persisted in the existing `local_panel` / `settings` NVS value. BLE commands, authenticated Wi-Fi transport and native USB share the validator and settings owner; existing CAN/UART commands and event types 19/20/21 keep their meanings. The owner also runs without a working LCD. A motor pulse in progress or settings initialization still in progress rejects a settings request with insufficient resources (`17`); a read-only query may be retried after startup.

| Offset | Bytes | Command `0x19` |
| --- | --- | --- |
| 0 | 1 | Version = 1 |
| 1 | 1 | Type = `0x19` |
| 2 | 1 | Operation: 0 query, 1 set |
| 3 | 1 | Set mask: bit0 network, bit1 data, bit2 UART baud; no other bits |
| 4 | 1 | Network: 0 BLE, 1 Wi-Fi, 2 dual A, 3 dual B, 4 USB |
| 5 | 1 | Data: 0 CAN, 1 UART |
| 6 | 2 | Reserved, zero |
| 8 | 4 | UART baud, one of 9600/19200/38400/57600/115200/230400/460800/921600 |
| 12 | 4 | Reserved, zero |
| 16 | 4 | Nonzero request token |

A query requires bytes 3–15 to be zero. A set requires mask 1–7, and every unselected field must be zero. Validation covers the complete request before any modification; invalid values/padding return ATT value-not-allowed (`19`, also in the USB type18 admission result) and do not alter settings.

Event **22 decimal** acknowledges an admitted valid request: byte0=1, byte1=22, byte2 result (`0` queried, `1` saved, `2` NVS failure), byte3 configured network, byte4 configured data, bytes5–7 zero, baud u32 at8, bytes12–15 zero, original token u32 at16. Result1 is emitted only after NVS commit, or when the identical value is already durable. A save failure preserves the previous configuration. A query returns the shared configured values; it does not reread or flush NVS, and can include a recent screen change awaiting its delayed save. The independent settings worker applies the shared configuration to transports and the UART driver shortly afterwards; event22 is not UART readiness or a byte-delivery acknowledgment. Use existing event21 and actual traffic for those checks.

The screen reads the same settings snapshot. Its delayed saves use compare-and-set so an older screen snapshot cannot overwrite a concurrent remote save. Switching transport resets the old connection and pending commands; its completion reply may be lost, so reconnect and query using a fresh token. Entering USB requires the normal HELLO before CAN/UART traffic. Do not automatically retry a CAN/UART send whose completion is unknown.

Native USB additionally supports a limited bootstrap before HELLO and while another network mode is selected: a valid query, or a valid set whose network bit is selected and network is **4**, can be sent in the existing CRC-protected CUSB frame with a nonzero session ID. Firmware replies directly with type18 and, if admitted, event22 under that ID, without creating a data session. Data-only changes and selecting another network are rejected before a USB session; while a session exists, settings requests under other session IDs are ignored. The existing HELLO session replacement rule is unchanged. This physical USB path avoids consuming external UART data as AT commands. See [USB.md](USB.md) for the CLI.

## Frames and results

| Offset | Bytes | Meaning |
| --- | --- | --- |
| 0 | 1 | Version = 1 |
| 1 | 1 | RX = 1, TX success = 2, TX failure = 3, send command = 0x10 |
| 2 | 1 | Flags: bit 0 extended ID, bit 1 RTR; type 3 additionally uses the failure flags below; send commands require other bits zero |
| 3 | 1 | DLC 0–8 |
| 4 | 4 | CAN ID: standard ≤ 0x7FF, extended ≤ 0x1FFFFFFF |
| 8 | 8 | Data padded with zeros; RTR has no data |
| 16 | 4 | RX: device uptime in ms (wraps at 2^32); command/TX result: request token |

The phone uses nonzero tokens to correlate CAN TX completion; device-generated periodic frames use token 0. TX success is emitted only from a successful TWAI completion callback, with normal ACK mode enabled. Failure is returned for failed transmission or driver enqueue rejection. No retransmission is performed by the app automatically. Missing result due to disconnect/overflow means **unknown**, not success. Notification delivery itself is not application-level acknowledged.

Since firmware 1.16.2, TX failure type3 byte2 uses bit2 ACK error, bit3 bit error, bit4 form error, bit5 stuffing error and bit6 driver rejection. Multiple flags may occur together. Bit7 remains the earlier pure-arbitration diagnostic marker; it is excluded from communication-failure totals and App failure lists. A mixed arbitration/communication error carries its communication-error bits without bit7. Low EXT/RTR bits, DLC, payload and request token are unchanged. No error-detail bits means the device did not provide a specific cause (including older firmware); the App must not infer one from a cumulative counter or payload. These flags describe observed TX failure categories, not a diagnosis of physical wiring. Successful frames and commands do not carry failure bits.

In App1.16.2 the RX/TX/failure statistic cards select received frames, successful TX frames, or failure-reason records respectively. CAN ID filtering combines with that view; an All control restores the combined stream. Failure records are retained under the TX limit independently of the boot-total failure counter; counters may include earlier or unreported events, while retained records can be limited, cleared or dropped.

## Status (type 4, every second)

| Offset | Bytes | Meaning |
| --- | --- | --- |
| 0 | 1 | Version = 1 |
| 1 | 1 | Type = 4 |
| 2 | 1 | TWAI state: 0 active, 1 warning, 2 passive, 3 bus-off |
| 3 | 1 | Periodic demo enabled: 0/1 |
| 4 | 4 | RX frames seen by ISR |
| 8 | 4 | Successful TX frames |
| 12 | 4 | Failed TX requests |
| 16 | 4 | Monitoring drops: CAN software RX queue + BLE notification queue/failures |

Counters are device boot totals and wrap at 2^32. Monitoring drops do not count frames intentionally unobserved while no BLE subscriber is connected, nor hardware FIFO losses. App UI queues have separate drop accounting. CAN continues while BLE is disconnected.

## Device-timed RX/TX rates (App/firmware 1.16.3, type 14)

Packet bytes0/1 are 1/14, bytes2–3 zero, u32 RX frame delta at4, u32 successful TX delta at8, actual ESP32 elapsed microseconds at12, and bitrate at16. Each window is at least one second; a delayed owner iteration uses its real longer interval. RX counts successful ISR receives before software queue drops, while TX counts successful completions, including queries. The App computes each rate as `delta * 1,000,000 / window_us`. Phone arrival time is used only for four-second display expiry, never as the denominator. Batched, lost or delayed BLE notifications therefore cannot inflate the reported rate.

The owner resets the window on bitrate changes; unsigned counter subtraction tolerates wrap. Backward clocks and intervals exceeding uint32 microseconds rebase without a sample. Type14 is a periodic control-queue statistic, never an ACK or raw frame. It is allowed only in mode0 and suppressed before queueing and delivery in home/detail/silent modes. Android dispatches it separately from the lossy raw UI queue, only to the confirmed active full-monitor channel. Disconnect, subscription changes and bitrate changes clear cached rates. Type4 totals remain unchanged for compatibility but no longer produce RX/TX rates in the new App. Old firmware without type14 displays no rate and an update hint instead of a potentially misleading phone-timed estimate. CAN-ID/direction filtering and retained-record limits do not affect these device-wide rates.

## Device-side estimated CAN load (App 1.14, type 13)

The ESP32 accumulates the estimated length of every successfully received Classic CAN frame in the RX ISR, before software queues, motor sampling or BLE filtering, and every successful TX completion, including device-side Damiao polling. Failed TX attempts and pure arbitration losses do not increment this estimate. Each frame is counted once in its direction. Statistics continue without a BLE connection.

| Offset | Bytes | Meaning |
| --- | --- | --- |
| 0 | 1 | Version = 1 |
| 1 | 1 | Type = 13 (decimal) |
| 2 | 2 | Device-computed load in 0.01% units, 0–10000 |
| 4 | 4 | Estimated RX bits in the window |
| 8 | 4 | Estimated successful TX bits in the window |
| 12 | 4 | Actual statistics window duration in microseconds |
| 16 | 4 | Actual CAN bitrate in bits/second |

Estimated frame length includes intermission: standard = 47 + 8 × data bytes, extended = 67 + 8 × data bytes; RTR contributes no data bytes. The device computes `100 × (RX bits + TX bits) / (bitrate × window_seconds)`, truncates to 0.01% and clamps to 100%. Windows are about one second, use the device clock, and reset when CAN bitrate changes so rates are never mixed. Unsigned cumulative counters are sampled by differences, including across 32-bit wrap.

This is an estimate of successfully observed frame traffic, not a physical measurement of bus busy time. It excludes bit stuffing, error/overload frames, failed transmission attempts and hardware receive losses. Software RX queue drops, motor selection/rate limiting and BLE raw-frame drops do not remove frames from the device's estimate. Losing a load notification delays the displayed update; the App never reconstructs load from delivered raw frames.

Type13 uses the bounded telemetry queue and is emitted only in full-monitor mode0. Home mode1, single-motor detail mode2 and silent mode3 suppress it, including already queued packets checked before delivery. This keeps the page-specific BLE traffic rules. App1.14 displays the device percentage and expires it after four seconds; reconnecting or switching page modes clears the cached value. Firmware without type13 shows no load value and an update hint instead of the previous App-side estimate. No new command or capability bit is required.

## Periodic demo command (type 0x11)

Byte 0 = 1, byte 1 = 0x11, byte 2 = 0/1 (disable/enable), all other bytes zero. Device status confirms the applied setting. Starts disabled after reset in App 1.1 firmware; when enabled, sends standard ID 0x123 once per second, first four bytes a little-endian counter followed by `11 22 33 44`. The CAN owner processes commands in order; a control command waits for an outstanding TX to finish.

## Scope

Bench prototype, default 1 Mbit/s Classic CAN (configurable through the extension below). BLE is unbonded/unencrypted and any nearby central can connect and issue CAN commands. Use only on your controlled test bus; authenticated pairing is not implemented. This is a bounded real-time monitor, not a lossless full-load recorder.

## Motor-panel extension (App 1.1)

Existing 20-byte v1 packets remain compatible. New firmware boots with demo TX **disabled**. Default bitrate is 1000000 until explicitly changed. Motor-capable clients wait for type 5 before allowing motor shortcuts.

### Bitrate command 0x12 / configuration event 5

Command: version 1, type 0x12, u32 bitrate at offset 4, token at offset 16, other bytes zero. Allowed rates 125000/250000/500000/1000000. Applied by the CAN owner with no outstanding TX; rejected during recovery. Disables demo TX. Changes are volatile and do not configure any motor.

Configuration event 5 is sent once a second and on command completion: byte 2 result (0 success/status, 1 failure), byte 3 capabilities (bit0 rate changes, bit1 bounded pulse, bit2 persistent BLE name, bit3 device-side polling), u32 actual bitrate at offset 4, token at offset 16 (0 for periodic status). Remaining bytes zero. Match a nonzero token to acknowledge a specific request.

### Bounded motor pulse 0x20 / stop 0x21 / progress event 6

Pulse command uses ID at offset 4, data at 8, token at 16. Byte 2 is **exactly 0x40 for DJI zero-group ending, or 0x80 for Damiao disable ending**; these are not CAN flags. All pulse CAN frames are standard data frames. DLC (byte 3) is 8 for DJI, 4 or 8 for Damiao. Execution lasts at most 500 ms of scheduled setpoint sends, period approximately 10 ms, one frame outstanding at a time. No automatic motor enable is added.

The ending frame uses the same CAN ID, DLC 8: DJI eight zero bytes; Damiao seven FF bytes and FD. Stop command 0x21 sets an out-of-band stop flag and discards queued commands, rather than waiting behind them. BLE disconnect also requests stop. A failed setpoint TX aborts the pulse. A physically failed/bus-off CAN link can delay or prevent the ending frame; there is no hard real-time physical-stop guarantee.

Progress event 6: byte 2 stage (1 started, 2 finished, 3 cancelled, 4 aborted after TX failure, 5 ending-frame failed); CAN ID at 4, successful TX count at 8, failed TX count at 12, pulse token at 16. Counts include the ending frame. Stages 2–4 indicate the ending frame obtained CAN ACK, not confirmed mechanical stop. Type 2/3 frame events for pulse frames use token 0, so clients must use event 6 for pulse completion.

While a pulse is accepted/executing, other writes are rejected with ATT insufficient-resources, except stop. Clients serialize GATT writes; a pending stop should be the next write. The reference Android client disables profile/mode changes while an operation is in flight. Control/status notifications have a separate priority queue from RX telemetry, but delivery remains best effort; missing completion means unknown.


## Persistent BLE name (App 1.6 firmware)

Capability bit 2 (value 4) in type 5 indicates support. Names are strict UTF-8, 1–16 bytes, no ASCII/C1 controls, not blank. Stored in NVS namespace `can_scope`, key `ble_name`; invalid/missing stored values fall back to `ESP32-CAN`. Ordinary power cycling and app flashing preserve the name; erasing NVS does not.

Rename command: 20 bytes, byte 0 = 1, byte 1 = `0x13`, byte 2 = UTF-8 byte length, byte 3 = 0, bytes 4–19 = name padded with zeros. This is a BLE configuration command; it never enters the CAN command queue. It is rejected while a motor pulse is busy. NVS set/commit happens before a success event; identical persisted names skip writes. GAP name is updated, next advertising uses the new name.

Name event type 7: byte 0 = 1, byte 1 = 7, byte 2 result (`0` periodic report, `1` saved, `2` save/apply failure), byte 3 name length, bytes 4–19 current stored name. Periodic report once per second while subscribed, plus command completion. There is no token: clients allow only one rename in flight and require result 1 with the requested name. ATT write completion alone is not the UI success signal. Missing result means unknown; reconnect/read the periodic report to verify. Names may be durable even if the connection drops before acknowledgment.


## App 1.9 feedback polling (no firmware protocol change)

Each enabled Damiao monitor entry is scheduled by the Android foreground Activity: at most 5 queries/s per motor and 20/s per channel, one outstanding CAN TX result, 3 s result timeout. The existing 0x10 BLE command wraps STD CAN 0x7FF, DLC 4, payload `[motor_id_low, motor_id_high, 0xCC, 0]`, padded with zeros. Query IDs use a token range starting above 0x40000000. The native monitor allowlist checks ID/flags/DLC/opcode/padding and motor-ID bounds; arbitrary CAN commands remain rejected.

TX result type 2/3 completes the query transaction; actual motor status continues through RX type 1. Failure/timeout disables that entry. Queries are foreground session state, not backup configuration. Disconnect/background clears the schedule; switching visible channel retains it. A stopped query already handed to CAN may still complete. No firmware update is required.


## App 1.10 device-side feedback polling (supersedes 1.9 scheduling)

Requires capability bit 3 (8) in event 5. The app defaults each DM motor to 100 Hz; `pollHz` is an optional persisted/backup integer 1–1000 (missing means 100). Activation is never persisted.

Command 0x14: motor base ID u32 at offset 4 (1–0x7FE), frequency u32 at 8 (0 disables, 1–1000 enables/renews), token at 16. Bytes 2,3,12–15 must be zero. 0x15 stops all polling: only version, command and token may be nonzero. The foreground app renews each motor about once a second; each entry expires independently after 3 seconds without renewal. Configuration renewals do not reset phase. Disconnect, bus-off, explicit stop, name/rate changes and legacy pulse stop polling. TX failure removes that motor.

Event 8: result byte 2 (0 active, 1 stopped, 2 bandwidth/capacity rejected, 3 invalid, 4 TX/bus failure, 5 lease expired), motor ID u32 at 4, configured Hz at 8, measured successful-query TX/s at 12, command token at 16 (0 asynchronous status). Disabling an unknown ID succeeds. Periodic measured rates use actual elapsed microseconds; configuration ACK alone is not a motor response.

Up to 32 entries; total configured Hz cannot exceed bitrate/400 (chosen 240 bits per query/reply and 60% reserve). A rejected update leaves an existing schedule unchanged; the reference app then disables that entry. This conservative configured-rate limit cannot account for external traffic. A 250 us esp_timer wakeup plus TX-completion notification drives the single CAN owner; no catch-up bursts. CAN payload remains STD 0x7FF DLC4 `[ID_L,ID_H,CC,00]`. Pending CAN buffers remain valid until completion. Poll TX counts remain included in event 4 and event 8. Since firmware 1.16.2, actual query completions additionally emit type 2 (success) or type 3 (failure); immediate driver rejection emits type 3. Query results carry the original STD 0x7FF DLC4 payload and token zero, are counted only once, and produce no per-frame serial log. Type 2/3 with token zero share the ordinary best-effort RX telemetry queue; nonzero legacy request tokens retain their control-queue route. Query configuration/renewal acknowledgments retain the separate priority ACK queue. Home/detail/silent modes suppress query raw TX both before queueing and before delivery; switching to raw mode does not replay earlier query frames. App TX retention includes successful and failed records, but pure-arbitration diagnostic frames remain hidden. RX and query TX telemetry can be dropped under congestion independently of authoritative device counters and CAN load.


## App 1.10.2 bounded query retries / diagnostic capability

Capability bit 4 (16) advertises query retry diagnostics. Event 8 byte 3 is now a bitmask: arbitration=1, ACK=2, bit=4, form=8, stuff=16, driver/enqueue=32, bus-off=64, unknown=128. Result 0 with nonzero detail means still active/retrying, result 4 means stopped. A renewal and periodic status preserve outstanding retry diagnostics; success clears diagnostics and both failure budgets. Old clients continue to interpret result 0 as active.

Classic ESP32 uses hardware single-shot mode; bounded retries are implemented only in the monitor scheduler, preserving asynchronous-buffer ownership and stop/lease semantics. Pure arbitration loss has a limit of 32 failed attempts without success; other failures have a limit of 5. Mixed arbitration+bus error consumes the communication-error budget. Retry backoff is 2 ms times arbitration streak (capped at 8), or 5 ms times communication-error streak, plus a small deterministic ID/streak offset; it never advances the next configured slot. No catch-up burst or unbounded hardware retransmission. Lease renewals never reset failure budgets. Bus-off stops all polls immediately with detail 64. Removing/stopping an entry cancels retries; a late completion cannot recreate it.

Error flags are captured by on_error while a frame is outstanding and queued with its TX completion. They represent controller observations during that attempt, not proof that a motor application accepted or rejected a command. Firmware can transmit again on the next allowed slot after an intermittent error; arbitrary motion commands retain their previous behavior.


## App 1.10.3 communication-failure counters (arbitration hidden)

Capability bit5 (32) identifies separated counters. Event 4 offset12 now counts failed TX attempts other than pure arbitration loss; mixed arbitration+ACK/bit/etc, unknown failure flags and immediate driver rejection still count. Successful TX still increments only event4 offset8. A private diagnostic arbitration counter increments only when success=false and error_flags equals exactly 1. Event5 offset8 now carries this diagnostic u32 counter; App never displays it. All counters reset at boot, and a later successful retry does not decrement genuine communication failures.

Event8 reason0/detail1 is rendered exactly like normal active query status, with no arbitration count or retry text. Reason4/detail1 retains the stopped state but displays only “总线持续繁忙，查询已停止”. The bounded retry policy is unchanged. For legacy raw/pulse TX result3, flag byte2 bit7 marks pure arbitration and is ignored by the monitor frame list; the low CAN flag bits and completion token remain intact for legacy clients. All other type3 frames remain visible.

The App labels event4 offset12 “通信失败 · 累计” only on firmware with bit5; on older firmware it displays “需更新” instead of falsely labelling the combined count. Connection/reset clears cached metrics, and configuration/status arrival order is handled.


## App 1.11 home online summaries

Capability bit6 (64) enables page-scoped telemetry subscriptions. Existing clients default to full raw telemetry. New clients configure every channel, choose quiet mode for home and all hidden channels, and full mode only for the selected channel. Subscriptions do not generate CAN traffic, change bitrate, or stop polling leases.

All commands are version1,20 bytes. Stage command0x16: slot index byte2(0–31), family byte3(0 DJI/1 DM), base motorID u32@4, feedback CAN ID u32@8, reserved u32@12=0, nonzero revision u32@16. A different revision starts a fresh staging table. Commit command0x17: mode byte2(1 quiet/0 full), count byte3(0–32), bytes4–15 zero, revision@16. Commit requires exactly slots0..count-1 staged under that revision (zero count needs none), applies atomically, and preserves last-seen only for identical matching rules. Existing staged revision can switch mode without resending rows. The app serializes writes, waits for revision-matched ACK and retries incomplete transactions after2s; stale summaries/ACKs are ignored.

Event10 acknowledges mode: byte2 mode, byte3 count, revision@16. Event9 once per second in quiet mode: byte2=1, byte3=count, online bitmap u32@4, ever-seen bitmap u32@8, zero@12, revision@16. Bitmap index matches subscription slot, including bit31. Online means matching standard data feedback in the last2000ms. DJI allows DLC7/8; DM requires DLC8, ID-low-nibble match and excludes known mode-register replies. This is feedback presence, not enabled/motion status. BLE disconnect/reset discards subscription and timestamps. App expires missing summary after3s.

Event16 adds motor temperatures to the same once-per-second quiet-mode cycle without changing event9 or subscription commands: start slot byte2 (0/10/20/30), total registry count byte3 (1–32), little-endian u16 fresh bitmap@4, up to ten unsigned temperature bytes@6–15, revision u32@16. Batch length is min(10,count-start); unused bits and bytes are zero. Set bits identify matching motor feedback less than2000ms old; a clear bit means unavailable, including never-seen motors and IMU entries. DJI uses feedback byte6; DM uses motor temperature byte7, separate from MOS temperature byte6. The App excludes C610 and GM3510 because their protocols do not provide motor temperature, validates revision/count/start, and exposes nullable values to home cards. Same matching rules preserve temperature and timestamps across an atomic registry update. Queued event16 is discarded after leaving quiet mode or changing revision. Old firmware continues sending event9 and leaves home temperatures unavailable; no raw stream or additional CAN traffic is enabled.

Quiet mode suppresses raw events1/2/3 at the bridge before queueing and rechecks queued data before delivery; pending raw RX queue is cleared on mode commit. Intentional suppression does not increment monitor-drop counters. Essential status and command/query confirmations remain. Quiet mode suppresses periodic config5/token0 and name7/result0 as redundant; initial name is sent on notification subscription and capability/config is received before the app enters quiet mode. Explicit name-save results and bitrate command ACKs still pass. A notification already handed to the BLE stack may still arrive while switching. No history backfill on return to full mode; each channel retains its configured history limits and registry independently.


## App 1.12 single-motor detail and silent channels

Capability bit7 (128) adds commit modes 2 (single motor) and 3 (silent); bits0–6 remain unchanged, current capabilities=255. Command0x17 byte2 is mode0–3, byte3 count; u32@4 selects a staged slot for mode2 (must be less than count), otherwise must be zero. Bytes8–15 remain zero. The same revision-matched transaction/ACK rules apply. Mode2 requires at least one staged motor; mode3 may commit an empty table. Android stages only the selected motor in slot0 for mode2 and sends an empty mode3 commit to all other channels. In the home page all channels return to mode1 with their full motor registries. Ordinary channel pages retain mode0 for the active channel and mode1 for other channels.

Event11 is a single-motor sample: version1@0, type11@1, original CAN flags@2, DLC@3, subscription revision u32@4, payload bytes8–15, original CAN receive timestamp u32@16 (milliseconds, wrapping). The revision maps to the single selected feedback address/configuration; no redundant CAN ID is transmitted. Firmware keeps only the most recent matching feedback and emits at most one sample per50ms. No fresh feedback means no sample; it never fabricates repeated points. Matching uses the same standard-frame, DLC, DM low-nibble and mode-reply exclusion rules as home summaries. Old samples are discarded on commit and revision mismatch. Intentional coalescing is not a monitor drop. The App discards stale revisions and samples for other motor keys, and displays no raw packet traffic in the detail page.

Modes2/3 suppress raw1/2/3, periodic stats4, config5/token0, name7/result0 and summaries9. Mode2 emits event11 and optionally selected-motor query progress; mode3 emits no periodic monitoring data. Explicit command acknowledgments, lease-renewal acknowledgments and terminal query failures remain available for transport/lease correctness. Queries already enabled continue on ESP32; changing a page neither enables nor stops CAN queries. Disconnect retains existing query-stop behavior. Thus a hidden channel with active polling may still exchange necessary query management packets. A BLE notification already submitted before commit may finish in transit; Android immediately gates raw data and checks subscription revisions.

Detail curves retain at most1200 latest samples/60seconds, offer10/30/60second windows and frozen-curve display, separate gaps above250ms, and show feedback timeout after2seconds without samples. C620/C610/GM6020 use rotor single-turn degrees, rpm and raw current feedback; C610 has no temperature curve. DM uses configured rad/rad/s/N·m ranges and separate motor/MOS temperatures. Sampling is for visual monitoring, not lossless capture of high-frequency peaks. Old firmware without bit7 cannot provide detail sampling or silent channels and must be upgraded; the App shows an upgrade hint instead of substituting raw data for the detail page.


## App 1.13 adjustable detail sampling (supersedes fixed-rate detail above)

Config event5 u32@12 advertises the maximum supported detail sample rate (1000 on this firmware; previous versions send0). Capability byte remains255. Commit0x17 mode2 u32@8 is now sample limit Hz: 1–1000, or0 for legacy20Hz. Other modes still require0. ACK10 u32@8 returns effective rate (20 for legacy, requested rate for explicit detail,0 otherwise). The existing revision transaction rejects old samples/ACKs when changing rate. No additional CAN command is sent when changing detail rate.

With an explicit rate, selection occurs at the CAN RX path, using receive timestamps captured in the RX ISR with millisecond resolution, carried in event11 u32@16. A phase accumulator handles non-divisor frequencies and unsigned32 timestamp wrap, skips missed periods without replaying frames, and emits selected frames immediately into a bounded256-packet BLE queue. Legacy rate0 remains latest-only20Hz. No fabricated repeats if feedback is absent. BLE submission/queue failures remain monitor-drop counters; deliberate rate limiting is not a drop. No full-throughput delivery guarantee.

Event12 appears only in explicit-rate detail mode, about once/second: version1/type12, reserved bytes2–3 zero, cumulative matching RX count u32@4, cumulative selected sample count u32@8, statistics elapsed milliseconds u32@12, subscription revision u32@16. Counts reset on commit. Rates use deltas between consecutive matching revisions; sample count is before BLE delivery, not acknowledgment from the phone. Mode3/home/raw and legacy mode2 suppress this event. App1.18.2 batches event11 delivery and records every accepted sample, then updates the visible detail view via requestAnimationFrame. The timer requests the same paint scheduler to maintain offline status, rates and chart time progression. The phone requests high connection priority while viewing CAN monitoring or motor details, and balanced priority otherwise; achieved throughput depends on BLE negotiation and rendering capacity.

Per-channel optional backup field detailRates maps profile:id:feedbackId to integer1–1000; unknown motor keys are ignored, absent map defaults100Hz. Modifying this map does not change a motor query key or pollHz, so it does not toggle an existing DM query. Old firmware is detected from event5@12=0, uses legacy subscription and disables rate application. Chart history is bounded to60000 live samples/60seconds with a small amortized trim buffer, and at most one frozen copy; timestamp-equal samples are retained. Wide views preserve chronological first/min/max/last points per pixel bucket. Display refresh rate is distinct from requested sample rate; batched screen painting does not remove accepted samples or their peaks.

App1.18.2 bounds native receipt to512 ordinary events and64 UI management events per channel. The UI drains at most128 events per16ms scheduling interval, with at most one ordinary WebView delivery awaiting completion. Overload discards oldest ordinary events and increments App drops; UI management events drain first and are never evicted by ordinary traffic. Query acknowledgments and write completions keep their separate FIFO control-worker path, now bounded to128 events. A management overflow closes the connection with an explicit error. Receipt age and WebView delivery age are carried to JavaScript so queued data cannot refresh stale feedback. Connection and page generations reject delayed callbacks.


## App 1.14.1 query-configuration timeout recovery

The v1 command/event layouts are unchanged. Query configuration and lease renewal (`0x14`) and stop-all (`0x15`) use a separate firmware management queue. The CAN owner handles them even when a CAN transmission is still outstanding. This never replaces or releases an in-flight CAN buffer; its eventual completion cannot recreate a stopped polling entry. Stop requests remain valid during bus-off recovery. A positive-frequency request during recovery receives event8 result4, detail64 (bus-off) and its original token instead of being discarded without acknowledgment.

Explicit configuration acknowledgments use a separate bounded priority notification queue, ahead of periodic states and raw telemetry. Stop-all confirmation cannot be crowded out by up to32 asynchronous motor-stop reports. Local BLE submission failures are retried within a bounded budget; notification delivery is still best effort and does not prove a motor response. Periodic query progress keeps token0 and the existing page-specific filtering rules.

The Android query-management path runs separately from the UI. GATT API submission, GATT write completion and the device's token-matched configuration acknowledgment are distinct phases. A busy/rejected submission is not treated as an accepted configuration that is waiting for a reply. Transient submission failures use finite retries; a missing configuration acknowledgment stops local lease renewals, requests stop-all within a finite budget, and preserves the BLE monitoring connection. Stop-all acknowledgment loss does not recursively schedule more stop-all requests. The device's unchanged three-second lease expiration bounds abandoned polling when no stop confirmation arrives.

Actual GATT transport failures or a permanently stuck write can still close the connection. Configuration timeouts do not reconnect, automatically restart queries or persist activation. Reopening queries requires explicit user action after stop confirmation or the lease-expiration waiting period. Token, entry identity and connection generation prevent delayed acknowledgments from re-enabling a removed, changed or timed-out query. The BLE control path processes configuration acknowledgments in arrival order independently of per-frame UI rendering.

App1.14.1 submission retries have at most three attempts with100/200ms backoff. An accepted Android GATT write has a separate6s completion deadline; only after successful write completion does its2s configuration-ACK deadline start. An early ACK can complete the request before the GATT callback without recreating a pending confirmation. The control inbox records arrivals before taking the UI-shared lock and drains in FIFO order before timeout checks. Each channel has its own worker and model.

Asynchronous event8 token0/result1 stopped summaries cannot terminate a locally reopened polling entry, and normal token0 progress cannot enable a new entry before its own configuration ACK. Token0/result4 or5 remains authoritative and stops renewals. Those asynchronous events do not carry a query generation: a delayed old fault can conservatively stop a newly opened query; the App never ignores a fault or automatically restarts. Delayed recovery UI messages also exclude keys currently active again.

Firmware1.14.1 also protects the NimBLE ATT buffer pool for every notification class. A20-byte notification uses a payload mbuf and an additional ATT-header mbuf. After payload allocation, delivery requires at least7 free blocks in that allocation's own pool:1 for the header and6 reserved as management headroom. The total free count across different-size pools is not sufficient because this IDF allocator does not fall back from the selected exhausted pool. Low-watermark delivery releases its payload; notify_custom otherwise consumes it on either success or failure. Raw submission failures yield10ms before checking the priority queue again; healthy delivery has no added frequency cap. ACK/control retry budgets remain finite. This is buffer headroom protection, not a radio-completion count or a guarantee of lossless telemetry.

## UART 数据族（固件 1.23.0）

继续使用版本 1 的固定 20 字节包，BLE 通知和 LAN 帧格式不变。下表所有整数均为小端；事件类型采用十进制，命令写作十六进制。

| 方向 / 类型 | byte 2 | byte 3 | byte 4–15 | byte 16–19 |
|---|---|---|---|---|
| App→设备 `0x18` | 长度 1–12 | 0 | 原始串口字节，尾部零填充 | 非零请求 token |
| 设备→App 19，UART RX | 长度 1–12 | 0 | 原始字节，尾部零填充 | 递增包序号，允许回绕 |
| 设备→App 20，UART TX 结果 | 0 完成 / 2 驱动错误 / 3 取消 | 原请求长度 | 原请求数据 | 原请求 token |
| 设备→App 21，数据状态 | 0 CAN / 1 UART | 0 UART 就绪 / 1 未就绪 | u32 波特率、累计 RX 字节、累计 TX 字节 | u32 累计丢弃字节 |

数据状态约每 500ms 上报，CAN 模式也上报，App 据此切换页面。CAN 模式下 UART 未就绪属于正常状态。计数为运行期累计 uint32，可能回绕，重启清零。

UART 命令仅在已选择 UART 且驱动就绪时接受；长度、保留位、填充、token 均须有效。GATT/LAN 接受命令不是串口输出完成，必须等待类型 20 的同 token 成功结果。App 同时等待原生写完成，避免早到的串口确认与下一次原生写重叠。结果超时不自动重试。UART RX 走有界原始数据队列，TX 结果走优先控制队列；旧 CAN 解码器不解析 UART 数据。CAN/UART 互斥与吞吐限制见 [UART.md](UART.md)。

## 原生 USB 传输

固件 1.24.0 / Desktop 1.2.0 增加 USB直连，使用 32 字节 CRC32 / 会话号封装既有 20 字节报文。具体格式、握手和租约见 [USB.md](USB.md)。CAN 与 UART 均支持。
