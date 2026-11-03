# 终端 Wi-Fi 配网

脚本为 `scripts/wifi-provision.py`，仅由用户本人用普通桌面用户运行。**本脚本会配对所选设备并修改设备的 Wi-Fi 设置。** 固定目标 STA ID 为 `90da7273722c`，写入桌面端通道 1 的 `connection-1.json`。

## 运行

关闭桌面端、手机 App 和其他设备连接；在 ESP32 屏幕选择蓝牙模式。使用可用的系统蓝牙配对代理（例如桌面蓝牙管理器），电脑保持现有网络连接。路由器须提供可供 ESP32 使用的 2.4 GHz 网络，设备地址须落在 `192.168.1.0/24`，电脑须能访问该网段。

在仓库根目录运行：

```bash
desktop/.venv/bin/python scripts/wifi-provision.py --help
desktop/.venv/bin/python scripts/wifi-provision.py --dry-run
desktop/.venv/bin/python scripts/wifi-provision.py
```

前两条命令只做无凭据离线检查。真实运行时会看到：

1. 上述配对及修改设置的提示，要求输入 `90da7273722c` 确认。
2. 扫描得到的 CAN Scope BLE 地址列表，要求输入已核对属于目标设备的完整地址。BLE 地址可能不同于 STA ID；不能确认时回车退出。
3. 一次系统配对/认证提示；在系统弹窗中输入目标 ESP32 屏幕显示的六位码。已绑定设备可能不会再次弹窗，脚本仍只调用一次 `.pair()`。
4. 通过加密且要求 MITM 的配网特征读取 ID、证书指纹与令牌；ID 不符立即停止，此时尚未读取 Wi-Fi 密码。
5. `SSID` 使用 `input`；`Wi-Fi 密码` 使用 `getpass`，不回显。没有安全隐藏输入条件时停止，不回退到明文输入。开放网络可留空密码。
6. 配网、一次 Wi-Fi 模式设置、私有缓存保存、UDP 发现与设备 IP、一次 TLS 连接及一次只读 QUERY 的状态。

脚本复用桌面端 `BleTransport.provision`、协议编码、`Channel` 的设置/信任处理和 `LanTransport`；后续配网复用同一连接上的认证。仅向用户确认的 BLE 地址连接/配对，不删除配对、不重置适配器、不修改电脑网卡或路由、不打开串口。广播使用 `192.168.1.255:32323`，最长约 30 秒，只接受目标 ID 且来源在指定子网内的发现结果。

## 成功标准

必须出现：

```text
成功：TLS 认证通过；QUERY result=0 network=1 data=... baud=...；IP=192.168.1.x
```

这表示已通过经 BLE 获取的证书 SHA-256 pin 校验、令牌认证及 Wi-Fi 通道上的一次设置 QUERY。`data` 和 `baud` 是设备实际回读值；脚本只切换通信模式，保留这两个设置。仅看到模式设置返回或 IP 不足以算成功。TLS 不重连，QUERY 不重试，也不发送 CAN/UART 业务数据。

信任缓存位于桌面端相同的 Qt `AppLocalDataLocation`；通常为 `~/.local/share/Qianmo/CAN Scope Desktop/connection-1.json`（遵循系统 XDG 设置）。通过认证的 `id/fp/token/ip` 按桌面端方式保存，保留其他字段，目录 `0700`、原子替换后的文件 `0600`。已有缓存损坏时在配对前停止。不要粘贴或打印缓存内容。

## 故障排查

- 扫不到：检查设备屏幕是否为蓝牙模式、是否被手机/桌面端占用、系统蓝牙是否可用。脚本不重启 hci0；不可随意删除其他设备的绑定。
- 配对失败/超时：检查系统蓝牙配对代理与六位码，核对实体设备。脚本不自动重试配对。解决后由用户决定是否重新运行。
- ID 不符：取消后重新核对 BLE 地址；脚本没有发送 Wi-Fi 凭据，也没有覆盖信任缓存。
- 密码格式错误：支持 8–63 个可打印 ASCII 字符、64 位十六进制密钥或开放网络空密码。不支持的非 ASCII 输入会以固定错误提示拒绝，避免编码异常泄露输入。
- 配网失败：核对 SSID、2.4 GHz、密码、信号和路由器 DHCP。固件错误码含义为 2=认证失败、3=未找到 AP、4=接入失败、5=保存失败；脚本不打印原始异常或网络状态 JSON。设备成功完成 APPLY 才算已保存；中止或超时的结果可能未知，先检查屏幕/QUERY，不要盲目重发。
- Wi-Fi 模式回执未知：切换可能先重置通知路径；脚本不重发 SET，继续通过发现和最终 TLS QUERY 判断。只有最终成功行能证明模式及通道都可用。
- 发现超时：检查设备屏幕 IP 是否在 `192.168.1.0/24`，排除访客网络/客户端隔离、UDP 32323 被过滤。已有电脑网络保持不变；脚本不会改路由。广播失败也可能仍可从桌面端手动输入设备屏幕 IP。
- TLS/QUERY 失败：检查 TCP 32323、设备 Wi-Fi 模式和其他占用会话。证书 pin 不匹配时禁止绕过校验；重新核对实体设备后由用户决定是否重新进行 BLE 认证。缓存可能已安全保存，不能因此当作 TLS 已成功。

密码只通过交互读取，不接受命令行参数或环境变量，不写文件或日志、不格式化进异常信息或 `repr`。脚本关闭后端日志、隐藏第三方异常内容、禁止本进程 core dump；发送缓冲区在用完及异常退出时覆盖为零，尽早释放密码引用。CPython、getpass 和 BLE 库使用不可变字符串/内部副本，因此不能承诺所有内存副本的物理擦除。不要启用外部调试器、终端录制或自行记录 GATT 内容。

## 恢复 USB直连 + UART + 115200

先退出脚本、关闭桌面端和所有串口监视器。在仓库根目录**顺序**执行，每条 Python 命令是独立进程，不要并发打开串口：

```bash
desktop/.venv/bin/python scripts/serial-settings.py --port /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_90:DA:72:73:72:2C-if00
desktop/.venv/bin/python scripts/serial-settings.py --port /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_90:DA:72:73:72:2C-if00 --mode usb --data uart --baud 115200
sleep 0.5
desktop/.venv/bin/python scripts/serial-settings.py --port /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_90:DA:72:73:72:2C-if00
```

最后应为 `result=0, network_mode=4, data_mode=1, baudrate=115200`。只传 `--port` 的命令为独立只读 QUERY。SET 结果未知时先 QUERY，不直接重复 SET。该工具输出中的 `token` 是串口请求关联号，不是 TLS 认证令牌。恢复只修改通信模式/数据类型/波特率，不清除已保存的 Wi-Fi 凭据、BLE 绑定或桌面端 TLS 信任缓存。
