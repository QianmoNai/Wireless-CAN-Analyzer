package dev.qianmo.canview;
import java.util.*;
/** Serial GATT subscription transaction with revision-matched online bitmap. */
final class HomeTelemetry {
 private int[][] desired=new int[0][],sent=new int[0][];
 private int mode=1,sentMode,target,sentTarget,hz,sentHz;private int revision=1000,index;private boolean dirty=true,awaiting;private long deadline;
 void configure(boolean q,int[][] rows){configure(q?1:0,0,rows);}
 void configure(int m,int t,int[][] rows){configure(m,t,rows,0);}
 void configure(int m,int t,int[][] rows,int rate){if(m==mode&&t==target&&rate==hz&&Arrays.deepEquals(rows,desired))return;mode=m;target=t;hz=rate;desired=rows;dirty=true;}
 void reset(){dirty=true;awaiting=false;index=0;}
 byte[] poll(long now,boolean ready){
  if(!ready)return null;
  if(dirty)awaiting=false;
  if(awaiting){if(now<deadline)return null;awaiting=false;dirty=true;}
  if(dirty){sent=desired;sentMode=mode;sentTarget=target;sentHz=hz;revision=revision==Integer.MAX_VALUE?1000:revision+1;index=0;dirty=false;}
  if(index>sent.length)return null;
  byte[] p=new byte[20];p[0]=1;FeedbackQueries.Request.put(p,16,revision);
  if(index<sent.length){int[] m=sent[index];p[1]=0x16;p[2]=(byte)index;p[3]=(byte)m[0];FeedbackQueries.Request.put(p,4,m[1]);FeedbackQueries.Request.put(p,8,m[2]);index++;}
  else{p[1]=0x17;p[2]=(byte)sentMode;p[3]=(byte)sent.length;FeedbackQueries.Request.put(p,4,sentMode==2?sentTarget:0);FeedbackQueries.Request.put(p,8,sentMode==2?sentHz:0);index++;awaiting=true;deadline=now+2000;}
  return p;
 }
 boolean accept(int rev){return rev==revision&&!dirty&&index>sent.length;}
 void acknowledge(int rev){if(accept(rev))awaiting=false;}
 void failed(){reset();}
 int revision(){return revision;}
 boolean quiet(){return sentMode==1;}
 /** Decode only the current quiet-mode registry; null values mean no fresh feedback. */
 Integer[] temperatures(byte[] p){
  if(p==null||p.length!=20||p[0]!=1||p[1]!=16||sentMode!=1||!accept(java.nio.ByteBuffer.wrap(p).order(java.nio.ByteOrder.LITTLE_ENDIAN).getInt(16)))return null;
  int start=p[2]&255,count=p[3]&255;
  if(count!=sent.length||count==0||count>32||start>=count||start%10!=0)return null;
  int length=Math.min(10,count-start),fresh=(p[4]&255)|((p[5]&255)<<8);
  if((fresh>>length)!=0)return null;
  Integer[] result=new Integer[length];
  for(int i=0;i<length;i++)if((fresh&(1<<i))!=0&&sent[start+i][0]!=2)result[i]=p[6+i]&255;
  return result;
 }
}
