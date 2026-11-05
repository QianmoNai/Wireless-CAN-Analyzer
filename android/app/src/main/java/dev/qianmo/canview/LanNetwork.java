package dev.qianmo.canview;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import java.io.IOException;
import java.net.Socket;

/** Route only a directly attached device socket over physical Wi-Fi. */
final class LanNetwork {
 private LanNetwork(){}
 static Socket socket(Context context,String ip)throws IOException{
  LanTransport.validateIp(ip);
  ConnectivityManager manager=context.getSystemService(ConnectivityManager.class);
  Network selected=null;int longest=0;
  if(manager!=null)for(Network network:manager.getAllNetworks()){
   NetworkCapabilities capabilities=manager.getNetworkCapabilities(network);
   LinkProperties properties=manager.getLinkProperties(network);
   if(capabilities==null||properties==null||!capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)||!capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN))continue;
   for(LinkAddress address:properties.getLinkAddresses()){
    int prefix=address.getPrefixLength();
    if(prefix>longest&&LanSubnet.contains(ip,address.getAddress().getAddress(),prefix)){selected=network;longest=prefix;}
   }
  }
  Socket socket=new Socket();
  try{if(selected!=null)selected.bindSocket(socket);return socket;}
  catch(IOException|RuntimeException failure){try{socket.close();}catch(IOException ignored){}throw failure;}
 }
}
