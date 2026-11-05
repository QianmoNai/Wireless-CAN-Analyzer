#include "can_bus.h"
#include "motor_sim_config.h"
#include "imu_sim.h"
#include <string.h>

FDCAN_HandleTypeDef hfdcan1;
#if CAN_DUAL_BENCH
FDCAN_HandleTypeDef hfdcan2;
#endif
static const uint32_t dlc[9] = {
  FDCAN_DLC_BYTES_0, FDCAN_DLC_BYTES_1, FDCAN_DLC_BYTES_2,
  FDCAN_DLC_BYTES_3, FDCAN_DLC_BYTES_4, FDCAN_DLC_BYTES_5,
  FDCAN_DLC_BYTES_6, FDCAN_DLC_BYTES_7, FDCAN_DLC_BYTES_8
};

static HAL_StatusTypeDef controller_init(FDCAN_HandleTypeDef *h, FDCAN_GlobalTypeDef *instance, uint32_t offset);

HAL_StatusTypeDef CAN_Bus_Init(void)
{
  RCC_PeriphCLKInitTypeDef clock = {0};
  GPIO_InitTypeDef gpio = {0};
  clock.PeriphClockSelection = RCC_PERIPHCLK_FDCAN;
  clock.FdcanClockSelection = RCC_FDCANCLKSOURCE_HSE;
  if (HAL_RCCEx_PeriphCLKConfig(&clock) != HAL_OK) return HAL_ERROR;
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_FDCAN_CLK_ENABLE();
  gpio.Pin = GPIO_PIN_0 | GPIO_PIN_1;
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  gpio.Alternate = GPIO_AF9_FDCAN1;
  HAL_GPIO_Init(GPIOD, &gpio);

  if (controller_init(&hfdcan1, FDCAN1, 0) != HAL_OK) return HAL_ERROR;
#if CAN_DUAL_BENCH
  __HAL_RCC_GPIOB_CLK_ENABLE();
  gpio.Pin = GPIO_PIN_12 | GPIO_PIN_13;
  gpio.Alternate = GPIO_AF9_FDCAN2;
  HAL_GPIO_Init(GPIOB, &gpio);
  /* Each controller uses <= 615 words. Keep independent nonoverlapping banks. */
  if (hfdcan1.msgRam.EndAddress > SRAMCAN_BASE + 1024U * 4U) return HAL_ERROR;
  return controller_init(&hfdcan2, FDCAN2, 1024);
#else
  return HAL_OK;
#endif
}

static HAL_StatusTypeDef controller_init(FDCAN_HandleTypeDef *h, FDCAN_GlobalTypeDef *instance, uint32_t offset)
{
  FDCAN_FilterTypeDef filter = {0};
  h->Instance = instance;
  h->Init.FrameFormat = FDCAN_FRAME_CLASSIC;
  h->Init.Mode = CAN_LOOPBACK ? FDCAN_MODE_INTERNAL_LOOPBACK : FDCAN_MODE_NORMAL;
  h->Init.AutoRetransmission = ENABLE;
  h->Init.TransmitPause = DISABLE;
  h->Init.ProtocolException = DISABLE;
  /* 25 MHz / (1 * (1 + 19 + 5)) = 1 Mbit/s, sample point 80%. */
  h->Init.NominalPrescaler = 1;
  h->Init.NominalSyncJumpWidth = 4;
  h->Init.NominalTimeSeg1 = 19;
  h->Init.NominalTimeSeg2 = 5;
  h->Init.DataPrescaler = 5;
  h->Init.DataSyncJumpWidth = 2;
  h->Init.DataTimeSeg1 = 7;
  h->Init.DataTimeSeg2 = 2;
  h->Init.MessageRAMOffset = offset;
  h->Init.StdFiltersNbr = (SIM_IS_BROADCAST ? 5 : 2) + (SIM_IMU_ENABLED ? 2 : 0);
  h->Init.ExtFiltersNbr = 0;
  h->Init.RxFifo0ElmtsNbr = 32;
  h->Init.RxFifo0ElmtSize = FDCAN_DATA_BYTES_64;
  h->Init.TxFifoQueueElmtsNbr = 8;
  h->Init.TxFifoQueueMode = FDCAN_TX_FIFO_OPERATION;
  h->Init.TxElmtSize = FDCAN_DATA_BYTES_8;
  if (HAL_FDCAN_Init(h) != HAL_OK) return HAL_ERROR;
  filter.IdType = FDCAN_STANDARD_ID;
  filter.FilterType = FDCAN_FILTER_MASK;
  filter.FilterConfig = FDCAN_FILTER_TO_RXFIFO0;
#if SIM_IS_BROADCAST
  const uint32_t command_ids[] = {1, 2, 3, 4, 0x7FF};
#elif SIM_IS_DJI
  const uint32_t command_ids[] = {SIM_DJI_GROUP1, SIM_DJI_GROUP2};
#else
  const uint32_t command_ids[] = {SIM_DM_CONTROL_ID, 0x7FF};
#endif
  for (unsigned i = 0; i < sizeof(command_ids)/sizeof(command_ids[0]); ++i) {
    filter.FilterIndex = i;
    filter.FilterID1 = command_ids[i];
    filter.FilterID2 = SIM_IS_BROADCAST && command_ids[i] != 0x7FF ? 0xFF : 0x7FF;
    if (HAL_FDCAN_ConfigFilter(h, &filter) != HAL_OK) return HAL_ERROR;
  }
  if (SIM_IMU_ENABLED) {
    filter.FilterID2 = 0x7FF;
    filter.FilterIndex = sizeof(command_ids)/sizeof(command_ids[0]);
    filter.FilterID1 = SIM_IMU_CAN_ID;
    if (HAL_FDCAN_ConfigFilter(h, &filter) != HAL_OK) return HAL_ERROR;
    ++filter.FilterIndex;
    filter.FilterID1 = 0x6FF;
    if (HAL_FDCAN_ConfigFilter(h, &filter) != HAL_OK) return HAL_ERROR;
  }
#if CAN_DUAL_BENCH
  /* Read-only sink instrumentation also observes forwarded EXT/RTR frames. */
  if (HAL_FDCAN_ConfigGlobalFilter(h, FDCAN_ACCEPT_IN_RX_FIFO0, FDCAN_ACCEPT_IN_RX_FIFO0,
      FDCAN_FILTER_REMOTE, FDCAN_FILTER_REMOTE) != HAL_OK) return HAL_ERROR;
#else
  if (HAL_FDCAN_ConfigGlobalFilter(h, FDCAN_REJECT, FDCAN_REJECT,
      FDCAN_REJECT_REMOTE, FDCAN_REJECT_REMOTE) != HAL_OK) return HAL_ERROR;
#endif
  return HAL_FDCAN_Start(h);
}

static HAL_StatusTypeDef controller_send(FDCAN_HandleTypeDef *h, const CAN_Frame *frame)
{
  FDCAN_TxHeaderTypeDef header = {0};
  uint8_t payload[8] = {0}; /* HAL reads words, including partial final word. */
  if (!frame || frame->length > 8 || frame->extended > 1 || frame->remote > 1 ||
      frame->id > (frame->extended ? 0x1FFFFFFFU : 0x7FFU)) return HAL_ERROR;
  if (h->State != HAL_FDCAN_STATE_BUSY) return HAL_ERROR;
  if (HAL_FDCAN_GetTxFifoFreeLevel(h) == 0) return HAL_BUSY;
  header.Identifier = frame->id;
  header.IdType = frame->extended ? FDCAN_EXTENDED_ID : FDCAN_STANDARD_ID;
  header.TxFrameType = frame->remote ? FDCAN_REMOTE_FRAME : FDCAN_DATA_FRAME;
  header.DataLength = dlc[frame->length];
  header.ErrorStateIndicator = FDCAN_ESI_ACTIVE;
  header.BitRateSwitch = FDCAN_BRS_OFF;
  header.FDFormat = FDCAN_CLASSIC_CAN;
  header.TxEventFifoControl = FDCAN_NO_TX_EVENTS;
  if (!frame->remote) memcpy(payload, frame->data, frame->length);
  return HAL_FDCAN_AddMessageToTxFifoQ(h, &header, payload);
}

static HAL_StatusTypeDef controller_receive(FDCAN_HandleTypeDef *h, CAN_Frame *frame)
{
  FDCAN_RxHeaderTypeDef header;
  uint8_t payload[64];
  if (!frame || h->State != HAL_FDCAN_STATE_BUSY) return HAL_ERROR;
  if (HAL_FDCAN_GetRxFifoFillLevel(h, FDCAN_RX_FIFO0) == 0) return HAL_BUSY;
  if (HAL_FDCAN_GetRxMessage(h, FDCAN_RX_FIFO0, &header, payload) != HAL_OK) return HAL_ERROR;
  if (header.FDFormat != FDCAN_CLASSIC_CAN) return HAL_ERROR;
#if !CAN_DUAL_BENCH
  if (header.RxFrameType != FDCAN_DATA_FRAME) return HAL_ERROR;
#endif
  uint8_t length = 0;
  while (length < 9 && dlc[length] != header.DataLength) ++length;
  /* Classical CAN DLC 9..15 also represents an 8-byte payload. */
  if (length > 8) length = 8;
  memset(frame, 0, sizeof(*frame));
  frame->id = header.Identifier;
  frame->extended = (header.IdType == FDCAN_EXTENDED_ID);
  frame->length = length;
  frame->remote = header.RxFrameType == FDCAN_REMOTE_FRAME;
  if (!frame->remote) memcpy(frame->data, payload, length);
  return HAL_OK;
}

HAL_StatusTypeDef CAN_Bus_Send(const CAN_Frame *frame) { return controller_send(&hfdcan1, frame); }
HAL_StatusTypeDef CAN_Bus_Receive(CAN_Frame *frame) { return controller_receive(&hfdcan1, frame); }
#if CAN_DUAL_BENCH
HAL_StatusTypeDef CAN_Bus2_Send(const CAN_Frame *frame) { return controller_send(&hfdcan2, frame); }
HAL_StatusTypeDef CAN_Bus2_Receive(CAN_Frame *frame) { return controller_receive(&hfdcan2, frame); }
#endif
