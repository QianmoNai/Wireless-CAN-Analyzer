"""Run the production DMA callback with an SPI driver that ignores its return.

ESP-IDF 6.0.2's SPI panel callback does not propagate the callback's bool to
portYIELD_FROM_ISR. A woken display task must explicitly request the ISR yield.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT/'firmware/main/board_ui.c').read_text()
start = source.index('static bool color_done(')
opening = source.index('{', start)
depth = 0
for end in range(opening, len(source)):
    depth += (source[end] == '{') - (source[end] == '}')
    if depth == 0:
        callback = source[start:end+1]
        break
else:
    raise AssertionError('Unclosed DMA callback')

prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
typedef int BaseType_t;
typedef void *esp_lcd_panel_io_handle_t;
typedef void esp_lcd_panel_io_event_data_t;
#define pdFALSE 0
#define pdTRUE 1
static int display_task=42;
static unsigned notifications,yields;
static BaseType_t wake_task;
static void vTaskNotifyGiveFromISR(int task,BaseType_t *wake){
    assert(task==display_task);notifications++;*wake=wake_task;
}
#define portYIELD_FROM_ISR() (++yields)
'''
test = r'''
int main(void){
    // Mirror SPI post_trans_cb: it calls the callback and discards the bool.
    wake_task=pdTRUE;
    (void)color_done(NULL,NULL,NULL);
    assert(notifications==1 && yields==1);
    wake_task=pdFALSE;
    (void)color_done(NULL,NULL,NULL);
    assert(notifications==2 && yields==1);
    wake_task=pdTRUE;
    for(unsigned band=0;band<22;band++)(void)color_done(NULL,NULL,NULL);
    assert(notifications==24 && yields==23);
    puts("PASS: actual panel DMA callback wakes the display task immediately even when SPI discards its return; no redundant ISR yield.");
}
'''
with tempfile.TemporaryDirectory(prefix='can-panel-dma-') as temporary:
    directory = Path(temporary)
    (directory/'test.c').write_text(prelude+callback+test)
    binary = directory/'test'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    str(directory/'test.c'), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
