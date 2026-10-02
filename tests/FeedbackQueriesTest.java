package dev.qianmo.canview;
import java.util.*;
public final class FeedbackQueriesTest {
    static void check(boolean b){if(!b)throw new AssertionError();}
    static FeedbackQueries one(){FeedbackQueries q=new FeedbackQueries();check(q.enable("one",0x123,100,0));return q;}
    static FeedbackQueries.Request submit(FeedbackQueries q,long now){FeedbackQueries.Request r=q.poll(now,true);check(r!=null&&MonitorCommands.allowed(r.packet));q.submitted(r,now);return r;}
    static void ack(FeedbackQueries q,FeedbackQueries.Request r,long written,long received){q.written(r,written);FeedbackQueries.Result result=q.complete(r.token,0,received);check(result!=null&&result.enabled&&result.key.equals(r.key));}
    static void busyThenRecovery(){
        FeedbackQueries q=one();FeedbackQueries.Request r=q.poll(0,true);check(q.rejected(r,0)==null);
        check(q.expire(9000)==null);check(q.poll(99,true)==null);check(q.poll(100,true)==r);check(r.attempts==2);
        q.submitted(r,100);check(q.expire(2300)==null);q.written(r,2500);check(q.expire(4499)==null);
        FeedbackQueries.Result result=q.complete(r.token,0,2600);check(result.enabled&&q.keys().contains("one")&&!q.waiting());
    }
    static void boundedBusyAndStopFailure(){
        FeedbackQueries q=one();FeedbackQueries.Request r=q.poll(0,true);check(q.rejected(r,0)==null);
        check(q.poll(100,true)==r);check(q.rejected(r,100)==null);check(q.poll(300,true)==r);
        FeedbackQueries.Failure f=q.rejected(r,300);check(f.kind==FeedbackQueries.Failure.SUBMISSION&&f.keys.contains("one"));
        check(q.keys().isEmpty()&&!q.canEnable(1000));
        FeedbackQueries.Request stop=q.poll(320,true);check(stop.packet[1]==0x15);check(q.rejected(stop,320)==null);
        check(q.poll(420,true)==stop);check(q.rejected(stop,420)==null);check(q.poll(620,true)==stop);
        check(q.rejected(stop,620).kind==FeedbackQueries.Failure.STOP_UNCONFIRMED);check(stop.attempts==FeedbackQueries.SUBMIT_LIMIT);
        check(q.poll(10000,true)==null);check(q.recovered(3300).contains("one"));check(q.canEnable(3300));check(q.enable("one",0x123,100,3300));
    }
    static void writeAndAckStagesAndLostStopAck(){
        FeedbackQueries q=one();FeedbackQueries.Request r=submit(q,0);
        check(q.expire(2000)==null&&q.expire(5900)==null);q.written(r,5900);
        check(q.expire(7899)==null);FeedbackQueries.Failure f=q.expire(7900);
        check(f.kind==FeedbackQueries.Failure.CONFIRMATION&&f.keys.contains("one")&&q.keys().isEmpty());
        check(!q.enable("one",0x123,100,8000));
        FeedbackQueries.Request stop=submit(q,7920);check(stop.packet[1]==0x15);q.written(stop,7950);
        check(q.expire(9949)==null);check(q.expire(9950).kind==FeedbackQueries.Failure.STOP_UNCONFIRMED);
        check(q.poll(10000,true)==null);check(!q.canEnable(10899));check(q.recovered(10900).contains("one"));check(q.canEnable(10900));
        check(q.complete(r.token,0,10900)==null&&q.complete(stop.token,1,10900)==null);
        check(q.poll(50000,true)==null); // Never recreate stop-all after its ACK is lost.
    }
    static void earlyAckAndDelayedUi(){
        FeedbackQueries q=one();FeedbackQueries.Request r=submit(q,0);
        check(q.complete(r.token,0,20).enabled);q.written(r,30);check(!q.waiting()&&q.expire(9000)==null);
        FeedbackQueries.Request renewal=submit(q,1000);q.written(renewal,1010);
        // The control inbox drains an ACK received at 1100 before an expiry check
        // at 5000, even while the independent UI queue is stalled.
        check(q.complete(renewal.token,0,1100).enabled);check(q.expire(5000)==null);
        q.stopAll(5100);FeedbackQueries.Request stop=submit(q,5120);
        check(q.complete(stop.token,1,5140)==null);check(q.canEnable(5140));q.written(stop,5150);
        check(q.expire(10000)==null&&q.recovered(5150).contains("one"));
    }
    static void disabledEditedAndLateAck(){
        FeedbackQueries q=one();FeedbackQueries.Request r=submit(q,0);q.disable("one");
        check(q.complete(r.token,0,20)==null);check(q.keys().isEmpty());
        FeedbackQueries.Request stop=submit(q,30);check(stop.packet[8]==0);
        check(q.enable("one",0x123,200,40)); // A newly enabled entry has a new identity.
        check(q.complete(stop.token,1,50)==null); // Old stop ACK cannot hide the new query.
        FeedbackQueries.Request newer=submit(q,60);check((newer.packet[8]&255)==200);
        ack(q,newer,70,80);check(q.keys().contains("one"));
        q.disable("one");check(q.enable("edited",0x123,300,90));
        stop=submit(q,100);q.written(stop,110);check(!q.complete(stop.token,1,120).enabled);
        newer=submit(q,130);check(newer.key.equals("edited"));ack(q,newer,140,150);
        q.disable("edited");FeedbackQueries.Request unsent=q.poll(160,true);q.disable("edited");
        check(q.keys().isEmpty());q.submitted(unsent,160);q.written(unsent,170);q.complete(unsent.token,1,180);
    }
    static void terminalArrivalOrderAndSessions(){
        FeedbackQueries q=one();FeedbackQueries.Request r=submit(q,0);
        check(!q.periodic(0x123,4).enabled);check(q.complete(r.token,0,30)==null);check(q.keys().isEmpty());
        q.reset();check(q.enable("one",0x123,100,40));FeedbackQueries.Request newer=submit(q,40);
        check(newer.token!=r.token);check(q.complete(r.token,0,50)==null&&q.waiting());
        check(q.complete(newer.token,0,60).enabled);check(!q.periodic(0x123,5).enabled);check(q.periodic(0x123,0)==null);
        q.reset();check(q.poll(1000,true)==null); // No redundant stop on a new connection.
    }
    static void lateGlobalStopSummaries(){
        FeedbackQueries q=one();FeedbackQueries.Request old=submit(q,0);ack(q,old,10,20);
        q.stopAll(30);FeedbackQueries.Request stop=submit(q,40);q.written(stop,50);q.complete(stop.token,1,60);
        check(q.canEnable(60));check(q.enable("one",0x123,100,60));FeedbackQueries.Request newer=submit(q,70);
        check(q.periodic(0x123,1)==null&&q.periodic(0x123,2)==null&&q.periodic(0x123,3)==null);
        check(q.periodic(0x123,0)==null); // Old progress cannot confirm a new enable.
        check(q.keys().contains("one")&&q.waiting());ack(q,newer,80,90);
        check(q.periodic(0x123,1)==null&&q.keys().contains("one"));
        check(!q.recovered(100).contains("one")); // Deferred recovery UI cannot hide the reopened entry.
        check(q.periodic(0x123,0).enabled);check(!q.periodic(0x123,4).enabled&&q.keys().isEmpty());
    }
    static void fourChannelsAndLimits(){
        FeedbackQueries[] channels=new FeedbackQueries[4];FeedbackQueries.Request[] requests=new FeedbackQueries.Request[4];
        for(int i=0;i<4;i++){channels[i]=one();requests[i]=submit(channels[i],0);channels[i].written(requests[i],10);}
        check(channels[0].expire(2010)!=null&&channels[0].keys().isEmpty());
        for(int i=1;i<4;i++)check(channels[i].complete(requests[i].token,0,100).enabled&&channels[i].keys().contains("one"));
        FeedbackQueries q=new FeedbackQueries();check(!q.enable("bad",0,100,0)&&!q.enable("bad",1,0,0)&&!q.enable("bad",1,1001,0));
        for(int i=1;i<=32;i++)check(q.enable("motor"+i,i,1,0));check(!q.enable("extra",33,1,0));check(!q.enable("duplicate",1,2,0));
        Set<String> seen=new HashSet<>();for(int i=0;i<32;i++){FeedbackQueries.Request r=submit(q,i*20);seen.add(r.key);ack(q,r,i*20+2,i*20+3);}check(seen.size()==32);
        FeedbackQueries.Request r=submit(q,1000);check(q.complete(r.token+1,0,1002)==null&&q.waiting());
        byte[] p=r.packet.clone();p[4]=0;check(!MonitorCommands.allowed(p));p=r.packet.clone();p[8]=(byte)0xe9;p[9]=3;check(!MonitorCommands.allowed(p));
        p=r.packet.clone();p[2]=2;check(!MonitorCommands.allowed(p));p=r.packet.clone();p[12]=1;check(!MonitorCommands.allowed(p));
    }
    public static void main(String[] args){busyThenRecovery();boundedBusyAndStopFailure();writeAndAckStagesAndLostStopAck();earlyAckAndDelayedUi();disabledEditedAndLateAck();terminalArrivalOrderAndSessions();lateGlobalStopSummaries();fourChannelsAndLimits();System.out.println("PASS: bounded GATT retries, distinct write/ACK deadlines, early ACK/UI stall, finite stop recovery, entry/session isolation, edits/disable, 4 channels and 32-motor validation.");}
}
