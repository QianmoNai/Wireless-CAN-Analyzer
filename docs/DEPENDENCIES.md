# 依赖与协议来源

本仓库不打包工具链或第三方 SDK。外部依赖遵守各自许可证；本项目许可证不替代这些依赖的许可条款。

- [ESP-IDF](https://github.com/espressif/esp-idf)：固件框架，包含 BLE、TWAI、ST7789 等驱动与组件。具体组件许可证见上游仓库。
- Android SDK / JDK：App 构建工具；运行界面使用系统 WebView。
- [Playwright](https://github.com/microsoft/playwright-python)：可选浏览器回归测试依赖。
- 大疆、达妙协议及型号资料来源见 [电机型号说明](MOTOR_CATALOG.md)、[电机协议](MOTOR_PROTOCOLS.md)、[DM-IMU-L1](DM_IMU_L1.md)。
- ST7789 同分辨率面板参考来源见 [本机面板说明](LOCAL_PANEL.md)。

本项目不隶属于大疆、达妙或乐鑫；品牌和型号用于说明兼容设备。
