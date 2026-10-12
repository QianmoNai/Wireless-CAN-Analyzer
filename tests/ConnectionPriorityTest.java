package dev.qianmo.canview;
public class ConnectionPriorityTest{
 static void check(boolean b){if(!b)throw new AssertionError();}
 public static void main(String[] args){
  ConnectionPriority p=new ConnectionPriority();check(p.poll(0,false)==-1);int request=p.poll(0,true);check(request==0);p.result(request,true);check(p.poll(2000,true)==-1);
  p.want(true,2000);check(p.poll(2000,true)==1);p.result(1,true);
  p.want(false,2100);check(p.poll(2499,true)==-1);p.want(true,2500);check(p.poll(2500,true)==-1);
  p.want(false,2600);p.want(false,2900);check(p.delay(2900)==200);check(p.poll(3099,true)==-1);check(p.poll(3100,true)==0);p.result(0,false);
  check(p.delay(3200)==900&&p.poll(3200,true)==-1);check(p.poll(4100,true)==0);p.result(0,true);check(p.delay(4200)==-1);
  p.reset();p.want(true,5000);check(p.poll(5000,true)==1);p.result(1,true);check(p.poll(9000,true)==-1);
  System.out.println("PASS: immediate high-priority requests, debounced balanced mode, no duplicate requests, rejected-request retry and reconnect reset.");
 }
}
