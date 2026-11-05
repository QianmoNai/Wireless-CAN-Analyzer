package dev.qianmo.canview;
import java.util.*;
public class TelemetryInboxTest{
 static void check(boolean b){if(!b)throw new AssertionError();}
 static byte[] packet(int type,int value){byte[] p=new byte[20];p[0]=1;p[1]=(byte)type;p[8]=(byte)value;return p;}
 public static void main(String[] args)throws Exception{
  TelemetryInbox q=new TelemetryInbox(3,2);
  for(int i=0;i<10;i++)check(q.offer(packet(11,i),7,100+i));
  check(q.dropped()==7);check(q.offer(packet(10,50),7,90));check(q.offer(packet(9,51),7,91));
  check(!q.offer(packet(7,52),7,92));
  List<TelemetryInbox.Frame> m=q.drain(10,false);check(m.size()==2&&m.get(0).data[1]==10&&m.get(1).data[1]==9);
  check(q.pending(true)&&!q.pending(false));
  List<TelemetryInbox.Frame> d=q.drain(2,true);check(d.size()==2&&d.get(0).data[8]==7&&d.get(0).arrived==107&&d.get(0).session==7);
  check(d.get(1).data[8]==8&&q.drain(10,true).get(0).data[8]==9);
  byte[] p=packet(1,42);q.offer(p,8,200);p[8]=99;check(q.drain(1,true).get(0).data[8]==42);
  q.offer(packet(11,1),8,201);q.offer(packet(10,2),8,202);q.discardData();check(q.drain(10,true).size()==1);
  q.offer(packet(19,99),8,203);q.offer(packet(11,1),8,204);q.discardData();
  List<TelemetryInbox.Frame> serial=q.drain(10,true);check(serial.size()==1&&serial.get(0).data[1]==19);
  byte[] tx=packet(2,3);check(TelemetryInbox.ordinary(tx));tx[16]=1;check(!TelemetryInbox.ordinary(tx));
  q.clear();check(q.dropped()==0&&!q.pending(true));
  // An early capability/status packet and ordinary CAN frame survive several
  // pre-ready drain attempts, retain receipt age/session, then management wins.
  check(q.offer(packet(5,60),12,400));check(q.offer(packet(1,61),12,401));
  for(int attempt=0;attempt<3;attempt++){
   check(q.drain(128,true,false).isEmpty());check(!q.pending(true,false));
  }
  check(q.pending(true)&&q.dropped()==0);
  List<TelemetryInbox.Frame> early=q.drain(128,false,true);
  check(early.size()==1&&early.get(0).data[1]==5&&early.get(0).arrived==400&&early.get(0).session==12);
  check(q.pending(true,true)&&!q.pending(false,true));
  check(q.drain(128,true,true).get(0).data[8]==61);q.clear();
  check(q.offer(packet(5,62),12,402));q.clear();check(q.drain(128,true,true).isEmpty());
  for(int type:new int[]{4,5,12,13,14,16})check(!TelemetryInbox.ordinary(packet(type,0)));
  check(q.offer(packet(13,20),8,300));for(int i=0;i<100;i++)check(q.offer(packet(1,i),8,301+i));
  check(q.drain(1,false).get(0).data[1]==13);q.clear();
  TelemetryInbox concurrent=new TelemetryInbox(512,64);
  Thread writer=new Thread(()->{for(int i=0;i<20000;i++)check(concurrent.offer(packet(1,i),9,i));});writer.start();
  int received=0;while(writer.isAlive()||concurrent.pending(true))received+=concurrent.drain(128,true).size();writer.join();
  check(received+concurrent.dropped()==20000);
  System.out.println("PASS: bounded ingress, newest telemetry, management isolation/overflow, immutable receipt time/session, UI backpressure, reset and concurrent accounting.");
 }
}
