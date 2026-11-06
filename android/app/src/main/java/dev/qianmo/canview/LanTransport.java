package dev.qianmo.canview;
import javax.net.ssl.*;
import java.net.*;
import java.io.*;
import java.security.*;
import java.security.cert.*;
import java.util.*;
import java.util.concurrent.*;
/** TLS with a per-device certificate pin and token received over authenticated BLE. */
final class LanTransport implements AutoCloseable {
 interface Listener {void connected(LanTransport source);void packet(LanTransport source,byte[] packet);void failed(LanTransport source,String message);}
 private final Listener listener;
 private final ArrayBlockingQueue<byte[]> writes=new ArrayBlockingQueue<>(32);
 private volatile boolean closed;
 private volatile Socket socket,rawSocket;
 interface SocketOpener {Socket open(String ip)throws IOException;}
 private final SocketOpener sockets;
 LanTransport(Listener listener){this(listener,ip->new Socket());}
 LanTransport(Listener listener,SocketOpener sockets){this.listener=listener;this.sockets=Objects.requireNonNull(sockets);}
 static byte[] unhex(String s){if(s==null||(s.length()&1)!=0||!s.matches("[0-9a-fA-F]*"))throw new IllegalArgumentException("无效的设备凭据");byte[] b=new byte[s.length()/2];for(int i=0;i<b.length;i++)b[i]=(byte)Integer.parseInt(s.substring(2*i,2*i+2),16);return b;}
 static String hex(byte[] b){StringBuilder s=new StringBuilder();for(byte v:b)s.append(String.format(Locale.ROOT,"%02x",v&255));return s.toString();}
 static void verifyPin(X509Certificate[] chain,String fingerprint)throws CertificateException{
  try{if(chain==null||chain.length==0||fingerprint.length()!=64||!MessageDigest.isEqual(unhex(fingerprint),MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded())))throw new CertificateException("设备证书不匹配，请通过蓝牙重新确认设备");}catch(GeneralSecurityException|IllegalArgumentException e){throw new CertificateException(e);}
 }
 static String validateIp(String ip){if(ip==null||!ip.matches("(?:[0-9]{1,3}\\.){3}[0-9]{1,3}"))throw new IllegalArgumentException("请输入设备的 IPv4 地址");for(String part:ip.split("\\."))if(Integer.parseInt(part)>255)throw new IllegalArgumentException("IP 地址无效");return ip;}
 void connect(String ip,int port,String fingerprint,String token){new Thread(()->{
  try{
   validateIp(ip);byte[] key=unhex(token);if(key.length!=32)throw new IOException("请先通过蓝牙配置 Wi-Fi");
   SSLContext context=SSLContext.getInstance("TLS");context.init(null,new TrustManager[]{new X509TrustManager(){
    public X509Certificate[] getAcceptedIssuers(){return new X509Certificate[0];}
    public void checkClientTrusted(X509Certificate[] c,String a)throws CertificateException{throw new CertificateException("Unsupported");}
    public void checkServerTrusted(X509Certificate[] c,String a)throws CertificateException{verifyPin(c,fingerprint);}
   }},new SecureRandom());
   Socket tcp=sockets.open(ip);rawSocket=tcp;socket=tcp;if(closed){tcp.close();return;}tcp.connect(new InetSocketAddress(ip,port),5000);tcp.setSoTimeout(5000);
   SSLSocket tls=(SSLSocket)context.getSocketFactory().createSocket(tcp,ip,port,true);socket=tls;tls.setEnabledProtocols(new String[]{"TLSv1.2"});tls.startHandshake();
   if(closed){tls.close();return;}OutputStream out=tls.getOutputStream();out.write(new byte[]{'C','A','N','1'});out.write(key);out.flush();Arrays.fill(key,(byte)0);
   DataInputStream in=new DataInputStream(tls.getInputStream());byte[] accepted=new byte[4];in.readFully(accepted);if(!Arrays.equals(accepted,new byte[]{'O','K','0','1'}))throw new IOException("设备未接受局域网连接，请检查屏幕通信模式");
   tls.setSoTimeout(12000);listener.connected(this);
   new Thread(()->{try{while(!closed){byte[] p=writes.poll(3,TimeUnit.SECONDS);if(p==null){p=new byte[20];p[0]=1;p[1]=0x7f;}out.write(p);out.flush();}}catch(Exception e){failure(e);}},"CAN-LAN-write").start();
   while(!closed){byte[] p=new byte[20];in.readFully(p);if(p[0]!=1)throw new IOException("局域网报文格式错误");if(p[1]!=0x7f)listener.packet(this,p);}
  }catch(Exception e){failure(e);}
 },"CAN-LAN-connect").start();}
 boolean send(byte[] p){return !closed&&p.length==20&&writes.offer(p.clone());}
 static String failureMessage(Exception e){
  if(e instanceof SocketTimeoutException)return "局域网连接超时，请检查 Wi-Fi 和设备模式";
  if(e instanceof EOFException)return "设备已断开局域网连接，请检查 Wi-Fi 和设备通信模式后重新连接";
  String detail=e.getMessage();
  return detail==null||detail.trim().isEmpty()?"局域网连接中断，请检查 Wi-Fi 和设备通信模式后重新连接":"局域网连接中断或验证失败："+detail;
 }
 // LAN send() only enqueues a command; writeDone comes from the device ACK.
 static String commandTimeoutMessage(boolean lan){return lan?"局域网指令未在限时内确认，请检查连接后重新连接":"蓝牙指令写入未在限时内完成，请重新连接";}
 private void failure(Exception e){if(closed)return;close();listener.failed(this,failureMessage(e));}
 public synchronized void close(){
  if(closed)return;
  closed=true;writes.clear();
  // Conscrypt close can write close_notify and trip Android's main-thread
  // network guard. Abort pending I/O first, then clean up TLS off the UI thread.
  Socket raw=rawSocket,tls=socket;
  if(raw!=null)try{raw.close();}catch(IOException ignored){}
  if(tls!=null&&tls!=raw)new Thread(()->{try{tls.close();}catch(IOException ignored){}},"CAN-LAN-close").start();
 }
 interface Discovery {void found(String ip,String id);void finished();}
 static void discover(String expectedId,Discovery listener){new Thread(()->{
  try(DatagramSocket s=new DatagramSocket()){s.setBroadcast(true);Set<InetAddress> addresses=new HashSet<>();addresses.add(InetAddress.getByName("255.255.255.255"));
   Enumeration<NetworkInterface> interfaces=NetworkInterface.getNetworkInterfaces();while(interfaces.hasMoreElements())for(InterfaceAddress a:interfaces.nextElement().getInterfaceAddresses())if(a.getBroadcast()!=null)addresses.add(a.getBroadcast());
   probeDiscovery(s,addresses,expectedId,listener,32323);
  }catch(Exception ignored){}finally{listener.finished();}
 },"CAN-LAN-discover").start();}
 static void probeDiscovery(DatagramSocket s,Collection<InetAddress> addresses,String expectedId,Discovery listener,int port)throws IOException{
  byte[] q="CAN_SCOPE_DISCOVER_1".getBytes("US-ASCII");Set<String> seen=new HashSet<>();
  long start=System.nanoTime(),end=start+TimeUnit.SECONDS.toNanos(3),nextProbe=start;int attempts=0;
  while(System.nanoTime()<end){
   long now=System.nanoTime();
   if(attempts<3&&now>=nextProbe){
    // Read-only probes may be lost while Wi-Fi joins. Retry within the same
    // bounded scan; discovery never changes saved trust or sends CAN data.
    for(InetAddress a:addresses)for(int size:new int[]{q.length,q.length-1})try{s.send(new DatagramPacket(q,size,a,port));}catch(IOException ignored){}
    attempts++;nextProbe=start+TimeUnit.SECONDS.toNanos(attempts);
   }
   long waitUntil=attempts<3?Math.min(nextProbe,end):end;
   long remaining=waitUntil-System.nanoTime();if(remaining<=0)continue;
   s.setSoTimeout((int)Math.max(1,Math.min(400,TimeUnit.NANOSECONDS.toMillis(remaining))));
   try{byte[] b=new byte[128];DatagramPacket p=new DatagramPacket(b,b.length);s.receive(p);String[] fields=new String(b,0,p.getLength(),"US-ASCII").split(" ");if(p.getPort()==port&&fields.length==3&&fields[0].equals("CAN_SCOPE_1")&&fields[1].equals(expectedId)&&fields[2].equals("32323")){String ip=p.getAddress().getHostAddress();if(seen.add(ip))listener.found(ip,fields[1]);}}catch(SocketTimeoutException ignored){}
  }
 }

}
