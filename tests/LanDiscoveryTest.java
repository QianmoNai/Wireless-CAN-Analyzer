package dev.qianmo.canview;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
public class LanDiscoveryTest {
 static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);}
 public static void main(String[] args)throws Exception{
  InetAddress loop=InetAddress.getByName("127.0.0.1");List<Throwable> errors=new CopyOnWriteArrayList<>();List<String> found=new CopyOnWriteArrayList<>();
  try(DatagramSocket peer=new DatagramSocket(0,loop);DatagramSocket wrongSource=new DatagramSocket(0,loop);DatagramSocket client=new DatagramSocket(0,loop)){
   peer.setSoTimeout(4000);
   Thread server=new Thread(()->{try{
    for(int i=0;i<6;i++){
     byte[] buffer=new byte[128];DatagramPacket request=new DatagramPacket(buffer,buffer.length);peer.receive(request);
     String text=new String(buffer,0,request.getLength(),StandardCharsets.US_ASCII);
     check(text.equals(i%2==0?"CAN_SCOPE_DISCOVER_1":"CAN_SCOPE_DISCOVER_"),"canonical/legacy probe order");
     // Simulate a device not ready during the first probe pair.
     if(i==2){
      for(String bad:new String[]{"CAN_SCOPE_1 other 32323","CAN_SCOPE_1 fixture 123","CAN_SCOPE_1 fixture 32323 extra"}){
       byte[] bytes=bad.getBytes(StandardCharsets.US_ASCII);peer.send(new DatagramPacket(bytes,bytes.length,request.getSocketAddress()));
      }
      byte[] valid="CAN_SCOPE_1 fixture 32323".getBytes(StandardCharsets.US_ASCII);
      wrongSource.send(new DatagramPacket(valid,valid.length,request.getSocketAddress()));
      peer.send(new DatagramPacket(valid,valid.length,request.getSocketAddress()));
     }
    }
   }catch(Throwable error){errors.add(error);}});server.start();
   long start=System.nanoTime();
   LanTransport.probeDiscovery(client,Collections.singleton(loop),"fixture",new LanTransport.Discovery(){
    public void found(String ip,String id){found.add(ip+" "+id);}
    public void finished(){throw new AssertionError("probe does not own completion callback");}
   },peer.getLocalPort());
   server.join(4500);check(!server.isAlive(),"probe peer did not finish");
   check(errors.isEmpty(),errors.toString());check(found.equals(Collections.singletonList("127.0.0.1 fixture")),"delayed discovery or source/identity validation: "+found);
   check(System.nanoTime()-start<TimeUnit.SECONDS.toNanos(5),"discovery exceeded bounded window");
  }
  System.out.println("PASS: bounded three-round UDP discovery, delayed peer, canonical/legacy probes, identity/port/source rejection; loopback only");
 }
}
