// Exercise the real driver against a small output-state model. This checks
// control/failure behavior, not the active buzzer's acoustic response.
#include <assert.h>
#include <stdio.h>
#include "board_buzzer.h"
#include "board_ui_model.h"
#include "driver/gpio.h"
#include "driver/ledc.h"

enum { FAIL_NONE,FAIL_CHANNEL,FAIL_SET,FAIL_UPDATE,FAIL_STOP };
static unsigned pending_duty,active_duty,output_level,writes,failure;
static bool pwm;
static esp_err_t result(unsigned operation)
{
    if(failure==operation){failure=FAIL_NONE;return ESP_FAIL;}
    return ESP_OK;
}
static void check_channel(ledc_mode_t mode,ledc_channel_t channel)
{
    // Channel/timer 0 belong to the display backlight and must stay untouched.
    assert(mode==LEDC_LOW_SPEED_MODE && channel==LEDC_CHANNEL_1);
}
esp_err_t gpio_set_level(gpio_num_t pin,uint32_t level)
{
    assert(pin==13 && level==0);output_level=level;return ESP_OK;
}
esp_err_t gpio_set_direction(gpio_num_t pin,gpio_mode_t mode)
{
    assert(pin==13 && mode==GPIO_MODE_OUTPUT);return ESP_OK;
}
esp_err_t ledc_timer_config(const ledc_timer_config_t *c)
{
    assert(c->timer_num==LEDC_TIMER_1 && c->speed_mode==LEDC_LOW_SPEED_MODE);
    assert(c->duty_resolution==LEDC_TIMER_10_BIT && c->freq_hz==20000);
    return ESP_OK;
}
esp_err_t ledc_channel_config(const ledc_channel_config_t *c)
{
    check_channel(c->speed_mode,c->channel);
    assert(c->gpio_num==13 && c->timer_sel==LEDC_TIMER_1 && c->duty==0);
    pwm=true;pending_duty=active_duty=c->duty;
    return result(FAIL_CHANNEL);
}
esp_err_t ledc_stop(ledc_mode_t mode,ledc_channel_t channel,uint32_t level)
{
    check_channel(mode,channel);writes++;assert(level<=1);
    if(result(FAIL_STOP)!=ESP_OK)return ESP_FAIL;
    pwm=false;output_level=level;return ESP_OK;
}
esp_err_t ledc_set_duty(ledc_mode_t mode,ledc_channel_t channel,uint32_t duty)
{
    check_channel(mode,channel);writes++;assert(duty>0 && duty<1024);
    if(result(FAIL_SET)!=ESP_OK)return ESP_FAIL;
    pending_duty=duty;return ESP_OK;
}
esp_err_t ledc_update_duty(ledc_mode_t mode,ledc_channel_t channel)
{
    check_channel(mode,channel);writes++;
    if(result(FAIL_UPDATE)!=ESP_OK)return ESP_FAIL;
    pwm=true;active_duty=pending_duty;return ESP_OK;
}
static void silent(void) { assert(pwm?active_duty==0:output_level==0); }
int main(void)
{
    assert(board_buzzer_set(true,UI_VOLUME_HIGH)==ESP_ERR_INVALID_STATE);
    board_buzzer_stop();silent();
    failure=FAIL_CHANNEL;assert(board_buzzer_init()==ESP_FAIL);silent();
    assert(board_buzzer_init()==ESP_OK);silent();
    assert(board_buzzer_set(true,UI_VOLUME_LOW)==ESP_OK && pwm);
    unsigned low=active_duty;
    unsigned previous_writes=writes;
    assert(board_buzzer_set(true,UI_VOLUME_LOW)==ESP_OK && writes==previous_writes);
    assert(board_buzzer_set(true,UI_VOLUME_MEDIUM)==ESP_OK && pwm && active_duty>low);
    assert(board_buzzer_set(true,UI_VOLUME_HIGH)==ESP_OK && !pwm && output_level==1);
    assert(board_buzzer_set(true,UI_VOLUME_LOW)==ESP_OK && pwm && active_duty==low);
    assert(board_buzzer_set(false,255)==ESP_OK);silent();
    previous_writes=writes;
    for(unsigned volume=0;volume<UI_VOLUME_COUNT;volume++) {
        assert(board_buzzer_set(false,volume)==ESP_OK);silent();
    }
    assert(writes==previous_writes);
    assert(board_buzzer_set(true,UI_VOLUME_MEDIUM)==ESP_OK && pwm);
    assert(board_buzzer_set(true,255)==ESP_ERR_INVALID_ARG);silent();
    for(unsigned error=FAIL_SET;error<=FAIL_STOP;error++) {
        assert(board_buzzer_set(true,UI_VOLUME_HIGH)==ESP_OK);
        failure=error;
        assert(board_buzzer_set(error!=FAIL_STOP,UI_VOLUME_LOW)==ESP_FAIL);silent();
        assert(board_buzzer_set(true,UI_VOLUME_LOW)==ESP_OK && pwm && active_duty==low);
    }
    assert(board_buzzer_set(true,UI_VOLUME_HIGH)==ESP_OK);
    failure=FAIL_STOP;board_buzzer_stop(); // Failed cleanup cannot cache mute.
    assert(board_buzzer_set(false,UI_VOLUME_HIGH)==ESP_OK);silent();
    assert(board_buzzer_set(true,UI_VOLUME_LOW)==ESP_OK && pwm);
    board_buzzer_stop();silent();
    puts("board buzzer: initial silence, independent PWM, levels, steady high, resume, mute, error recovery PASS");
}
