package dev.qianmo.canview;

public class LanSubnetTest {
 static void check(boolean value){if(!value)throw new AssertionError();}
 public static void main(String[] args){
  byte[] local={(byte)192,(byte)168,1,(byte)232};
  check(LanSubnet.contains("192.168.1.221",local,24));
  check(!LanSubnet.contains("192.168.2.221",local,24));
  check(!LanSubnet.contains("8.8.8.8",local,24));
  check(LanSubnet.contains("192.168.1.232",local,32));
  check(!LanSubnet.contains("192.168.1.233",local,32));
  check(LanSubnet.contains("192.168.1.233",local,31));
  check(!LanSubnet.contains("192.168.1.234",local,31));
  check(LanSubnet.contains("192.169.9.2",local,15));
  check(!LanSubnet.contains("192.170.9.2",local,15));
  check(!LanSubnet.contains("192.168.1.221",local,0));
  check(!LanSubnet.contains("192.168.1.221",local,33));
  check(!LanSubnet.contains("192.168.1.221",new byte[16],24));
  check(!LanSubnet.contains("192.168.1.221",null,24));
  System.out.println("LAN subnet: IPv4 prefixes, boundaries and default-route exclusion PASS");
 }
}
