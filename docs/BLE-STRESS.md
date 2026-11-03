# BLE transport stress and test diagnostics

Run as the desktop user with the independent UART fixture wired as in
[UART.md](UART.md). Close other owners of both serial ports first.

```bash
desktop/.venv/bin/python scripts/test-ble-stress.py \
  --output ~/grokbot/ble-run.json
```

Defaults: 115200/921600, 1000/2000/4000/8000 B/s and maximum fixture supply,
10 seconds each, RX-only and duplex separately. `--duration 60 --rates 4000
--directions duplex --bauds 921600` selects one longer confirmation point.
Paced supply uses the same seeded 120-byte blocks across candidates; maximum
supply uses 4096-byte blocks. Host write completions are compared with device
RX, never assumed to prove UART physical delivery. Throughput includes the
drained tail divided by the supply duration; actual write span is retained.

All GATT operations share one lock spanning write **and token reply**, including
read-only diagnostic reads. TX segments are recorded before submission; unknown
results stop the run, remain unknown even after a late reply, and are never
replayed. Setup establishes BLE→UART and UART→BLE markers before counting.
ROM prefixes are saved verbatim before that boundary. Measured data is never
trimmed to make a comparison pass. Raw input, RX, peer and submitted TX streams
are saved next to the JSON in a `.streams` directory with hashes in the report.

The default tests BLE transport. `--channel-rx` separately measures the actual
Channel receive/flush batches with immediate harness delivery receipts; it
records bytes/hash, drops, queue high water and age alongside the raw transport.
TX/query ownership stays the same. This does not test visible Qt or slow-page
delivery (a separate end-to-end acceptance stage). BLE has no USB HELLO/PING; it measures
`0x19` query/type22 RTT and physical-UART type20 completion RTT. It requests no
pairing, CAN transmissions, PHY changes, or global adapter changes. Two explicit
close/reconnect cycles follow successful matrices. Old callback epochs cannot
complete new waiters. Linux scanning retains the project's unfiltered BlueZ
scan and application UUID selection.

Every normal, exception, cancellation or SIGTERM exit stops/joins supply, closes
the UART fixture (restoring its host baud), closes BLE, then independently opens
native USB for QUERY → explicit SET USB/UART/115200 → wait → separate QUERY.
An unknown SET is followed by QUERY with no replay. Cleanup errors and partial
test data survive in the report. SIGKILL/power loss cannot run Python cleanup;
recover with the two `serial-settings.py` commands in the approved bench plan.

## Test-only read characteristic

`CONFIG_BLE_TEST_DIAGNOSTICS` defaults off. Enabling it adds read-only UUID
`c0de0005-1212-efde-1523-785feabcd123`; the existing v1 characteristics and
20-byte messages are unchanged. Instrumented queue entries carry timestamps;
normal builds keep 20-byte queue entries. No diagnostic logs enter UART/CUSB.
Compare diagnostics-on/off builds before interpreting small performance changes.
Use `--require-diagnostics` for instrumented runs to fail before supply when the
new characteristic is hidden by a stale GATT cache.

The bounded value is version 1, 80 little-endian uint32 words (320 bytes).
`scripts/ble_stress.py` defines the field order. The header includes actual
MTU, interval (1.25 ms units), latency, timeout (10 ms units), parameter change
status/count, disconnect reason/count, selected-pool minimum after payload
allocation, heap free/minimum, local notify attempts/events, last notify return
code, last nonzero error, bounded ENOMEM/ENOTCONN/EAGAIN/other error counts, and
configured pool counts. PHY comes from the controller's read/update
events; zero means unavailable. Local notify events prove neither controller
completion nor remote application receipt.

Raw/control/ACK counters distinguish enqueue rejection, high water, maximum
observed queue age, allocation/reserve/submit failures, accepted submissions,
and terminal drop bytes by cause. Failed **attempts** can exceed terminal
discards because bounded control/ACK retries remain. UART type21 drop increments
must equal raw full bytes + terminal allocation/reserve/submit + reset + other
bytes after drain. Only UART type19 contributes payload bytes. Queue full is
counted by this layer even when the upstream UART worker owns the actual drop
increment; no second UART increment is introduced.

Counters are lifetime modulo 2³²; fields are individually atomic samples, not a
single global transaction. Read at low frequency before/after drained cases.
High water and maximum age are lifetime maxima, not per-case deltas. Initial
pool minimum `0xffffffff` means no observed allocation yet. Parameter requests
and actual negotiated changes have separate fields. Diagnostics can change the
GATT database; use fresh discovery for this device rather than deleting bonds
or changing other Bluetooth devices.

Only if fresh discovery still exposes the old database, first stop/join the
fixture, close BLE and restore/read back USB/UART/115200. Inspect
`org.bluez.Device1` at the **selected analyzer's** object path and require
`Connected=false` and `Paired=false`. Call `org.bluez.Adapter1.RemoveDevice`
with that one object path, then scan/discover afresh. Record the target and
check that other Device1 object paths are unchanged. This clears that unpaired
device's cache only; do not pair, delete another device, restart hci0, or remove
system-wide BlueZ storage. Stop rather than applying these steps to a paired
analyzer without an explicit device-specific plan.
