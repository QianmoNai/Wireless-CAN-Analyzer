package dev.qianmo.canview;
public class HomeTelemetryTest {
 static void check(boolean b){if(!b)throw new AssertionError();}
 public static void main(String[] args){HomeTelemetry h=new HomeTelemetry();int[][] rows={{0,1,0x201},{1,2,0}};h.configure(true,rows);
 check(h.poll(0,false)==null);byte[] p=h.poll(0,true);check(p[1]==0x16&&p[2]==0&&MonitorCommands.allowed(p));int rev=h.revision();check(!h.accept(rev));
 p=h.poll(20,true);check(p[2]==1&&p[3]==1&&MonitorCommands.allowed(p));p=h.poll(40,true);check(p[1]==0x17&&p[2]==1&&p[3]==2&&MonitorCommands.allowed(p));check(h.accept(rev));h.acknowledge(rev+1);check(h.poll(100,true)==null);h.acknowledge(rev);check(h.poll(3000,true)==null);
 h.configure(true,new int[][]{{0,1,0x201},{1,2,0}});check(h.poll(4000,true)==null);
 h.configure(false,rows);p=h.poll(4001,true);check(!h.accept(rev)&&p[1]==0x16);h.poll(4020,true);p=h.poll(4040,true);check(p[2]==0&&!h.quiet());
 check(h.poll(6039,true)==null);p=h.poll(6040,true);check(p[1]==0x16);h.failed();p=h.poll(6060,true);check(p[2]==0);
 h.configure(true,new int[0][]);p=h.poll(6100,true);check(p[1]==0x17&&p[3]==0&&MonitorCommands.allowed(p));h.reset();check(!h.accept(h.revision()));
 p[3]=33;check(!MonitorCommands.allowed(p));p[3]=0;p[2]=2;check(!MonitorCommands.allowed(p));
 h.configure(2,1,rows);h.poll(7000,true);h.poll(7020,true);p=h.poll(7040,true);check(p[2]==2&&p[4]==1&&MonitorCommands.allowed(p));rev=h.revision();
 h.configure(3,0,new int[0][]);check(!h.accept(rev));p=h.poll(7060,true);check(p[2]==3&&p[3]==0&&MonitorCommands.allowed(p));p[4]=1;check(!MonitorCommands.allowed(p));p[4]=0;p[2]=4;check(!MonitorCommands.allowed(p));
 h.configure(2,0,new int[][]{{0,1,0x201}},1000);h.poll(8000,true);p=h.poll(8020,true);check(p[1]==0x17&&MonitorCommands.allowed(p)&&java.nio.ByteBuffer.wrap(p).order(java.nio.ByteOrder.LITTLE_ENDIAN).getInt(8)==1000);rev=h.revision();
 h.configure(2,0,new int[][]{{0,1,0x201}},100);check(!h.accept(rev));h.poll(8040,true);p=h.poll(8060,true);check(p[8]==100&&MonitorCommands.allowed(p));p[2]=3;check(!MonitorCommands.allowed(p));p[2]=2;FeedbackQueries.Request.put(p,8,1001);check(!MonitorCommands.allowed(p));
 h.configure(2,0,new int[][]{{2,0x123,0x11}},100);
 p=h.poll(9000,true);check(p[1]==0x16&&p[3]==2&&MonitorCommands.allowed(p));
 byte[] invalid=p.clone();invalid[3]=3;check(!MonitorCommands.allowed(invalid));invalid=p.clone();FeedbackQueries.Request.put(invalid,4,0);check(!MonitorCommands.allowed(invalid));invalid=p.clone();FeedbackQueries.Request.put(invalid,4,0x7ff);check(!MonitorCommands.allowed(invalid));invalid=p.clone();FeedbackQueries.Request.put(invalid,8,0x800);check(!MonitorCommands.allowed(invalid));
 p=h.poll(9020,true);check(p[1]==0x17&&p[2]==2&&p[3]==1&&MonitorCommands.allowed(p));
 System.out.println("PASS: home/detail subscriptions, ordered writes, capability gating, stale ACK, timeout retry, zero motors and strict allowlist.");}
}
