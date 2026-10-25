package app.prismix.band;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.webkit.JavascriptInterface;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

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
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * Shell around the host's /remote page. On launch it finds the host on its own: the saved one first, then mDNS
 * (_prismix._tcp) and a /24 sweep in parallel. The WebView trusts the host's self-signed cert for that host only.
 */
public class Main extends Activity {
    private static final String BG = "#121110";
    private static final int PORT = 8443;
    private static final String SERVICE = "_prismix._tcp.";

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final Map<String, String> found = new LinkedHashMap<>(); // address -> host name
    private SharedPreferences prefs;
    private WebView web;
    private LinearLayout panel;
    private String host = "";
    private int gen;
    private NsdManager nsd;
    private NsdManager.DiscoveryListener nsdListener;
    private ExecutorService pool;
    private static Main current;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        current = this;
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        bars(BG, false);
        prefs = getSharedPreferences("band", MODE_PRIVATE);

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor(BG));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        if (Build.VERSION.SDK_INT >= 26) web.setRendererPriorityPolicy(WebView.RENDERER_PRIORITY_IMPORTANT, false);
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new Client(this));
        web.addJavascriptInterface(new Bridge(this), "Android");

        panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER_HORIZONTAL);
        panel.setPadding(dp(28), dp(96), dp(28), dp(28));
        panel.setBackgroundColor(Color.parseColor(BG));

        LinearLayout root = new LinearLayout(this);
        root.addView(web, new LinearLayout.LayoutParams(-1, -1));
        root.addView(panel, new LinearLayout.LayoutParams(-1, -1));
        setContentView(root);
        open(getIntent());
    }

    /** Notifications need a grant on Android 13+; battery savers kill a silent background app, so ask once to be exempt. */
    private void ask() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED) {
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
        if (h == null) { discover(true); return; }
        // Android's resolver often cannot do mDNS: a `name.local` link carries the address in ?ip= for that case.
        String ip = d.getQueryParameter("ip");
        if (h.endsWith(".local") && ip != null && !ip.isEmpty()) connect(ip);
        else if (h.endsWith(".local")) discover(true);
        else connect(h);
    }

    @Override
    protected void onDestroy() {
        stopSearch();
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

    static String hostName() {
        Main m = current;
        return m == null ? "" : m.host;
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
            try {
                if (Build.VERSION.SDK_INT >= 26) m.startForegroundService(i); else m.startService(i);
                return true;
            } catch (RuntimeException e) { return false; /* started from the background: the page retries */ }
        }
        @JavascriptInterface public void tap() {
            m.ui.post(() -> m.web.performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP));
        }
        @JavascriptInterface public void download(String url, String folder) { Saver.save(m.getApplicationContext(), url, folder); }
        @JavascriptInterface public void mediaStop() { m.stopService(new Intent(m, MediaService.class)); }
        @JavascriptInterface public void switchHost() { m.ui.post(() -> m.discover(false)); }
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
        public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
            if (r.isForMainFrame()) m.discover(false); // host moved or went away: look again
        }
    }

    // ── discovery ──

    private void discover(boolean trySaved) {
        stopSearch();
        final int g = ++gen;
        found.clear();
        showSearching();
        pool = Executors.newFixedThreadPool(48);
        final ExecutorService p = pool;
        final String saved = prefs.getString("host", "");
        p.execute(() -> {
            if (trySaved && !saved.isEmpty() && hello(saved) != null) { ui.post(() -> { if (g == gen) connect(saved); }); return; }
            ui.post(() -> { if (g == gen) startNsd(g); });
            String base = subnet();
            if (base == null) return;
            for (int i = 1; i < 255; i++) {
                final String ip = base + i;
                p.execute(() -> {
                    String n = hello(ip);
                    if (n != null) ui.post(() -> add(g, ip, n));
                });
            }
        });
        ui.postDelayed(() -> { if (g == gen && found.isEmpty()) { stopSearch(); showNone(); } }, 9000);
    }

    private void stopSearch() {
        if (pool != null) { pool.shutdownNow(); pool = null; }
        if (nsd != null && nsdListener != null) {
            try { nsd.stopServiceDiscovery(nsdListener); } catch (RuntimeException e) { /* already stopped */ }
        }
        nsdListener = null;
    }

    private void add(int g, String addr, String name) {
        if (g != gen || found.containsKey(addr)) return;
        found.put(addr, name);
        if (found.size() == 1) ui.postDelayed(() -> decide(g), 1200); // let a second host show up
    }

    private void decide(int g) {
        if (g != gen) return;
        stopSearch();
        // The same computer keeps its name when DHCP gives it a new address: reconnect to it without asking.
        String name = prefs.getString("name", "");
        String pick = null;
        for (Map.Entry<String, String> e : found.entrySet()) if (!name.isEmpty() && name.equals(e.getValue())) pick = e.getKey();
        if (pick == null && found.size() == 1) pick = found.keySet().iterator().next();
        if (pick != null) connect(pick); else showList();
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
            String n = hello(ip);
            if (n != null) ui.post(() -> add(g, ip, n));
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

    /** Host name if a Prismix host answers at this address, else null. Only used to discover, never to send data. */
    private static String hello(String addr) {
        HttpsURLConnection c = null;
        try {
            SSLContext ctx = SSLContext.getInstance("TLS");
            ctx.init(null, TRUST_ALL, new SecureRandom());
            c = (HttpsURLConnection) new URL("https://" + addr + ":" + PORT + "/remote/hello").openConnection();
            c.setSSLSocketFactory(ctx.getSocketFactory());
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
            return "prismix".equals(j.optString("app")) ? j.optString("name", addr) : null;
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
        b.setBackgroundColor(Color.parseColor(primary ? "#ff9a3d" : "#2a2724"));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, dp(52));
        lp.topMargin = dp(10);
        b.setLayoutParams(lp);
        return b;
    }

    private void screen(String title, String sub) {
        panel.removeAllViews();
        web.setVisibility(View.GONE);
        panel.setVisibility(View.VISIBLE);
        bars(BG, false);
        panel.addView(text("Prismix Band", 28, Color.WHITE, 6));
        panel.addView(text(title, 16, Color.parseColor("#e8e2da"), 4));
        if (sub != null) panel.addView(text(sub, 14, Color.parseColor("#9b948b"), 20));
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

    private void showSearching() {
        screen("Looking for your band's host…", "Open Prismix on the computer and share to the network.");
        panel.addView(new ProgressBar(this));
        scanButton();
    }

    private void showList() {
        screen("Pick a host", null);
        for (final Map.Entry<String, String> e : found.entrySet()) {
            Button b = button(e.getValue() + "  ·  " + e.getKey(), true);
            b.setOnClickListener(v -> connect(e.getKey()));
            panel.addView(b);
        }
        Button again = button("Search again", false);
        again.setOnClickListener(v -> discover(false));
        panel.addView(again);
        scanButton();
    }

    private void showNone() {
        screen("No host found", "Phone and computer must be on the same Wi-Fi, and the host must be sharing.");
        Button retry = button("Search again", true);
        retry.setOnClickListener(v -> discover(false));
        panel.addView(retry);
        scanButton();
        final EditText in = new EditText(this);
        in.setHint("Address, e.g. 192.168.1.20");
        in.setTextColor(Color.WHITE);
        in.setHintTextColor(Color.GRAY);
        in.setSingleLine(true);
        in.setImeOptions(EditorInfo.IME_ACTION_GO);
        in.setText(prefs.getString("host", ""));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(24);
        panel.addView(in, lp);
        Button go = button("Connect to this address", false);
        go.setOnClickListener(v -> connect(in.getText().toString()));
        in.setOnEditorActionListener((v, a, ev) -> { connect(in.getText().toString()); return true; });
        panel.addView(go);
    }

    private void connect(String raw) {
        String a = raw.trim().replaceFirst("^[a-z]+://", "");
        a = a.replaceFirst("/.*$", "").replaceFirst(":\\d+$", "");
        if (a.isEmpty()) return;
        stopSearch();
        gen++;
        host = a;
        SharedPreferences.Editor ed = prefs.edit().putString("host", a);
        if (found.containsKey(a)) ed.putString("name", found.get(a));
        ed.apply();
        panel.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        web.loadUrl("https://" + a + ":" + PORT + "/remote");
    }

    @Override
    public void onBackPressed() {
        if (web.getVisibility() == View.VISIBLE && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
