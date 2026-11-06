package dev.qianmo.canview;
import java.io.*;
import java.util.*;
/** Stream framing regression; generated data only, no physical CAN or TLS timing. */
public class LanPacketReadTest {
 static void check(boolean b){if(!b)throw new AssertionError();}
 static class Chunked extends ByteArrayInputStream {
  int calls,limit;
  Chunked(byte[] bytes,int limit){super(bytes);this.limit=limit;}
  public synchronized int read(byte[] b,int off,int len){calls++;return super.read(b,off,Math.min(len,limit));}
 }
 static byte[] wire(int count){byte[] bytes=new byte[count*20];for(int i=0;i<count;i++){int at=i*20;bytes[at]=1;bytes[at+1]=(byte)(i%17==0?0x7f:1);for(int j=2;j<20;j++)bytes[at+j]=(byte)(i*29+j);}return bytes;}
 public static void main(String[] args)throws Exception{
  final int total=100000;byte[] input=wire(total);List<byte[]> reference=new ArrayList<>();
  for(int i=0;i<total;i++)if(input[i*20+1]!=0x7f)reference.add(Arrays.copyOfRange(input,i*20,i*20+20));
  for(int size:new int[]{1,7,19,20,21,63,127,1280,4096}){
   List<byte[]> actual=new ArrayList<>();LanTransport t=new LanTransport(new LanTransport.Listener(){public void connected(LanTransport s){}public void packet(LanTransport s,byte[] b){actual.add(b);}public void failed(LanTransport s,String m){throw new AssertionError(m);}});Chunked stream=new Chunked(input,size);
   try{t.readPackets(stream);throw new AssertionError("EOF missing");}catch(EOFException expected){}
   check(actual.size()==reference.size());for(int i=0;i<actual.size();i++)check(Arrays.equals(actual.get(i),reference.get(i)));t.close();
   Chunked old=new Chunked(input,size);DataInputStream baseline=new DataInputStream(old);try{while(true){byte[] b=new byte[20];baseline.readFully(b);}}catch(EOFException expected){}
   System.out.println("chunk="+size+" frames="+actual.size()+" read_calls="+old.calls+"->"+stream.calls);
  }
  List<byte[]> stopped=new ArrayList<>();LanTransport t=new LanTransport(new LanTransport.Listener(){public void connected(LanTransport s){}public void packet(LanTransport s,byte[] b){stopped.add(b);s.close();}public void failed(LanTransport s,String m){}});t.readPackets(new ByteArrayInputStream(input));check(stopped.size()==1);
  for(int tail:new int[]{0,1,7,19}){
   List<byte[]> frames=new ArrayList<>();LanTransport partial=new LanTransport(new LanTransport.Listener(){public void connected(LanTransport s){}public void packet(LanTransport s,byte[] b){frames.add(b);}public void failed(LanTransport s,String m){}});
   byte[] bytes=Arrays.copyOfRange(input,20,40+tail);
   try{partial.readPackets(new ByteArrayInputStream(bytes));throw new AssertionError("EOF missing");}catch(EOFException expected){}
   check(frames.size()==1&&Arrays.equals(frames.get(0),Arrays.copyOfRange(input,20,40)));partial.close();
  }
  List<byte[]> prefix=new ArrayList<>();LanTransport invalid=new LanTransport(new LanTransport.Listener(){public void connected(LanTransport s){}public void packet(LanTransport s,byte[] b){prefix.add(b);}public void failed(LanTransport s,String m){}});
  byte[] corrupted=Arrays.copyOfRange(input,20,100);corrupted[20]=2;
  try{invalid.readPackets(new ByteArrayInputStream(corrupted));throw new AssertionError("Malformed header accepted");}catch(IOException expected){check(expected.getMessage().equals("局域网报文格式错误"));}
  check(prefix.size()==1);invalid.close();
  System.out.println("Exact fragmented/coalesced packet identities, heartbeat filtering, EOF and callback-close PASS");
 }
}
