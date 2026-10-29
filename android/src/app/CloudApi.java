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
import java.io.FilterInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The player's own page, answered from the band cloud. The page asks the host API it always asks (library, lyrics, tab,
 * stems); here each path is translated to the cloud's song files and fetched with the member token.
 */
final class CloudApi {
    static final String HOST = "cloud.prismix";
    private static final Pattern ID = Pattern.compile("[A-Za-z0-9_-]{11}");
    private static final Pattern LIBRARY = Pattern.compile("^/api/library/([A-Za-z0-9_-]{11})/([A-Za-z0-9_-]{1,40})$");
    private static final Pattern STEM = Pattern.compile("^/media/([A-Za-z0-9_-]{11})/([A-Za-z0-9_-]{1,40})/([A-Za-z0-9_-]{1,40})\\.m4a$");
    private static final Pattern FILE = Pattern.compile("^/(?:media/([A-Za-z0-9_-]{11})/(structure\\.json|thumb\\.jpg)|api/(lyrics|tabdata)/([A-Za-z0-9_-]{11}))$");

    private CloudApi() { }

    /** The band this cloud belongs to, by host; a cloud nobody registered is just the band cloud. */
    private static String bandName(Cloud.Credential c) {
        return c != null && "band.deltaclans.win".equals(c.host()) ? "TigerKiss" : "Band cloud";
    }

    static WebResourceResponse serve(Context ctx, Cloud.Credential c, String path) {
        if (path == null || path.isEmpty()) path = "/";
        if (path.contains("..") || path.contains("//") || path.contains("\\")) return status(404);
        try {
            if (path.equals("/") || path.equals("/remote")) return Offline.asset(ctx, "remote.html", "no-cache");
            if (path.equals("/remote/hello")) return json("{\"app\":\"prismix\",\"name\":\"" + bandName(c) + "\",\"offline\":true,\"pin\":false}");
            if (c == null) return status(401);
            if (path.equals("/api/playlists")) return json("[]");
            if (path.equals("/api/library")) return json(library(c));
            Matcher m = LIBRARY.matcher(path);
            if (m.matches()) return json(meta(c, m.group(1), m.group(2)));
            m = STEM.matcher(path);
            if (m.matches()) {
                String stem = m.group(3);
                String remote = "/files/" + m.group(1) + "/" + (stem.equals("mix") ? "mix.m4a" : "stems/" + stem + ".m4a");
                // Stems never change for a given song and model, so they are kept on the phone after the first download.
                return cached(ctx, new File(cacheRoot(ctx), m.group(1) + "/" + m.group(2) + "/" + stem + ".m4a"), c, remote, "audio/mp4");
            }
            m = FILE.matcher(path);
            if (m.matches()) {
                String id = m.group(1) != null ? m.group(1) : m.group(4);
                String file = m.group(2) != null ? m.group(2) : m.group(3).equals("lyrics") ? "lyrics.json" : "tab.json";
                if (file.equals("thumb.jpg")) file = "cover.jpg";
                return pipe(c, "/files/" + id + "/" + file, file.endsWith(".jpg") ? "image/jpeg" : "application/json");
            }
            if (path.startsWith("/api/") || path.startsWith("/media/")) return status(404);
            return Offline.asset(ctx, path.substring(1), "no-cache");
        } catch (Exception e) {
            return status(502);
        }
    }

    /** The cloud index in the shape of the host's library list. */
    private static String library(Cloud.Credential c) throws Exception {
        JSONArray songs = new JSONObject(new String(fetch(c, "/api/v1/index.json"), StandardCharsets.UTF_8)).optJSONArray("songs");
        JSONArray out = new JSONArray();
        for (int i = 0; songs != null && i < songs.length(); i++) {
            JSONObject s = songs.getJSONObject(i);
            if (!ID.matcher(s.optString("id")).matches()) continue;
            JSONArray models = s.optJSONArray("models");
            if (models == null || models.length() == 0) continue;
            JSONObject it = new JSONObject();
            it.put("id", s.getString("id"));
            it.put("title", s.optString("title", s.getString("id")));
            it.put("channel", s.optString("artist", ""));
            it.put("duration", s.optDouble("duration", 0));
            it.put("models", models);
            it.put("analyzed", models);
            it.put("added", s.optLong("updated", 0));
            out.put(it);
        }
        return out.toString();
    }

    /** The stems of one song: the full mix alone for a single-stem song, the cloud's stem files otherwise. */
    private static String meta(Cloud.Credential c, String id, String model) throws Exception {
        JSONObject man = new JSONObject(new String(fetch(c, "/api/v1/songs/" + id + "/manifest.json"), StandardCharsets.UTF_8));
        JSONArray names = man.optJSONArray("stems");
        JSONArray stems = new JSONArray();
        if (names == null || names.length() == 0) {
            stems.put(new JSONObject().put("name", "mix").put("file", "mix.m4a"));
        } else {
            for (int i = 0; i < names.length(); i++) stems.put(new JSONObject().put("name", names.getString(i)).put("file", names.getString(i) + ".m4a"));
        }
        return new JSONObject()
                .put("id", id)
                .put("model", model)
                .put("title", man.optString("title", id))
                .put("channel", man.optString("artist", ""))
                .put("duration", man.optDouble("duration", 0))
                .put("stems", stems)
                .toString();
    }

    private static HttpURLConnection open(Cloud.Credential c, String path) throws Exception {
        HttpURLConnection h = (HttpURLConnection) new URL(c.origin + path).openConnection();
        h.setRequestProperty("Authorization", "Bearer " + c.token);
        h.setConnectTimeout(8000);
        h.setReadTimeout(60000);
        return h;
    }

    private static byte[] fetch(Cloud.Credential c, String path) throws Exception {
        HttpURLConnection h = open(c, path);
        try {
            if (h.getResponseCode() != 200) throw new java.io.IOException("cloud said " + h.getResponseCode());
            try (InputStream in = h.getInputStream()) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buf = new byte[32 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                return out.toByteArray();
            }
        } finally {
            h.disconnect();
        }
    }

    /** A cloud file streamed straight to the page. */
    private static WebResourceResponse pipe(Cloud.Credential c, String path, String mime) throws Exception {
        HttpURLConnection h = open(c, path);
        int code = h.getResponseCode();
        if (code != 200) { h.disconnect(); return status(code == 404 ? 404 : 502); }
        Map<String, String> head = new HashMap<>();
        int len = h.getContentLength();
        if (len >= 0) head.put("Content-Length", String.valueOf(len));
        return new WebResourceResponse(mime, null, 200, "OK", head, h.getInputStream());
    }

    private static final long CACHE_MAX = 1536L << 20;

    private static File cacheRoot(Context ctx) {
        return new File(ctx.getCacheDir(), "cloud-stems");
    }

    /** A cloud file from the phone's disk when it is there; otherwise streamed to the page while it is saved for next time. */
    private static WebResourceResponse cached(Context ctx, File file, Cloud.Credential c, String path, String mime) throws Exception {
        Map<String, String> head = new HashMap<>();
        head.put("Cache-Control", "private, max-age=31536000, immutable");
        if (file.isFile() && file.length() > 0) {
            file.setLastModified(System.currentTimeMillis());
            head.put("Content-Length", String.valueOf(file.length()));
            return new WebResourceResponse(mime, null, 200, "OK", head, new FileInputStream(file));
        }
        HttpURLConnection h = open(c, path);
        int code = h.getResponseCode();
        if (code != 200) { h.disconnect(); return status(code == 404 ? 404 : 502); }
        int len = h.getContentLength();
        if (len >= 0) head.put("Content-Length", String.valueOf(len));
        File dir = file.getParentFile();
        if (dir == null || !(dir.isDirectory() || dir.mkdirs())) return new WebResourceResponse(mime, null, 200, "OK", head, h.getInputStream());
        File part = new File(dir, file.getName() + ".part");
        return new WebResourceResponse(mime, null, 200, "OK", head, new Tee(h.getInputStream(), h, part, file, len, cacheRoot(ctx)));
    }

    /** Passes the download through and keeps a copy; the copy only counts if the whole file arrived. */
    private static final class Tee extends FilterInputStream {
        private final HttpURLConnection conn;
        private final File part, done, root;
        private final long expect;
        private FileOutputStream out;
        private long got;
        private boolean finished;

        Tee(InputStream in, HttpURLConnection conn, File part, File done, long expect, File root) throws IOException {
            super(in);
            this.conn = conn; this.part = part; this.done = done; this.expect = expect; this.root = root;
            this.out = new FileOutputStream(part);
        }

        @Override public int read() throws IOException {
            int b = super.read();
            if (b >= 0) save(new byte[] {(byte) b}, 0, 1); else finish();
            return b;
        }

        @Override public int read(byte[] buf, int off, int len) throws IOException {
            int n = super.read(buf, off, len);
            if (n > 0) save(buf, off, n); else if (n < 0) finish();
            return n;
        }

        private void save(byte[] buf, int off, int n) {
            if (out == null) return;
            try { out.write(buf, off, n); got += n; } catch (IOException e) { abandon(); }
        }

        private void finish() {
            if (finished) return;
            finished = true;
            if (out == null) return;
            try { out.close(); } catch (IOException e) { abandon(); return; }
            out = null;
            if ((expect >= 0 && got != expect) || got == 0 || !part.renameTo(done)) part.delete(); else trim(root);
        }

        private void abandon() {
            try { if (out != null) out.close(); } catch (IOException ignored) { }
            out = null;
            part.delete();
        }

        @Override public void close() throws IOException {
            if (!finished) abandon();
            try { super.close(); } finally { conn.disconnect(); }
        }
    }

    /** Keeps the stem cache under its size limit by dropping the songs used longest ago. */
    private static void trim(File root) {
        File[] songs = root.listFiles();
        if (songs == null) return;
        long total = 0;
        java.util.List<File> files = new java.util.ArrayList<>();
        for (File song : songs) collect(song, files);
        for (File f : files) total += f.length();
        if (total <= CACHE_MAX) return;
        File[] all = files.toArray(new File[0]);
        Arrays.sort(all, Comparator.comparingLong(File::lastModified));
        for (File f : all) {
            if (total <= CACHE_MAX * 3 / 4) break;
            long n = f.length();
            if (f.delete()) total -= n;
        }
    }

    private static void collect(File f, java.util.List<File> out) {
        if (f.isFile()) { out.add(f); return; }
        File[] kids = f.listFiles();
        if (kids != null) for (File k : kids) collect(k, out);
    }

    private static WebResourceResponse json(String body) {
        Map<String, String> h = new HashMap<>();
        h.put("Cache-Control", "no-store");
        return new WebResourceResponse("application/json", "utf-8", 200, "OK", h, new ByteArrayInputStream(body.getBytes(StandardCharsets.UTF_8)));
    }

    private static WebResourceResponse status(int code) {
        return new WebResourceResponse("text/plain", "utf-8", code, code == 404 ? "Not Found" : "Error", new HashMap<String, String>(), new ByteArrayInputStream(new byte[0]));
    }
}
