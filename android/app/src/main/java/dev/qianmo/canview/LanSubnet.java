package dev.qianmo.canview;

/** Match an IPv4 literal against a directly attached IPv4 address and prefix. */
final class LanSubnet {
 private LanSubnet(){}
 static boolean contains(String ip,byte[] local,int prefix){
  if(local==null||local.length!=4||prefix<1||prefix>32)return false;
  String[] parts=LanTransport.validateIp(ip).split("\\.");
  for(int i=0;i<4;i++){
   int bits=Math.min(8,Math.max(0,prefix-i*8));
   int mask=(0xff<<(8-bits))&0xff;
   if((Integer.parseInt(parts[i])&mask)!=(local[i]&mask))return false;
  }
  return true;
 }
}
