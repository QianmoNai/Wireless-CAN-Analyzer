#include "board_buzzer.h"
#include "board_ui_model.h"
#include "driver/gpio.h"
#include "driver/ledc.h"

enum { BUZZER_PIN=13, PWM_FULL=1024 };
static bool initialized;
static unsigned applied;

esp_err_t board_buzzer_init(void)
{
    esp_err_t err=gpio_set_level(BUZZER_PIN,0);
    if(err!=ESP_OK)return err;
    err=gpio_set_direction(BUZZER_PIN,GPIO_MODE_OUTPUT);
    if(err!=ESP_OK)return err;
    ledc_timer_config_t timer={.speed_mode=LEDC_LOW_SPEED_MODE,.duty_resolution=LEDC_TIMER_10_BIT,
        .timer_num=LEDC_TIMER_1,.freq_hz=20000,.clk_cfg=LEDC_AUTO_CLK};
    err=ledc_timer_config(&timer);
    if(err!=ESP_OK)return err;
    ledc_channel_config_t channel={.gpio_num=BUZZER_PIN,.speed_mode=LEDC_LOW_SPEED_MODE,
        .channel=LEDC_CHANNEL_1,.timer_sel=LEDC_TIMER_1,.duty=0};
    err=ledc_channel_config(&channel);
    if(err!=ESP_OK){ledc_stop(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_1,0);return err;}
    initialized=true;applied=0;
    return ESP_OK;
}

void board_buzzer_stop(void)
{
    if(initialized){
        esp_err_t err=ledc_stop(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_1,0);
        // A failed stop must never be cached as a successful mute.
        applied=err==ESP_OK?0:UINT32_MAX;
    }else gpio_set_level(BUZZER_PIN,0);
}

esp_err_t board_buzzer_set(bool enabled, uint8_t volume)
{
    if(!initialized)return ESP_ERR_INVALID_STATE;
    if(enabled && volume>=UI_VOLUME_COUNT){board_buzzer_stop();return ESP_ERR_INVALID_ARG;}
    // Experimental power-gating levels, not calibrated loudness percentages.
    // Conservative low/mid duties limit oscillator interruption; high preserves
    // the original steady DC drive. Actual acoustic response needs listening tests.
    const unsigned duties[]={PWM_FULL*60/100,PWM_FULL*80/100,PWM_FULL};
    unsigned duty=enabled?duties[volume]:0;
    if(duty==applied)return ESP_OK;
    esp_err_t err;
    if(duty==0 || duty==PWM_FULL)err=ledc_stop(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_1,duty==PWM_FULL);
    else {
        err=ledc_set_duty(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_1,duty);
        if(err==ESP_OK)err=ledc_update_duty(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_1);
    }
    if(err==ESP_OK)applied=duty;
    else board_buzzer_stop();
    return err;
}
