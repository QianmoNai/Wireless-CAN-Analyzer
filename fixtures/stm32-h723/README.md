# STM32H723 双路 CAN 台架

本目录保存实物测试使用的模拟器源码，不包含 STM32CubeH7 库或任何构建产物。原始开发目录为 `../STM32_project/can_h723`；电机和 IMU 模型、原单路配置见 [SIMULATOR.md](SIMULATOR.md)。这是模拟设备，不代表真实电机已验证。

| 控制器 | RX | TX | 配置 |
| --- | --- | --- | --- |
| FDCAN1 | PD0 | PD1 | AF9、经典 CAN、1 Mbit/s |
| FDCAN2 | PB12 | PB13 | AF9、经典 CAN、1 Mbit/s；仅双路构建启用 |

每路分别通过 CAN 收发器连接一台分析仪，两条总线互不并接。板卡采用 25 MHz HSE；第二控制器的 Message RAM 从第 1024 个 32 位字开始，运行时检查两个控制器区域不重叠。两路电机/IMU 状态和计数独立，STM32 不在两路间转发。

```bash
cmake -S fixtures/stm32-h723 -B /tmp/can-h723-dual \
  -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi.cmake \
  -DSTM32_CUBE_H7="$HOME/STM32Cube/Repository/STM32Cube_FW_H7_V1.13.0" \
  -DCAN_DUAL_BENCH=ON -DSIM_PROFILE=SIM_ALL_FEEDBACK
cmake --build /tmp/can-h723-dual -j4
bash fixtures/stm32-h723/tests/run.sh
```

默认 `CAN_DUAL_BENCH=OFF`，保留原单路配置。双路构建额外导出 `can1_periodic`、`can2_periodic` 和 `can_bench[2]`，通过 SWD 暂停周期上报、逐帧注入并读取另一总线实际收到的完整帧。仅在明确的模拟台架运行这些测试。测试脚本先核对正在运行的 Flash 与所给 ELF/BIN 一致，不依赖固定 RAM 地址。

在启动双机测试前，通过对应 ELF 符号把两个周期开关置零；测试结束先恢复两台分析仪 USB＋CAN，再恢复周期开关。双机转发期间同时运行两套相同反馈 ID 会混合测试数据。

```bash
PYTHONPATH=desktop desktop/.venv/bin/python scripts/dual-can-pair.py \
  --a /dev/ttyACM0 --b /dev/ttyACM1 --keep-dual --output /tmp/pair.json
PYTHONPATH=desktop desktop/.venv/bin/python scripts/test-dual-can-hardware.py \
  --fixture stm32h723-dual --elf /tmp/can-h723-dual/can_h723.elf \
  --a /dev/ttyACM0 --b /dev/ttyACM1 --output /tmp/dual-can.json
```

OpenOCD 需已使用 J-Link/SWD 连接对应 STM32，并在本机 TCP 6666 提供 Tcl 接口。这里的计时包含 SWD 观察开销，不作为无线延迟测量。ST 启动文件保留原版权头，适用 [ST 许可证](LICENSE-ST.txt)；其他文件遵循仓库许可证。
