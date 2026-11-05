#include "lcd_display.h"
#include "motor_sim_config.h"
#include "imu_sim.h"
#include <string.h>

#define WIDTH 240U
#define HEIGHT 240U
#define CS GPIO_PIN_8
#define DC GPIO_PIN_15
#define BL GPIO_PIN_12
#define X_OFFSET 0U
#define Y_OFFSET 0U
static SPI_HandleTypeDef spi;
volatile uint32_t lcd_ready, lcd_errors;
static char rows[15][21];
static uint8_t pixels[WIDTH * 2];
static uint16_t scanline = HEIGHT;
static uint32_t last_refresh;
/* Original 5x7 column glyphs, bit 0 is top; scaled 2x. */
static const char alphabet[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ:-./";
static const uint8_t glyphs[][5] = {
 {62,81,73,69,62},{0,66,127,64,0},{98,81,73,73,70},{34,65,73,73,54},
 {24,20,18,127,16},{39,69,69,69,57},{62,73,73,73,50},{1,113,9,5,3},
 {54,73,73,73,54},{38,73,73,73,62},
 {126,9,9,9,126},{127,73,73,73,54},{62,65,65,65,34},{127,65,65,34,28},
 {127,73,73,73,65},{127,9,9,9,1},{62,65,73,73,122},{127,8,8,8,127},
 {0,65,127,65,0},{32,64,65,63,1},{127,8,20,34,65},{127,64,64,64,64},
 {127,2,12,2,127},{127,4,8,16,127},{62,65,65,65,62},{127,9,9,9,6},
 {62,65,81,33,94},{127,9,25,41,70},{38,73,73,73,50},{1,1,127,1,1},
 {63,64,64,64,63},{31,32,64,32,31},{63,64,56,64,63},{99,20,8,20,99},
 {3,4,120,4,3},{97,81,73,69,67},
 {0,54,54,0,0},{8,8,8,8,8},{0,96,96,0,0},{32,16,8,4,2}
};
_Static_assert(sizeof(glyphs)/sizeof(glyphs[0]) == sizeof(alphabet)-1, "font size");

static HAL_StatusTypeDef transfer(uint8_t command, uint8_t *data, uint16_t length)
{
  HAL_GPIO_WritePin(GPIOG, CS | DC, GPIO_PIN_RESET);
  HAL_StatusTypeDef result = HAL_SPI_Transmit(&spi, &command, 1, 5);
  HAL_GPIO_WritePin(GPIOG, DC, GPIO_PIN_SET);
  if (result == HAL_OK && length) result = HAL_SPI_Transmit(&spi, data, length, 5);
  HAL_GPIO_WritePin(GPIOG, CS, GPIO_PIN_SET);
  return result;
}

HAL_StatusTypeDef LCD_Init(void)
{
  GPIO_InitTypeDef gpio = {0};
  RCC_PeriphCLKInitTypeDef clock = {0};
  lcd_ready = 0;
  clock.PeriphClockSelection = RCC_PERIPHCLK_SPI6;
  clock.Spi6ClockSelection = RCC_SPI6CLKSOURCE_D3PCLK1;
  if (HAL_RCCEx_PeriphCLKConfig(&clock) != HAL_OK) goto error;
  __HAL_RCC_GPIOG_CLK_ENABLE();
  __HAL_RCC_SPI6_CLK_ENABLE();
  HAL_GPIO_WritePin(GPIOG, CS | DC, GPIO_PIN_SET);
  HAL_GPIO_WritePin(GPIOG, BL, GPIO_PIN_RESET);
  gpio.Pin = CS | DC | BL;
  gpio.Mode = GPIO_MODE_OUTPUT_PP;
  gpio.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(GPIOG, &gpio);
  gpio.Pin = GPIO_PIN_13 | GPIO_PIN_14;
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Alternate = GPIO_AF5_SPI6;
  HAL_GPIO_Init(GPIOG, &gpio);
  spi.Instance = SPI6;
  spi.Init.Mode = SPI_MODE_MASTER;
  spi.Init.Direction = SPI_DIRECTION_2LINES_TXONLY;
  spi.Init.DataSize = SPI_DATASIZE_8BIT;
  spi.Init.CLKPolarity = SPI_POLARITY_LOW;
  spi.Init.CLKPhase = SPI_PHASE_1EDGE;
  spi.Init.NSS = SPI_NSS_SOFT;
  spi.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_8; /* 64 / 8 = 8 MHz */
  spi.Init.FirstBit = SPI_FIRSTBIT_MSB;
  spi.Init.TIMode = SPI_TIMODE_DISABLE;
  spi.Init.CRCCalculation = SPI_CRCCALCULATION_DISABLE;
  spi.Init.NSSPMode = SPI_NSS_PULSE_DISABLE;
  spi.Init.FifoThreshold = SPI_FIFO_THRESHOLD_01DATA;
  spi.Init.MasterKeepIOState = SPI_MASTER_KEEP_IO_STATE_ENABLE;
  if (HAL_SPI_Init(&spi) != HAL_OK) goto error;
  HAL_Delay(10);
  if (transfer(0x01, NULL, 0) != HAL_OK) goto error;
  HAL_Delay(150);
  if (transfer(0x11, NULL, 0) != HAL_OK) goto error;
  HAL_Delay(120);
  uint8_t format = 0x55, rotation = 0;
  if (transfer(0x3A, &format, 1) != HAL_OK || transfer(0x36, &rotation, 1) != HAL_OK ||
      transfer(0x21, NULL, 0) != HAL_OK || transfer(0x13, NULL, 0) != HAL_OK ||
      transfer(0x29, NULL, 0) != HAL_OK) goto error;
  HAL_Delay(100);
  scanline = HEIGHT;
  last_refresh = HAL_GetTick() - 500U;
  lcd_ready = 1;
  return HAL_OK;
error:
  ++lcd_errors;
  return HAL_ERROR;
}

static void label(unsigned row, const char *text)
{
  unsigned i = 0;
  while (i < 20 && text[i]) { rows[row][i] = text[i]; ++i; }
}
static void number(unsigned row, unsigned col, uint32_t value, unsigned base, unsigned width)
{
  static const char digits[] = "0123456789ABCDEF";
  while (width) { rows[row][col + --width] = digits[value % base]; value /= base; }
}
static void signed_number(unsigned row, int32_t value)
{
  rows[row][9] = value < 0 ? '-' : ' ';
  number(row, 10, value < 0 ? (uint32_t)(-(int64_t)value) : (uint32_t)value, 10, 8);
}
static void snapshot(const LCD_Status *s)
{
  memset(rows, ' ', sizeof(rows));
  if (SIM_IMU_ENABLED && (HAL_GetTick() / 5000U) % 2U) {
    label(0, "DM IMU L1 SIM"); label(1, "CAN 1 MBIT/S");
    label(2, "CMD ID:"); number(2, 10, SIM_IMU_CAN_ID, 16, 3);
    label(3, "REPLY ID:"); number(3, 10, SIM_IMU_MASTER_ID, 16, 3);
    label(4, s->imu_active ? "MODE: AUTO" : "MODE: QUERY");
    label(5, "PERIOD MS:"); number(5, 11, s->imu_period, 10, 5);
    label(6, "01 ACCEL  02 GYRO"); label(7, "03 EULER  04 QUAT");
    label(8, "IMU TX:"); number(8, 9, s->imu_tx, 10, 10);
    label(9, "IMU DROP:"); number(9, 10, s->imu_dropped, 10, 9);
    label(10, s->bus_off ? "STATE: BUS OFF" : s->tx_full ? "STATE: TX FULL" : "STATE: RUNNING");
    label(11, "TX Q:"); number(11, 7, s->tx, 10, 10);
    label(12, "RX:"); number(12, 7, s->rx, 10, 10);
    label(13, "ERROR:"); number(13, 7, s->errors, 10, 10);
    label(14, "SYNTHETIC IMU DATA"); return;
  }
  if (SIM_IS_BROADCAST) {
    label(0, "DJI AUTO DM QUERY"); label(1, "CAN 1 MBIT/S");
    label(2, "DJI MS:"); number(2, 11, SIM_BROADCAST_PERIOD_MS, 10, 5);
    label(3, s->bus_off ? "STATE: BUS OFF" : s->tx_full ? "STATE: TX FULL" : "STATE: SENDING");
    label(4, "C620     201"); label(5, "C610     202"); label(6, "GM6020   205");
    label(7, "DM MIT   011"); label(8, "DM P/V   012");
    label(9, "DM VEL   013"); label(10, "DM P/F   014");
    label(11, "TX Q:"); number(11, 7, s->tx, 10, 10);
    label(12, "QUERY:"); number(12, 7, s->rx, 10, 10);
    label(13, "DROP:"); number(13, 7, s->reply_dropped, 10, 10);
    label(14, "DM REPLY ON REQUEST"); return;
  }
  if ((HAL_GetTick() / 5000U) % 2U == 0) {
    label(0, SIM_PROFILE_NAME);
    label(1, "CAN 1 MBIT/S");
    if (SIM_IS_DJI) {
    label(2, "DJI ID:"); number(2, 9, SIM_DJI_FIRST_ID, 10, 1);
    label(3, "RPM:"); signed_number(3, s->dji_rpm);
    label(4, "I RAW:"); signed_number(4, s->dji_current);
    label(5, "CMD:"); number(5, 9, s->dji_commands, 10, 10);
    } else {
    label(6, "DM ID:"); number(6, 7, SIM_DM_ID, 16, 2);
    label(7, s->dm_status == 1 ? "DM: ENABLED" : s->dm_status == 13 ? "DM: TIMEOUT" : "DM: DISABLED");
    label(8, "P MRAD:"); signed_number(8, s->dm_position_mrad);
    label(9, "V MRAD/S:"); signed_number(9, s->dm_velocity_mrad);
    label(10, "T MNM:"); signed_number(10, s->dm_torque_mnm);
    label(11, "CMD:"); number(11, 9, s->dm_commands, 10, 10);
    }
    label(12, "DROP:"); number(12, 9, s->reply_dropped, 10, 10);
    label(13, "TIMEOUT:"); number(13, 9, s->timeouts, 10, 10);
    label(14, s->bus_off ? "CAN BUS OFF" : s->tx_full ? "CAN TX FULL" : "SIMULATION ONLY");
    return;
  }
  label(0, "STM32H723 CAN");
  label(1, CAN_LOOPBACK ? "INTERNAL LOOPBACK" : "FDCAN1 1 MBIT/S");
  label(3, s->bus_off ? "STATE: BUS OFF" : s->tx_full ? "STATE: TX FULL" : "STATE: RUNNING");
  label(4, "TX Q:"); number(4, 6, s->tx, 10, 10);
  label(5, "BUSY:"); number(5, 6, s->busy, 10, 10);
  label(6, "RX  :"); number(6, 6, s->rx, 10, 10);
  label(7, "ERR :"); number(7, 6, s->errors, 10, 10);
  label(8, "LOST:"); number(8, 6, s->lost, 10, 10);
  if (!s->rx) { label(10, "WAITING FOR RX"); return; }
  label(10, s->last_rx.extended ? "EXT ID:" : "STD ID:");
  number(10, 8, s->last_rx.id, 16, 8);
  label(11, "DLC:"); number(11, 5, s->last_rx.length, 10, 1);
  for (unsigned i = 0; i < s->last_rx.length && i < 8; ++i)
    number(12 + i / 4, (i % 4) * 3, s->last_rx.data[i], 16, 2);
}

void LCD_Process(const LCD_Status *status)
{
  if (!lcd_ready || !status) return;
  if (scanline == HEIGHT) {
    if ((uint32_t)(HAL_GetTick() - last_refresh) < 500U) return;
    last_refresh = HAL_GetTick();
    snapshot(status);
    scanline = 0;
  }
  unsigned row = scanline / 16, bit = (scanline % 16) / 2;
  uint16_t ink = row < 2 ? 0x07FF : row == 3 ? 0xFFE0 : 0xFFFF;
  for (unsigned cell = 0; cell < 20; ++cell) {
    const char *match = strchr(alphabet, rows[row][cell]);
    for (unsigned dx = 0; dx < 12; ++dx) {
      uint16_t color = 0x0841;
      if (match && dx < 10 && bit < 7 && (glyphs[match-alphabet][dx/2] & (1U << bit))) color = ink;
      unsigned index = (cell * 12 + dx) * 2;
      pixels[index] = color >> 8;
      pixels[index+1] = color & 255;
    }
  }
  uint16_t y = scanline + Y_OFFSET;
  uint8_t xwin[4] = {X_OFFSET >> 8, X_OFFSET & 255, (X_OFFSET+WIDTH-1) >> 8, (X_OFFSET+WIDTH-1) & 255};
  uint8_t ywin[4] = {y >> 8, y & 255, y >> 8, y & 255};
  if (transfer(0x2A, xwin, 4) != HAL_OK || transfer(0x2B, ywin, 4) != HAL_OK ||
      transfer(0x2C, pixels, sizeof(pixels)) != HAL_OK) {
    ++lcd_errors;
    lcd_ready = 0; /* Do not repeatedly stall CAN on a failed SPI peripheral. */
    HAL_GPIO_WritePin(GPIOG, BL, GPIO_PIN_RESET);
    return;
  }
  if (++scanline == HEIGHT) HAL_GPIO_WritePin(GPIOG, BL, GPIO_PIN_SET);
}
