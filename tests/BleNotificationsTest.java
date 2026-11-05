package dev.qianmo.canview;
import java.util.*;
public class BleNotificationsTest {
 static void check(boolean value){if(!value)throw new AssertionError();}
 public static void main(String[] args){
  check(BleNotifications.select(true).equals(BleNotifications.BATCH));
  check(BleNotifications.select(false).equals(BleNotifications.LEGACY));
  for(int count:new int[]{1,2,10,12}){
   byte[] value=new byte[count*20];
   for(int i=0;i<count;i++){value[i*20]=1;value[i*20+1]=(byte)(i%2==0?8:11);value[i*20+16]=(byte)i;}
   ArrayList<byte[]> packets=new ArrayList<>();
   check(BleNotifications.dispatch(value,true,packets::add)&&packets.size()==count);
   for(int i=0;i<count;i++)check(packets.get(i).length==20&&packets.get(i)[16]==i);
   if(count>1){value[16]=99;check(packets.get(0)[16]==0);}
   if(count>1){
    packets.clear();check(!BleNotifications.dispatch(value,false,packets::add)&&packets.isEmpty());
    value[value.length-20]=2;check(!BleNotifications.dispatch(value,true,packets::add)&&packets.isEmpty());
   }
  }
  for(byte[] invalid:new byte[][]{null,new byte[0],new byte[19],new byte[21],new byte[260],new byte[20]}){
   ArrayList<byte[]> packets=new ArrayList<>();check(!BleNotifications.dispatch(invalid,true,packets::add)&&packets.isEmpty());
  }
  System.out.println("PASS: legacy/batch selection, MTU23 single frame, 2/10/12 frame values, ACK/sample order, immutable split payloads and atomic malformed rejection.");
 }
}
