package dev.qianmo.canview;
import java.util.*;
/** Device-side query timers, with bounded submission retries and separate transport/ACK stages. */
final class FeedbackQueries {
    static final int SUBMIT_LIMIT=3, ACK_MS=2000, LEASE_MS=3000;
    static final class Entry { final String key; final int id,hz; long due; boolean confirmed; Entry(String k,int i,int h){key=k;id=i;hz=h;} }
    static final class Request {
        static final int AVAILABLE=0,OFFERED=1,WRITING=2,ACK=3;
        final Entry entry; final String key; final int token,epoch; final byte[] packet;
        int stage=AVAILABLE,attempts; long retryAt,deadline;
        Request(Entry e,int t,int epoch){entry=e;key=e.key;token=t;this.epoch=epoch;packet=new byte[20];packet[0]=1;packet[1]=(byte)(e.id==0?0x15:0x14);put(packet,4,e.id);put(packet,8,e.hz);put(packet,16,t);}
        static void put(byte[] p,int offset,int value){for(int j=0;j<4;j++)p[offset+j]=(byte)(value>>>(8*j));}
    }
    static final class Result { final String key; final boolean enabled; Result(String k,boolean on){key=k;enabled=on;} }
    static final class Failure {
        static final int SUBMISSION=1,CONFIRMATION=2,STOP_UNCONFIRMED=3;
        final int kind; final Set<String> keys;
        Failure(int k,Set<String> affected){kind=k;keys=affected;}
    }
    private final LinkedHashMap<String,Entry> entries=new LinkedHashMap<>();
    private final ArrayDeque<Entry> stops=new ArrayDeque<>();
    private final LinkedHashSet<String> recoveryKeys=new LinkedHashSet<>(),recovered=new LinkedHashSet<>();
    private Request pending; private long next,holdUntil,lastDelivery; private int token=0x40000000,epoch;
    synchronized boolean canEnable(long now){return now>=holdUntil&&!globalStopPending();}
    synchronized boolean enable(String key,int id,int hz,long now){
        if(!canEnable(now)||key==null||key.length()>2048||id<1||id>0x7fe||hz<1||hz>1000)return false;
        for(Entry e:entries.values())if(e.id==id&&!e.key.equals(key))return false;
        if(!entries.containsKey(key)&&entries.size()>=32)return false;
        entries.put(key,new Entry(key,id,hz));return true;
    }
    synchronized void disable(String key){
        Entry e=entries.remove(key);if(e==null)return;
        if(pending!=null&&pending.entry==e&&pending.stage<=Request.OFFERED)pending=null;
        stops.add(new Entry(key,e.id,0));
    }
    synchronized Set<String> keys(){return new LinkedHashSet<>(entries.keySet());}
    synchronized void reset(){epoch++;entries.clear();stops.clear();pending=null;next=holdUntil=lastDelivery=0;recoveryKeys.clear();recovered.clear();}
    synchronized Set<String> stopAll(long now){
        LinkedHashSet<String> affected=new LinkedHashSet<>(entries.keySet());
        if(pending!=null&&!pending.key.isEmpty())affected.add(pending.key);
        recoveryKeys.addAll(affected);entries.clear();stops.clear();
        if(pending==null||pending.entry.id!=0){pending=null;stops.add(new Entry("",0,0));}
        holdUntil=Math.max(holdUntil,Math.max(now,lastDelivery)+LEASE_MS);next=0;return affected;
    }
    private boolean globalStopPending(){if(pending!=null&&pending.entry.id==0)return true;for(Entry e:stops)if(e.id==0)return true;return false;}
    synchronized Request poll(long now,boolean transportReady){
        if(!transportReady||now<next)return null;
        if(pending==null){
            Entry chosen=stops.pollFirst();
            if(chosen==null)for(Entry e:entries.values())if(e.due<=now&&(chosen==null||e.due<chosen.due))chosen=e;
            if(chosen==null)return null;
            if(token==0x7ffffffe)token=0x40000000;pending=new Request(chosen,++token,epoch);
        }
        if(pending.stage!=Request.AVAILABLE||now<pending.retryAt)return null;
        pending.stage=Request.OFFERED;pending.attempts++;next=now+10;return pending;
    }
    synchronized void submitted(Request request,long now){
        if(pending!=request)return;pending.stage=Request.WRITING;pending.entry.due=now+1000;
        // No application-ACK deadline exists while Android is still writing.
        pending.deadline=0;
    }
    synchronized Failure rejected(Request request,long now){
        if(pending!=request)return null;
        if(request.attempts<SUBMIT_LIMIT){request.stage=Request.AVAILABLE;request.retryAt=now+(100L<<(request.attempts-1));return null;}
        if(request.entry.id==0){pending=null;return new Failure(Failure.STOP_UNCONFIRMED,new LinkedHashSet<>(recoveryKeys));}
        Set<String> affected=stopAll(now);return new Failure(Failure.SUBMISSION,affected);
    }
    synchronized void written(Request request,long now){
        if(request==null||request.epoch!=epoch)return;
        if(request.entry.hz>0){lastDelivery=Math.max(lastDelivery,now);if(holdUntil>0)holdUntil=Math.max(holdUntil,now+LEASE_MS);}
        if(pending==request&&request.stage==Request.WRITING){request.stage=Request.ACK;request.deadline=now+ACK_MS;}
    }
    synchronized Result complete(int ackToken,int reason,long arrived){
        if(pending==null||pending.token!=ackToken||pending.stage<Request.WRITING)return null;
        Request request=pending;pending=null;
        if(request.entry.id==0){
            if(reason<=1){holdUntil=0;recovered.addAll(recoveryKeys);recoveryKeys.clear();}
            return null;
        }
        if(request.entry.hz==0)return entries.containsKey(request.key)?null:new Result(request.key,false);
        if(entries.get(request.key)!=request.entry)return null;
        if(reason!=0){entries.remove(request.key);stops.add(new Entry(request.key,request.entry.id,0));return new Result(request.key,false);}
        request.entry.confirmed=true;lastDelivery=Math.max(lastDelivery,arrived);return new Result(request.key,true);
    }
    synchronized Result periodic(int id,int reason){
        // Token-zero stopped summaries can follow a higher-priority stop ACK
        // after the user has reopened a motor. Only runtime errors/lease expiry
        // are terminal here; validation results belong to their command token.
        if(reason!=0&&reason!=4&&reason!=5)return null;
        Entry entry=null;for(Entry e:entries.values())if(e.id==id){entry=e;break;}if(entry==null)return null;
        if(reason==0)return entry.confirmed?new Result(entry.key,true):null;
        // A terminal device event wins over a delayed ACK for the cancelled request.
        entries.remove(entry.key);if(pending!=null&&pending.entry==entry)pending=null;
        stops.add(new Entry(entry.key,entry.id,0));return new Result(entry.key,false);
    }
    synchronized Failure expire(long now){
        if(pending==null||pending.stage!=Request.ACK||now<pending.deadline)return null;
        if(pending.entry.id==0){pending=null;return new Failure(Failure.STOP_UNCONFIRMED,new LinkedHashSet<>(recoveryKeys));}
        Set<String> affected=stopAll(now);return new Failure(Failure.CONFIRMATION,affected);
    }
    synchronized Set<String> recovered(long now){
        if(holdUntil>0&&now>=holdUntil&&!globalStopPending()){holdUntil=0;recovered.addAll(recoveryKeys);recoveryKeys.clear();}
        Set<String> result=new LinkedHashSet<>(recovered);result.removeAll(entries.keySet());recovered.clear();return result;
    }
    synchronized boolean waiting(){return pending!=null;}
}
