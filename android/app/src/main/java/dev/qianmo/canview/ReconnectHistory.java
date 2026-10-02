package dev.qianmo.canview;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** Proven subscriptions and explicitly restored history stay separate from device metadata. */
final class ReconnectHistory {
    private String address,name,pending="";
    ReconnectHistory(String savedAddress,String savedName){address=normalize(savedAddress);name=cleanName(savedName);}
    static String normalize(String value){return value!=null&&value.matches("(?i)[0-9a-f]{2}(:[0-9a-f]{2}){5}")?value.toUpperCase(Locale.ROOT):"";}
    static ReconnectHistory imported(String address,String name){
        if(normalize(address).isEmpty()||name==null||name.length()>64||name.matches("(?s).*[\\x00-\\x1f\\x7f].*"))throw new IllegalArgumentException("上次成功连接设备记录格式错误");
        return new ReconnectHistory(address,name);
    }
    private static String cleanName(String value){return value==null||value.isEmpty()?"ESP32-CAN":value;}
    synchronized String address(){return address;}
    synchronized String name(){return name;}
    synchronized boolean known(){return !address.isEmpty();}
    synchronized void begin(String attemptedAddress){pending=normalize(attemptedAddress);}
    synchronized void failed(){pending="";}
    synchronized boolean subscribed(String connectedAddress,String connectedName){
        String confirmed=normalize(connectedAddress);
        if(confirmed.isEmpty()||!confirmed.equals(pending))return false;
        address=confirmed;name=cleanName(connectedName);pending="";return true;
    }
    synchronized boolean renamed(String connectedAddress,String connectedName){
        if(address.isEmpty()||!address.equals(normalize(connectedAddress)))return false;
        name=cleanName(connectedName);return true;
    }
    synchronized void clear(){address="";name="ESP32-CAN";pending="";}

    /** A system prompt may stop the Activity; it never permits a background start. */
    static final class Gate {
        static final int NONE=0,PERMISSION=1,ENABLE=2;
        private boolean foreground,pending;
        private int prompt;
        void foreground(){foreground=true;}
        void background(){foreground=false;if(prompt==NONE)cancel();}
        boolean begin(){if(!foreground||pending||prompt!=NONE)return false;pending=true;return true;}
        boolean pending(){return pending;}
        boolean prompting(){return prompt!=NONE;}
        boolean waiting(int kind){return prompt==kind&&kind!=NONE;}
        boolean canStart(){return foreground&&pending&&prompt==NONE;}
        void prompt(int kind){if(!canStart()||kind==NONE)throw new IllegalStateException();prompt=kind;}
        boolean result(int kind,boolean accepted){if(prompt!=kind)return false;prompt=NONE;if(!accepted)cancel();return pending&&accepted;}
        // Keep an outstanding result occupied after cancellation so it cannot
        // accidentally resume a new request that reuses the Android request code.
        void cancel(){pending=false;}
        void finish(){pending=false;}
    }

    static final int CONNECT=0,READY=1,ACTIVE=2,LOADING=3,NO_HISTORY=4,DUPLICATE=5,CLOSED=6;
    static final class Channel {
        final int slot;final String successAddress,activeAddress;
        final boolean ready,active,loaded,closed;
        Channel(int slot,String successAddress,String activeAddress,boolean ready,boolean active,boolean loaded,boolean closed){
            this.slot=slot;this.successAddress=normalize(successAddress);this.activeAddress=normalize(activeAddress);
            this.ready=ready;this.active=active;this.loaded=loaded;this.closed=closed;
        }
    }
    static final class Choice {
        final Channel channel;final int kind,owner;
        Choice(Channel channel,int kind,int owner){this.channel=channel;this.kind=kind;this.owner=owner;}
    }
    static int claimingSlot(List<Channel> channels,String candidate,int exceptSlot){
        String address=normalize(candidate);if(address.isEmpty())return 0;
        for(Channel c:channels)if(c.slot!=exceptSlot&&!c.closed&&!c.activeAddress.isEmpty()&&address.equals(c.activeAddress))return c.slot;
        return 0;
    }
    static List<Choice> plan(List<Channel> channels){
        Map<String,Integer> reserved=new HashMap<>();
        for(Channel c:channels)if(!c.closed&&!c.activeAddress.isEmpty())reserved.put(c.activeAddress,c.slot);
        List<Choice> result=new ArrayList<>();
        for(Channel c:channels){
            int kind=c.closed?CLOSED:c.ready?READY:c.active?ACTIVE:!c.loaded?LOADING:c.successAddress.isEmpty()?NO_HISTORY:reserved.containsKey(c.successAddress)?DUPLICATE:CONNECT;
            int owner=kind==DUPLICATE?reserved.get(c.successAddress):0;
            if(kind==CONNECT)reserved.put(c.successAddress,c.slot);
            result.add(new Choice(c,kind,owner));
        }
        return result;
    }
}
