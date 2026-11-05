package dev.qianmo.canview;
import javax.net.ssl.*;
import java.security.*;
import java.security.cert.X509Certificate;
import java.io.*;
import java.util.*;
import java.util.concurrent.*;
public class LanTransportTest {
 static void check(boolean b){if(!b)throw new AssertionError();}
 public static void main(String[] args)throws Exception{
  KeyStore keys=KeyStore.getInstance("PKCS12");try(InputStream in=new FileInputStream(args[0])){keys.load(in,"testpass".toCharArray());}
  KeyManagerFactory km=KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());km.init(keys,"testpass".toCharArray());
  SSLContext ctx=SSLContext.getInstance("TLS");ctx.init(km.getKeyManagers(),null,null);
  X509Certificate cert=(X509Certificate)keys.getCertificate("test");String pin=LanTransport.hex(MessageDigest.getInstance("SHA-256").digest(cert.getEncoded()));String token=String.join("",Collections.nCopies(32,"ab"));
  LanTransport.verifyPin(new X509Certificate[]{cert},pin);
  try{LanTransport.verifyPin(new X509Certificate[]{cert},token);throw new AssertionError();}catch(java.security.cert.CertificateException expected){}
  for(String ip:new String[]{"x","127.0.0.256","127.1","127.0.0.1:32323"})try{LanTransport.validateIp(ip);throw new AssertionError();}catch(IllegalArgumentException expected){}
  check(LanTransport.validateIp("127.0.0.1").equals("127.0.0.1"));
  for(boolean correctPin:new boolean[]{true,false}){
   SSLServerSocket server=(SSLServerSocket)ctx.getServerSocketFactory().createServerSocket(0);server.setSoTimeout(8000);
   CountDownLatch packets=new CountDownLatch(3),failed=new CountDownLatch(1),command=new CountDownLatch(1);List<Throwable> errors=new CopyOnWriteArrayList<>();
   Thread peer=new Thread(()->{try(SSLSocket sock=(SSLSocket)server.accept()){
    sock.setSoTimeout(7000);sock.startHandshake();if(!correctPin)throw new AssertionError("Unpinned peer accepted");
    DataInputStream in=new DataInputStream(sock.getInputStream());OutputStream out=sock.getOutputStream();byte[] auth=new byte[36];in.readFully(auth);check(new String(auth,0,4,"US-ASCII").equals("CAN1"));check(Arrays.equals(Arrays.copyOfRange(auth,4,36),LanTransport.unhex(token)));
    out.write(new byte[]{'O','K','0','1'});out.flush();
    byte[] frames=new byte[60];for(int i=0;i<3;i++){frames[i*20]=1;frames[i*20+1]=(byte)(i+1);}
    out.write(frames,0,7);out.flush();Thread.sleep(20);out.write(frames,7,53);out.flush();
    byte[] request=new byte[20];in.readFully(request);check(request[0]==1&&request[1]==0x12&&request[19]==42);command.countDown();
    // Suspended channels still need a round-trip heartbeat with no telemetry.
    byte[] heartbeat=new byte[20];in.readFully(heartbeat);check(heartbeat[0]==1&&heartbeat[1]==0x7f);out.write(heartbeat);out.flush();Thread.sleep(30);
   }catch(IOException expected){if(correctPin)errors.add(expected);}catch(Throwable e){errors.add(e);}finally{try{server.close();}catch(Exception ignored){}}});peer.start();
   java.util.concurrent.atomic.AtomicInteger opened=new java.util.concurrent.atomic.AtomicInteger();
   LanTransport transport=new LanTransport(new LanTransport.Listener(){
    public void connected(LanTransport source){check(correctPin);byte[] p=new byte[20];p[0]=1;p[1]=0x12;p[19]=42;check(source.send(p));p[19]=0;}
    public void packet(LanTransport source,byte[] p){check(p.length==20&&p[0]==1&&p[1]==4-packets.getCount());packets.countDown();}
    public void failed(LanTransport source,String message){failed.countDown();}
   },ip->{check(ip.equals("127.0.0.1"));opened.incrementAndGet();return new java.net.Socket();});transport.connect("127.0.0.1",server.getLocalPort(),correctPin?pin:token,token);
   if(correctPin){check(packets.await(10,TimeUnit.SECONDS));check(command.await(10,TimeUnit.SECONDS));}
   if(!failed.await(10,TimeUnit.SECONDS))throw new AssertionError("No failure callback, pinned="+correctPin);transport.close();peer.join(10000);check(!peer.isAlive());check(opened.get()==1);if(!errors.isEmpty())throw new AssertionError(errors.toString());if(!correctPin)check(packets.getCount()==3);
  }
  // An idle telemetry stream must be cancellable without waiting for the read
  // timeout. The Android acceptance separately exercises Conscrypt/StrictMode.
  SSLServerSocket idleServer=(SSLServerSocket)ctx.getServerSocketFactory().createServerSocket(0);
  CountDownLatch idleReady=new CountDownLatch(1),idleEnded=new CountDownLatch(1);
  List<Throwable> idleErrors=new CopyOnWriteArrayList<>();
  Thread idlePeer=new Thread(()->{boolean authenticated=false;try(SSLSocket sock=(SSLSocket)idleServer.accept()){
   sock.setSoTimeout(4000);DataInputStream in=new DataInputStream(sock.getInputStream());byte[] auth=new byte[36];in.readFully(auth);
   check(Arrays.equals(Arrays.copyOfRange(auth,4,36),LanTransport.unhex(token)));
   sock.getOutputStream().write(new byte[]{'O','K','0','1'});sock.getOutputStream().flush();authenticated=true;
   check(in.read()==-1);
  }catch(IOException expected){if(!authenticated)idleErrors.add(expected);}catch(Throwable error){idleErrors.add(error);}finally{idleEnded.countDown();}});idlePeer.start();
  LanTransport idle=new LanTransport(new LanTransport.Listener(){
   public void connected(LanTransport source){idleReady.countDown();}
   public void packet(LanTransport source,byte[] packet){idleErrors.add(new AssertionError("Unexpected idle packet"));}
   public void failed(LanTransport source,String message){idleErrors.add(new AssertionError("Explicit close reported failure"));}
  });idle.connect("127.0.0.1",idleServer.getLocalPort(),pin,token);
  check(idleReady.await(5,TimeUnit.SECONDS));long before=System.nanoTime();idle.close();idle.close();
  check(System.nanoTime()-before<TimeUnit.MILLISECONDS.toNanos(500));
  check(idleEnded.await(2,TimeUnit.SECONDS));idlePeer.join(2000);idleServer.close();check(idleErrors.isEmpty());
  System.out.println("LAN transport: pinned TLS, token handshake, fragmented/coalesced frames, copied writes and idle disconnect PASS");
 }
}
