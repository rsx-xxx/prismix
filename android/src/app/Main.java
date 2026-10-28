package app.prismix.band;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.SharedPreferences;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.net.Uri;
import android.net.http.SslError;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.format.DateUtils;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.webkit.JavascriptInterface;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * Shell around the host's /remote page. On launch it finds hosts on its own with mDNS (_prismix._tcp) and a /24 sweep
 * in parallel: one host is joined directly, several are listed with their connection history. The WebView trusts the
 * host's self-signed cert for that host only. The screen stays on only while something plays.
 *
 * The process is bound to the Wi-Fi network, so a VPN or per-app proxy on the phone (Cloudflare WARP and the like) does
 * not swallow traffic to the local network. A host can be given by address or by name (prismix.local); connecting
 * shows progress until the page is really there instead of a black screen.
 */
public class Main extends Activity {
    private static final String BG = "#121110";
    static final int PORT = 8443;
    private static final String SERVICE = "_prismix._tcp.";

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final Map<String, Host> found = new LinkedHashMap<>(); // address -> host
    private boolean listing; // the host list is on screen: late arrivals refresh it
    private SharedPreferences prefs;
    private WebView web;
    private LinearLayout panel;
    private ScrollView scroll; // the screens can outgrow a small phone, especially with the keyboard up
    private String host = "";
    private int gen;
    private ConnectivityManager cm;
    private Network wifi;
    private ConnectivityManager.NetworkCallback wifiCallback;
    private boolean netReady; // Wi-Fi bound, or we gave up waiting for it
    private Runnable afterNet;
    private String wantName; // a name the user typed, looked for among the discovered hosts
    private boolean connecting; // the page is loading behind the progress screen
    private boolean cloudMode; // the web view shows the Band Cloud player, not a LAN host
    private boolean cloudFresh; // history of the sign-in redirect is dropped once the player has loaded
    private NsdManager nsd;
    private NsdManager.DiscoveryListener nsdListener;
    private ExecutorService pool;
    private static Main current;

    /** A locally built APK may carry a member invite: take it once, so signing out stays signed out. */
    private void importEmbeddedInvite() {
        if (prefs.getBoolean("embeddedDone", false)) return;
        String invite = Embedded.invite(this);
        if (invite == null) return;
        try {
            Cloud.save(prefs, Cloud.parse(invite, ""));
            prefs.edit().putBoolean("embeddedDone", true).apply();
        } catch (Cloud.Failure e) { /* a bad embedded invite: the sign-in screen is still there */ }
    }

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        current = this;
        bars(BG, false);
        prefs = getSharedPreferences("band", MODE_PRIVATE);
        importEmbeddedInvite();

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor(BG));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        if (Build.VERSION.SDK_INT >= 26) web.setRendererPriorityPolicy(WebView.RENDERER_PRIORITY_IMPORTANT, false);
        web.setWebChromeClient(new Chrome(this));
        web.setWebViewClient(new Client(this));
        web.addJavascriptInterface(new Bridge(this), "Android");

        panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER_HORIZONTAL);
        panel.setPadding(dp(28), dp(96), dp(28), dp(28));
        panel.setBackgroundColor(Color.parseColor(BG));

        scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Color.parseColor(BG));
        scroll.addView(panel, new FrameLayout.LayoutParams(-1, -2));

        FrameLayout root = new FrameLayout(this);
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        root.addView(scroll, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        bindWifi();
        open(getIntent());
    }

    /** Pins this process to the Wi-Fi network: a VPN owns the default route, but the host is on the local network. */
    private void bindWifi() {
        try {
            cm = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
            NetworkRequest req = new NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build();
            wifiCallback = new Wifi(this);
            cm.requestNetwork(req, wifiCallback);
        } catch (RuntimeException e) {
            wifiCallback = null; // no Wi-Fi API: the default route has to do
        }
        ui.postDelayed(this::netUp, 1500); // mobile data only, or no answer: do not wait for it
    }

    /** Wi-Fi came back (maybe another network): if the host on screen is gone from it, search again instead of waiting forever. */
    private void recheck() {
        final String a = host;
        if (a.isEmpty() || web.getVisibility() != View.VISIBLE || connecting) return;
        final int g = gen;
        new Thread(() -> {
            Host h = null;
            for (int i = 0; i < 3 && h == null; i++) {
                if (i > 0) { try { Thread.sleep(1500); } catch (InterruptedException e) { return; } }
                h = hello(a);
            }
            final boolean gone = h == null;
            ui.post(() -> { if (gone && g == gen && a.equals(host) && web.getVisibility() == View.VISIBLE) discover(); });
        }, "recheck").start();
    }

    private void netUp() {
        netReady = true;
        Runnable r = afterNet;
        afterNet = null;
        if (r != null) r.run();
    }

    /** Runs `r` once the Wi-Fi binding is in place (or given up on), so the first probes already take the right route. */
    private void whenNet(Runnable r) {
        if (netReady) r.run(); else afterNet = r;
    }

    /** Notifications need a grant on Android 13+; battery savers kill a silent background app, so ask once to be exempt. */
    private void ask() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED
                && !prefs.getBoolean("notifAsked", false)) {
            prefs.edit().putBoolean("notifAsked", true).apply(); // the page reports media every few seconds: never nag
            requestPermissions(new String[] { "android.permission.POST_NOTIFICATIONS" }, 1);
            return;
        }
        if (Build.VERSION.SDK_INT >= 23 && !prefs.getBoolean("batteryAsked", false)) {
            android.os.PowerManager pm = (android.os.PowerManager) getSystemService(POWER_SERVICE);
            if (!pm.isIgnoringBatteryOptimizations(getPackageName())) {
                prefs.edit().putBoolean("batteryAsked", true).apply();
                try {
                    startActivity(new Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
                } catch (RuntimeException e) { /* no such screen on this ROM */ }
            }
        }
    }

    @Override
    protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        open(i);
    }

    /** A scanned QR / link carries the host; otherwise discover. */
    private void open(Intent i) {
        Uri d = i == null ? null : i.getData();
        String h = d == null ? null : d.getHost();
        if (h == null) { discover(); return; }
        // Android's resolver often cannot do mDNS: a `name.local` link carries the address in ?ip= for that case.
        String ip = d.getQueryParameter("ip");
        if (h.endsWith(".local") && ip != null && !ip.isEmpty()) go(ip);
        else connect(h);
    }

    @Override
    protected void onDestroy() {
        stopSearch();
        if (cm != null && wifiCallback != null) {
            try { cm.unregisterNetworkCallback(wifiCallback); cm.bindProcessToNetwork(null); } catch (RuntimeException e) { /* gone */ }
        }
        stopService(new Intent(this, MediaService.class));
        if (current == this) current = null;
        super.onDestroy();
    }

    /** Runs a button press from the notification in the page. */
    static void js(String op, double arg) {
        Main m = current;
        if (m != null) m.ui.post(() -> m.web.evaluateJavascript("window.__media&&window.__media('" + op + "'," + arg + ")", null));
    }

    static void post(Runnable r) {
        Main m = current;
        if (m != null) m.ui.post(r);
    }

    /** Runs a script in the page, if there is one. */
    static void eval(final String script) {
        Main m = current;
        if (m != null) m.ui.post(() -> m.web.evaluateJavascript(script, null));
    }

    /** The signed-in Band Cloud member credential, if any. */
    static Cloud.Credential cloud() {
        Main m = current;
        return m == null ? null : Cloud.load(m.prefs);
    }

    static String hostName() {
        Main m = current;
        return m == null ? "" : m.host;
    }

    /** A Prismix computer on the network: stable id, the name its owner gave it, current address. */
    private static class Host {
        final String addr, id, name, domain;
        Host(String addr, String id, String name, String domain) { this.addr = addr; this.id = id; this.name = name; this.domain = domain; }
    }

    private void keepAwake(boolean on) {
        if (on) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }

    private JSONObject history() {
        try { return new JSONObject(prefs.getString("history", "{}")); } catch (JSONException e) { return new JSONObject(); }
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private void bars(String bg, boolean darkIcons) {
        int c = Color.parseColor(bg);
        getWindow().setStatusBarColor(c);
        getWindow().setNavigationBarColor(c);
        int f = 0;
        if (darkIcons && Build.VERSION.SDK_INT >= 23) f |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
        if (darkIcons && Build.VERSION.SDK_INT >= 26) f |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
        getWindow().getDecorView().setSystemUiVisibility(f);
    }

    /** What the web UI can ask of the shell. */
    private static class Bridge {
        private final Main m;
        Bridge(Main m) { this.m = m; }
        @JavascriptInterface public String deviceName() { return Build.MODEL == null ? "" : Build.MODEL; }
        @JavascriptInterface public void setBars(final String bg, final boolean darkIcons) {
            m.ui.post(() -> { try { m.bars(bg, darkIcons); m.web.setBackgroundColor(Color.parseColor(bg)); } catch (RuntimeException e) { /* bad colour */ } });
        }
        @JavascriptInterface public boolean media(String title, String artist, boolean playing, long pos, long dur, float rate, String art) {
            Intent i = new Intent(m, MediaService.class).putExtra("title", title).putExtra("artist", artist).putExtra("playing", playing)
                    .putExtra("pos", pos).putExtra("dur", dur).putExtra("rate", rate).putExtra("art", art);
            m.ui.post(m::ask);
            m.ui.post(() -> m.keepAwake(playing));
            try {
                if (Build.VERSION.SDK_INT >= 26) m.startForegroundService(i); else m.startService(i);
                return true;
            } catch (RuntimeException e) { return false; /* started from the background: the page retries */ }
        }
        @JavascriptInterface public void tap() {
            m.ui.post(() -> m.web.performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP));
        }
        @JavascriptInterface public void download(String url, String folder) { Saver.save(m.getApplicationContext(), url, folder); }
        /** A file link in the cloud player: saved through the member token, since the page's cookie does not reach a download. */
        @JavascriptInterface public void cloudDownload(String url, String name) { if (m.cloudMode) Saver.saveCloud(m.getApplicationContext(), url, name); }
        @JavascriptInterface public void mediaStop() { m.stopService(new Intent(m, MediaService.class)); m.ui.post(() -> m.keepAwake(false)); }
        @JavascriptInterface public void switchHost() { m.ui.post(() -> m.discover()); }
        @JavascriptInterface public String offlineList() { return Offline.list(m.getApplicationContext()).toString(); }
        @JavascriptInterface public void offlineSave(String id, String model, String pin) { Offline.save(m.getApplicationContext(), id, model, pin); }
        @JavascriptInterface public void offlineRemove(String id) { Offline.remove(m.getApplicationContext(), id); }
        /** The page's settings (name, part, theme...) live per origin: keep a copy so the offline page starts with them. */
        @JavascriptInterface public void stash(String json) { if (json != null && json.length() < 16384) m.prefs.edit().putString("stash", json).apply(); }
        @JavascriptInterface public String stashed() { return m.prefs.getString("stash", "{}"); }
    }

    private static class Chrome extends WebChromeClient {
        private final Main m;
        Chrome(Main m) { this.m = m; }
        @Override
        public void onProgressChanged(WebView v, int p) { if (p >= 70) m.reveal(); }
    }

    private static class Wifi extends ConnectivityManager.NetworkCallback {
        private final Main m;
        Wifi(Main m) { this.m = m; }
        @Override
        public void onAvailable(Network n) {
            m.wifi = n;
            try { m.cm.bindProcessToNetwork(n); } catch (RuntimeException e) { /* keep the default route */ }
            m.ui.post(m::netUp);
            m.ui.post(m::recheck);
        }

        @Override
        public void onLost(Network n) {
            if (n.equals(m.wifi)) m.wifi = null;
            try { m.cm.bindProcessToNetwork(null); } catch (RuntimeException e) { /* nothing bound */ }
        }
    }

    private static class Client extends WebViewClient {
        private final Main m;
        Client(Main m) { this.m = m; }
        @Override
        public void onReceivedSslError(WebView v, SslErrorHandler h, SslError e) {
            // Self-signed on the LAN: accept only the host we chose.
            String got = Uri.parse(e.getUrl()).getHost();
            if (got != null && got.equals(m.host)) h.proceed(); else h.cancel();
        }

        @Override
        public void onPageFinished(WebView v, String url) {
            if (m.cloudMode && m.cloudFresh && !url.contains("/join")) { m.cloudFresh = false; v.clearHistory(); }
            m.reveal();
        }

        /** The offline origin is answered from the phone itself; everything else goes to the host. */
        @Override
        public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest r) {
            Uri u = r.getUrl();
            if (!Offline.HOST.equals(u.getHost())) return null;
            return Offline.serve(m.getApplicationContext(), u.getPath());
        }

        @Override
        public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
            if (!r.isForMainFrame()) return;
            if (m.cloudMode) m.showCloud("The cloud is not answering"); // the cloud is not a host to search for
            else m.discover(); // host moved or went away: look again
        }
    }

    // ── discovery ──

    private void discover() {
        discover(null);
    }

    /** Looks for hosts; with a name, joins the one that answers to it. */
    private void discover(String want) {
        wantName = want;
        connecting = false;
        stopSearch();
        final int g = ++gen;
        found.clear();
        listing = false;
        showSearching();
        whenNet(() -> sweep(g));
        ui.postDelayed(() -> { if (g == gen && (found.isEmpty() || wantName != null)) { stopSearch(); showNone(); } }, 10500);
    }

    private void sweep(final int g) {
        if (g != gen) return;
        pool = Executors.newFixedThreadPool(48);
        final ExecutorService p = pool;
        p.execute(() -> {
            ui.post(() -> { if (g == gen) startNsd(g); });
            String base = subnet();
            if (base == null) return;
            for (int i = 1; i < 255; i++) {
                final String ip = base + i;
                p.execute(() -> {
                    Host h = hello(ip);
                    if (h != null) ui.post(() -> add(g, h));
                });
            }
        });
    }

    private void stopSearch() {
        if (pool != null) { pool.shutdownNow(); pool = null; }
        if (nsd != null && nsdListener != null) {
            try { nsd.stopServiceDiscovery(nsdListener); } catch (RuntimeException e) { /* already stopped */ }
        }
        nsdListener = null;
    }

    private void add(int g, Host h) {
        if (g != gen || found.containsKey(h.addr)) return;
        found.put(h.addr, h);
        if (wantName != null && wantName.equalsIgnoreCase(h.domain)) { go(h.addr); return; }
        if (wantName != null) return; // looking for one particular host
        if (listing) showList();
        else if (found.size() == 1) ui.postDelayed(() -> decide(g), 2000); // give a second host time to answer
    }

    /** One host: join it. Several: always ask, with the history shown. */
    private void decide(int g) {
        if (g != gen) return;
        if (found.size() == 1) { go(found.keySet().iterator().next()); return; }
        listing = true;
        showList();
    }

    private void startNsd(final int g) {
        try {
            nsd = (NsdManager) getSystemService(NSD_SERVICE);
            nsdListener = (NsdManager.DiscoveryListener) listener(NsdManager.DiscoveryListener.class, g);
            nsd.discoverServices(SERVICE, NsdManager.PROTOCOL_DNS_SD, nsdListener);
        } catch (RuntimeException e) {
            nsdListener = null; // the sweep still covers us
        }
    }

    // d8 8.2 crashes on classes that implement the NsdManager listener interfaces, so use dynamic proxies.
    private Object listener(Class<?> type, final int g) {
        return java.lang.reflect.Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[] { type }, (proxy, m, args) -> {
            String n = m.getName();
            if (n.equals("onServiceFound")) {
                nsd.resolveService((NsdServiceInfo) args[0], (NsdManager.ResolveListener) listener(NsdManager.ResolveListener.class, g));
            } else if (n.equals("onServiceResolved")) {
                InetAddress a = ((NsdServiceInfo) args[0]).getHost();
                if (a instanceof Inet4Address) probe(g, a.getHostAddress());
            } else if (n.equals("hashCode")) {
                return System.identityHashCode(proxy);
            } else if (n.equals("equals")) {
                return proxy == args[0];
            } else if (n.equals("toString")) {
                return "NsdListener";
            }
            return null;
        });
    }

    private void probe(final int g, final String ip) {
        ExecutorService p = pool;
        if (p == null) return;
        p.execute(() -> {
            Host h = hello(ip);
            if (h != null) ui.post(() -> add(g, h));
        });
    }

    /** First private IPv4 prefix ("192.168.1.") of this device, preferring Wi-Fi. */
    private static String subnet() {
        String best = null;
        try {
            for (NetworkInterface n : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!n.isUp() || n.isLoopback()) continue;
                for (InetAddress a : Collections.list(n.getInetAddresses())) {
                    if (!(a instanceof Inet4Address) || !a.isSiteLocalAddress()) continue;
                    String s = a.getHostAddress();
                    s = s.substring(0, s.lastIndexOf('.') + 1);
                    if (n.getName().startsWith("wlan")) return s;
                    if (best == null) best = s;
                }
            }
        } catch (Exception e) { /* no interfaces */ }
        return best;
    }

    static final TrustManager[] TRUST_ALL = { new X509TrustManager() {
        public void checkClientTrusted(X509Certificate[] c, String t) { }
        public void checkServerTrusted(X509Certificate[] c, String t) { }
        public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
    } };

    private static SSLSocketFactory factory;

    /** One socket factory for every call to the host: building an SSLContext per probe made the /24 sweep needlessly slow. */
    static synchronized SSLSocketFactory tls() throws Exception {
        if (factory == null) {
            SSLContext ctx = SSLContext.getInstance("TLS");
            ctx.init(null, TRUST_ALL, new SecureRandom());
            factory = ctx.getSocketFactory();
        }
        return factory;
    }

    /** Only the local network is ever joined: a link or QR from elsewhere must not point the app (and its bridge) at the internet. */
    static boolean isLocal(String ip) {
        if (!ip.matches("\\d{1,3}(\\.\\d{1,3}){3}")) return false;
        String[] p = ip.split("\\.");
        int a = Integer.parseInt(p[0]), b = Integer.parseInt(p[1]);
        if (a > 255 || b > 255 || Integer.parseInt(p[2]) > 255 || Integer.parseInt(p[3]) > 255) return false;
        return a == 10 || a == 127 || (a == 172 && b >= 16 && b < 32) || (a == 192 && b == 168) || (a == 169 && b == 254)
                || (a == 100 && b >= 64 && b < 128); // CGNAT, which Tailscale and some hotspots use
    }

    /** The host answering at this address, else null. Only used to discover, never to send data. */
    private static Host hello(String addr) {
        HttpsURLConnection c = null;
        try {
            c = (HttpsURLConnection) new URL("https://" + addr + ":" + PORT + "/remote/hello").openConnection();
            c.setSSLSocketFactory(tls());
            c.setHostnameVerifier((h, s) -> true);
            c.setConnectTimeout(700);
            c.setReadTimeout(1500);
            if (c.getResponseCode() != 200) return null;
            InputStream in = c.getInputStream();
            java.io.ByteArrayOutputStream o = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[512];
            int n;
            while ((n = in.read(buf)) > 0 && o.size() < 4096) o.write(buf, 0, n);
            JSONObject j = new JSONObject(o.toString("UTF-8"));
            if (!"prismix".equals(j.optString("app"))) return null;
            return new Host(addr, j.optString("id", addr), j.optString("name", addr), j.optString("domain", ""));
        } catch (Exception e) {
            return null;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    // ── screens ──

    private TextView text(String s, int sp, int color, int bottom) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(color);
        t.setGravity(Gravity.CENTER);
        t.setPadding(0, 0, 0, dp(bottom));
        return t;
    }

    private Button button(String label, boolean primary) {
        Button b = new Button(this);
        b.setText(label);
        b.setAllCaps(false);
        b.setTextColor(primary ? Color.parseColor("#1a1206") : Color.WHITE);
        GradientDrawable fill = new GradientDrawable();
        fill.setColor(Color.parseColor(primary ? "#ff9a3d" : "#2a2724"));
        fill.setCornerRadius(dp(14));
        b.setBackground(new RippleDrawable(ColorStateList.valueOf(Color.argb(60, 255, 255, 255)), fill, null));
        b.setStateListAnimator(null);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, dp(52));
        lp.topMargin = dp(10);
        b.setLayoutParams(lp);
        return b;
    }

    private void screen(String title, String sub) {
        cloudMode = false;
        panel.removeAllViews();
        web.setVisibility(View.GONE);
        scroll.setVisibility(View.VISIBLE);
        scroll.scrollTo(0, 0);
        bars(BG, false);
        panel.addView(text("Prismix Band", 28, Color.WHITE, 6));
        panel.addView(text(title, 16, Color.parseColor("#e8e2da"), 4));
        if (sub != null) panel.addView(text(sub, 14, Color.parseColor("#9b948b"), 20));
    }

    private ProgressBar spinner() {
        ProgressBar p = new ProgressBar(this);
        p.setIndeterminateTintList(ColorStateList.valueOf(Color.parseColor("#ff9a3d")));
        return p;
    }

    private void scanButton() {
        Button b = button("Scan QR code", false);
        b.setOnClickListener(v -> startActivityForResult(new Intent(this, ScanActivity.class), 5));
        panel.addView(b);
    }

    @Override
    protected void onActivityResult(int code, int res, Intent data) {
        super.onActivityResult(code, res, data);
        if (code == 5 && res == RESULT_OK && data != null) open(data);
    }

    /** Songs saved on the phone can be played without any host. */
    private void offlineButton() {
        int n = Offline.list(getApplicationContext()).length();
        if (n == 0) return;
        Button b = button("Play saved songs (" + n + ")", false);
        b.setOnClickListener(v -> goOffline());
        panel.addView(b);
    }

    private void goOffline() {
        cloudMode = false;
        stopSearch();
        ++gen;
        host = "";
        wantName = null;
        listing = false;
        connecting = true;
        web.setVisibility(View.INVISIBLE);
        web.loadUrl("https://" + Offline.HOST + "/remote");
    }

    // ── Band Cloud ──

    private void cloudButton() {
        Button b = button("Band Cloud", false);
        b.setOnClickListener(v -> showCloud(null));
        panel.addView(b);
    }

    /** Sign in to the private cloud with an invite link (it carries the address and the token); once signed in, open the player. */
    private void showCloud(String problem) {
        stopSearch();
        ++gen;
        connecting = false;
        host = "";
        listing = false;
        final Cloud.Credential have = Cloud.load(prefs);
        screen("Band Cloud", have == null ? "Paste the invite link you were sent." : "Signed in to " + have.host());
        final TextView err = text(problem == null ? "" : problem, 14, Color.parseColor("#ff6b5b"), 8);
        if (have != null) {
            Button open = button("Open Band Cloud", true);
            open.setOnClickListener(v -> openCloud(have));
            panel.addView(open);
            Button out = button("Sign out", false);
            out.setOnClickListener(v -> signOutCloud());
            panel.addView(out);
        }
        final EditText link = field("Invite link");
        final Button in = button(have == null ? "Sign in" : "Sign in with another invite", have == null);
        in.setOnClickListener(v -> {
            in.setEnabled(false);
            err.setText("");
            signInCloud(link.getText().toString(), "", err, in);
        });
        link.setOnEditorActionListener((v, a, ev) -> { in.performClick(); return true; });
        panel.addView(link, fieldParams());
        panel.addView(in);
        panel.addView(err);
        Button lan = button("LAN connections", false);
        lan.setOnClickListener(v -> discover());
        panel.addView(lan);
    }

    private EditText field(String hint) {
        EditText e = new EditText(this);
        e.setHint(hint);
        e.setTextColor(Color.WHITE);
        e.setHintTextColor(Color.GRAY);
        e.setSingleLine(true);
        e.setImeOptions(EditorInfo.IME_ACTION_GO);
        e.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        return e;
    }

    private LinearLayout.LayoutParams fieldParams() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(16);
        return lp;
    }

    private void signInCloud(final String text, final String address, final TextView err, final Button in) {
        whenNet(() -> new Thread(() -> {
            String bad = null;
            Cloud.Credential c = null;
            try {
                c = Cloud.parse(text, address);
                Cloud.verifyMember(c);
            } catch (Cloud.Failure f) {
                bad = f.getMessage();
            }
            final Cloud.Credential ok = bad == null ? c : null;
            final String why = bad;
            ui.post(() -> {
                if (ok == null) { err.setText(why); in.setEnabled(true); return; }
                Cloud.save(prefs, ok); // only the member credential is kept
                openCloud(ok);
            });
        }, "cloud-login").start());
    }

    /** The join page turns the token into the session cookie and redirects to the player. */
    private void openCloud(final Cloud.Credential c) {
        stopSearch();
        final int g = ++gen;
        screen("Opening Band Cloud…", c.host());
        panel.addView(spinner());
        cloudMode = true;
        cloudFresh = true;
        host = "";
        listing = false;
        connecting = true;
        web.setVisibility(View.INVISIBLE);
        whenNet(() -> {
            web.loadUrl(c.joinUrl());
            ui.postDelayed(() -> { if (g == gen && connecting) { connecting = false; showCloud("The cloud is not answering"); } }, 20000);
        });
    }

    private void signOutCloud() {
        Cloud.clear(prefs);
        android.webkit.CookieManager.getInstance().removeAllCookies(null);
        android.webkit.WebStorage.getInstance().deleteAllData();
        web.clearCache(true);
        showCloud(null);
    }

    private void showSearching() {
        screen(wantName != null ? "Looking for " + wantName + "…" : "Looking for your band's host…",
                "Open Prismix on the computer and share to the network.");
        panel.addView(spinner());
        scanButton();
        offlineButton();
        cloudButton();
    }

    private void showList() {
        screen("Pick a host", found.size() + " computers are sharing on this network.");
        final JSONObject hist = history();
        java.util.List<Host> hosts = new java.util.ArrayList<>(found.values());
        Collections.sort(hosts, (a, b) -> Long.compare(hist.optJSONObject(b.id) == null ? 0 : hist.optJSONObject(b.id).optLong("last"),
                hist.optJSONObject(a.id) == null ? 0 : hist.optJSONObject(a.id).optLong("last")));
        boolean first = true;
        for (final Host h : hosts) {
            JSONObject past = hist.optJSONObject(h.id);
            String note = past == null ? "New" : "Connected before · " + DateUtils.getRelativeTimeSpanString(past.optLong("last"),
                    System.currentTimeMillis(), DateUtils.MINUTE_IN_MILLIS);
            Button b = button(h.name + "\n" + h.addr + " · " + note, first && past != null);
            b.getLayoutParams().height = dp(72);
            b.setOnClickListener(v -> go(h.addr));
            panel.addView(b);
            first = false;
        }
        Button again = button("Search again", false);
        again.setOnClickListener(v -> discover());
        panel.addView(again);
        scanButton();
        offlineButton();
        cloudButton();
        manualEntry();
    }

    private void showNone() {
        showNone(wantName != null ? "Could not find " + wantName : "No host found",
                "Phone and computer must be on the same Wi-Fi, and the host must be sharing. "
                + "A VPN that blocks everything outside its tunnel has to allow the local network.");
    }

    private void showNone(String what, String why) {
        screen(what, why);
        Button retry = button("Search again", true);
        retry.setOnClickListener(v -> discover());
        panel.addView(retry);
        scanButton();
        offlineButton();
        cloudButton();
        manualEntry();
    }

    /** A host typed by name (prismix.local, or any domain that points at it) or by address. */
    private void manualEntry() {
        final EditText in = new EditText(this);
        in.setHint("Name or address, e.g. prismix.local");
        in.setTextColor(Color.WHITE);
        in.setHintTextColor(Color.GRAY);
        in.setSingleLine(true);
        in.setImeOptions(EditorInfo.IME_ACTION_GO);
        in.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        in.setText(prefs.getString("typed", prefs.getString("host", "")));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(24);
        panel.addView(in, lp);
        Button go = button("Connect", false);
        go.setOnClickListener(v -> connect(in.getText().toString()));
        in.setOnEditorActionListener((v, a, ev) -> { connect(in.getText().toString()); return true; });
        panel.addView(go);
    }

    private void showConnecting(String name) {
        screen("Connecting to " + name + "…", "Opening the band page on your local network.");
        panel.addView(spinner());
        Button other = button("Pick another host", false);
        other.setOnClickListener(v -> discover());
        panel.addView(other);
    }

    private static boolean isAddress(String a) {
        return a.matches("\\d{1,3}(\\.\\d{1,3}){3}");
    }

    /** An address, or a name that is looked up on the local network first. */
    private void connect(String raw) {
        String a = raw.trim().replaceFirst("^[a-zA-Z]+://", "");
        a = a.replaceFirst("[/?#].*$", "").replaceFirst(":\\d+$", "").toLowerCase();
        if (a.isEmpty()) return;
        if (!isAddress(a)) prefs.edit().putString("typed", a).apply();
        if (isAddress(a)) go(a); else resolveName(a);
    }

    /** Name -> address through the Wi-Fi network (DNS, or mDNS where the system does it), else by asking the hosts. */
    private void resolveName(final String name) {
        stopSearch();
        final int g = ++gen;
        showConnecting(name);
        whenNet(() -> new Thread(() -> {
            String ip = null;
            try {
                InetAddress[] all = wifi != null ? wifi.getAllByName(name) : InetAddress.getAllByName(name);
                for (InetAddress a : all) if (a instanceof Inet4Address) { ip = a.getHostAddress(); break; }
            } catch (Exception e) { /* mDNS names often do not resolve here: ask the hosts instead */ }
            final String found4 = ip;
            ui.post(() -> {
                if (g != gen) return;
                if (found4 != null) go(found4); else discover(name);
            });
        }, "resolve").start());
    }

    /** Opens the host's page. Progress stays on screen until the page really appears, so there is no black gap. */
    private void go(final String a) {
        if (!isLocal(a)) { stopSearch(); ++gen; connecting = false; showNone("Not on your local network", a + " is not a local address, so the app will not connect to it."); return; }
        stopSearch();
        final int g = ++gen;
        host = a;
        wantName = null;
        listing = false;
        connecting = true;
        showConnecting(found.containsKey(a) ? found.get(a).name : a);
        web.setVisibility(View.INVISIBLE);
        whenNet(() -> new Thread(() -> {
            Host h = null;
            for (int i = 0; i < 3 && h == null; i++) h = hello(a);
            final Host seen = h;
            ui.post(() -> {
                if (g != gen) return;
                if (seen == null) { connecting = false; discover(); return; } // not there (any more): look again
                SharedPreferences.Editor ed = prefs.edit().putString("host", a).putString("name", seen.name);
                try {
                    JSONObject hist = history();
                    hist.put(seen.id, new JSONObject().put("name", seen.name).put("addr", a).put("last", System.currentTimeMillis()));
                    ed.putString("history", hist.toString());
                } catch (JSONException e) { /* history is a nicety */ }
                ed.apply();
                web.loadUrl("https://" + a + ":" + PORT + "/remote");
                ui.postDelayed(() -> { if (g == gen && connecting) { connecting = false; discover(); } }, 15000);
            });
        }, "hello").start());
    }

    private void reveal() {
        if (!connecting) return;
        connecting = false;
        scroll.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
    }

    @Override
    public void onBackPressed() {
        if (cloudMode && web.getVisibility() == View.VISIBLE) {
            // the player closes its own sheet first; with nothing open, back leaves for the cloud screen
            web.evaluateJavascript("window.__back&&window.__back()", v -> { if (!"true".equals(v)) showCloud(null); });
        } else if (web.getVisibility() == View.VISIBLE && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
