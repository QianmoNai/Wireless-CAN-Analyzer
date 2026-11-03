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

热点为 `CANScope-Test`、WPA2-PSK CCMP、PMF=0、20 MHz、10.77.0.1/24，DHCP 10.77.0.20–29（最多四个租约），不下发网关/DNS。不创建 NM shared 连接，不加 NAT、路由规则或防火墙规则，不更改全局 ip_forward；只对新增 csap0 关闭 IPv4 入向转发并禁用 IPv6。这里是主命名空间的专用接口与服务，不是主机本地端口的安全隔离。

普通用户运行配网；密码由脚本从文件读取，经加密且校验 MITM 的 BLE 发给设备，不在终端输入/显示 Wi-Fi 密码：

```bash
desktop/.venv/bin/python scripts/wifi-provision.py \
  --ssid CANScope-Test \
  --password-file /home/qianmo/grokbot/secret/canscope-test.password \
  --subnet 10.77.0.0/24 --reuse-bond
```

按提示输入目标 ID `90da7273722c`，再输入扫描列表中已确认的完整 BLE 地址。只复用 Paired=true 且 Bonded=true 的目标；没有绑定就停止，不重新配对，不要求六位配对码。可选 `--target '90da7273722c@完整BLE地址'` 跳过交互确认；仍须扫描到该地址并在受保护状态里核对 ID，不能把 STA MAC 当作 BLE MAC。

另一个终端取证（只输出白名单事件，丢弃原始 daemon 正文和客户端主机名）：

```bash
journalctl -f -u canscope-test-hostapd.service -u canscope-test-dhcp.service
```

正常可见认证、关联、四次握手完成以及 DHCPDISCOVER/OFFER/REQUEST/ACK。日志不用 `-K`、`-d` 或 `-dd`，不打印 PSK。主机已有 DHCP 服务时，本测试服务只绑定 csap0；失败则回滚，不停止其他 dnsmasq。

## 清理

```bash
sudo /usr/bin/python scripts/test-hotspot.py down
python scripts/test-hotspot.py status
```

down 可重复运行：停止本次专用服务，关闭并删除所拥有的 csap0，撤销其 NM managed 临时变化，删除三份私有文件、租约和 root 运行目录；比较 wlan0 的 BSSID/信道/IP/活动连接、默认路由及 10.42.0.1 的地址/链路状态与启动前是否一致。发现外部变化只报告，不重连 wlan0。root 侧回滚失败会保留状态，可重跑 down；定时回滚失败还会自动重试。

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
