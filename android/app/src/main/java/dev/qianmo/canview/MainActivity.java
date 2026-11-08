package dev.qianmo.canview;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.bluetooth.*;
import android.bluetooth.le.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.content.pm.ActivityInfo;
import android.content.res.Configuration;
import android.graphics.Color;
import android.location.LocationManager;
import android.os.*;
import android.provider.Settings;
import android.webkit.*;
import android.view.*;
import android.view.inputmethod.InputMethodManager;
import android.widget.*;
import org.json.*;
import java.util.*;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.atomic.AtomicBoolean;
import java.io.*;
import java.nio.charset.StandardCharsets;
import android.net.Uri;

/** Each channel owns an independent GATT, write queue, callbacks and WebView state. */
public class MainActivity extends Activity {
    private static final int MAX_CHANNELS = 4;
    private final ArrayList<DevicePanel> panels = new ArrayList<>();
    private DevicePanel selected, permissionPanel,manualResumePanel;
    private LinearLayout tabs,rootLayout;
    private HorizontalScrollView channelBar;
    private DevicePanel chartPanel;
    private String chartMotorKey="";
    private boolean chartFullscreen,previousFullscreenFlag,previousActionBarShowing;
    private int chartSession,previousOrientation,previousUiFlags,previousCutoutMode,previousBarBehavior,previousVisibleBars,previousChannelVisibility;
    private Object modernBackCallback;
    // Isolate Android 13 platform types so API 26–32 do not resolve them.
    private static final class ModernBack {
        static Object register(Activity activity,Runnable action){android.window.OnBackInvokedCallback callback=action::run;activity.getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,callback);return callback;}
        static void unregister(Activity activity,Object callback){activity.getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback((android.window.OnBackInvokedCallback)callback);}
    }
    private FrameLayout content;
    private Button add;
    private WebView home;
    private int backNavigationRequest;
    private boolean homeReady;
    private boolean homeUpdatePending;
    private final Runnable homeUpdate=()->{homeUpdatePending=false;updateHome();};
    private void scheduleHomeUpdate(){if(!homeReady||selected!=null||homeUpdatePending)return;homeUpdatePending=true;ui.postDelayed(homeUpdate,75);}
    private volatile boolean configBusy;
    private boolean restoring;
    private String exportText, configMessage="";
    private final Handler ui=new Handler(Looper.getMainLooper());
    private int configGeneration;
    private volatile int connectionEpoch;
    private boolean foreground,reconnectLaunching,manualPermissionWaiting,manualEnableWaiting;
    private final ReconnectHistory.Gate reconnectGate=new ReconnectHistory.Gate();
    private final Set<DevicePanel> reconnectAttempts=new HashSet<>();
    private final LinkedHashMap<Integer,String> reconnectResults=new LinkedHashMap<>();
    private String reconnectMessage="";
    private int reconnectSucceeded,reconnectFailed;
    private static final int RECONNECT_PERMISSION=12,RECONNECT_ENABLE=13;
    private static final int EXPORT_CONFIG=20,IMPORT_CONFIG=21,MAX_CONFIG_BYTES=1048576;
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().setStatusBarColor(Color.rgb(10,15,25));
        getWindow().setNavigationBarColor(Color.rgb(10,15,25));
        rootLayout=new LinearLayout(this);rootLayout.setOrientation(LinearLayout.VERTICAL);
        rootLayout.setBackgroundColor(Color.rgb(10,15,25));
        rootLayout.setOnApplyWindowInsetsListener((view,insets)->{
            if(Build.VERSION.SDK_INT>=30){
                int types=chartFullscreen?WindowInsets.Type.displayCutout():WindowInsets.Type.systemBars()|WindowInsets.Type.displayCutout();
                android.graphics.Insets edges=insets.getInsets(types);view.setPadding(edges.left,edges.top,edges.right,edges.bottom);
            }else if(chartFullscreen){
                DisplayCutout cutout=Build.VERSION.SDK_INT>=28?insets.getDisplayCutout():null;
                view.setPadding(cutout==null?0:cutout.getSafeInsetLeft(),cutout==null?0:cutout.getSafeInsetTop(),cutout==null?0:cutout.getSafeInsetRight(),cutout==null?0:cutout.getSafeInsetBottom());
            }else view.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets();
        });
        channelBar=new HorizontalScrollView(this);
        tabs=new LinearLayout(this);tabs.setOrientation(LinearLayout.HORIZONTAL);
        channelBar.addView(tabs);rootLayout.addView(channelBar,new LinearLayout.LayoutParams(-1,-2));
        content=new FrameLayout(this);rootLayout.addView(content,new LinearLayout.LayoutParams(-1,0,1));
        setContentView(rootLayout);
        if(Build.VERSION.SDK_INT>=33)modernBackCallback=ModernBack.register(this,()->onBackPressed());
        createHome();
        String slots=getPreferences(MODE_PRIVATE).getString("channels","1");
        for(String part:slots.split(","))try{int slot=Integer.parseInt(part);if(slot>=1&&slot<=MAX_CHANNELS&&panels.stream().noneMatch(p->p.slot==slot))createChannel(slot);}catch(NumberFormatException ignored){}
        if(panels.isEmpty())createChannel(1);
        showHome();
    }
    private void addChannel() {
        if(configBusy){Toast.makeText(this,"请等待配置操作结束",Toast.LENGTH_SHORT).show();return;}
        if (panels.size() >= MAX_CHANNELS) return;
        int slot=1;
        while (true) { boolean used=false; for(DevicePanel p:panels) if(p.slot==slot) used=true; if(!used)break; slot++; }
        DevicePanel panel=createChannel(slot);saveChannels();select(panel);
    }
    private DevicePanel createChannel(int slot) {
        DevicePanel panel=new DevicePanel(slot);panels.add(panel);panels.sort(Comparator.comparingInt(p->p.slot));
        content.addView(panel.web,new FrameLayout.LayoutParams(-1,-1));panel.web.setVisibility(View.GONE);return panel;
    }
    private void saveChannels(){
        StringJoiner slots=new StringJoiner(",");for(DevicePanel p:panels)slots.add(String.valueOf(p.slot));
        getPreferences(MODE_PRIVATE).edit().putString("channels",slots.toString()).apply();
    }
    private void createHome(){
        home=new WebView(this);home.setBackgroundColor(Color.rgb(10,15,25));
        home.getSettings().setJavaScriptEnabled(true);home.getSettings().setDomStorageEnabled(true);home.getSettings().setAllowFileAccess(false);home.getSettings().setAllowContentAccess(false);
        home.addJavascriptInterface(new HomeBridge(),"HomeAndroid");
        home.setWebViewClient(new WebViewClient(){
            @Override public WebResourceResponse shouldInterceptRequest(WebView v,WebResourceRequest r){
                String url=r.getUrl().toString();String asset=r.getUrl().getLastPathSegment();
                if(url.equals("https://home.local/home.html")||url.equals("https://home.local/home.js")||url.equals("https://home.local/config_io.js")||url.equals("https://home.local/motors.js"))try{return new WebResourceResponse(asset.endsWith(".js")?"application/javascript":"text/html","UTF-8",getAssets().open(asset));}catch(Exception ignored){}
                return new WebResourceResponse("text/plain","UTF-8",403,"Blocked",null,null);
            }
            @Override public boolean shouldOverrideUrlLoading(WebView v,WebResourceRequest r){return true;}
            @Override public void onPageFinished(WebView v,String url){homeReady=true;updateHome();configNotice(configMessage,configBusy);}
        });
        content.addView(home,new FrameLayout.LayoutParams(-1,-1));home.loadUrl("https://home.local/home.html");
    }
    @Override public void onBackPressed(){
        int request=++backNavigationRequest;
        if(chartFullscreen){exitChartFullscreen(true);return;}
        DevicePanel panel=selected;
        if(panel!=null){
            if(panel.closed||!panel.pageReady){showHome();return;}
            int session=panel.generation;
            panel.web.evaluateJavascript("typeof consumeChannelBack==='function'&&consumeChannelBack()",result->{
                if(request!=backNavigationRequest||selected!=panel||panel.closed||panel.generation!=session||!foreground)return;
                if(!"true".equals(result))showHome();
            });
            return;
        }
        if(Build.VERSION.SDK_INT>=33)finish();else super.onBackPressed();
    }
    @Override public void onWindowFocusChanged(boolean focused){super.onWindowFocusChanged(focused);if(focused&&chartFullscreen){if(chartOwnerCurrent())applyChartImmersive();else exitChartFullscreen(true);}}
    @Override public void onConfigurationChanged(Configuration configuration){super.onConfigurationChanged(configuration);if(chartFullscreen){if(chartOwnerCurrent())applyChartImmersive();else exitChartFullscreen(true);}if(rootLayout!=null)rootLayout.requestApplyInsets();}
    private boolean chartOwnerCurrent(){return chartPanel!=null&&!chartPanel.closed&&selected==chartPanel&&chartSession==chartPanel.generation&&!chartMotorKey.isEmpty()&&chartMotorKey.equals(chartPanel.detailKey);}
    private void enterChartFullscreen(DevicePanel panel,int session,String motorKey){
        if(panel.closed||!panel.pageReady||selected!=panel||motorKey.isEmpty()||!motorKey.equals(panel.detailKey)||session!=panel.generation)return;
        if(chartFullscreen){if(chartPanel==panel&&chartMotorKey.equals(motorKey)&&chartSession==session){applyChartImmersive();return;}exitChartFullscreen(true);}
        Window window=getWindow();View decor=window.getDecorView();
        previousOrientation=getRequestedOrientation();previousUiFlags=decor.getSystemUiVisibility();
        previousFullscreenFlag=(window.getAttributes().flags&WindowManager.LayoutParams.FLAG_FULLSCREEN)!=0;
        previousChannelVisibility=channelBar.getVisibility();previousActionBarShowing=getActionBar()!=null&&getActionBar().isShowing();
        previousBarBehavior=-1;previousVisibleBars=0;
        if(Build.VERSION.SDK_INT>=30){
            WindowInsetsController controller=window.getInsetsController();if(controller!=null)previousBarBehavior=controller.getSystemBarsBehavior();
            WindowInsets insets=decor.getRootWindowInsets();int[] types={WindowInsets.Type.statusBars(),WindowInsets.Type.navigationBars(),WindowInsets.Type.captionBar()};
            for(int type:types)if(insets==null||insets.isVisible(type))previousVisibleBars|=type;
        }
        if(Build.VERSION.SDK_INT>=28){WindowManager.LayoutParams attributes=window.getAttributes();previousCutoutMode=attributes.layoutInDisplayCutoutMode;attributes.layoutInDisplayCutoutMode=Build.VERSION.SDK_INT>=30?WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS:WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;window.setAttributes(attributes);}
        chartPanel=panel;chartMotorKey=motorKey;chartSession=session;chartFullscreen=true;
        channelBar.setVisibility(View.GONE);if(getActionBar()!=null)getActionBar().hide();
        View focused=getCurrentFocus();if(focused!=null)focused.clearFocus();
        InputMethodManager input=(InputMethodManager)getSystemService(INPUT_METHOD_SERVICE);if(input!=null)input.hideSoftInputFromWindow(decor.getWindowToken(),0);
        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE);applyChartImmersive();rootLayout.requestApplyInsets();
    }
    private void applyChartImmersive(){
        if(!chartFullscreen)return;
        Window window=getWindow();window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN);
        if(Build.VERSION.SDK_INT>=30){WindowInsetsController controller=window.getInsetsController();if(controller!=null){controller.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);controller.hide(WindowInsets.Type.systemBars()|WindowInsets.Type.ime());}}
        else window.getDecorView().setSystemUiVisibility(previousUiFlags|View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY|View.SYSTEM_UI_FLAG_FULLSCREEN|View.SYSTEM_UI_FLAG_HIDE_NAVIGATION|View.SYSTEM_UI_FLAG_LAYOUT_STABLE|View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN|View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
    }
    private void exitChartFullscreen(boolean closePage){
        if(!chartFullscreen)return;
        DevicePanel owner=chartPanel;chartFullscreen=false;chartPanel=null;chartMotorKey="";
        if(closePage&&owner!=null&&!owner.closed&&owner.pageReady)owner.web.evaluateJavascript("window.closeDetailChartFullscreen&&closeDetailChartFullscreen(false)",null);
        Window window=getWindow();
        if(previousFullscreenFlag)window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN);else window.clearFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN);
        if(Build.VERSION.SDK_INT>=28){WindowManager.LayoutParams attributes=window.getAttributes();attributes.layoutInDisplayCutoutMode=previousCutoutMode;window.setAttributes(attributes);}
        if(Build.VERSION.SDK_INT>=30){WindowInsetsController controller=window.getInsetsController();if(controller!=null){if(previousBarBehavior>=0)controller.setSystemBarsBehavior(previousBarBehavior);int hidden=WindowInsets.Type.systemBars()&~previousVisibleBars;if(previousVisibleBars!=0)controller.show(previousVisibleBars);if(hidden!=0)controller.hide(hidden);}}
        else window.getDecorView().setSystemUiVisibility(previousUiFlags);
        if(getActionBar()!=null&&previousActionBarShowing)getActionBar().show();
        channelBar.setVisibility(previousChannelVisibility);setRequestedOrientation(previousOrientation);rootLayout.requestApplyInsets();
    }
    private void closeChartForDisconnectedPanel(DevicePanel panel){
        if(Looper.myLooper()!=Looper.getMainLooper()){ui.post(()->closeChartForDisconnectedPanel(panel));return;}
        if(chartFullscreen&&chartPanel==panel&&(panel.closed||chartSession!=panel.generation))exitChartFullscreen(true);
    }
    private void showHome(){backNavigationRequest++;exitChartFullscreen(true);selected=null;for(DevicePanel p:panels){p.detailKey="";p.web.evaluateJavascript("window.closeMotorDetail&&closeMotorDetail(false)",null);p.web.setVisibility(View.GONE);p.configureTelemetry();}home.setVisibility(View.VISIBLE);renderTabs();updateHome();}
    private void updateHome(){
        ui.removeCallbacks(homeUpdate);homeUpdatePending=false;
        if(!homeReady||selected!=null)return;
        JSONArray channels=new JSONArray();
        for(DevicePanel p:panels)try{
            JSONObject data=new JSONObject();data.put("slot",p.slot);data.put("name",p.deviceName);data.put("address",p.address);data.put("connected",p.ready);data.put("connecting",(p.gatt!=null||p.lanConnecting)&&!p.ready);data.put("canReconnect",(p.connectionMode.equals("wifi")?!p.networkPin.isEmpty()&&!p.networkIp.isEmpty():p.history.known())&&!p.ready&&p.gatt==null&&!p.lanConnecting&&!p.scanning&&p.pageReady);data.put("connectionMessage",p.connectionMessage);data.put("label",p.label);data.put("motors",p.snapshot);data.put("imuSupported",p.imuSupported);data.put("age",SystemClock.elapsedRealtime()-p.snapshotTime);channels.put(data);
        }catch(JSONException ignored){}
        home.evaluateJavascript("window.updateHome("+channels.toString()+")",null);
        publishReconnectStatus();
    }
    private class HomeBridge {
        @JavascriptInterface public void exportConfig(){runOnUiThread(()->beginExport());}
        @JavascriptInterface public void importConfig(){runOnUiThread(()->beginImport());}
        @JavascriptInterface public void addChannel(){runOnUiThread(()->MainActivity.this.addChannel());}
        @JavascriptInterface public void connectAll(){int epoch=connectionEpoch;runOnUiThread(()->{if(epoch!=connectionEpoch){publishReconnectStatus();return;}connectAllChannels();});}
        @JavascriptInterface public void openChannel(int slot){runOnUiThread(()->{for(DevicePanel p:panels)if(p.slot==slot){p.detailKey="";select(p);p.web.evaluateJavascript("showTab('motor');$('rosterPanel').scrollIntoView()",null);break;}});}
        @JavascriptInterface public void deleteMotor(int slot,String key){runOnUiThread(()->{
            if(configBusy){Toast.makeText(MainActivity.this,"请等待配置操作结束",Toast.LENGTH_SHORT).show();return;}
            for(DevicePanel p:panels)if(p.slot==slot){
                JSONObject motor=null;
                for(int i=0;i<p.snapshot.length();i++){JSONObject candidate=p.snapshot.optJSONObject(i);if(candidate!=null&&key.equals(candidate.optString("key"))){motor=candidate;break;}}
                if(motor==null)return;
                int epoch=connectionEpoch;
                new AlertDialog.Builder(MainActivity.this).setTitle("删除电机？")
                    .setMessage("通道 "+slot+" · "+motor.optString("name")+"\n电机 ID "+motor.optString("id")+" · 反馈 "+motor.optString("feedbackId")+"\n\n删除此电机的监控配置并停止持续查询。不会发送电机停机或失能指令。")
                    .setNegativeButton("取消",null).setPositiveButton("删除",(dialog,which)->{
                        if(configBusy||epoch!=connectionEpoch||!panels.contains(p)){Toast.makeText(MainActivity.this,"通道配置已变化，请重试",Toast.LENGTH_SHORT).show();return;}
                        p.web.evaluateJavascript("window.dashboardDelete("+JSONObject.quote(key)+")",result->{
                            String message="删除未完成，请重试";
                            try{message=new JSONArray("["+result+"]").getString(0);}catch(JSONException ignored){}
                            Toast.makeText(MainActivity.this,message,Toast.LENGTH_SHORT).show();
                        });
                    }).show();break;
            }
        });}
        @JavascriptInterface public void editMotor(int slot,String key){runOnUiThread(()->{
            if(configBusy){Toast.makeText(MainActivity.this,"请等待配置操作结束",Toast.LENGTH_SHORT).show();return;}
            for(DevicePanel p:panels)if(p.slot==slot){p.detailKey="";select(p);p.web.evaluateJavascript("window.dashboardEdit("+JSONObject.quote(key)+")",null);break;}
        });}
        @JavascriptInterface public void openMotor(int slot,String key){runOnUiThread(()->{for(DevicePanel p:panels)if(p.slot==slot){p.detailKey=key;select(p);p.web.evaluateJavascript("window.dashboardOpen("+JSONObject.quote(key)+")",null);break;}});}
    }
    private void select(DevicePanel panel) {
        backNavigationRequest++;
        if(chartFullscreen&&(chartPanel!=panel||!chartMotorKey.equals(panel.detailKey)||chartSession!=panel.generation))exitChartFullscreen(true);
        selected=panel;home.setVisibility(View.GONE);
        for(DevicePanel p:panels){p.web.setVisibility(p==panel?View.VISIBLE:View.GONE);p.configureTelemetry();}
        renderTabs();
    }
    private void renderTabs() {
        if(tabs==null)return;
        if(chartFullscreen)channelBar.setVisibility(View.GONE);
        tabs.setVisibility(selected!=null&&!selected.detailKey.isEmpty()?View.GONE:View.VISIBLE);
        tabs.removeAllViews();
        Button overview=new Button(this);overview.setText("首页");overview.setTextColor(selected==null?Color.rgb(101,230,203):Color.LTGRAY);overview.setOnClickListener(v->showHome());tabs.addView(overview);
        for(DevicePanel p:panels) {
            Button tab=new Button(this);
            String suffix=p.address.isEmpty()?"": " · " + p.address.substring(Math.max(0,p.address.length()-5));
            tab.setText("通道 "+p.slot+suffix+"\n"+p.label);
            tab.setTextSize(11);tab.setAllCaps(false);
            tab.setTextColor(p==selected?Color.rgb(101,230,203):Color.LTGRAY);
            tab.setOnClickListener(v->select(p));
            tab.setOnLongClickListener(v->{
                if(configBusy){Toast.makeText(this,"请等待配置操作结束",Toast.LENGTH_SHORT).show();return true;}
                new AlertDialog.Builder(this).setTitle("关闭通道 "+p.slot).setMessage("断开该通道的设备并清除本次监控记录？其他通道继续连接。")
                    .setPositiveButton("关闭",(d,w)->{
                        if(permissionPanel==p)permissionPanel=null;
                        if(manualResumePanel==p)manualResumePanel=null;
                        finishReconnectAttempt(p,false,"通道已移除");p.clearReconnectHistory();
                        p.shutdown();content.removeView(p.web);panels.remove(p);
                        if(panels.isEmpty())createChannel(1);saveChannels();showHome();
                    }).setNegativeButton("取消",null).show();return true;
            });
            tabs.addView(tab,new LinearLayout.LayoutParams(-2,-2));
        }
        add=new Button(this);add.setText("＋ 添加设备");add.setAllCaps(false);add.setTextSize(12);
        add.setEnabled(panels.size()<MAX_CHANNELS);add.setOnClickListener(v->addChannel());tabs.addView(add);updateHome();
    }
    private boolean reconnectBusy(){return reconnectGate.pending()||reconnectLaunching||!reconnectAttempts.isEmpty();}
    private void publishReconnectStatus(){
        if(!homeReady)return;
        try{JSONObject status=new JSONObject().put("busy",reconnectBusy()).put("message",reconnectMessage);home.evaluateJavascript("window.reconnectStatus&&window.reconnectStatus("+status+")",null);}catch(JSONException ignored){}
    }
    private List<ReconnectHistory.Channel> reconnectChannels(){
        List<ReconnectHistory.Channel> result=new ArrayList<>();
        for(DevicePanel p:panels)result.add(new ReconnectHistory.Channel(p.slot,p.connectionMode.equals("wifi")?(!p.networkPin.isEmpty()&&!p.networkIp.isEmpty()?p.networkAddress:""):p.history.address(),p.gatt==null&&p.lan==null?"":p.address,p.ready,p.gatt!=null||p.lanConnecting||p.scanning,p.pageReady,p.closed));
        return result;
    }
    private boolean connectionPermission(){return Build.VERSION.SDK_INT<31||checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)==PackageManager.PERMISSION_GRANTED;}
    private BluetoothAdapter bluetoothAdapter(){BluetoothManager manager=(BluetoothManager)getSystemService(BLUETOOTH_SERVICE);return manager==null?null:manager.getAdapter();}
    private void connectAllChannels(){
        if(!foreground||selected!=null){reconnectMessage="请在首页连接设备";publishReconnectStatus();return;}
        if(configBusy){reconnectMessage="配置处理中，请稍后连接";publishReconnectStatus();return;}
        if(reconnectBusy()){publishReconnectStatus();return;}
        if(permissionPanel!=null||manualResumePanel!=null||manualPermissionWaiting||manualEnableWaiting||reconnectGate.prompting()){reconnectMessage="请先完成当前的蓝牙权限操作";publishReconnectStatus();return;}
        reconnectResults.clear();reconnectSucceeded=reconnectFailed=0;
        boolean eligible=false;
        for(ReconnectHistory.Choice choice:ReconnectHistory.plan(reconnectChannels()))if(choice.kind==ReconnectHistory.CONNECT)eligible=true;
        if(!eligible){summarizeReconnectPlan();return;}
        if(!reconnectGate.begin()){publishReconnectStatus();return;}
        reconnectMessage="正在准备连接各通道上次成功连接的设备…";publishReconnectStatus();resumeReconnect();
    }
    private void resumeReconnect(){
        if(!reconnectGate.canStart())return;
        if(configBusy||selected!=null){cancelReconnect("连接已取消，请返回首页后重试");return;}
        boolean needsBle=false;
        for(ReconnectHistory.Choice choice:ReconnectHistory.plan(reconnectChannels()))if(choice.kind==ReconnectHistory.CONNECT)
            for(DevicePanel p:panels)if(p.slot==choice.channel.slot&&p.connectionMode.equals("ble"))needsBle=true;
        BluetoothAdapter adapter=null;
        if(needsBle){
            if(!connectionPermission()){
                reconnectGate.prompt(ReconnectHistory.Gate.PERMISSION);reconnectMessage="请允许附近设备权限，以连接已保存的设备";publishReconnectStatus();
                try{requestPermissions(new String[]{Manifest.permission.BLUETOOTH_CONNECT},RECONNECT_PERMISSION);}catch(Exception e){reconnectGate.result(ReconnectHistory.Gate.PERMISSION,false);cancelReconnect("无法申请蓝牙权限，请在系统设置允许");}return;
            }
            adapter=bluetoothAdapter();if(adapter==null){cancelReconnect("这台手机不支持蓝牙");return;}
            try{if(!adapter.isEnabled()){
                reconnectGate.prompt(ReconnectHistory.Gate.ENABLE);reconnectMessage="请开启蓝牙，开启后将继续连接";publishReconnectStatus();
                try{startActivityForResult(new Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE),RECONNECT_ENABLE);}catch(Exception e){reconnectGate.result(ReconnectHistory.Gate.ENABLE,false);cancelReconnect("无法开启蓝牙，请在系统设置开启后重试");}return;
            }}catch(SecurityException e){cancelReconnect("蓝牙权限已失效，请重新授权后连接");return;}
        }
        reconnectLaunching=true;reconnectGate.finish();
        for(ReconnectHistory.Choice choice:ReconnectHistory.plan(reconnectChannels())){
            DevicePanel p=null;for(DevicePanel candidate:panels)if(candidate.slot==choice.channel.slot){p=candidate;break;}
            if(p==null)continue;
            if(choice.kind!=ReconnectHistory.CONNECT){recordReconnectSkip(p,choice);continue;}
            if(p.connectionMode.equals("wifi")){p.connectLan(p.networkIp);if(p.lanConnecting)reconnectAttempts.add(p);else{reconnectFailed++;reconnectResults.put(p.slot,"局域网连接未发起，请检查配网信息");}continue;}
            try{BluetoothDevice device=adapter.getRemoteDevice(choice.channel.successAddress);p.startConnection(device,choice.channel.successAddress,p.history.name(),true);}
            catch(Exception e){reconnectFailed++;p.connectionMessage="无法连接保存的设备："+e.getMessage();reconnectResults.put(p.slot,p.connectionMessage);}
        }
        reconnectLaunching=false;finishReconnectSummary();updateHome();
    }
    private void summarizeReconnectPlan(){
        for(ReconnectHistory.Choice choice:ReconnectHistory.plan(reconnectChannels()))for(DevicePanel p:panels)if(p.slot==choice.channel.slot)recordReconnectSkip(p,choice);
        finishReconnectSummary();updateHome();
    }
    private void recordReconnectSkip(DevicePanel p,ReconnectHistory.Choice choice){
        String reason=choice.kind==ReconnectHistory.READY?"已连接":choice.kind==ReconnectHistory.ACTIVE?"正在连接或扫描，已跳过":choice.kind==ReconnectHistory.LOADING?"页面载入中，请稍后重试":choice.kind==ReconnectHistory.DUPLICATE?"设备已由通道 "+choice.owner+" 连接，本通道已跳过":choice.kind==ReconnectHistory.NO_HISTORY?"无成功连接记录，请手动连接一次":"通道已关闭";
        reconnectResults.put(p.slot,reason);
        // A batch skip must not reset an existing connection or its query UI.
        if(choice.kind!=ReconnectHistory.READY&&choice.kind!=ReconnectHistory.ACTIVE)p.connectionMessage=reason;
    }
    private void finishReconnectAttempt(DevicePanel p,boolean success,String reason){
        if(!reconnectAttempts.remove(p))return;
        if(success)reconnectSucceeded++;else reconnectFailed++;
        reconnectResults.put(p.slot,reason);finishReconnectSummary();publishReconnectStatus();
    }
    private void finishReconnectSummary(){
        StringBuilder result=new StringBuilder();
        if(!reconnectAttempts.isEmpty())result.append("正在连接 ").append(reconnectAttempts.size()).append(" 个通道…");
        else result.append("连接完成：成功 ").append(reconnectSucceeded).append("，失败 ").append(reconnectFailed);
        for(Map.Entry<Integer,String> row:reconnectResults.entrySet())result.append("；通道 ").append(row.getKey()).append("：").append(row.getValue());
        reconnectMessage=result.toString();publishReconnectStatus();
    }
    private void cancelReconnect(String reason){reconnectGate.cancel();reconnectLaunching=false;reconnectAttempts.clear();reconnectMessage=reason;publishReconnectStatus();}
    @Override protected void onStart(){super.onStart();foreground=true;reconnectGate.foreground();}
    @Override protected void onResume(){super.onResume();if(foreground){DevicePanel p=manualResumePanel;manualResumePanel=null;if(p!=null&&panels.contains(p)&&!p.closed)p.beginScan();resumeReconnect();}}
    @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] results){
        super.onRequestPermissionsResult(request,permissions,results);
        if(request==RECONNECT_PERMISSION){if(!reconnectGate.waiting(ReconnectHistory.Gate.PERMISSION)){publishReconnectStatus();return;}boolean accepted=connectionPermission();if(reconnectGate.result(ReconnectHistory.Gate.PERMISSION,accepted)){if(foreground)resumeReconnect();}else cancelReconnect(accepted?"连接请求已取消，请点击连接重试":"未获得蓝牙连接权限，请在系统设置允许后重试");return;}
        if(request!=10)return;
        manualPermissionWaiting=false;
        DevicePanel p=permissionPanel;permissionPanel=null;
        if(p!=null&&panels.contains(p)){if(p.permissions()){if(foreground)p.beginScan();else manualResumePanel=p;}else p.error("需要附近设备权限，请在系统设置允许");}
    }
    @Override protected void onActivityResult(int request,int result,Intent data){
        super.onActivityResult(request,result,data);
        if(request==RECONNECT_ENABLE){if(!reconnectGate.waiting(ReconnectHistory.Gate.ENABLE)){publishReconnectStatus();return;}if(reconnectGate.result(ReconnectHistory.Gate.ENABLE,result==RESULT_OK)){if(foreground)resumeReconnect();}else cancelReconnect("已取消开启蓝牙或连接请求，点击连接可重试");return;}
        if(request==EXPORT_CONFIG||request==IMPORT_CONFIG){
            if(result!=RESULT_OK||data==null||data.getData()==null){configGeneration++;exportText=null;configNotice("已取消，当前配置未改变",false);return;}
            if(request==EXPORT_CONFIG)writeExport(data.getData());else readImport(data.getData());return;
        }
        if(request!=11)return;
        manualEnableWaiting=false;DevicePanel p=permissionPanel;permissionPanel=null;
        if(result==RESULT_OK&&p!=null&&panels.contains(p)){if(foreground)p.beginScan();else manualResumePanel=p;}
    }
    @Override protected void onStop(){
        exitChartFullscreen(true);
        foreground=false;connectionEpoch++;reconnectGate.background();
        if(!reconnectGate.pending())cancelReconnect("App 已进入后台，请点击连接重试");
        if(!manualPermissionWaiting&&!manualEnableWaiting){permissionPanel=null;manualResumePanel=null;}
        super.onStop();
        for(DevicePanel p:panels){if(p.networkBusy()){int session=p.generation;ui.postDelayed(()->{if(!foreground&&session==p.generation){p.disconnectDevice();p.state("idle","配网界面已关闭，请重新连接");}},65000);}else{p.disconnectDevice();p.state("idle","App 已进入后台，请重新连接");}}
    }
    @Override protected void onDestroy(){
        exitChartFullscreen(false);
        foreground=false;cancelReconnect("连接已取消");permissionPanel=manualResumePanel=null;
        if(Build.VERSION.SDK_INT>=33&&modernBackCallback!=null){ModernBack.unregister(this,modernBackCallback);modernBackCallback=null;}
        for(DevicePanel p:panels)p.shutdown();panels.clear();configGeneration++;ui.removeCallbacksAndMessages(null);homeReady=false;home.removeJavascriptInterface("HomeAndroid");home.destroy();super.onDestroy();
    }
    private void configNotice(String message,boolean busy){
        configMessage=message;configBusy=busy;
        if(homeReady)home.evaluateJavascript("window.configStatus("+JSONObject.quote(message)+","+busy+")",null);
    }
    private boolean devicesDisconnected(){for(DevicePanel p:panels)if(p.gatt!=null||p.lan!=null||p.lanConnecting||p.ready)return false;return true;}
    private void beginExport(){
        if(configBusy)return;
        for(DevicePanel p:panels)if(!p.pageReady){configNotice("通道仍在载入，请稍后重试",false);return;}
        configNotice("正在收集所有通道配置…",true);final int operation=++configGeneration;
        ui.postDelayed(()->{if(operation==configGeneration&&configBusy&&exportText==null){configGeneration++;configNotice("读取配置超时，请重试",false);}},10000);
        collectExport(new ArrayList<>(panels),0,new JSONArray(),operation);
    }
    private void collectExport(ArrayList<DevicePanel> source,int index,JSONArray configs,int operation){
        if(operation!=configGeneration)return;
        if(index==source.size()){
            try{
                JSONObject backup=new JSONObject();backup.put("format","can-scope-config");backup.put("version",1);backup.put("exportedAt",java.time.Instant.now().toString());backup.put("channels",configs);exportText=backup.toString(2);
                Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("application/json");
                intent.putExtra(Intent.EXTRA_TITLE,"CAN-Scope-config-"+java.time.LocalDate.now()+".json");startActivityForResult(intent,EXPORT_CONFIG);configNotice("请选择配置文件保存位置",true);
            }catch(Exception e){exportText=null;configNotice("无法导出："+e.getMessage(),false);}return;
        }
        DevicePanel p=source.get(index);
        // Selected metadata may describe a failed attempt. Only the separate
        // confirmed/imported history can become the portable reconnect target.
        String success=p.history.known()?","+p.obj("address",p.history.address(),"name",p.history.name()).toString():"";
        String expression="JSON.stringify((()=>{try{return {ok:true,data:ConfigIO.exportChannel("+p.slot+","+JSONObject.quote(p.deviceName)+","+JSONObject.quote(p.address)+success+")}}catch(e){return {ok:false,error:e.message}}})())";
        p.web.evaluateJavascript(expression,result->{
            if(operation!=configGeneration)return;
            try{JSONObject reply=jsResult(result);if(!reply.getBoolean("ok"))throw new IOException(reply.optString("error"));configs.put(reply.getJSONObject("data"));collectExport(source,index+1,configs,operation);}
            catch(Exception e){configGeneration++;configNotice("导出失败："+e.getMessage(),false);}
        });
    }
    private static JSONObject jsResult(String result)throws JSONException{
        Object value=new JSONTokener(result==null?"null":result).nextValue();
        if(!(value instanceof String))throw new JSONException("页面未返回有效配置");return new JSONObject((String)value);
    }
    private void writeExport(Uri uri){
        final String text=exportText;exportText=null;final int operation=++configGeneration;
        if(text==null){configNotice("导出操作已中断，请重新导出",false);return;}
        configNotice("正在保存配置文件…",true);
        new Thread(()->{String message;
            try(OutputStream out=getContentResolver().openOutputStream(uri,"wt")){
                if(out==null)throw new IOException("无法打开保存位置");out.write(text.getBytes(StandardCharsets.UTF_8));out.flush();message="配置已导出。可将 JSON 文件复制到其他手机导入。";
            }catch(Exception e){message="保存失败，目标文件可能不完整，请重新导出："+e.getMessage();}
            final String status=message;runOnUiThread(()->{if(operation==configGeneration)configNotice(status,false);});
        },"config-export").start();
    }
    private void beginImport(){
        if(configBusy)return;
        if(!devicesDisconnected()){configNotice("请先断开所有通道连接，再导入配置",false);return;}
        try{configGeneration++;configNotice("请选择 CAN Scope JSON 配置文件",true);Intent intent=new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");startActivityForResult(intent,IMPORT_CONFIG);}
        catch(Exception e){configNotice("无法打开文件选择器："+e.getMessage(),false);}
    }
    private void readImport(Uri uri){
        final int operation=++configGeneration;configNotice("正在检查配置文件…",true);
        new Thread(()->{
            try(InputStream in=getContentResolver().openInputStream(uri);ByteArrayOutputStream out=new ByteArrayOutputStream()){
                if(in==null)throw new IOException("无法读取文件");byte[] buf=new byte[8192];int count;
                while((count=in.read(buf))!=-1){if(out.size()+count>MAX_CONFIG_BYTES)throw new IOException("文件不能超过 1 MB");out.write(buf,0,count);}
                String text=StandardCharsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(out.toByteArray())).toString();
                runOnUiThread(()->{if(operation==configGeneration)previewImport(text,operation);});
            }catch(Exception e){runOnUiThread(()->{if(operation==configGeneration)configNotice("导入失败，当前配置未改变："+e.getMessage(),false);});}
        },"config-import").start();
    }
    private void previewImport(String text,int operation){
        if(!homeReady){configNotice("首页未准备好，请重新导入",false);return;}
        home.evaluateJavascript("JSON.stringify(window.validateImport("+JSONObject.quote(text)+"))",result->{
            if(operation!=configGeneration)return;
            try{
                JSONObject reply=jsResult(result);if(!reply.getBoolean("ok"))throw new IOException(reply.optString("error"));
                JSONObject backup=reply.getJSONObject("data");JSONArray channels=backup.getJSONArray("channels");int motors=0;
                StringBuilder lines=new StringBuilder();for(int i=0;i<channels.length();i++){JSONObject c=channels.getJSONObject(i);int n=c.getJSONArray("motors").length();motors+=n;ReconnectHistory saved=importedHistory(c);lines.append("通道 ").append(c.getInt("slot")).append(" · ").append(c.getString("name")).append("：").append(n).append(" 台电机\n");if(saved==null)lines.append("  无重连目标，需手动连接一次\n");else lines.append("  重连目标：").append(saved.name()).append(" · ").append(saved.address()).append("\n");}
                String message="将恢复 "+channels.length()+" 个通道、"+motors+" 台电机：\n\n"+lines+"\n将替换当前全部通道和电机列表，并清除本次报文记录。不会连接设备、发送控制指令或修改 ESP32 内部名称。建议先导出当前配置。";
                configNotice("文件检查通过，请确认导入摘要",true);
                new AlertDialog.Builder(this).setTitle("确认恢复配置").setMessage(message).setNegativeButton("取消",(d,w)->configNotice("已取消，当前配置未改变",false)).setOnCancelListener(d->configNotice("已取消，当前配置未改变",false)).setPositiveButton("替换并导入",(d,w)->commitImport(backup)).show();
            }catch(Exception e){configNotice("配置检查失败，当前配置未改变："+e.getMessage(),false);}
        });
    }
    private static ReconnectHistory importedHistory(JSONObject channel)throws JSONException{
        if(!channel.has("lastSuccessfulDevice"))return null;
        Object raw=channel.get("lastSuccessfulDevice");if(!(raw instanceof JSONObject))throw new JSONException("上次成功连接设备记录格式错误");
        JSONObject saved=(JSONObject)raw;Object address=saved.get("address"),name=saved.get("name");
        if(!(address instanceof String)||!(name instanceof String))throw new JSONException("上次成功连接设备地址和名称必须为文本");
        try{return ReconnectHistory.imported((String)address,(String)name);}catch(IllegalArgumentException e){throw new JSONException(e.getMessage());}
    }
    private void commitImport(JSONObject backup){
        if(!devicesDisconnected()){configNotice("设备已连接，请断开后重新导入",false);return;}
        try{
            String revision=UUID.randomUUID().toString();JSONObject ledger=new JSONObject().put("revision",revision).put("backup",backup);
            SharedPreferences.Editor prefs=getPreferences(MODE_PRIVATE).edit().putString("restore_bundle",ledger.toString());
            for(int slot=1;slot<=MAX_CHANNELS;slot++){prefs.remove("name_"+slot);prefs.remove("address_"+slot);prefs.remove("last_success_address_"+slot);prefs.remove("last_success_name_"+slot);}
            JSONArray channels=backup.getJSONArray("channels");StringJoiner slots=new StringJoiner(",");
            for(int i=0;i<channels.length();i++){JSONObject c=channels.getJSONObject(i);int slot=c.getInt("slot");slots.add(String.valueOf(slot));prefs.putString("name_"+slot,c.getString("name")).putString("address_"+slot,c.getString("address"));ReconnectHistory saved=importedHistory(c);if(saved!=null)prefs.putString("last_success_address_"+slot,saved.address()).putString("last_success_name_"+slot,saved.name());}
            // One durable native commit. Each channel applies this revision once; restart retries an interrupted restore.
            if(!prefs.putString("channels",slots.toString()).commit())throw new IOException("无法保存配置");
            cancelReconnect("配置已导入，请在首页连接设备");
            restoring=true;configNotice("正在恢复各通道…",true);
            for(DevicePanel p:panels){p.clearNetworkIdentity();p.history.clear();p.shutdown();content.removeView(p.web);}panels.clear();
            for(int i=0;i<channels.length();i++)createChannel(channels.getJSONObject(i).getInt("slot"));showHome();
        }catch(Exception e){configNotice("导入未完成："+e.getMessage()+"；请重新打开 App 检查",false);}
    }
    private void applyStoredImport(DevicePanel panel){
        try{
            String saved=getPreferences(MODE_PRIVATE).getString("restore_bundle",null);JSONObject channel=null;String revision="";
            if(saved!=null){JSONObject ledger=new JSONObject(saved);revision=ledger.getString("revision");JSONArray list=ledger.getJSONObject("backup").getJSONArray("channels");for(int i=0;i<list.length();i++)if(list.getJSONObject(i).getInt("slot")==panel.slot)channel=list.getJSONObject(i);}
            if(channel==null&&saved!=null){channel=new JSONObject().put("slot",panel.slot).put("name",panel.deviceName).put("address",panel.address).put("motors",new JSONArray()).put("historyLimits",new JSONObject().put("rx",300).put("tx",300));}
            if(channel==null){panelReady(panel);return;}
            // Replay installs WebView settings only. Native reconnect history was
            // committed once with metadata; replay must preserve later successes.
            panel.web.evaluateJavascript("JSON.stringify(ConfigIO.install("+channel.toString()+","+JSONObject.quote(revision)+"))",result->{
                if(panel.closed)return;
                try{JSONObject reply=jsResult(result);if(!reply.getBoolean("ok"))throw new IOException(reply.optString("error"));if(reply.getBoolean("reload")){panel.snapshot=new JSONArray();panel.web.reload();}else panelReady(panel);}
                catch(Exception e){configNotice("通道 "+panel.slot+" 恢复失败："+e.getMessage()+"；重新打开 App 可重试",false);}
            });
        }catch(Exception e){configNotice("无法读取导入记录："+e.getMessage(),false);}
    }
    private void panelReady(DevicePanel panel){
        panel.pageReady=true;panel.state("idle","准备连接 ESP32-CAN");
        if(restoring){boolean done=true,hasSaved=false;for(DevicePanel p:panels){done&=p.pageReady;hasSaved|=p.history.known();}if(done){restoring=false;configNotice(hasSaved?"配置导入完成；请核对电机地址，在首页点击“连接”恢复已保存的设备；无成功记录的通道需先手动连接一次。":"配置导入完成；请核对电机地址，各通道手动连接成功一次后即可使用首页“连接”。",false);}}
    }
    private class DevicePanel {
    private final UUID SERVICE = UUID.fromString("c0de0001-1212-efde-1523-785feabcd123");
    private final UUID WRITE = UUID.fromString("c0de0002-1212-efde-1523-785feabcd123");
    private final UUID NOTIFY = BleNotifications.LEGACY;
    private volatile UUID notificationUuid=BleNotifications.LEGACY;
    private boolean awaitingNotificationMtu;
    private final UUID NETWORK = UUID.fromString("c0de0004-1212-efde-1523-785feabcd123");
    private final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    private final Handler main = new Handler(Looper.getMainLooper());
    private WebView web;
    private BluetoothAdapter adapter;
    private BluetoothLeScanner scanner;
    private volatile BluetoothGatt gatt;
    private volatile BluetoothGattCharacteristic writer;
    private volatile LanTransport lan;
    private volatile boolean lanConnecting,deviceWifiMode;
    private volatile WifiProvisioner provisioner;
    private String connectionMode="ble";
    private String networkId="",networkIp="",networkPin="",networkToken="",networkAddress="";
    private int discoveryGeneration;
    private void clearNetworkIdentity(){
        networkId=networkIp=networkPin=networkToken=networkAddress="";connectionMode="ble";
        android.content.SharedPreferences.Editor edit=getPreferences(MODE_PRIVATE).edit();
        for(String key:new String[]{"network_id_","network_ip_","network_pin_","network_token_","network_address_","connection_mode_"})edit.remove(key+slot);
        edit.apply();networkState();
    }
    private boolean networkBusy(){return provisioner!=null&&provisioner.active();}
    private void networkState(){emit("networkMode",obj("mode",connectionMode,"id",networkId,"ip",networkIp,"paired",!networkPin.isEmpty(),"busy",networkBusy()));}
    private void chooseConnectionMode(String mode){
        if(!mode.equals("ble")&&!mode.equals("wifi"))return;
        if(networkBusy()){error("请先完成 Wi-Fi 配置");networkState();return;}
        if(!connectionMode.equals(mode)){disconnectDevice();connectionMode=mode;getPreferences(MODE_PRIVATE).edit().putString("connection_mode_"+slot,mode).apply();state("idle",mode.equals("wifi")?"请把设备屏幕的通信模式切换为 Wi-Fi，再连接局域网":"请选择蓝牙设备");}
        networkState();
    }
    private void scanLan(){
        if(closed||!foreground||configBusy||ready||lanConnecting)return;
        if(networkId.isEmpty()||networkPin.isEmpty()){error("请先通过蓝牙进入设备设置，完成 Wi-Fi 配置和安全配对");return;}
        int scan=++discoveryGeneration;state("scanning","正在搜索局域网中的已配对设备…");emit("scanStart",obj());
        if(!networkIp.isEmpty())emit("device",obj("address","wifi:"+networkIp,"name",deviceName,"rssi",0,"transport","wifi"));
        LanTransport.discover(networkId,new LanTransport.Discovery(){
            public void found(String ip,String id){main.post(()->{if(scan!=discoveryGeneration||closed||!foreground)return;networkIp=ip;getPreferences(MODE_PRIVATE).edit().putString("network_ip_"+slot,ip).apply();networkState();emit("device",obj("address","wifi:"+ip,"name",deviceName,"rssi",0,"transport","wifi"));});}
            public void finished(){main.post(()->{if(scan==discoveryGeneration&&!closed&&!ready&&!lanConnecting)state("idle","搜索完成；未发现时可输入屏幕显示的 IP 地址");});}
        });
    }
    private void connectLan(String ip){
        if(closed||!foreground||!pageReady||configBusy||networkBusy()||ready||lanConnecting)return;
        if(networkPin.isEmpty()||networkToken.isEmpty()){error("请先通过蓝牙完成配网");return;}
        try{LanTransport.validateIp(ip);}catch(IllegalArgumentException e){error(e.getMessage());return;}
        for(DevicePanel p:panels)if(p!=this&&(p.ready||p.lanConnecting)&&networkId.equals(p.networkId)){error("设备已由通道 "+p.slot+" 连接");return;}
        disconnectDevice();networkIp=ip;connectionMode="wifi";getPreferences(MODE_PRIVATE).edit().putString("network_ip_"+slot,ip).putString("connection_mode_"+slot,"wifi").apply();
        lanConnecting=true;state("connecting","正在验证设备并连接局域网…");networkState();
        LanTransport next=new LanTransport(new LanTransport.Listener(){
            public void connected(LanTransport source){main.post(()->{if(lan!=source||closed||!foreground){source.close();return;}synchronized(transportLock){ready=true;lanConnecting=false;}state("ready","局域网已连接 · "+networkIp);networkState();scheduleReceiveDrain();});}
            public void packet(LanTransport source,byte[] packet){if(lan!=source)return;if(packet[1]==18){enqueueControl(null,packet[2]&255,null,SystemClock.elapsedRealtime());return;}receive(null,packet);}
            public void failed(LanTransport source,String message){main.post(()->{if(lan==source)fail(message);});}
        },target->LanNetwork.socket(MainActivity.this,target));lan=next;next.connect(ip,32323,networkPin,networkToken);
    }
    private void configureWifi(int op,String ssid,String password){
        if(closed||configBusy||!ready||gatt==null||networkBusy()){error("请先通过蓝牙连接设备，并等待当前配置结束");return;}
        BluetoothGattService service=gatt.getService(SERVICE);BluetoothGattCharacteristic c=service==null?null:service.getCharacteristic(NETWORK);
        if(c==null){error("请将 ESP32 更新到支持 Wi-Fi 的固件");return;}
        int session=generation;
        try{
            WifiProvisioner next=new WifiProvisioner(gatt,c,main,new WifiProvisioner.Listener(){
                public void status(JSONObject data){
                    if(session!=generation)return;
                    String id=data.optString("id"),pin=data.optString("fp"),token=data.optString("token");
                    if(id.matches("[0-9a-f]{12}")&&pin.matches("[0-9a-f]{64}")&&token.matches("[0-9a-f]{64}")){
                        networkId=id;networkPin=pin;networkToken=token;networkAddress=address;
                        if(!data.optString("ip").isEmpty())networkIp=data.optString("ip");
                        getPreferences(MODE_PRIVATE).edit().putString("network_id_"+slot,networkId).putString("network_pin_"+slot,networkPin).putString("network_token_"+slot,networkToken).putString("network_address_"+slot,networkAddress).putString("network_ip_"+slot,networkIp).apply();
                    }
                    data.remove("token");data.remove("fp");emit("wifiStatus",data);networkState();
                }
                public void networks(JSONArray rows){if(session==generation)emit("wifiScan",obj("rows",rows));}
                public void finished(String message){if(session==generation){emit("wifiResult",obj("message",message,"busy",false));}}
            },op,ssid,password);
            provisioner=next;emit("wifiResult",obj("message","正在安全配对；未弹出输入框时，请打开手机通知栏的蓝牙配对请求，输入设备屏幕上的六位码","busy",true));
            long until=SystemClock.elapsedRealtime()+6500;
            main.post(new Runnable(){public void run(){if(session!=generation||!next.active())return;if(writing){if(SystemClock.elapsedRealtime()>until){next.cancel();emit("wifiResult",obj("message","设备忙，请稍后重试","busy",false));}else main.postDelayed(this,50);return;}next.start();}});
        }catch(IllegalArgumentException e){error(e.getMessage());}
    }

    private volatile BluetoothGattDescriptor subscriptionDescriptor;
    private volatile boolean scanning, ready, writing, pageReady, closed;
    private volatile int generation = 0;
    private volatile int writeSequence = 0;
    private String deviceName = "ESP32-CAN";
    private JSONArray snapshot=new JSONArray();
    private long snapshotTime;
    private final Map<String,String> scanNames=new HashMap<>();
    private final Map<String, BluetoothDevice> devices = new HashMap<>();
    private final TelemetryInbox received=new TelemetryInbox(512,64);
    private final AtomicBoolean receiveDrainScheduled=new AtomicBoolean();
    private boolean telemetryDeliveryPending;
    private int pageGeneration;
    private String registrySignature="";
    private final ConnectionPriority linkPriority=new ConnectionPriority();
    private boolean monitorVisible;
    private final Runnable priorityTask=new Runnable(){public void run(){
        if(closed||!ready||gatt==null)return;
        int requested=linkPriority.poll(SystemClock.elapsedRealtime(),true);
        if(requested>=0){boolean accepted=false;try{accepted=gatt.requestConnectionPriority(requested==1?BluetoothGatt.CONNECTION_PRIORITY_HIGH:BluetoothGatt.CONNECTION_PRIORITY_BALANCED);}catch(SecurityException ignored){}linkPriority.result(requested,accepted);}
        long delay=linkPriority.delay(SystemClock.elapsedRealtime());if(delay>=0)main.postDelayed(this,Math.max(16,delay));
    }};
    private void updatePriority(){long now=SystemClock.elapsedRealtime();linkPriority.want(selected==this&&(monitorVisible||!detailKey.isEmpty()),now);main.removeCallbacks(priorityTask);long delay=linkPriority.delay(now);if(ready&&delay>=0)main.postDelayed(priorityTask,delay);}
    private int uiDropped;
    private final FeedbackQueries queries=new FeedbackQueries();
    private volatile long settingsHoldUntil;
    private volatile int settingType,settingToken;
    private boolean queryWrite;
    private volatile boolean deviceUart,imuSupported,devicePolling,homeTelemetrySupported,detailSupported;
    private boolean telemetryWrite;
    private volatile String detailKey="";
    private int detailMaxHz;
    private boolean wantsBusLoad,busLoadActive;
    private final HomeTelemetry telemetry=new HomeTelemetry();
    private final Object transportLock=new Object();
    private final HandlerThread controlThread;
    private final Handler control;
    private long writeDeadline,telemetryRetryAt;
    private FeedbackQueries.Request currentQueryWrite;
    private final ArrayBlockingQueue<ControlEvent> controlEvents=new ArrayBlockingQueue<>(128);
    private final AtomicBoolean receiveOverflowPending=new AtomicBoolean();
    private final Map<String,Long> queryUiVersions=new HashMap<>();
    private long queryUiSequence;
    private final class ControlEvent {
        final BluetoothGatt source;final int session,sequence,status;final long arrived;final byte[] ack;
        ControlEvent(BluetoothGatt source,int status,byte[] ack,long arrived){this.source=source;this.session=generation;this.sequence=writeSequence;this.status=status;this.ack=ack;this.arrived=arrived;}
    }
    private void configureTelemetry(){synchronized(transportLock){
        updatePriority();
        boolean wantLoad=selected==this&&monitorVisible&&detailKey.isEmpty();
        if(wantsBusLoad!=wantLoad){wantsBusLoad=wantLoad;busLoadActive=false;emit("busLoadReset",obj());}
        try{int[][] rows=new int[snapshot.length()][3];
            for(int i=0;i<rows.length;i++){JSONObject m=snapshot.getJSONObject(i),c=new JSONObject(m.getString("key"));rows[i]=new int[]{"imu".equals(m.optString("family"))?(imuSupported?2:0):"dm".equals(m.optString("family"))?1:0,"imu".equals(m.optString("family"))&&!imuSupported?1:c.getInt("id"),"imu".equals(m.optString("family"))&&!imuSupported?0x7ff:c.getInt("feedbackId")};}
            boolean detail=selected!=null&&!selected.detailKey.isEmpty();
            if(detail){
                int target=-1;for(int i=0;i<snapshot.length();i++)if(snapshot.getJSONObject(i).getString("key").equals(detailKey))target=i;
                boolean supported=detailSupported&&(target<0||!"imu".equals(snapshot.getJSONObject(target).optString("family"))||imuSupported);
                if(supported)telemetry.configure(selected==this&&target>=0?2:3,0,selected==this&&target>=0?new int[][]{rows[target]}:new int[0][],selected==this&&target>=0&&detailMaxHz>0?Math.max(1,Math.min(detailMaxHz,snapshot.getJSONObject(target).optInt("sampleHz",100))):0);
                else telemetry.configure(3,0,new int[0][]);
                if(selected==this)emit("detailSupport",obj("supported",supported,"maxHz",supported?detailMaxHz:0,"channel","通道 "+slot+" · "+deviceName));
            }else telemetry.configure(selected==this&&monitorVisible?0:1,0,rows);
        }catch(JSONException ignored){}
    }}
    private String packetHex(byte[] data){char[] out=new char[data.length*2];final String digits="0123456789abcdef";for(int i=0;i<data.length;i++){out[i*2]=digits.charAt((data[i]&255)>>>4);out[i*2+1]=digits.charAt(data[i]&15);}return new String(out);}
    private void queryStatus(String key,boolean enabled,String message){
        queryStatus(key,enabled,message,false);
    }
    private void queryStatus(String key,boolean enabled,String message,boolean pending){
        int session=generation;long version=++queryUiSequence;queryUiVersions.put(key,version);
        JSONObject data=obj("key",key,"enabled",enabled,"message",message,"pending",pending);
        main.post(()->{
            boolean current;synchronized(transportLock){current=session==generation&&Objects.equals(queryUiVersions.get(key),version);}
            if(current&&!closed&&pageReady)web.evaluateJavascript("window.onNative(\"queryState\","+data.toString()+")",null);
        });
    }
    private void clearQueries(String message){synchronized(transportLock){for(String key:queries.stopAll(SystemClock.elapsedRealtime()))queryStatus(key,false,message);}}
    private void queryFailure(FeedbackQueries.Failure failure){
        if(failure==null)return;
        android.util.Log.w("CANScope","channel "+slot+" query recovery kind="+failure.kind);
        String message=failure.kind==FeedbackQueries.Failure.CONFIRMATION?"查询配置确认超时，已停止续租；监控保持连接。正在确认设备停止，等待停止确认或租约到期后可重开":failure.kind==FeedbackQueries.Failure.SUBMISSION?"查询配置连续提交失败，已停止续租；监控保持连接，正在确认设备停止": "尚未确认设备停止，已停止续租；监控保持连接，等待查询租约到期";
        for(String key:failure.keys)queryStatus(key,false,message);
    }
    private void enqueueControl(BluetoothGatt source,int status,byte[] ack,long arrived){
        if(closed||source!=gatt)return;
        // Receipt is recorded before taking any lock also used by the UI.
        if(!controlEvents.offer(new ControlEvent(source,status,ack,arrived))){receiveOverflow();return;}
        control.post(()->{synchronized(transportLock){drainControlEvents();}});
    }
    private void drainControlEvents(){
        ControlEvent event;
        while((event=controlEvents.poll())!=null){
            if(event.source!=gatt||event.session!=generation||!ready)continue;
            if(event.ack!=null){
                java.nio.ByteBuffer b=java.nio.ByteBuffer.wrap(event.ack).order(java.nio.ByteOrder.LITTLE_ENDIAN);
                int id=b.getInt(4),hz=b.getInt(8),actual=b.getInt(12),token=b.getInt(16),reason=event.ack[2]&255;
                FeedbackQueries.Result result=token==0?queries.periodic(id,reason):queries.complete(token,reason,event.arrived);
                if(result!=null)queryStatus(result.key,result.enabled,PollingStatus.message(reason,event.ack[3]&255,hz,actual));
                continue;
            }
            if(!writing||event.sequence!=writeSequence)continue;
            writing=false;
            FeedbackQueries.Request request=currentQueryWrite;currentQueryWrite=null;
            if(request!=null){
                if(event.status==0)queries.written(request,event.arrived);
                else queryFailure(queries.rejected(request,event.arrived));
            }else if(event.status!=0){if(telemetryWrite){telemetry.failed();telemetryRetryAt=event.arrived+200;}else settingsHoldUntil=0;error("设备拒绝指令（"+event.status+"），请检查参数或稍后重试");}
            telemetryWrite=false;queryWrite=false;
            if(request==null)emit("writeDone",obj("success",event.status==0));
        }
    }
    private final Runnable queryTick=new Runnable(){public void run(){
        synchronized(transportLock){
            if(closed)return;long now=SystemClock.elapsedRealtime();
            // Consume management events in arrival order before checking timeouts.
            // This runs outside the WebView/UI Looper, so per-frame drawing cannot
            // delay lease renewals or discard a confirmation already in the inbox.
            drainControlEvents();
            if(writing&&now>=writeDeadline){fail(LanTransport.commandTimeoutMessage(lan!=null));}
            queryFailure(queries.expire(now));
            for(String key:queries.recovered(now))queryStatus(key,false,"查询已停止，可重新开启");
            FeedbackQueries.Request request=queries.poll(now,ready&&!deviceWifiMode&&!deviceUart&&devicePolling&&!writing&&!networkBusy()&&now>=settingsHoldUntil);
            if(request!=null)writePacket(packetHex(request.packet),request);
            else if(ready&&!deviceWifiMode&&!deviceUart&&homeTelemetrySupported&&!writing&&!networkBusy()&&!queries.waiting()&&now>=settingsHoldUntil&&now>=telemetryRetryAt){byte[] packet=telemetry.poll(now,true);if(packet!=null)writePacket(packetHex(packet),null);}
        }
        control.postDelayed(this,20);
    }};

    final int slot;
    final String origin;
    final ReconnectHistory history;
    String address = "", label = "未连接";
    String connectionMessage="";
    DevicePanel(int slot) {
        this.slot = slot;
        controlThread=new HandlerThread("CAN-BLE-"+slot);controlThread.start();control=new Handler(controlThread.getLooper());
        connectionMode=getPreferences(MODE_PRIVATE).getString("connection_mode_"+slot,"ble");
        networkAddress=getPreferences(MODE_PRIVATE).getString("network_address_"+slot,"");
        networkId=getPreferences(MODE_PRIVATE).getString("network_id_"+slot,"");networkIp=getPreferences(MODE_PRIVATE).getString("network_ip_"+slot,"");
        networkPin=getPreferences(MODE_PRIVATE).getString("network_pin_"+slot,"");networkToken=getPreferences(MODE_PRIVATE).getString("network_token_"+slot,"");
        deviceName=getPreferences(MODE_PRIVATE).getString("name_"+slot,"ESP32-CAN");
        address=getPreferences(MODE_PRIVATE).getString("address_"+slot,"");
        history=new ReconnectHistory(getPreferences(MODE_PRIVATE).getString("last_success_address_"+slot,""),getPreferences(MODE_PRIVATE).getString("last_success_name_"+slot,"ESP32-CAN"));
        origin = slot == 1 ? "https://app.local/" : "https://channel-" + slot + ".local/";
        web = new WebView(MainActivity.this);
        web.setBackgroundColor(Color.rgb(10, 15, 25));
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setDomStorageEnabled(true);
        web.getSettings().setAllowFileAccess(false);
        web.getSettings().setAllowContentAccess(false);
        web.addJavascriptInterface(new Bridge(), "CanAndroid");
        web.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                if (url.equals(origin + "uart.js") || url.equals(origin + "uart_waveform.js") || url.equals(origin + "index.html") || url.equals(origin + "motors.js") || url.equals(origin + "history.js") || url.equals(origin + "monitor.js") || url.equals(origin + "motor_roster.js") || url.equals(origin + "motor_detail.js") || url.equals(origin + "device_settings.js") || url.equals(origin + "network_settings.js") || url.equals(origin + "config_io.js")) {
                    String asset = request.getUrl().getLastPathSegment();
                    try { return new WebResourceResponse(asset.endsWith(".js") ? "application/javascript" : "text/html", "UTF-8", getAssets().open(asset)); }
                    catch (Exception e) { return new WebResourceResponse("text/plain", "UTF-8", 500, "Error", null, null); }
                }
                return new WebResourceResponse("text/plain", "UTF-8", 403, "Blocked", null, null);
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) { return true; }
            @Override public void onPageStarted(WebView view,String url,android.graphics.Bitmap favicon) {
                pageReady=false;pageGeneration++;telemetryDeliveryPending=false;
            }
            @Override public void onPageFinished(WebView view, String url) {
                if (closed) return;
                String version="";
                try { version=getPackageManager().getPackageInfo(getPackageName(),0).versionName; }
                catch (PackageManager.NameNotFoundException ignored) {}
                String about="CAN Scope "+version+" · 本地运行，无云端服务 · 退出到后台时断开监控连接，返回后可重新连接。";
                view.evaluateJavascript("document.querySelectorAll('.protocol').forEach(p=>p.textContent="+JSONObject.quote(about)+")",null);
                applyStoredImport(DevicePanel.this);
            }
        });

        BluetoothManager manager = (BluetoothManager)getSystemService(BLUETOOTH_SERVICE);
        adapter = manager == null ? null : manager.getAdapter();
        web.loadUrl(origin + "index.html");

        control.postDelayed(queryTick,50);
    }

    private void scheduleReceiveDrain(){if(!closed&&receiveDrainScheduled.compareAndSet(false,true))main.postDelayed(drainReceived,16);}
    private final Runnable drainReceived=new Runnable(){public void run(){
        if(closed){receiveDrainScheduled.set(false);return;}
        int session=generation,page=pageGeneration;JSONArray raw=new JSONArray(),samples=new JSONArray();String key=detailKey;
        for(TelemetryInbox.Frame frame:received.drain(128,!telemetryDeliveryPending,ready)){
            if(frame.session!=generation||!ready)continue;
            processReceived(frame,raw,samples,key);
        }
        uiDropped=received.dropped();
        if(pageReady&&(raw.length()>0||samples.length()>0)){
            String type=samples.length()>0?"motorSamples":"packets";
            JSONArray batch=samples.length()>0?samples:raw;
            JSONObject data=obj("items",batch,"dropped",uiDropped,"key",key,"packets",samples.length()>0?raw:new JSONArray(),"sentAt",System.currentTimeMillis());
            telemetryDeliveryPending=true;
            web.evaluateJavascript("window.onNative("+JSONObject.quote(type)+","+data+")",value->{if(session!=generation||page!=pageGeneration||closed)return;telemetryDeliveryPending=false;if(received.pending(true,ready))scheduleReceiveDrain();});
        }
        receiveDrainScheduled.set(false);
        if(received.pending(!telemetryDeliveryPending,ready))scheduleReceiveDrain();
    }};
    private JSONObject obj(Object... pairs) {
        JSONObject out = new JSONObject();
        try { for (int i = 0; i < pairs.length; i += 2) out.put((String)pairs[i], pairs[i + 1]); }
        catch (JSONException e) { throw new IllegalArgumentException(e); }
        return out;
    }
    private void emit(String type, JSONObject data) {
        int session=generation;if(data.has("age"))try{data.put("sentAt",System.currentTimeMillis());}catch(JSONException ignored){}
        Runnable delivery=()->{if(!closed&&pageReady&&session==generation)web.evaluateJavascript("window.onNative("+JSONObject.quote(type)+","+data.toString()+")",null);};
        if(Looper.myLooper()==Looper.getMainLooper())delivery.run();else main.post(delivery);
    }
    private void state(String value, String message) {
        if(Looper.myLooper()!=Looper.getMainLooper()){int session=generation;main.post(()->{if(session==generation)state(value,message);});return;}
        if (closed) return;
        connectionMessage=message;
        label = value.equals("ready") ? "已连接" : value.equals("connecting") ? "连接中" : value.equals("scanning") ? "扫描中" : "未连接";
        if(value.equals("ready")||value.equals("idle"))finishReconnectAttempt(this,value.equals("ready"),message);
        renderTabs();
        emit("state", obj("value", value, "message", message, "transport",connectionMode,"name", deviceName + (connectionMode.equals("wifi")?" · "+networkIp:address.isEmpty()?"":" · "+address)));
    }
    private void error(String message) { emit("error", obj("message", message)); }
    private class Bridge {
        @JavascriptInterface public void connectionMode(String mode){main.post(()->chooseConnectionMode(mode));}
        @JavascriptInterface public void networkState(){main.post(()->DevicePanel.this.networkState());}
        @JavascriptInterface public void wifiConfigure(int operation,String ssid,String password){main.post(()->configureWifi(operation,ssid,password));}
        @JavascriptInterface public void connectLan(String ip){main.post(()->DevicePanel.this.connectLan(ip));}

        @JavascriptInterface public void showDetail(String key){int session=generation;main.post(()->{if(closed||session!=generation)return;boolean found=false;for(int i=0;i<snapshot.length();i++)if(key.equals(snapshot.optJSONObject(i).optString("key")))found=true;if(!found)return;detailKey=key;select(DevicePanel.this);});}
        @JavascriptInterface public void setChartFullscreen(boolean enabled){int session=generation;String motorKey=detailKey;main.post(()->{if(closed||session!=generation||!motorKey.equals(detailKey))return;if(enabled)enterChartFullscreen(DevicePanel.this,session,motorKey);else if(chartPanel==DevicePanel.this&&chartMotorKey.equals(motorKey))exitChartFullscreen(false);});}
        @JavascriptInterface public void leaveDetail(){int session=generation;main.post(()->{if(closed||session!=generation)return;if(chartPanel==DevicePanel.this)exitChartFullscreen(true);detailKey="";for(DevicePanel p:panels)p.configureTelemetry();renderTabs();});}
        @JavascriptInterface public void home(){main.post(()->showHome());}
        @JavascriptInterface public void setMonitorVisible(boolean visible){int session=generation;main.post(()->{if(closed||session!=generation||monitorVisible==visible)return;monitorVisible=visible;configureTelemetry();});}
        @JavascriptInterface public void dashboard(String json) { main.post(()->{
            if(closed||!pageReady||json==null||json.length()>65536)return;
            try{JSONArray list=new JSONArray(json);if(list.length()<=32){JSONArray rules=new JSONArray();for(int i=0;i<list.length();i++){JSONObject motor=list.getJSONObject(i);rules.put(motor.getString("key")+"|"+motor.optInt("sampleHz",100));}String signature=rules.toString();snapshot=list;snapshotTime=SystemClock.elapsedRealtime();if(!registrySignature.equals(signature)){registrySignature=signature;configureTelemetry();}scheduleHomeUpdate();}}catch(JSONException ignored){}
        }); }
        @JavascriptInterface public void scan() { int epoch=connectionEpoch;main.post(() -> {if(epoch==connectionEpoch){if(connectionMode.equals("wifi"))scanLan();else beginScan();}}); }
        @JavascriptInterface public void connect(String address) { int epoch=connectionEpoch;main.post(() -> {if(epoch==connectionEpoch){if(address.startsWith("wifi:"))connectLan(address.substring(5));else connectDevice(address);}}); }
        @JavascriptInterface public void disconnect() { main.post(() -> { disconnectDevice(); state("idle", "已断开连接"); }); }
        @JavascriptInterface public void setPolling(String key,int motorId,boolean enabled,int hz){int session=generation;control.post(()->{synchronized(transportLock){
            if(closed||session!=generation)return;
            long now=SystemClock.elapsedRealtime();
            if(!enabled){boolean pending=queries.disable(key);queryStatus(key,false,pending?"正在关闭查询，等待设备确认…":queries.canEnable(now)?"查询未开启":"等待已有停止确认或租约到期后再开启",pending);return;}
            if(!ready||!pageReady){queryStatus(key,false,"请先连接 ESP32");return;}
            if(configBusy||now<settingsHoldUntil){queryStatus(key,false,"请等待设备设置完成");return;}
            if(!devicePolling){queryStatus(key,false,"请先更新 ESP32 固件，才能使用可调频率查询");return;}
            if(!queries.canEnable(now)){queryStatus(key,false,"正在停止查询，请等待停止确认或查询租约到期后重开");return;}
            if(!queries.enable(key,motorId,hz,now)){queryStatus(key,false,"无法开启：电机 ID 重复、无效或超过数量限制");return;}
            queryStatus(key,false,"正在应用 "+hz+" Hz…",true);
        }});}
        @JavascriptInterface public void send(String hex) { int session=generation;control.post(() -> {synchronized(transportLock){
            if(closed||session!=generation)return;
            if(hex!=null&&hex.matches("(?i)0118[0-9a-f]{36}")){writePacket(hex,null);return;}
            clearQueries("设备设置变化，已停止续租；正在确认设备停止，完成后可重新开启");settingsHoldUntil=SystemClock.elapsedRealtime()+7000;
            settingType=0;settingToken=0;
            if(hex!=null&&hex.matches("[0-9a-fA-F]{40}")){settingType=Integer.parseInt(hex.substring(2,4),16);for(int i=0;i<4;i++)settingToken|=Integer.parseInt(hex.substring(32+i*2,34+i*2),16)<<(8*i);}
            writePacket(hex,null);
        }}); }

    }
    private boolean permissions() {
        if (Build.VERSION.SDK_INT >= 31)
            return checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN) == PackageManager.PERMISSION_GRANTED && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED;
        return checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }
    private void beginScan() {
        if (closed||!foreground) return;
        if(!pageReady||configBusy){error("配置处理中，请稍后连接");return;}
        if(reconnectGate.pending()||reconnectGate.prompting()){error("请先完成首页的连接操作");return;}
        if(manualPermissionWaiting||manualEnableWaiting){error("请先完成当前的蓝牙权限操作");return;}
        if(permissionPanel!=null&&permissionPanel!=this){error("请先完成其他通道的蓝牙权限操作");return;}
        if (adapter == null) { error("这台手机不支持蓝牙"); return; }
        if (!permissions()) {
            permissionPanel = this;
            manualPermissionWaiting=true;
            try{requestPermissions(Build.VERSION.SDK_INT >= 31 ? new String[]{Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT} : new String[]{Manifest.permission.ACCESS_FINE_LOCATION}, 10);}catch(Exception e){manualPermissionWaiting=false;permissionPanel=null;error("无法申请蓝牙权限，请在系统设置允许");}
            return;
        }
        try {
            if (!adapter.isEnabled()) { permissionPanel = this;manualEnableWaiting=true; startActivityForResult(new Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE), 11); return; }
            if (Build.VERSION.SDK_INT < 31) {
                LocationManager lm = (LocationManager)getSystemService(LOCATION_SERVICE);
                if (lm != null && !lm.isProviderEnabled(LocationManager.GPS_PROVIDER) && !lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                    error("Android 8–11 扫描 BLE 需要开启系统定位；开启后请重新扫描");
                    startActivity(new Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS)); return;
                }
            }
            for (DevicePanel panel : panels) if (panel != this && panel.scanning) {
                panel.stopScan(); panel.state("idle", "扫描已停止，可重新扫描");
            }
            finishReconnectAttempt(this,false,"已改为手动扫描");disconnectDevice();
            devices.clear();scanNames.clear();
            emit("scanStart", obj());
            scanner = adapter.getBluetoothLeScanner();
            if (scanner == null) { error("蓝牙扫描器不可用，请重新开启蓝牙"); return; }
            scanning = true;
            state("scanning", "正在寻找附近的 CAN 设备…");
            ScanFilter filter = new ScanFilter.Builder().setServiceUuid(new ParcelUuid(SERVICE)).build();
            scanner.startScan(Collections.singletonList(filter), new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(), scanCallback);
            int token = generation;
            main.postDelayed(() -> { if (token == generation && scanning) { stopScan(); state("idle", devices.isEmpty() ? "未发现设备，请确认 ESP32 已上电" : "扫描完成，请选择设备"); } }, 12000);
        } catch (Exception e) {if(permissionPanel==this){permissionPanel=null;manualPermissionWaiting=manualEnableWaiting=false;}stopScan(); state("idle", "扫描失败"); error("扫描失败：" + e.getMessage()); }
    }
    private void stopScan() {
        if (scanning && scanner != null) try { scanner.stopScan(scanCallback); } catch (Exception ignored) {}
        scanning = false;
    }
    private final ScanCallback scanCallback = new ScanCallback() {
        @Override public void onScanResult(int type, ScanResult result) { main.post(() -> {
            if (!scanning) return;
            BluetoothDevice device = result.getDevice();
            String address = device.getAddress();
            devices.put(address, device);
            String name = result.getScanRecord() == null ? null : result.getScanRecord().getDeviceName();
            scanNames.put(address,name==null?"ESP32-CAN":name);
            emit("device", obj("address", address, "name", name == null ? "ESP32-CAN" : name, "rssi", result.getRssi()));
        }); }
        @Override public void onScanFailed(int code) { main.post(() -> { scanning = false; state("idle", "扫描失败，错误码 " + code); }); }
    };
    private void connectDevice(String address) {
        if (closed||!foreground) return;
        if(!pageReady||configBusy){error("配置处理中，请稍后连接");return;}
        if(reconnectGate.pending()||reconnectGate.prompting()){error("请先完成首页的连接操作");return;}
        address=ReconnectHistory.normalize(address);
        BluetoothDevice device = devices.get(address);
        if (device == null) { error("请先扫描并选择设备"); return; }
        startConnection(device,address,scanNames.getOrDefault(address,"ESP32-CAN"),false);
    }
    private void startConnection(BluetoothDevice device,String address,String name,boolean batch) {
        if(closed||!foreground||!pageReady||configBusy){if(batch){reconnectFailed++;reconnectResults.put(slot,"页面或配置处理中，请稍后重试");}return;}
        if(ready||gatt!=null||lanConnecting){if(batch)reconnectResults.put(slot,"已连接或正在连接，已跳过");return;}
        if(!connectionPermission()){if(batch){reconnectFailed++;reconnectResults.put(slot,"蓝牙连接权限已失效");}else error("需要附近设备权限，请先扫描授权");return;}
        int owner=ReconnectHistory.claimingSlot(reconnectChannels(),address,slot);
        if(owner!=0){String message="该设备已在通道 "+owner+" 连接，请切换到该通道操作";if(batch)reconnectResults.put(slot,message);else error(message);return;}
        disconnectDevice();
        if(!networkAddress.isEmpty()&&!networkAddress.equals(address))clearNetworkIdentity();
        connectionMode="ble";
        this.address = address;
        deviceName = name;
        history.begin(address);
        getPreferences(MODE_PRIVATE).edit().putString("name_"+slot,deviceName).putString("address_"+slot,address).apply();
        if(batch)reconnectAttempts.add(this);
        state("connecting", "正在连接并订阅 CAN 数据…");
        try {
            gatt = device.connectGatt(MainActivity.this, false, callback, BluetoothDevice.TRANSPORT_LE);
            if(gatt==null){fail("蓝牙无法创建连接，请重新开启蓝牙后重试");return;}
            int token = generation;
            main.postDelayed(() -> { if (token == generation && !ready && gatt != null) fail("连接超时，请靠近设备重试"); }, 15000);
        } catch (Exception e) { fail("连接失败：" + e.getMessage()); }
    }
    private void disconnectDevice() {
        deviceUart=false;
        discoveryGeneration++;
        WifiProvisioner pending=provisioner;if(pending!=null)pending.cancel();provisioner=null;
        LanTransport oldLan=lan;lan=null;lanConnecting=false;if(oldLan!=null)oldLan.close();
        BluetoothGatt old;
        synchronized(transportLock){
            imuSupported=false;devicePolling=false;homeTelemetrySupported=false;detailSupported=false;detailMaxHz=0;busLoadActive=false;telemetry.reset();
            generation++;for(String key:queries.keys())queryStatus(key,false,"连接已断开，查询已停止");queries.reset();queryUiVersions.clear();controlEvents.clear();settingsHoldUntil=0;
            ready=false;deviceWifiMode=false;writing=false;writer=null;subscriptionDescriptor=null;awaitingNotificationMtu=false;notificationUuid=NOTIFY;currentQueryWrite=null;telemetryWrite=queryWrite=false;telemetryRetryAt=0;history.failed();
            old=gatt;gatt=null;
            emit("busLoadReset",obj());
        }
        closeChartForDisconnectedPanel(this);
        stopScan();
        int session=generation;Runnable cleanup=()->{if(session==generation){received.clear();receiveOverflowPending.set(false);telemetryDeliveryPending=false;receiveDrainScheduled.set(false);main.removeCallbacks(drainReceived);main.removeCallbacks(priorityTask);linkPriority.reset();}};if(Looper.myLooper()==Looper.getMainLooper())cleanup.run();else main.post(cleanup);
        if(old!=null){try{old.disconnect();}catch(Exception ignored){}old.close();}
    }
    private void fail(String message) { disconnectDevice(); state("idle", message); error(message); }
    private void subscribeNotifications(BluetoothGatt g){
        if(g!=gatt||closed||ready)return;
        BluetoothGattService service=g.getService(SERVICE);
        BluetoothGattCharacteristic notifier=service==null?null:service.getCharacteristic(notificationUuid);
        if(notifier==null||!g.setCharacteristicNotification(notifier,true)){fail("CAN 特征配置失败");return;}
        BluetoothGattDescriptor descriptor=notifier.getDescriptor(CCCD);
        if(descriptor==null){fail("设备缺少通知订阅描述符");return;}
        subscriptionDescriptor=descriptor;
        boolean ok;
        if(Build.VERSION.SDK_INT>=33)ok=g.writeDescriptor(descriptor,BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)==BluetoothStatusCodes.SUCCESS;
        else {descriptor.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);ok=g.writeDescriptor(descriptor);}
        if(!ok)fail("无法订阅 CAN 数据");
    }
    private final BluetoothGattCallback callback = new BluetoothGattCallback() {
        @Override public void onConnectionStateChange(BluetoothGatt g, int status, int newState) { main.post(() -> {
            if (g != gatt) return;
            if (status != BluetoothGatt.GATT_SUCCESS || newState == BluetoothProfile.STATE_DISCONNECTED) {
                disconnectDevice(); state("idle", "连接已断开（" + status + "），可重新扫描连接"); return;
            }
            if (newState == BluetoothProfile.STATE_CONNECTED && !g.discoverServices()) fail("无法发现 BLE 服务");
        }); }
        @Override public void onServicesDiscovered(BluetoothGatt g, int status) { main.post(() -> {
            if (g != gatt) return;
            BluetoothGattService service = g.getService(SERVICE);
            if (status != 0 || service == null) { fail("设备没有 CAN Scope 服务，请检查固件"); return; }
            writer = service.getCharacteristic(WRITE);
            if(writer==null){fail("CAN 特征配置失败");return;}
            notificationUuid=BleNotifications.select(service.getCharacteristic(BleNotifications.BATCH)!=null);
            if(notificationUuid.equals(BleNotifications.BATCH)){
                // Serialize MTU and CCCD operations. Never overlap a pending
                // negotiation with a descriptor write or any CAN command.
                awaitingNotificationMtu=true;
                if(g.requestMtu(247)){
                    main.postDelayed(()->{if(g==gatt&&awaitingNotificationMtu){awaitingNotificationMtu=false;fail("蓝牙 MTU 协商超时，请重新连接");}},5000);
                    return;
                }
                awaitingNotificationMtu=false;
            }
            subscribeNotifications(g);
        }); }
        @Override public void onMtuChanged(BluetoothGatt g,int mtu,int status){main.post(()->{
            if(g!=gatt||!awaitingNotificationMtu)return;
            awaitingNotificationMtu=false;
            // Failed negotiation keeps MTU23 valid: firmware emits one frame.
            subscribeNotifications(g);
        });}
        @Override public void onDescriptorWrite(BluetoothGatt g, BluetoothGattDescriptor descriptor, int status) { main.post(() -> {
            if (g != gatt||descriptor!=subscriptionDescriptor||!CCCD.equals(descriptor.getUuid())||descriptor.getCharacteristic()==null||!notificationUuid.equals(descriptor.getCharacteristic().getUuid())) return;
            if (status != 0) { fail("订阅失败：" + status); return; }
            synchronized(transportLock){if(g!=gatt||ready)return;ready=true;subscriptionDescriptor=null;if(history.subscribed(address,deviceName))saveReconnectHistory();}
            updatePriority();
            state("ready", "已连接 · 正在接收实时数据");
            scheduleReceiveDrain();
        }); }
        @Override public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic characteristic, byte[] value) {
            if(g==gatt&&notificationUuid.equals(characteristic.getUuid()))BleNotifications.dispatch(value,notificationUuid.equals(BleNotifications.BATCH),p->receive(g,p));
        }
        @Override public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic characteristic) {
            if(Build.VERSION.SDK_INT<33&&g==gatt&&notificationUuid.equals(characteristic.getUuid()))BleNotifications.dispatch(characteristic.getValue(),notificationUuid.equals(BleNotifications.BATCH),p->receive(g,p));
        }
        @Override public void onCharacteristicRead(BluetoothGatt g,BluetoothGattCharacteristic c,byte[] value,int status){networkRead(g,c,value,status);}
        @Override public void onCharacteristicRead(BluetoothGatt g,BluetoothGattCharacteristic c,int status){if(Build.VERSION.SDK_INT<33)networkRead(g,c,c.getValue(),status);}
        private void networkRead(BluetoothGatt g,BluetoothGattCharacteristic c,byte[] value,int status){if(NETWORK.equals(c.getUuid()))main.post(()->{WifiProvisioner p=provisioner;if(g==gatt&&p!=null&&p.gatt==g)p.readResult(status,value);});}
        @Override public void onCharacteristicWrite(BluetoothGatt g,BluetoothGattCharacteristic characteristic,int status){
            if(NETWORK.equals(characteristic.getUuid())){main.post(()->{WifiProvisioner p=provisioner;if(g==gatt&&p!=null&&p.gatt==g)p.written(status);});return;}

            if(WRITE.equals(characteristic.getUuid()))enqueueControl(g,status,null,SystemClock.elapsedRealtime());
        }

    };
    private void receiveOverflow(){if(receiveOverflowPending.compareAndSet(false,true)){int session=generation;main.post(()->{if(session==generation&&!closed)fail("设备状态队列拥塞，请重新连接");});}}
    private void receive(BluetoothGatt g, byte[] data) {
        if(closed||g!=gatt||data==null||data.length!=20||data[0]!=1)return;
        if(data[1]==17&&g!=null){int session=generation;boolean wifi=data[2]!=0;main.post(()->{
            if(session!=generation||g!=gatt||wifi==deviceWifiMode)return;
            synchronized(transportLock){deviceWifiMode=wifi;for(String key:queries.keys())queryStatus(key,false,"设备通信模式已切换，查询已停止");queries.reset();telemetry.reset();devicePolling=homeTelemetrySupported=false;writing=false;currentQueryWrite=null;received.clear();}
            state("ready",wifi?"设备为 Wi-Fi 模式：蓝牙可配网；接收数据请切换 App 通信方式":"设备已切回蓝牙，可重新开启查询");emit("deviceMode",obj("wifi",wifi));
        });return;}
        long arrived=SystemClock.elapsedRealtime();
        if(data[1]==8){enqueueControl(g,0,data.clone(),arrived);return;}
        if(!received.offer(data,generation,arrived)){
            receiveOverflow();return;
        }
        scheduleReceiveDrain();
    }
    private void processReceived(TelemetryInbox.Frame frame,JSONArray raw,JSONArray samples,String batchKey){
        byte[] data=frame.data;long age=Math.max(0,SystemClock.elapsedRealtime()-frame.arrived);
        String value=packetHex(data);
            if(data[1]==21){synchronized(transportLock){
                boolean next=data[2]==1;
                if(next!=deviceUart){deviceUart=next;clearQueries("数据类型改变，查询已停止");if(next){detailKey="";busLoadActive=false;}else configureTelemetry();}
            }}
            synchronized(transportLock){
                if(settingType==0x13&&data[1]==7&&data[2]!=0)settingsHoldUntil=0;
                if(settingType==0x12&&data[1]==5){int token=(data[16]&255)|((data[17]&255)<<8)|((data[18]&255)<<16)|((data[19]&255)<<24);if(token==settingToken&&token!=0)settingsHoldUntil=0;}
            }
            if(data[1]==15){synchronized(transportLock){imuSupported=(data[2]&1)!=0;configureTelemetry();updateHome();}return;}
            if(data[1]==5){synchronized(transportLock){devicePolling=(data[3]&8)!=0;homeTelemetrySupported=(data[3]&64)!=0;detailSupported=(data[3]&128)!=0;int max=java.nio.ByteBuffer.wrap(data).order(java.nio.ByteOrder.LITTLE_ENDIAN).getInt(12);detailMaxHz=detailSupported&&max>=1&&max<=1000?max:0;configureTelemetry();}}
            if(data[1]==16){synchronized(transportLock){
                Integer[] values=telemetry.temperatures(data);
                if(values!=null){
                    JSONArray temperatures=new JSONArray();for(Integer temperature:values)temperatures.put(temperature==null?JSONObject.NULL:temperature);
                    emit("motorTemperatures",obj("start",data[2]&255,"count",data[3]&255,"temperatures",temperatures,"age",age));
                }
                return;
            }}
            if(data[1]==9||data[1]==10){synchronized(transportLock){
                java.nio.ByteBuffer b=java.nio.ByteBuffer.wrap(data).order(java.nio.ByteOrder.LITTLE_ENDIAN);
                int revision=b.getInt(16);
                if(telemetry.accept(revision)){
                    if(data[1]==10){telemetry.acknowledge(revision);received.discardData();busLoadActive=wantsBusLoad&&data[2]==0;emit("telemetryMode",obj("quiet",data[2]==1,"mode",data[2]&255,"hz",b.getInt(8)));}
                    else emit("onlineSummary",obj("online",b.getInt(4),"seen",b.getInt(8),"count",data[3]&255,"age",age));
                }
                return;
            }}
            if(data[1]==11){synchronized(transportLock){
                int revision=java.nio.ByteBuffer.wrap(data).order(java.nio.ByteOrder.LITTLE_ENDIAN).getInt(4);
                if(selected==this&&!detailKey.isEmpty()&&telemetry.accept(revision)){
                    if(detailKey.equals(batchKey))samples.put(obj("hex",value,"age",age));
                }
                return;
            }}
            if(data[1]==12){synchronized(transportLock){
                java.nio.ByteBuffer b=java.nio.ByteBuffer.wrap(data).order(java.nio.ByteOrder.LITTLE_ENDIAN);
                if(selected==this&&!detailKey.isEmpty()&&telemetry.accept(b.getInt(16)))emit("motorRates",obj("key",detailKey,"received",b.getInt(4),"sampled",b.getInt(8),"elapsed",b.getInt(12),"age",age));
                return;
            }}
            if(data[1]==13||data[1]==14){
                // Device-wide load is independent of the lossy raw-frame queue.
                // Only a confirmed full-telemetry channel may display this window.
                if(selected==this&&detailKey.isEmpty()&&busLoadActive)emit(data[1]==13?"busLoad":"busRates",obj("hex",value,"age",age));
                return;
            }
            if(data[1]==7&&data[2]!=2&&(data[3]&255)>0&&(data[3]&255)<=16){
                String reportedName=new String(data,4,data[3]&255,java.nio.charset.StandardCharsets.UTF_8);
                if(!reportedName.equals(deviceName)){deviceName=reportedName;getPreferences(MODE_PRIVATE).edit().putString("name_"+slot,deviceName).apply();}
                if(history.renamed(address,reportedName))saveReconnectHistory();
                emit("deviceInfo",obj("name",deviceName+" · "+(lan!=null?networkIp:address)));updateHome();
            }
            // Old queued raw packets cannot enter a motor detail page.
            if(selected!=null&&!selected.detailKey.isEmpty()&&data[1]>=1&&data[1]<=4)return;
            raw.put(obj("hex",value,"age",age));
    }
    private void writePacket(String hex,FeedbackQueries.Request queryRequest) {
        long now=SystemClock.elapsedRealtime();
        if(deviceWifiMode){if(queryRequest!=null)queryFailure(queries.rejected(queryRequest,now));else error("设备当前为 Wi-Fi 模式；请切换 App 通信方式，或把设备切回蓝牙");return;}
        if(!ready||(lan==null&&(gatt==null||writer==null))||writing||networkBusy()){if(queryRequest!=null)queryFailure(queries.rejected(queryRequest,now));else error(writing?"上一项设置正在保存，请稍后重试":"请先连接 ESP32");return;}
        if(hex==null||!hex.matches("[0-9a-fA-F]{40}")){if(queryRequest!=null)queryFailure(queries.rejected(queryRequest,now));else error("指令格式错误");return;}
        byte[] data=new byte[20];for(int i=0;i<20;i++)data[i]=(byte)Integer.parseInt(hex.substring(i*2,i*2+2),16);
        if(!MonitorCommands.allowed(data)){if(queryRequest!=null)queryFailure(queries.rejected(queryRequest,now));else error("监控版仅支持设备设置和达妙反馈查询");return;}
        if(deviceUart && (data[1]==0x10||data[1]==0x12||data[1]==0x20||(data[1]==0x14&&java.nio.ByteBuffer.wrap(data).order(java.nio.ByteOrder.LITTLE_ENDIAN).getInt(8)!=0))){error("当前为 UART 数据，请先在设备上切回 CAN");return;}
        // Reserve the callback identity before calling Android: callbacks may
        // arrive on another thread before writeCharacteristic returns.
        writeSequence++;
        boolean ok;
        try{
            if(lan!=null)ok=lan.send(data);
            else if(Build.VERSION.SDK_INT>=33)ok=gatt.writeCharacteristic(writer,data,BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT)==BluetoothStatusCodes.SUCCESS;
            else{writer.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);writer.setValue(data);ok=gatt.writeCharacteristic(writer);}
        }catch(Exception e){if(queryRequest!=null)queryFailure(queries.rejected(queryRequest,now));else error("蓝牙写入失败："+e.getMessage());return;}
        if(!ok){if(queryRequest!=null)queryFailure(queries.rejected(queryRequest,now));else{if(data[1]==0x16||data[1]==0x17){telemetry.failed();telemetryRetryAt=now+200;}else error("蓝牙繁忙，请重试");}return;}
        writing=true;telemetryWrite=data[1]==0x16||data[1]==0x17;queryWrite=queryRequest!=null;
        currentQueryWrite=queryRequest;writeDeadline=now+6000;
        if(queryRequest!=null)queries.submitted(queryRequest,now);
    }
    void shutdown() {
        if(chartPanel==this)exitChartFullscreen(true);
        closed = true; pageReady = false;
        disconnectDevice(); main.removeCallbacksAndMessages(null);control.removeCallbacksAndMessages(null);controlThread.quitSafely();
        web.removeJavascriptInterface("CanAndroid"); web.destroy();
    }
    private void saveReconnectHistory(){getPreferences(MODE_PRIVATE).edit().putString("last_success_address_"+slot,history.address()).putString("last_success_name_"+slot,history.name()).apply();}
    private void clearReconnectHistory(){clearNetworkIdentity();history.clear();getPreferences(MODE_PRIVATE).edit().remove("last_success_address_"+slot).remove("last_success_name_"+slot).apply();}

    }
}
