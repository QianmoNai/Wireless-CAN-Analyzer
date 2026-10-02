package dev.qianmo.canview;
public final class MonitorCommandsTest {
    public static void main(String[] args) {
        for(int type=0;type<256;type++){
            byte[] p=new byte[20];p[0]=1;p[1]=(byte)type;p[2]=3;p[4]=65;
            if(type!=0x13&&MonitorCommands.allowed(p))throw new AssertionError("Unexpected command "+type);
        }
        for(int rate:new int[]{125000,250000,500000,1000000}){
            byte[] p=new byte[20];p[0]=1;p[1]=0x12;for(int i=0;i<4;i++)p[4+i]=(byte)(rate>>>(8*i));
            if(!MonitorCommands.allowed(p))throw new AssertionError("Rate denied");
            p[3]=8;if(MonitorCommands.allowed(p))throw new AssertionError("Invalid flags accepted");
        }
        if(MonitorCommands.allowed(null)||MonitorCommands.allowed(new byte[19]))throw new AssertionError();
        byte[] p=new byte[20];p[0]=1;p[1]=0x13;p[2]=16;if(!MonitorCommands.allowed(p))throw new AssertionError();
        p[2]=17;if(MonitorCommands.allowed(p))throw new AssertionError();
        p[2]=0;if(MonitorCommands.allowed(p))throw new AssertionError();
        System.out.println("PASS: production native command filter rejects arbitrary CAN, pulse, stop, demo and unknown commands; settings remain available (exact query covered separately).");
    }
}
