package dev.qianmo.canview;
import android.bluetooth.*;
import android.os.*;
import org.json.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
/** Serial GATT configuration operation; ordinary CAN writes are paused while active. */
final class WifiProvisioner {
 interface Listener {void status(JSONObject value);void networks(JSONArray rows);void finished(String message);}
 final BluetoothGatt gatt;final BluetoothGattCharacteristic characteristic;
 private final Handler handler;private final Listener listener;
 private final ArrayDeque<byte[]> commands=new ArrayDeque<>();
 private final JSONArray networks=new JSONArray();
 private final int operation;private boolean active=true,waitingRead,bondRequested;private long deadline;private int page;
 WifiProvisioner(BluetoothGatt g,BluetoothGattCharacteristic c,Handler h,Listener l,int op,String ssid,String password){
  if(op!=1&&op!=2&&op!=5&&op!=6)throw new IllegalArgumentException("未知配网操作");
  gatt=g;characteristic=c;handler=h;listener=l;operation=op;
  if(op==5){byte[] name=ssid.getBytes(StandardCharsets.UTF_8),secret=password.getBytes(StandardCharsets.UTF_8);if(name.length<1||name.length>32||ssid.indexOf('\0')>=0||secret.length>64||(secret.length>0&&secret.length<8)||!password.matches("[\\x20-\\x7e]*")||(secret.length==64&&!password.matches("[0-9a-fA-F]{64}")))throw new IllegalArgumentException("Wi-Fi 名称须为 1–32 字节，密码为 8–63 个英文字符，或 64 位十六进制密钥；开放网络留空");
   int transaction=(int)(System.nanoTime()&255);byte[] begin=packet(3);begin[1]=(byte)transaction;begin[2]=(byte)name.length;begin[3]=(byte)secret.length;commands.add(begin);
   byte[] all=new byte[name.length+secret.length];System.arraycopy(name,0,all,0,name.length);System.arraycopy(secret,0,all,name.length,secret.length);
   for(int offset=0;offset<all.length;offset+=16){byte[] p=packet(4);p[1]=(byte)transaction;p[2]=(byte)offset;p[3]=(byte)Math.min(16,all.length-offset);System.arraycopy(all,offset,p,4,p[3]);commands.add(p);}byte[] apply=packet(5);apply[1]=(byte)transaction;commands.add(apply);Arrays.fill(all,(byte)0);Arrays.fill(secret,(byte)0);
  }else commands.add(packet(op));
 }
 boolean active(){return active;}
 private static byte[] packet(int op){byte[] p=new byte[20];p[0]=(byte)op;return p;}
 void start(){deadline=SystemClock.elapsedRealtime()+65000;handler.post(watchdog);awaitBond();}
 private final Runnable watchdog=new Runnable(){public void run(){if(!active)return;if(SystemClock.elapsedRealtime()>deadline){finish("操作超时，请确认手机配对及设备屏幕状态");return;}handler.postDelayed(this,500);}};
 private void awaitBond(){
  if(!active)return;try{BluetoothDevice d=gatt.getDevice();int state=d.getBondState();if(state==BluetoothDevice.BOND_BONDED){deadline=SystemClock.elapsedRealtime()+35000;writeNext();return;}if(state==BluetoothDevice.BOND_NONE){if(bondRequested){finish("配对已取消，请重试并输入屏幕上的配对码");return;}bondRequested=true;if(!d.createBond()){finish("无法发起配对，请在手机设置检查蓝牙");return;}}handler.postDelayed(this::awaitBond,500);}catch(SecurityException e){finish("缺少蓝牙连接权限");}
 }
 private void writeNext(){if(!active)return;byte[] p=commands.peek();if(p==null){read();return;}try{boolean ok;if(Build.VERSION.SDK_INT>=33)ok=gatt.writeCharacteristic(characteristic,p,BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT)==BluetoothStatusCodes.SUCCESS;else{characteristic.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);characteristic.setValue(p);ok=gatt.writeCharacteristic(characteristic);}if(!ok)finish("蓝牙繁忙，请重试 Wi-Fi 配置");}catch(Exception e){finish("无法发送 Wi-Fi 配置");}}
 void written(int status){if(!active)return;if(status!=0){finish("设备拒绝配网请求（"+status+"），请确认已完成屏幕配对");return;}byte[] p=commands.poll();if(p!=null)Arrays.fill(p,(byte)0);writeNext();}
 private void read(){try{waitingRead=true;if(!gatt.readCharacteristic(characteristic))finish("读取网络状态失败，请重试");}catch(Exception e){finish("读取网络状态失败");}}
 void readResult(int status,byte[] value){
  if(!active||!waitingRead)return;waitingRead=false;if(status!=0){finish("配网需要安全配对，请在手机输入屏幕上的六位配对码");return;}
  try{JSONObject data=new JSONObject(new String(value,StandardCharsets.UTF_8));
   if(data.has("scan")){JSONArray rows=data.getJSONArray("scan");for(int i=0;i<rows.length();i++)networks.put(rows.get(i));page+=rows.length();if(page<data.optInt("total")&&rows.length()>0){byte[] p=packet(7);p[2]=(byte)page;commands.add(p);writeNext();}else{listener.networks(networks);finish("扫描完成，请选择 Wi-Fi");}return;}
   listener.status(data);
   if(data.optBoolean("busy")){handler.postDelayed(()->{if(active){commands.add(packet(1));writeNext();}},700);return;}
   if(operation==2&&data.optInt("error")==0){commands.add(packet(7));writeNext();return;}
   int error=data.optInt("error");String[] errors={"","","Wi-Fi 认证失败，请检查密码","未找到该 Wi-Fi","连接失败或未取得 IP，请检查网络","配置保存失败，原配置已保留","扫描失败，请稍后重试"};
   finish(error==0?(operation==5?"Wi-Fi 已连接并保存；可在设备屏幕切换到 Wi-Fi 模式":operation==6?"Wi-Fi 配置已清除":"网络状态已更新"):error<errors.length?errors[error]:"网络初始化失败，请重启设备");
  }catch(Exception e){finish("设备网络状态格式错误，请更新固件");}
 }
 void cancel(){active=false;for(byte[] p:commands)Arrays.fill(p,(byte)0);commands.clear();handler.removeCallbacks(watchdog);}
 private void finish(String message){if(!active)return;cancel();listener.finished(message);}
}
