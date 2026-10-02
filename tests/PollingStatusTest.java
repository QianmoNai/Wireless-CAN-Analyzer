package dev.qianmo.canview;
public class PollingStatusTest {
 public static void main(String[] args){
  if(!PollingStatus.message(0,1,100,99).equals(PollingStatus.message(0,0,100,99)))throw new AssertionError();
  if(!PollingStatus.message(4,1,0,0).equals("总线持续繁忙，查询已停止"))throw new AssertionError();
  if(!PollingStatus.message(4,3,0,0).contains("ACK；连续通信失败 5 次"))throw new AssertionError();
  if(!PollingStatus.message(4,64,0,0).contains("Bus-off"))throw new AssertionError();
  if(PollingStatus.message(4,0,0,0).contains("5 次"))throw new AssertionError();
  if(PollingStatus.message(0,0,100,100).contains("重试"))throw new AssertionError();
  for(int flag:new int[]{2,4,8,16,32,128})if(!PollingStatus.message(0,flag,100,98).contains("正在重试"))throw new AssertionError();
  System.out.println("PASS: retry remains active, detailed failure messages, mixed flags, bus-off and old firmware compatibility.");
 }
}
