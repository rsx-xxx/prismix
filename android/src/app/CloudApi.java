package app.prismix.band;

import android.content.Context;
import android.webkit.WebResourceResponse;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
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
    private static final Pattern STEM = Pattern.compile("^/media/([A-Za-z0-9_-]{11})/[A-Za-z0-9_-]{1,40}/([A-Za-z0-9_-]{1,40})\\.m4a$");
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
                String stem = m.group(2);
                return pipe(c, "/files/" + m.group(1) + "/" + (stem.equals("mix") ? "mix.m4a" : "stems/" + stem + ".m4a"), "audio/mp4");
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

    private static WebResourceResponse json(String body) {
        Map<String, String> h = new HashMap<>();
        h.put("Cache-Control", "no-store");
        return new WebResourceResponse("application/json", "utf-8", 200, "OK", h, new ByteArrayInputStream(body.getBytes(StandardCharsets.UTF_8)));
    }

    private static WebResourceResponse status(int code) {
        return new WebResourceResponse("text/plain", "utf-8", code, code == 404 ? "Not Found" : "Error", new HashMap<String, String>(), new ByteArrayInputStream(new byte[0]));
    }
}
