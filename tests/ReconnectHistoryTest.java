package dev.qianmo.canview;

import java.util.Arrays;
import java.util.List;

public class ReconnectHistoryTest {
    static final String A="AA:BB:CC:DD:EE:01",B="AA:BB:CC:DD:EE:02",C="AA:BB:CC:DD:EE:03";
    static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);}
    static ReconnectHistory.Channel channel(int slot,String success,String active,boolean ready,boolean busy,boolean loaded){return new ReconnectHistory.Channel(slot,success,active,ready,busy,loaded,false);}
    static void history(){
        ReconnectHistory h=new ReconnectHistory("","");
        // Ordinary selected/imported metadata is never proof of subscription.
        h.begin(A);check(!h.known(),"selection must not create reconnect history");h.failed();
        check(!h.subscribed(A,"imported"),"a failed/stale attempt cannot confirm");
        h.begin(A);check(h.subscribed(A,"Board A"),"valid subscription seeds history");
        h.begin(B);h.failed();check(h.address().equals(A)&&h.name().equals("Board A"),"failure must preserve prior proven device");
        h.begin(B);h.begin(C);check(!h.subscribed(B,"stale"),"old subscription cannot seed current attempt");
        check(h.subscribed(C,"Board C"),"latest successful subscription replaces history");
        check(!h.renamed(A,"Wrong board"),"another device cannot rename successful history");
        check(h.renamed(C.toLowerCase(),"New name")&&h.name().equals("New name"),"same MAC rename preserves reconnect address");
        ReconnectHistory restarted=new ReconnectHistory(h.address().toLowerCase(),h.name());
        check(restarted.known()&&restarted.address().equals(C)&&restarted.name().equals("New name"),"confirmed record survives reload");
        restarted.clear();check(!restarted.known()&&!restarted.subscribed(C,"late"),"remove/import clears success and in-flight confirmation");
        check(!new ReconnectHistory("invalid","legacy").known(),"invalid saved address cannot connect");
        check(!new ReconnectHistory("", "legacy attempt").known(),"display name is not proof of past success");
    }
    static void portableHistory(){
        ReconnectHistory restored=ReconnectHistory.imported(A.toLowerCase(),"备份设备 A");
        check(restored.known()&&restored.address().equals(A)&&restored.name().equals("备份设备 A"),"explicit portable success record restores normalized reconnect target");
        // Metadata/failed new selections may refer to B while the saved target is A.
        restored.begin(B);restored.failed();check(restored.address().equals(A),"a restored success record is independent of later failed attempt metadata");
        List<ReconnectHistory.Choice> plan=ReconnectHistory.plan(Arrays.asList(channel(1,restored.address(),"",false,false,true),channel(2,"","",false,false,true)));
        check(plan.get(0).kind==ReconnectHistory.CONNECT&&plan.get(1).kind==ReconnectHistory.NO_HISTORY,"new file target is reconnectable; old file absence requires manual first connection");
        restored.begin(C);check(restored.subscribed(C,"新的设备 C"),"successful connection replaces imported target");
        ReconnectHistory restarted=new ReconnectHistory(restored.address(),restored.name());
        check(restarted.address().equals(C)&&restarted.name().equals("新的设备 C"),"restart uses latest committed success, not original imported A");
        check(ReconnectHistory.imported(A,"").name().equals("ESP32-CAN"),"portable empty name uses normal display fallback");
        String[] badAddresses={"", "not-a-mac", "AA:BB:CC:DD:EE", null};
        for(String bad:badAddresses){boolean rejected=false;try{ReconnectHistory.imported(bad,"Device");}catch(IllegalArgumentException e){rejected=true;}check(rejected,"invalid portable address rejected");}
        StringBuilder tooLong=new StringBuilder();for(int i=0;i<65;i++)tooLong.append('x');
        String[] badNames={null,"bad\nname","bad\u0000name","bad\u007fname",tooLong.toString()};
        for(String bad:badNames){boolean rejected=false;try{ReconnectHistory.imported(A,bad);}catch(IllegalArgumentException e){rejected=true;}check(rejected,"invalid portable name rejected");}
    }
    static void selection(){
        List<ReconnectHistory.Channel> channels=Arrays.asList(channel(1,B,A,true,true,true),channel(2,A,"",false,false,true),channel(3,C.toLowerCase(),"",false,false,true),channel(4,C,"",false,false,true));
        List<ReconnectHistory.Choice> plan=ReconnectHistory.plan(channels);
        check(plan.get(0).kind==ReconnectHistory.READY,"ready channel is preserved even when last success differs");
        check(plan.get(1).kind==ReconnectHistory.DUPLICATE&&plan.get(1).owner==1,"active GATT reserves physical board");
        check(plan.get(2).kind==ReconnectHistory.CONNECT,"eligible independent device connects");
        check(plan.get(3).kind==ReconnectHistory.DUPLICATE&&plan.get(3).owner==3,"batch reserves candidates before creating GATT");
        check(ReconnectHistory.claimingSlot(channels,A.toLowerCase(),2)==1,"manual connect shares normalized duplicate guard");
        check(ReconnectHistory.claimingSlot(channels,A,1)==0,"owner excluded from duplicate check");
        channels=Arrays.asList(channel(1,A,A,false,true,true),channel(2,B,"",false,true,true),channel(3,"","",false,false,true),channel(4,C,"",false,false,false));
        plan=ReconnectHistory.plan(channels);
        check(plan.get(0).kind==ReconnectHistory.ACTIVE&&plan.get(1).kind==ReconnectHistory.ACTIVE,"connecting/scanning channels skipped without restart");
        check(plan.get(2).kind==ReconnectHistory.NO_HISTORY,"legacy metadata requires manual first success");
        check(plan.get(3).kind==ReconnectHistory.LOADING,"unready WebView cannot start connection");
        channels=Arrays.asList(channel(1,A,"",false,false,true),channel(2,B,"",false,false,true),channel(3,C,"",false,false,true),new ReconnectHistory.Channel(4,A,A,false,true,true,true));
        plan=ReconnectHistory.plan(channels);
        check(plan.get(0).kind==0&&plan.get(1).kind==0&&plan.get(2).kind==0&&plan.get(3).kind==ReconnectHistory.CLOSED,"three eligible channels independent; removed GATT cannot reserve address");
    }
    static void lifecycle(){
        ReconnectHistory.Gate gate=new ReconnectHistory.Gate();
        check(!gate.begin(),"background requests ignored");gate.foreground();check(gate.begin(),"foreground starts request");
        check(!gate.begin(),"repeated button cannot duplicate pending operation");gate.background();check(!gate.pending()&&!gate.canStart(),"real background cancels unprompted request");
        gate.foreground();check(gate.begin(),"new request after return");gate.prompt(ReconnectHistory.Gate.PERMISSION);gate.background();
        check(gate.pending()&&!gate.canStart(),"own permission prompt may stop activity but never connects in background");
        check(gate.result(ReconnectHistory.Gate.PERMISSION,true)&&!gate.canStart(),"accepted result while stopped must wait for foreground");
        gate.foreground();check(gate.canStart(),"permission continuation resumes only in foreground");gate.prompt(ReconnectHistory.Gate.ENABLE);gate.background();
        check(gate.result(ReconnectHistory.Gate.ENABLE,true)&&!gate.canStart(),"own enable prompt continuation still waits for foreground");
        gate.foreground();check(gate.canStart(),"Bluetooth enabled resumes batch");gate.finish();check(!gate.pending(),"launch releases permission request");
        check(gate.begin(),"later request starts");gate.prompt(ReconnectHistory.Gate.PERMISSION);gate.cancel();
        check(!gate.begin(),"outstanding canceled prompt cannot share callback code with new request");
        check(!gate.result(ReconnectHistory.Gate.PERMISSION,true)&&!gate.canStart(),"late granted result cannot revive canceled request");
        check(gate.begin(),"new request allowed after old result consumed");gate.prompt(ReconnectHistory.Gate.ENABLE);
        check(!gate.result(ReconnectHistory.Gate.ENABLE,false)&&!gate.pending(),"enable cancellation terminates without retries");
        check(!gate.result(ReconnectHistory.Gate.ENABLE,true),"duplicate late result does not reopen finished request");
    }
    public static void main(String[] args){history();portableHistory();selection();lifecycle();System.out.println("PASS: proven/portable history, failed/replaced attempts, strict imported records, names/restart/removal, four-channel reservations, permission/enable continuation and background/late-result cancellation.");}
}
