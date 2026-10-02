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
}
