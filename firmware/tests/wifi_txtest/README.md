# 独立 ESP32-S3 Wi-Fi TX 测试

临时 app：只运行开放 SoftAP `CANScope-TX-<AP MAC末四位HEX>`，信道 1、HT20，最多四个客户端，默认本地 DHCP（192.168.4.1），没有上游、转发或 NAT。不编译正常主固件、BLE 或 CAN。

## 构建与兼容性

从仓库根目录运行：

```bash
source /home/qianmo/.espressif/tools/activate_idf_v6.0.2.sh
bash firmware/tests/wifi_txtest/build.sh
```

仅接受 `build`/`app`；输出到仓库外 `~/.cache/can-scope-wifi-txtest/<路径hash>/esp32s3/wifi_txtest.bin`，配置也在外部。使用相同 ESP-IDF 6.0.2、DIO/4MB/40MHz，直接引用 `firmware/partitions.csv`（NVS 0x9000，PHY 0xf000，factory app 0x10000/2MiB）。构建也生成 bootloader/分区表用于离线比较，**不要烧录它们，不使用构建末尾的整机 flash 命令或 `@flash_args`**。

实际复制镜像路径、大小、分区表比较结果见 `~/grokbot/wifi-txtest-result.md`。本测试没有修改任何正常固件源码/默认配置。

## 仅烧 app；只保留一个串口读取器

先退出桌面端、串口 monitor/miniterm 和其他串口客户端。在下面命令的最后填入报告中的镜像完整路径：

```bash
/home/qianmo/.espressif/tools/python/v6.0.2/venv/bin/python -m esptool \
  --chip esp32s3 \
  --port /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_90:DA:72:73:72:2C-if00 \
  --baud 460800 --before default-reset --after hard-reset \
  write-flash --flash-mode dio --flash-size 4MB --flash-freq 40m \
  0x10000 /完整路径/wifi-txtest-短hash-app.bin
```

普通用户只读接收 USB-Serial-JTAG 控制台（115200 是终端参数；USB 控制台不是独立 UART 引脚）：

```bash
desktop/.venv/bin/python -u - <<'PY'
import serial, sys
port = serial.Serial(port=None, baudrate=115200, timeout=1, exclusive=True)
port.dtr = False
port.rts = False
port.port = '/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_90:DA:72:73:72:2C-if00'
try:
    port.open()
    while True:
        sys.stdout.buffer.write(port.read(4096))
        sys.stdout.buffer.flush()
except KeyboardInterrupt:
    pass
finally:
    port.close()
PY
```

用 Ctrl+C 退出，下一次烧录前必须退出。上面只调用串口 read，不调用串口 write，也不把终端输入转发到设备；exclusive 尽量阻止其他串口使用者。显式 DTR/RTS 为 0，但打开串口仍可能有驱动线状态变化，不能并发运行其他读取器。周期日志每 5 秒一次，不必为了看首行再次复位。本机 miniterm 没有 `--rx-only`，因此这里使用这个明确只接收的命令。

启动和周期日志包含 AP/STA MAC、信道、TX功率上限读回、国家码、当前客户端数；加入/离开事件包含客户端 MAC、AID、离开 reason。探测请求日志最多每秒一条（MAC、RSSI），不输出帧内容或扫描到的其他 SSID。请求 TX=84，IDF 通常映射为 80，即 20 dBm；这是上限，不是测量到的瞬时发射功率。

## 电脑扫描 / 手机可选连接

普通用户扫描（会发送扫描请求，不连接、不改配置；不要 sudo）：

```bash
/usr/bin/python scripts/wifi-txtest-scan.py --interface wlan0 --rounds 3 --interval 5
```

依次尝试 `iw ... scan`、`nmcli ... wifi list --rescan yes`、`iwctl ... scan/get-networks`。这里只在用户运行时调用，不需要启动/停止电脑热点，不编辑 NM/iwd 配置或 profile。扫描仍占用射频，可能短暂影响吞吐。NM 使用 iwd backend 时仍可通过 nmcli 请求扫描。

仅显示 `CANScope-TX-*`：BSSID、信道、信号（iw 为 dBm，NM 为百分比）。iwd 表格只有信号格数时，尝试读取 iw 扫描缓存补充 BSSID/信道；没有权限则明确打印“未知”，不编造。任一轮发现返回 0，全部未发现或没有扫描权限返回 1（需结合提示区分），root/参数错误返回 2。

手机可选：打开 Wi-Fi 列表查找该开放网络，手动加入；没有密码、不需要输入或分享任何密钥。出现“无互联网”时选择保持连接。手机可能自动切换回其他网络，因此同时观察 AP 日志的 join/leave 和 station count；测试后手动断开此开放网络。

## NVS 和 PHY

- app 不调用 `nvs_flash_init()`，因此也没有 NO_FREE_PAGES/NEW_VERSION 后的擦除恢复路径；不调用 NVS erase/set/commit，不访问 BLE bond store。
- `CONFIG_ESP_WIFI_NVS_ENABLED=n`，初始化的 `nvs_enable=0`，并显式设置 `WIFI_STORAGE_RAM`。
- `CONFIG_ESP_PHY_CALIBRATION_AND_DATA_STORAGE=n`：在 IDF 6.0.2 中这会跳过 PHY 校准的 NVS load/store，并在本次启动做完整校准；`CONFIG_ESP_PHY_INIT_DATA_IN_PARTITION=n` 使用 app 内置 PHY init，不写 phy_init 分区。源码有配置编译守卫防止误启用持久化。
- 这会改变测试 app 的校准路径，**不是证明原 NVS 校准数据损坏**。不读取/打印原校准 blob，不把新校准结果保存回去。恢复原 app 后，原校准数据/设置/bond 仍在；RAM/运行时连接状态会随复位消失。

## 结果解释与恢复

电脑/手机能看到实时信标，说明至少存在有效 Wi-Fi TX；手机成功加入并在串口出现 join，则支持双向管理帧与关联正常。不能据此证明原 STA 认证、加密或 DHCP 路径已经修好。

如果串口确认 AP 正常启动、读回信道1/功率正常，而多个有扫描权限的独立客户端反复都看不到，硬件发射路径、天线/接头、供电/RF问题优先级上升；仍需排除客户端扫描限制、距离、测试 app/PHY/驱动问题，不能仅凭一次“未发现”判硬件损坏。可先近距离、检查供电和天线，禁止清空 NVS 来试错。

退出所有串口读取器后，用前述完全相同的 esptool 参数，只把镜像改为以下任一份，并仍只写 `0x10000`：

- `/home/qianmo/grokbot/wifi-bleoff-26ed9c1-app.bin`（优先，恢复当前正常固件）
- `/home/qianmo/grokbot/ble-known-good.bin`（已知好回退）

恢复后设置和 BLE bond 原样保留；本次测试没有任何 PC 连接/profile 需要回滚。若用户从手机连过开放 AP，手动断开即可。

离线解析测试：`/usr/bin/python -m unittest discover -s firmware/tests/wifi_txtest -p test_scan.py -q`。
