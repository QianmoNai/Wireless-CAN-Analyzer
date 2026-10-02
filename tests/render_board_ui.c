// Render real firmware drawing code with simulated data, never a hardware capture.
#include <stdio.h>
#include "board_ui_render.h"
int main(int argc,char **argv){
 if(argc!=2){fputs("usage: render_board_ui OUTPUT_DIR\n",stderr);return 2;}
 ui_model_t ui;ui_model_init(&ui,0); ui_snapshot_t s={.bitrate=1000000,.rx_hz=1000,.tx_hz=100,.load=1189,.rates_valid=true,.load_valid=true,.connected=true,.frame_count=5};
 for(int i=0;i<5;i++)s.frames[i]=(ui_frame_t){.kind=i%2+1,.id=i%2?0x7ff:0x201,.len=i%2?4:8,.data={0x01,0x02,0x32,0x45,0xcc,0x00,0x00,0x01}};
 for(int page=0;page<3;page++){
  ui.page=page; char filename[256];snprintf(filename,sizeof(filename),"%s/panel-%d.ppm",argv[1],page); FILE *out=fopen(filename,"wb"); if(!out)return 1;
  fprintf(out,"P6\n172 320\n255\n"); uint16_t band[UI_WIDTH*UI_BAND_HEIGHT];
  for(int y=0;y<UI_HEIGHT;y+=UI_BAND_HEIGHT){ui_render_band(band,y,&ui,&s,false);for(unsigned i=0;i<sizeof(band)/2;i++){unsigned v=band[i];unsigned char rgb[]={((v>>11)&31)*255/31,((v>>5)&63)*255/63,(v&31)*255/31};fwrite(rgb,1,3,out);}}
  fclose(out);
 }
}
