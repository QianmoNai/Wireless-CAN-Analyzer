package dev.qianmo.canview;
/** Event 8 diagnostics; reason 0 remains active during bounded retries. */
final class PollingStatus {
    static String detail(int flags) {
        if((flags&64)!=0)return "CAN 总线离线（Bus-off）";
        if((flags&2)!=0)return "未收到 CAN ACK";
        if((flags&4)!=0)return "CAN 位错误";
        if((flags&8)!=0)return "CAN 帧格式错误";
        if((flags&16)!=0)return "CAN 位填充错误";
        if((flags&32)!=0)return "CAN 发送队列或驱动忙";
        if(flags==1)return "";
        return "CAN 发送错误（原因未知）";
    }
    static String message(int reason,int flags,int hz,int actual) {
        String rate="设定 "+hz+" Hz · 实际查询 "+actual+" Hz";
        if(reason==0)return (flags==0||flags==1)?rate:detail(flags)+"，正在重试 · "+rate;
        if(reason==4){
            if((flags&64)!=0)return detail(flags)+"，查询已停止；检查接线和波特率";
            if(flags==1)return "总线持续繁忙，查询已停止";
            // Byte 3 was reserved/zero on old firmware; do not claim five attempts there.
            return detail(flags)+(flags==0?"，查询已停止":"；连续通信失败 5 次，查询已停止");
        }
        return reason==2?"超过查询带宽预算，请降低频率后重试":reason==5?"查询租约已过期，已停止":reason==1?"查询已关闭":"频率配置无效，已停止";
    }
}
