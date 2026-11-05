#include "can_bus.h"
#include "lcd_display.h"
#include "motor_sim.h"
#include "imu_sim.h"

/* Debugger watch variables. No UART or board LED dependency. */
volatile uint32_t can_tx_queued, can_tx_busy, can_rx_count, can_errors;
volatile uint32_t can_bus_off, can_rx_lost, fatal_error;
volatile CAN_Frame can_last_rx;
ImuSim imu_sim;
volatile uint32_t imu_tx_queued;
MotorSim motor_sim; /* Visible in debugger; foreground owns all updates. */

#if CAN_DUAL_BENCH
static MotorSim motor_sim2;
static ImuSim imu_sim2;
/* Independent counters and periodic gate for the second physical CAN bus. */
volatile uint32_t can2_bus_off, can2_rx_lost, can2_errors, can2_rx_count, can2_tx_busy, can2_tx_queued;
volatile uint32_t can1_periodic = 1, can2_periodic = 1;
/* SWD writes request first and trigger last; foreground owns acknowledgments. */
volatile struct {
  CAN_Frame request;
  uint32_t trigger, done, result, queued;
  CAN_Frame last_rx;
  uint32_t received;
} can_bench[2];
static void bench_send(unsigned bus)
{
  uint32_t trigger=can_bench[bus].trigger;
  if (!trigger || trigger==can_bench[bus].done) return;
  CAN_Frame frame=can_bench[bus].request;
  HAL_StatusTypeDef result=bus?CAN_Bus2_Send(&frame):CAN_Bus_Send(&frame);
  can_bench[bus].result=result;
  if (result==HAL_BUSY) return;
  if (result==HAL_OK) ++can_bench[bus].queued;
  can_bench[bus].done=trigger;
}
static void second_bus_process(void)
{
  static bool prefer_imu;
  CAN_Frame frame;
  FDCAN_ProtocolStatusTypeDef status;
  if (HAL_FDCAN_GetProtocolStatus(&hfdcan2, &status) == HAL_OK) can2_bus_off = status.BusOff;
  if (__HAL_FDCAN_GET_FLAG(&hfdcan2, FDCAN_FLAG_RX_FIFO0_MESSAGE_LOST)) {
    ++can2_rx_lost; __HAL_FDCAN_CLEAR_FLAG(&hfdcan2, FDCAN_FLAG_RX_FIFO0_MESSAGE_LOST);
  }
  for (unsigned i=0; i<16; ++i) {
    HAL_StatusTypeDef result=CAN_Bus2_Receive(&frame);
    if (result==HAL_BUSY) break;
    if (result!=HAL_OK) { ++can2_errors; break; }
    ++can2_rx_count;
    can_bench[1].last_rx=frame;++can_bench[1].received;
    if (frame.remote) continue;
    if (SIM_IMU_ENABLED && (frame.id==SIM_IMU_CAN_ID || frame.id==0x6FF))
      (void)ImuSim_OnFrame(&imu_sim2,&frame,HAL_GetTick());
    else (void)MotorSim_OnFrame(&motor_sim2,&frame,HAL_GetTick());
  }
  if (!can2_periodic) return;
  MotorSim_Update(&motor_sim2,HAL_GetTick()); ImuSim_Update(&imu_sim2,HAL_GetTick());
  if (can2_bus_off) return;
  for (unsigned i=0;i<8;++i) {
    bool imu=false;
    if (prefer_imu && ImuSim_PeekFeedback(&imu_sim2,&frame)) imu=true;
    else if (!MotorSim_PeekFeedback(&motor_sim2,&frame)) {
      if (!ImuSim_PeekFeedback(&imu_sim2,&frame)) break;
      imu=true;
    }
    HAL_StatusTypeDef result=CAN_Bus2_Send(&frame);
    if(result!=HAL_OK) { if(result==HAL_BUSY) ++can2_tx_busy; else ++can2_errors; break; }
    ++can2_tx_queued;
    if(imu) ImuSim_CommitFeedback(&imu_sim2); else MotorSim_CommitFeedback(&motor_sim2);
    prefer_imu=!imu;
  }
}
#endif

static void fail(uint32_t code)
{
  fatal_error = code;
  __disable_irq();
  while (1) {}
}

static void clock_init(void)
{
  RCC_OscInitTypeDef osc = {0};
  RCC_ClkInitTypeDef clk = {0};
  if (HAL_PWREx_ConfigSupply(PWR_LDO_SUPPLY) != HAL_OK) fail(1);
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE3);
  while (!__HAL_PWR_GET_FLAG(PWR_FLAG_VOSRDY)) {}
  /* CPU stays on internal 64 MHz HSI; CAN uses external 25 MHz crystal. */
  osc.OscillatorType = RCC_OSCILLATORTYPE_HSI | RCC_OSCILLATORTYPE_HSE;
  osc.HSIState = RCC_HSI_DIV1;
  osc.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  osc.HSEState = RCC_HSE_ON;
  osc.PLL.PLLState = RCC_PLL_NONE;
  if (HAL_RCC_OscConfig(&osc) != HAL_OK) fail(2);
  clk.ClockType = RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_PCLK1 |
                 RCC_CLOCKTYPE_PCLK2 | RCC_CLOCKTYPE_D1PCLK1 | RCC_CLOCKTYPE_D3PCLK1;
  clk.SYSCLKSource = RCC_SYSCLKSOURCE_HSI;
  clk.SYSCLKDivider = RCC_SYSCLK_DIV1;
  clk.AHBCLKDivider = RCC_HCLK_DIV1;
  clk.APB1CLKDivider = RCC_APB1_DIV1;
  clk.APB2CLKDivider = RCC_APB2_DIV1;
  clk.APB3CLKDivider = RCC_APB3_DIV1;
  clk.APB4CLKDivider = RCC_APB4_DIV1;
  if (HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_1) != HAL_OK) fail(3);
}

void SysTick_Handler(void) { HAL_IncTick(); }

int main(void)
{
  CAN_Frame tx;
  CAN_Frame rx;

  HAL_Init();
  clock_init();
  (void)LCD_Init(); /* Display failure must not prevent CAN operation. */
  if (CAN_Bus_Init() != HAL_OK) fail(4);
  MotorSim_Init(&motor_sim, HAL_GetTick());
  ImuSim_Init(&imu_sim, HAL_GetTick());
#if CAN_DUAL_BENCH
  MotorSim_Init(&motor_sim2, HAL_GetTick());
  ImuSim_Init(&imu_sim2, HAL_GetTick());
#endif
  bool prefer_imu = false;
  while (1) {
    FDCAN_ProtocolStatusTypeDef status;
    if (HAL_FDCAN_GetProtocolStatus(&hfdcan1, &status) == HAL_OK)
      can_bus_off = status.BusOff;
    if (__HAL_FDCAN_GET_FLAG(&hfdcan1, FDCAN_FLAG_RX_FIFO0_MESSAGE_LOST)) {
      ++can_rx_lost;
      __HAL_FDCAN_CLEAR_FLAG(&hfdcan1, FDCAN_FLAG_RX_FIFO0_MESSAGE_LOST);
    }
    /* Bounded work: keep the periodic sender responsive under bus load. */
    for (uint32_t i = 0; i < 16; ++i) {
      HAL_StatusTypeDef result = CAN_Bus_Receive(&rx);
      if (result == HAL_BUSY) break;
      if (result != HAL_OK) { ++can_errors; break; }
#if CAN_DUAL_BENCH
      can_bench[0].last_rx=rx;++can_bench[0].received;
      if (rx.remote) continue;
#endif
      can_last_rx = rx;
      ++can_rx_count;
      if (SIM_IMU_ENABLED && (rx.id == SIM_IMU_CAN_ID || rx.id == 0x6FF))
        (void)ImuSim_OnFrame(&imu_sim, &rx, HAL_GetTick());
      else
        (void)MotorSim_OnFrame(&motor_sim, &rx, HAL_GetTick());
    }
    MotorSim_Update(&motor_sim, HAL_GetTick());
    ImuSim_Update(&imu_sim, HAL_GetTick());
    if (!can_bus_off
#if CAN_DUAL_BENCH
        && can1_periodic
#endif
    ) {
      for (unsigned i = 0; i < 8; ++i) {
        bool from_imu = false;
        if (prefer_imu && ImuSim_PeekFeedback(&imu_sim, &tx)) from_imu = true;
        else if (!MotorSim_PeekFeedback(&motor_sim, &tx)) {
          if (!ImuSim_PeekFeedback(&imu_sim, &tx)) break;
          from_imu = true;
        }
        HAL_StatusTypeDef result = CAN_Bus_Send(&tx);
        if (result == HAL_OK) {
          ++can_tx_queued;
          if (from_imu) { ImuSim_CommitFeedback(&imu_sim); ++imu_tx_queued; }
          else MotorSim_CommitFeedback(&motor_sim);
          prefer_imu = !from_imu;
        } else {
          if (result == HAL_BUSY) ++can_tx_busy;
          else ++can_errors;
          break; /* Keep unsent feedback pending, never block waiting for ACK. */
        }
      }
    }
#if CAN_DUAL_BENCH
    bench_send(0);bench_send(1);
    second_bus_process();
#endif
    LCD_Status display = {
      .tx = can_tx_queued, .busy = can_tx_busy, .rx = can_rx_count,
      .errors = can_errors, .lost = can_rx_lost, .bus_off = can_bus_off,
      .tx_full = (HAL_FDCAN_GetTxFifoFreeLevel(&hfdcan1) == 0),
      .last_rx = can_last_rx,
      .dji_rpm = (int32_t)motor_sim.dji[0].rpm,
      .dji_current = motor_sim.dji[0].current,
      .dji_commands = motor_sim.dji[0].commands,
      .dm_position_mrad = (int32_t)(motor_sim.dm.position * 1000),
      .dm_velocity_mrad = (int32_t)(motor_sim.dm.velocity * 1000),
      .dm_torque_mnm = (int32_t)(motor_sim.dm.torque * 1000),
      .dm_status = motor_sim.dm.status, .dm_commands = motor_sim.dm.commands,
      .imu_tx = imu_tx_queued, .imu_active = imu_sim.active,
      .imu_period = imu_sim.period_ms, .imu_dropped = imu_sim.dropped,
      .reply_dropped = motor_sim.reply_dropped, .timeouts = motor_sim.timeouts
    };
    LCD_Process(&display);
  }
}
