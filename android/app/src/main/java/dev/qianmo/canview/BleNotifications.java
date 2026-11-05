package dev.qianmo.canview;
import java.util.Arrays;
import java.util.UUID;
import java.util.function.Consumer;

/** Optional v1 batching, with whole-value validation before any dispatch. */
final class BleNotifications {
 static final UUID LEGACY=UUID.fromString("c0de0003-1212-efde-1523-785feabcd123");
 static final UUID BATCH=UUID.fromString("c0de0006-1212-efde-1523-785feabcd123");
 static UUID select(boolean available){return available?BATCH:LEGACY;}
 static boolean dispatch(byte[] value,boolean batched,Consumer<byte[]> receiver){
  if(value==null||value.length==0||value.length>(batched?240:20)||value.length%20!=0)return false;
  for(int offset=0;offset<value.length;offset+=20)if(value[offset]!=1)return false;
  if(value.length==20)receiver.accept(value);
  else for(int offset=0;offset<value.length;offset+=20)receiver.accept(Arrays.copyOfRange(value,offset,offset+20));
  return true;
 }
}
