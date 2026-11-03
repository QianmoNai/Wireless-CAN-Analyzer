# 临时热点测试（Arch/Linux）

由用户在本机终端运行。先关闭占用设备的桌面端/手机 App，让设备保持 BLE 模式。
热点与 wlan0 共用 PHY，在主网络命名空间新建 csap0。wlan0 必须已经连接 2.4 GHz、20 MHz；热点采用它当前的信道，5 GHz 时拒绝启动。

## 只读检查

```bash
cd /home/qianmo/Documents/Project/BLE-CAN-Scope
python scripts/test-hotspot.py preflight
python scripts/test-hotspot.py status
```

preflight 不生成密码、不扫描 BLE、不使用 sudo。通过只说明静态条件满足；专用 DHCP 端口、NM/iwd 共存、路由和主机防火墙仍须在真正 up 后验证。

## 用户决定实际测试时

以下操作会创建热点并修改设备的 Wi-Fi 设置。`sudo` 只用于热点 helper，不用于桌面端或配网脚本；sudo 密码由用户在 sudo 提示处输入。

```bash
sudo /usr/bin/python scripts/test-hotspot.py up
```

先安排 root 的 20 分钟回滚，再生成 24 字符随机临时密码。私有目录 `~/grokbot/secret/` 必须为本人所有、0700；三个固定文件 `canscope-test.password`、`canscope-test.hostapd.conf`、`canscope-test.dnsmasq.conf` 均为本人所有、0600，不覆盖已有文件，不允许放入 Git 仓库。不要 cat 密码或配置。

创建 csap0 前，先在 `/run/NetworkManager/conf.d/canscope-test-unmanaged.conf` 写入 `[keyfile]` 下的 `unmanaged-devices+=interface-name:csap0`（追加，不覆盖原列表），核对有效磁盘配置只有这一项变化，再执行 `nmcli general reload conf`。不使用 SIGHUP、重启服务、连接重载或 DNS 重载。此后限时约 5 秒等待 `NM managed=no state=10（unmanaged）`；必要时仅对此接口设置一次 runtime managed=no。失败时显示 managed/state/reason 的安全字段。创建前后的已有网络快照必须一致。

CONF 重载只重读 NM 主配置，不重载连接配置；本规则只匹配 csap0。仍应确保系统没有其他人留下的无关待加载 NM 配置；真正 reload/up 的共存效果需要用户验证，脚本不承诺物理上绝无瞬断。

热点为 `CANScope-Test`、WPA2-PSK CCMP、PMF=0、20 MHz、10.77.0.1/24，DHCP 10.77.0.20–29（最多四个租约），不下发网关/DNS。不创建 NM shared 连接，不加 NAT、路由规则或防火墙规则，不更改全局 ip_forward；只对新增 csap0 关闭 IPv4 入向转发并禁用 IPv6。这里是主命名空间的专用接口与服务，不是主机本地端口的安全隔离。

隔离在 link up 前施加，并在接口启用后及 hostapd 启动后重新施加：仅写 csap0 的 IPv4/IPv6 forwarding=0、disable_ipv6=1 等接口参数；每次等待 0.2 秒再核对，最多三次，不与其他写入者无限争用。重新施加会显示安全的字段和值变化；失败及运行中监控会显示上次值/当前值/期望值以及只读的全局参考值，再回滚，不修改 all/default forwarding。

普通用户运行配网；密码由脚本从文件读取，经加密且校验 MITM 的 BLE 发给设备，不在终端输入/显示 Wi-Fi 密码：

```bash
desktop/.venv/bin/python scripts/wifi-provision.py \
  --ssid CANScope-Test \
  --password-file /home/qianmo/grokbot/secret/canscope-test.password \
  --subnet 10.77.0.0/24 --reuse-bond
```

按提示输入目标 ID `90da7273722c`，再输入扫描列表中已确认的完整 BLE 地址。只复用 Paired=true 且 Bonded=true 的目标；没有绑定就停止，不重新配对，不要求六位配对码。可选 `--target '90da7273722c@完整BLE地址'` 跳过交互确认；仍须扫描到该地址并在受保护状态里核对 ID，不能把 STA MAC 当作 BLE MAC。

带 `--password-file` 时，在任何 BLE 扫描/连接前先只检查文件存在、归属和 0600 权限，不提前读密码；实际读取前再次校验。文件不存在时会明确提示“密码文件不存在，请先运行 test-hotspot.py up 并确认 status 正常”。热点失败回滚会删除密码，因此只有 status 显示接口存在、NM managed=no/state=10、hostapd/dhcp/monitor/rollback.timer 全部 active 后才运行配网。

另一个终端取证（只输出白名单事件，丢弃原始 daemon 正文和客户端主机名）：

```bash
journalctl -f -u canscope-test-hostapd.service -u canscope-test-dhcp.service
```

正常可见认证、关联、四次握手完成以及 DHCPDISCOVER/OFFER/REQUEST/ACK。日志不用 `-K`、`-d` 或 `-dd`，不打印 PSK。主机已有 DHCP 服务时，本测试服务只绑定 csap0；失败则回滚，不停止其他 dnsmasq。

过滤器也保留认证/关联失败、响应未被 ACK、去认证/解除关联，并仅提取 STA MAC 与数字 status/reason，不转发原始消息。INFO 能看成功事件和部分失败，但没有日志不等于 AP 没收到帧；DEBUG 管理帧日志仍未启用。

新固件的受保护 STATUS 保留原字段，并增加固定 13 项 `diag` 数组，以满足 512 字节（含 NUL）的边界：

```text
[attempt_id, attempt_ms, sta_start_delta_ms, connected_delta_ms, got_ip_delta_ms,
 disconnect_count, bssid_hex, rssi, channel, country, tx_power_qdbm, sta_mac_hex, ble_interval]
```

`attempt_ms` 来自单调时钟，不是墙上时间；三个事件偏移以毫秒计，-1 表示本次 APPLY 内未观察到（STA 也可能此前已启动）。主机还原绝对单调毫秒，并在配网失败和 `--diagnose` 时显示。channel 来自 CONNECTED 事件，未连接时来自当前射频 getter，不能冒充已关联信道；RSSI 优先保留首次断开事件值。BSSID/STA MAC 为 12 位 hex，空串表示未知；country 为两个 ASCII 字符或空串；RSSI/TX 的 -128、channel/BLE interval 的 0 表示未知。TX 是驱动发射功率上限（0.25 dBm 单位），不是实测瞬时功率；BLE interval 是实际协商值（1.25 ms 单位）。断开计数饱和在 65535；attempt_id 从 1 开始，重启重置，溢出跳过 0。APPLY 在主动清理之前冻结整份快照，后续扫描、后台重连和清理不能覆盖。

`stage=4` 仍沿用原协议编号，但含义是 connect 请求已提交、等待接入或 IP，不能证明已关联。新增诊断不修改选网、重试、BLE 调度/包格式或认证策略。

## 清理

```bash
sudo /usr/bin/python scripts/test-hotspot.py down
python scripts/test-hotspot.py status
```

down 可重复运行：停止本次专用服务，关闭并删除所拥有的 csap0，撤销其 NM managed 临时变化，删除三份私有文件、租约和 root 运行目录；比较 wlan0 的 BSSID/信道/IP/活动连接、默认路由及 10.42.0.1 的地址/链路状态与启动前是否一致。发现外部变化只报告，不重连 wlan0。root 侧回滚失败会保留状态，可重跑 down；定时回滚失败还会自动重试。

接口删除前，在严格 unmanaged 规则仍在时清除本次 runtime 覆盖；删除接口后，按已记录的 inode 核对并删除本次 `/run` 规则，再只重载 `conf`。不删除被替换的规则，不覆盖其他 NM 文件。删除规则后重载失败会保留待重载标记，重跑 down/定时回滚仍会重试，即使规则文件已经删除。

2 秒监控发现信道/关联/路由/NM 管理/专用服务变化时，会提前回滚。代码不更改 wlan0 的连接配置；共享空口占用和驱动共存是否稳定需实际 up 验证，离线测试不能证明无线连接绝不中断。

突然断电/重启会消除接口和 transient 单元，但 home 中的三个私有文件可能留下。确认 status 无测试服务和接口后，用户可直接删除这三个固定文件，再重新 preflight/up；不要批量删除 secret 目录中的其他内容。

## 离线验证

```bash
desktop/.venv/bin/python -m py_compile scripts/wifi-provision.py scripts/test-hotspot.py
desktop/.venv/bin/python scripts/wifi-provision.py --help
desktop/.venv/bin/python scripts/wifi-provision.py --dry-run
desktop/.venv/bin/python -m unittest discover -s desktop/tests -q
PATH="$PWD/desktop/.venv/bin:$PATH" bash scripts/test-host.sh
```

热点生命周期测试 mock 所有进程、网卡操作和密码随机源；只写临时合成夹具，不打开热点、不连接蓝牙或串口。
