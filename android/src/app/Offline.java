package app.prismix.band;

import android.content.Context;
import android.webkit.WebResourceResponse;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Pattern;

import javax.net.ssl.HttpsURLConnection;

/**
 * Songs kept on the phone for use without a host. One folder per song under files/offline/<id>, holding the host's
 * answers at their own URL paths (api/library/<id>/<model>, media/<id>/structure.json, media/<id>/<model>/<stem file>),
 * so the page can be served from it unchanged. The page itself comes from the APK's assets. Everything is served on a
 * made-up origin (https://offline.prismix) that never touches the network.
 */
final class Offline {
    static final String HOST = "offline.prismix";
    private static final Pattern ID = Pattern.compile("[A-Za-z0-9_-]{11}");
    private static final Pattern NAME = Pattern.compile("[A-Za-z0-9_-]{1,40}");
    private static final Pattern FILE = Pattern.compile("[A-Za-z0-9_-]{1,40}\\.[a-z0-9]{2,5}");
    private static final Pattern OWNER = Pattern.compile("^/(?:media|api/(?:library|lyrics|tabdata))/([A-Za-z0-9_-]{11})(?:/|$)");
    private static final ExecutorService QUEUE = Executors.newSingleThreadExecutor();
    private static final Set<String> ACTIVE = ConcurrentHashMap.newKeySet();

    private Offline() { }

    private static File root(Context ctx) {
        return new File(ctx.getFilesDir(), "offline");
    }

    /** Finished songs: the host's library entries (one model each) with their size on disk, newest first. */
    static JSONArray list(Context ctx) {
        List<JSONObject> out = new ArrayList<>();
        File[] dirs = root(ctx).listFiles();
        if (dirs != null) {
            for (File d : dirs) {
                if (!ID.matcher(d.getName()).matches()) continue; // skips .part-* leftovers
                try {
                    JSONObject item = new JSONObject(new String(read(new File(d, "item.json")), StandardCharsets.UTF_8));
                    item.put("bytes", size(d));
                    out.add(item);
                } catch (Exception e) { /* half a song: not listed */ }
            }
        }
        Collections.sort(out, (a, b) -> Double.compare(b.optDouble("added", 0), a.optDouble("added", 0)));
        JSONArray arr = new JSONArray();
        for (JSONObject o : out) arr.put(o);
        return arr;
    }

    static void remove(Context ctx, String id) {
        if (ID.matcher(id).matches()) wipe(new File(root(ctx), id));
    }

    /** Fetches a song from the connected host in the background. Progress goes to the page as window.__offline(id, 0..1; -1 = failed). */
    static void save(final Context ctx, final String id, final String model, final String pin) {
        if (!ID.matcher(id).matches() || !NAME.matcher(model).matches() || !ACTIVE.add(id)) return;
        report(id, 0);
        QUEUE.execute(() -> {
            File tmp = new File(root(ctx), ".part-" + id);
            try {
                String host = Main.hostName();
                if (host.isEmpty()) throw new IllegalStateException("not connected to a host");
                String base = "https://" + host + ":" + Main.PORT;
                wipe(tmp);
                if (!tmp.mkdirs()) throw new IllegalStateException("no storage");

                String libPath = "/api/library/" + id + "/" + model;
                JSONArray all = new JSONArray(new String(get(base + "/api/library", pin, 30000), StandardCharsets.UTF_8));
                JSONObject item = null;
                for (int i = 0; i < all.length() && item == null; i++) if (id.equals(all.getJSONObject(i).optString("id"))) item = all.getJSONObject(i);
                if (item == null) throw new IllegalStateException("the host no longer has this song");
                byte[] metaBytes = get(base + libPath, pin, 30000);
                JSONArray stems = new JSONObject(new String(metaBytes, StandardCharsets.UTF_8)).getJSONArray("stems");
                int steps = stems.length() + 4, done = 0;

                item.put("models", new JSONArray().put(model));
                item.put("analyzed", item.optJSONArray("analyzed") != null && item.getJSONArray("analyzed").length() > 0 ? new JSONArray().put(model) : new JSONArray());
                write(new File(tmp, "item.json"), item.toString().getBytes(StandardCharsets.UTF_8));
                write(mirror(tmp, libPath), metaBytes);
                report(id, ++done / (float) steps);

                // The rest is nice to have: a song without lyrics or tab is still playable.
                optional(tmp, base, "/media/" + id + "/structure.json", pin, 30000);
                report(id, ++done / (float) steps);
                optional(tmp, base, "/api/tabdata/" + id, pin, 90000);
                report(id, ++done / (float) steps);
                optional(tmp, base, "/api/lyrics/" + id, pin, 6 * 60 * 1000); // the host may have to align the words first
                report(id, ++done / (float) steps);
                optional(tmp, base, "/media/" + id + "/thumb.jpg", pin, 30000);

                for (int i = 0; i < stems.length(); i++) {
                    JSONObject s = stems.getJSONObject(i);
                    String name = s.getString("name"), file = s.getString("file");
                    if (!NAME.matcher(name).matches() || !FILE.matcher(file).matches()) throw new IllegalStateException("odd stem name");
                    // AAC from the host's phone export: a fraction of the FLAC's size, and the page decodes it all the same.
                    String stem = get(tmp, base, "/api/dl/" + id + "/" + model + "/" + name + ".m4a", "/media/" + id + "/" + model + "/" + file, pin, 10 * 60 * 1000);
                    if (stem != null) throw new IllegalStateException(stem);
                    report(id, ++done / (float) steps);
                }

                File dest = new File(root(ctx), id);
                wipe(dest);
                if (!tmp.renameTo(dest)) throw new IllegalStateException("no storage");
                report(id, 1);
            } catch (Exception e) {
                wipe(tmp);
                report(id, -1);
                final String why = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
                Main.post(() -> android.widget.Toast.makeText(ctx, "Could not save the song offline (" + why + ")", android.widget.Toast.LENGTH_LONG).show());
            } finally {
                ACTIVE.remove(id);
            }
        });
    }

    private static void report(String id, float p) {
        Main.eval("window.__offline&&window.__offline('" + id + "'," + p + ")");
    }

    // ── serving ──

    /** The answer for a path on the offline origin: the page from the APK, the song data from disk. */
    static WebResourceResponse serve(Context ctx, String path) {
        if (path == null || path.isEmpty()) path = "/";
        if (path.contains("..") || path.contains("//") || path.contains("\\")) return notFound();
        try {
            if (path.equals("/") || path.equals("/remote")) return asset(ctx, "remote.html", "no-cache");
            if (path.equals("/remote/hello")) return bytes("application/json", "{\"app\":\"prismix\",\"name\":\"Offline songs\",\"offline\":true,\"pin\":false}".getBytes(StandardCharsets.UTF_8));
            if (path.equals("/api/library")) return bytes("application/json", list(ctx).toString().getBytes(StandardCharsets.UTF_8));
            if (path.equals("/api/playlists")) return bytes("application/json", "[]".getBytes(StandardCharsets.UTF_8));
            if (path.startsWith("/api/") || path.startsWith("/media/")) {
                java.util.regex.Matcher m = OWNER.matcher(path);
                if (!m.find()) return notFound();
                File f = new File(new File(root(ctx), m.group(1)), path.substring(1));
                if (!f.isFile()) return notFound();
                return file(f, mime(path.startsWith("/api/") && !path.contains(".") ? "x.json" : path));
            }
            return asset(ctx, path.substring(1), "no-cache");
        } catch (IOException e) {
            return notFound();
        }
    }

    private static WebResourceResponse notFound() {
        return new WebResourceResponse("text/plain", "utf-8", 404, "Not Found", new HashMap<String, String>(), new ByteArrayInputStream(new byte[0]));
    }

    static WebResourceResponse asset(Context ctx, String name, String cache) throws IOException {
        InputStream in = ctx.getAssets().open("web/" + name);
        Map<String, String> h = new HashMap<>();
        h.put("Cache-Control", cache);
        return new WebResourceResponse(mime(name), "utf-8", 200, "OK", h, in);
    }

    private static WebResourceResponse bytes(String mime, byte[] b) {
        Map<String, String> h = new HashMap<>();
        h.put("Cache-Control", "no-store");
        return new WebResourceResponse(mime, "utf-8", 200, "OK", h, new ByteArrayInputStream(b));
    }

    private static WebResourceResponse file(File f, String mime) throws IOException {
        Map<String, String> h = new HashMap<>();
        h.put("Content-Length", String.valueOf(f.length())); // the player's progress bar reads it
        return new WebResourceResponse(mime, null, 200, "OK", h, new FileInputStream(f));
    }

    private static String mime(String name) {
        String n = name.toLowerCase();
        if (n.endsWith(".html")) return "text/html";
        if (n.endsWith(".js") || n.endsWith(".mjs")) return "text/javascript";
        if (n.endsWith(".css")) return "text/css";
        if (n.endsWith(".json")) return "application/json";
        if (n.endsWith(".svg")) return "image/svg+xml";
        if (n.endsWith(".jpg") || n.endsWith(".jpeg")) return "image/jpeg";
        if (n.endsWith(".png")) return "image/png";
        if (n.endsWith(".m4a")) return "audio/mp4";
        if (n.endsWith(".flac")) return "audio/flac";
        if (n.endsWith(".wasm")) return "application/wasm";
        return "application/octet-stream";
    }

    // ── files and network ──

    private static File mirror(File dir, String urlPath) throws IOException {
        File f = new File(dir, urlPath.substring(1));
        File parent = f.getParentFile();
        if (!parent.isDirectory() && !parent.mkdirs()) throw new IOException("no storage");
        return f;
    }

    private static void write(File f, byte[] b) throws IOException {
        try (OutputStream out = new FileOutputStream(f)) { out.write(b); }
    }

    private static byte[] read(File f) throws IOException {
        try (InputStream in = new FileInputStream(f)) { return drain(in); }
    }

    private static byte[] drain(InputStream in) throws IOException {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        byte[] buf = new byte[64 * 1024];
        int n;
        while ((n = in.read(buf)) > 0) o.write(buf, 0, n);
        return o.toByteArray();
    }

    private static long size(File f) {
        if (f.isFile()) return f.length();
        long n = 0;
        File[] kids = f.listFiles();
        if (kids != null) for (File k : kids) n += size(k);
        return n;
    }

    private static void wipe(File f) {
        File[] kids = f.listFiles();
        if (kids != null) for (File k : kids) wipe(k);
        f.delete();
    }

    private static HttpsURLConnection open(String url, String pin, int readMs) throws Exception {
        URL u = new URL(url);
        if (!u.getHost().equals(Main.hostName())) throw new IllegalStateException("not the connected host");
        HttpsURLConnection c = (HttpsURLConnection) u.openConnection();
        c.setSSLSocketFactory(Main.tls());
        c.setHostnameVerifier((h, s) -> true);
        c.setConnectTimeout(8000);
        c.setReadTimeout(readMs);
        if (pin != null && !pin.isEmpty()) c.setRequestProperty("x-band-token", pin);
        return c;
    }

    private static byte[] get(String url, String pin, int readMs) throws Exception {
        HttpsURLConnection c = open(url, pin, readMs);
        try {
            if (c.getResponseCode() != 200) throw new IllegalStateException("host said " + c.getResponseCode());
            try (InputStream in = c.getInputStream()) { return drain(in); }
        } finally {
            c.disconnect();
        }
    }

    /** Streams `fetchPath` from the host into `tmp` at `storePath`. Returns an error text, or null when stored. */
    private static String get(File tmp, String base, String fetchPath, String storePath, String pin, int readMs) throws Exception {
        HttpsURLConnection c = open(base + fetchPath, pin, readMs);
        try {
            if (c.getResponseCode() != 200) return "host said " + c.getResponseCode();
            try (InputStream in = c.getInputStream(); OutputStream out = new FileOutputStream(mirror(tmp, storePath))) {
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            }
            return null;
        } finally {
            c.disconnect();
        }
    }

    private static void optional(File tmp, String base, String path, String pin, int readMs) {
        try {
            get(tmp, base, path, path, pin, readMs);
        } catch (Exception e) {
            new File(tmp, path.substring(1)).delete(); // never keep half an answer
        }
    }
}
