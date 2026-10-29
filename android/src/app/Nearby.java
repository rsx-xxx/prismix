package app.prismix.band;

import android.content.Context;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.os.Handler;
import android.os.Looper;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Watches the Wi-Fi for Prismix computers while the band cloud is on screen, so the app can offer to join one. mDNS browsing is
 * passive and stays on; the /24 sweep is a burst, run when watching starts and whenever the network or the screen comes back.
 * Each host is reported once per watch (the caller decides what to do about it), always on the main thread.
 */
final class Nearby {
    interface Listener { void onHost(Main.Host h); }

    private static final String SERVICE = "_prismix._tcp.";

    private final Context ctx;
    private final Listener out;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final Set<String> told = new HashSet<>();
    private NsdManager nsd;
    private NsdManager.DiscoveryListener nsdListener;
    private ExecutorService pool;
    private int gen;

    Nearby(Context ctx, Listener out) {
        this.ctx = ctx;
        this.out = out;
    }

    /** Starts (or refreshes) the watch: browse over mDNS and sweep the subnet once. */
    void watch() {
        if (pool == null) pool = Executors.newFixedThreadPool(16);
        final int g = gen;
        final ExecutorService p = pool;
        if (nsdListener == null) startNsd(g);
        final String base = Main.subnet();
        if (base == null) return; // no Wi-Fi address: mDNS alone
        for (int i = 1; i < 255; i++) probe(p, g, base + i);
    }

    /** Stops everything and forgets which hosts were reported. */
    void stop() {
        gen++;
        if (pool != null) { pool.shutdownNow(); pool = null; }
        if (nsd != null && nsdListener != null) {
            try { nsd.stopServiceDiscovery(nsdListener); } catch (RuntimeException e) { /* already stopped */ }
        }
        nsdListener = null;
        told.clear();
    }

    private void probe(ExecutorService p, final int g, final String ip) {
        try {
            p.execute(() -> {
                Main.Host h = Main.hello(ip);
                if (h != null) ui.post(() -> report(g, h));
            });
        } catch (RuntimeException e) { /* stopped meanwhile */ }
    }

    private void report(int g, Main.Host h) {
        if (g != gen || !told.add(h.id)) return;
        out.onHost(h);
    }

    private void startNsd(final int g) {
        try {
            nsd = (NsdManager) ctx.getSystemService(Context.NSD_SERVICE);
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
                ExecutorService p = pool;
                if (a instanceof Inet4Address && p != null) probe(p, g, a.getHostAddress());
            } else if (n.equals("hashCode")) {
                return System.identityHashCode(proxy);
            } else if (n.equals("equals")) {
                return proxy == args[0];
            } else if (n.equals("toString")) {
                return "NearbyListener";
            }
            return null;
        });
    }
}
