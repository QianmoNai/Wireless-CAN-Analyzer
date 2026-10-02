# 首次源码发布验证

发布组合：ESP32-S3 固件 1.19.0、Android App 1.18.1。

在整理后的本仓库目录完成：

- ESP-IDF v6.0.2 完整构建 ESP32-S3 固件，通过分区尺寸检查。
- Android SDK API 35 / Build Tools 35.0.0 构建 APK，v2/v3 签名校验通过。
- `bash scripts/test-host.sh`：C、Java、JavaScript 测试通过。
- Playwright：`test_home_sort.py`、`test_imu_ui.py`、`test_monitor.py` 通过。
- Git 暂存文件检查：不含构建缓存、签名密钥、凭证和本机绝对目录；Markdown 相对文件链接有效。

源码重新组织不改变固件和 App 业务功能。此前原工程的 1.19.0 固件已在 ESP32-S3 启动并观察到实时 CAN 接收；本次整理仓库没有再次刷写设备，不声称完成实体屏幕、全部电机型号或所有 Android 手机测试。
