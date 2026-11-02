package dev.qianmo.canview;
import java.util.*;
/** Bounds receipt before UI work, while isolating management events from telemetry. */
final class TelemetryInbox {
 static final class Frame {
  final byte[] data;final int session;final long arrived;
  Frame(byte[] data,int session,long arrived){this.data=data.clone();this.session=session;this.arrived=arrived;}
 }
 private final int capacity,managementCapacity;
 private final ArrayDeque<Frame> data=new ArrayDeque<>(),management=new ArrayDeque<>();
 private int dropped;
 TelemetryInbox(int capacity,int managementCapacity){this.capacity=capacity;this.managementCapacity=managementCapacity;}
 static boolean ordinary(byte[] p){int type=p[1]&255;return type==19||type==1||type==11||((type==2||type==3)&&read32(p,16)==0);}
 private static int read32(byte[] p,int at){return (p[at]&255)|((p[at+1]&255)<<8)|((p[at+2]&255)<<16)|((p[at+3]&255)<<24);}
 synchronized boolean offer(byte[] p,int session,long arrived){
  if(ordinary(p)){if(data.size()>=capacity){data.removeFirst();dropped++;}data.addLast(new Frame(p,session,arrived));return true;}
  // Caller reports a connection failure on overflow instead of silently losing an ACK.
  if(management.size()>=managementCapacity)return false;
  management.addLast(new Frame(p,session,arrived));return true;
 }
 synchronized List<Frame> drain(int limit,boolean includeData){
  ArrayList<Frame> out=new ArrayList<>(limit);while(out.size()<limit&&!management.isEmpty())out.add(management.removeFirst());
  if(includeData)while(out.size()<limit&&!data.isEmpty())out.add(data.removeFirst());return out;
 }
 synchronized boolean pending(boolean includeData){return !management.isEmpty()||(includeData&&!data.isEmpty());}
 synchronized int dropped(){return dropped;}
 synchronized void discardData(){data.removeIf(frame->frame.data[1]!=19);}
 synchronized void clear(){data.clear();management.clear();dropped=0;}
}
