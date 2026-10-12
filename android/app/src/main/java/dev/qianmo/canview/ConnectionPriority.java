package dev.qianmo.canview;
/** Immediate throughput upgrades, delayed downgrades, bounded request retries. */
final class ConnectionPriority {
 private boolean high;private int applied=-1;private long downgradeAt,retryAt;
 void want(boolean value,long now){if(value==high)return;high=value;downgradeAt=value?0:now+500;retryAt=0;}
 int poll(long now,boolean ready){int desired=high?1:0;if(!ready||(!high&&now<downgradeAt)||desired==applied||now<retryAt)return -1;retryAt=now+1000;return desired;}
 void result(int requested,boolean accepted){if(accepted)applied=requested;}
 long delay(long now){if(applied==(high?1:0))return -1;return Math.max(0,Math.max(retryAt,high?0:downgradeAt)-now);}
 void reset(){high=false;applied=-1;downgradeAt=retryAt=0;}
}
