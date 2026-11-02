package dev.qianmo.canview;
/** Transport allowlist for the monitor-only app. */
final class MonitorCommands {
    static boolean allowed(byte[] data) {
        if(data==null||data.length!=20||data[0]!=1)return false;
        int command=data[1]&255;
        if(command==0x18){
            int n=data[2]&255;if(n<1||n>12||data[3]!=0)return false;
            for(int i=4+n;i<16;i++)if(data[i]!=0)return false;
            return data[16]!=0||data[17]!=0||data[18]!=0||data[19]!=0;
        }
        if(command==0x16||command==0x17){
            java.nio.ByteBuffer b=java.nio.ByteBuffer.wrap(data).order(java.nio.ByteOrder.LITTLE_ENDIAN);
            if(b.getInt(12)!=0||b.getInt(16)==0)return false;
            int id=b.getInt(4),feedback=b.getInt(8),a=data[2]&255,f=data[3]&255;
            return command==0x16?a<32&&f<=2&&id>=1&&id<=(f==0?8:0x7fe)&&feedback>=0&&feedback<=0x7ff:a<=3&&f<=32&&(a==2?id>=0&&id<f:id==0)&&(a==2?feedback>=0&&feedback<=1000:feedback==0);
        }
        if(command==0x14||command==0x15){
            if(data[2]!=0||data[3]!=0)return false;
            for(int i=12;i<16;i++)if(data[i]!=0)return false;
            java.nio.ByteBuffer b=java.nio.ByteBuffer.wrap(data).order(java.nio.ByteOrder.LITTLE_ENDIAN);
            int id=b.getInt(4),hz=b.getInt(8);
            return command==0x15?id==0&&hz==0:id>=1&&id<=0x7fe&&hz>=0&&hz<=1000;
        }
        if(command==0x10){
            // Official DM refresh_motor_status: STD 0x7FF, DLC 4, [ID_L, ID_H, CC, 00].
            if(data[2]!=0||data[3]!=4||(data[4]&255)!=0xff||data[5]!=7||data[6]!=0||data[7]!=0||(data[10]&255)!=0xcc||data[11]!=0)return false;
            int id=(data[8]&255)|((data[9]&255)<<8);if(id<1||id>0x7fe)return false;
            for(int i=12;i<16;i++)if(data[i]!=0)return false;return true;
        }
        if(command==0x13)return data[2]>0&&data[2]<=16&&data[3]==0;
        if(command!=0x12||data[2]!=0||data[3]!=0)return false;
        int rate=(data[4]&255)|((data[5]&255)<<8)|((data[6]&255)<<16)|((data[7]&255)<<24);
        return rate==125000||rate==250000||rate==500000||rate==1000000;
    }
}
