package app.prismix.band;

import android.content.SharedPreferences;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** The private band cloud: a member signs in with an invite link or token and only that credential is kept. */
final class Cloud {
    /** A well-formed id that is never published: the admin check only reads the status code. */
    private static final String PROBE_ID = "AAAAAAAAAAA";
    private static final Pattern TOKEN = Pattern.compile("[A-Za-z0-9_-]{16,128}");
    private static final Pattern QUERY_T = Pattern.compile("(?:^|[?&])t=([^&#]*)");

    /** A failure whose message is safe to show to the user. */
    static final class Failure extends Exception {
        Failure(String message) { super(message); }
    }

    /** What the user gave us, split into the server's origin and the member token. */
    static final class Credential {
        final String origin, token;
        Credential(String origin, String token) { this.origin = origin; this.token = token; }
        String host() { return URI.create(origin).getHost(); }
        /** The page that turns the token into the session cookie and redirects to the player. */
        String joinUrl() { return origin + "/join?t=" + token; }
    }

    private Cloud() { }

    /** Invite link (address inside) or a bare token plus a typed address. */
    static Credential parse(String text, String address) throws Failure {
        text = text == null ? "" : text.trim();
        address = address == null ? "" : address.trim();
        String token = text;
        String base = address;
        if (text.contains("://")) {
            URI u = uri(text);
            Matcher m = u.getRawQuery() == null ? null : QUERY_T.matcher(u.getRawQuery());
            token = m != null && m.find() ? m.group(1) : "";
            if (base.isEmpty() && u.getScheme() != null && u.getRawAuthority() != null) base = u.getScheme() + "://" + u.getRawAuthority();
        }
        if (base.isEmpty()) throw new Failure("Paste the whole invite link");
        if (!base.contains("://")) base = "https://" + base;
        URI u = uri(base);
        String host = u.getHost();
        boolean local = "localhost".equals(host);
        if (host == null || host.isEmpty() || !("https".equals(u.getScheme()) || ("http".equals(u.getScheme()) && local))) {
            throw new Failure("The cloud address must be https");
        }
        if (!TOKEN.matcher(token).matches()) throw new Failure("That token does not look right");
        return new Credential(u.getScheme() + "://" + u.getRawAuthority(), token);
    }

    private static URI uri(String s) throws Failure {
        try { return new URI(s); } catch (java.net.URISyntaxException e) { throw new Failure("That address does not look right"); }
    }

    /**
     * Checks the token with the server. A member reads the index and is refused (403) when it tries to commit; the admin token
     * is accepted there as malformed (400). The admin token must never live on a phone, so it is turned away.
     */
    static void verifyMember(Credential c) throws Failure {
        int index = status(c, "GET", "/api/v1/index.json", null);
        if (index == 401) throw new Failure("The cloud refused this token");
        if (index == 429) throw new Failure("Too many failed attempts, try again in a few minutes");
        if (index != 200) throw new Failure("The cloud answered " + index);
        int probe = status(c, "POST", "/api/v1/songs/" + PROBE_ID + "/commit", "{}");
        if (probe == 403) return;
        if (probe == 400) throw new Failure("That is the admin token. Use a member invite on the phone");
        throw new Failure("The cloud answered " + probe);
    }

    private static int status(Credential c, String method, String path, String body) throws Failure {
        HttpURLConnection h = null;
        try {
            h = (HttpURLConnection) new URL(c.origin + path).openConnection(); // the system's own certificate checks: this is a public host
            h.setRequestMethod(method);
            h.setRequestProperty("Authorization", "Bearer " + c.token);
            h.setConnectTimeout(8000);
            h.setReadTimeout(15000);
            if (body != null) {
                h.setRequestProperty("Content-Type", "application/json");
                h.setDoOutput(true);
                try (OutputStream o = h.getOutputStream()) { o.write(body.getBytes(StandardCharsets.UTF_8)); }
            }
            int code = h.getResponseCode();
            InputStream in = code >= 400 ? h.getErrorStream() : h.getInputStream();
            if (in != null) in.close();
            return code;
        } catch (java.io.IOException e) {
            throw new Failure("The cloud is not answering");
        } finally {
            if (h != null) h.disconnect();
        }
    }

    // ── what is kept ──

    static Credential load(SharedPreferences p) {
        String o = p.getString("cloudOrigin", ""), t = p.getString("cloudToken", "");
        return o.isEmpty() || t.isEmpty() ? null : new Credential(o, t);
    }

    static void save(SharedPreferences p, Credential c) {
        p.edit().putString("cloudOrigin", c.origin).putString("cloudToken", c.token).apply();
    }

    static void clear(SharedPreferences p) {
        p.edit().remove("cloudOrigin").remove("cloudToken").apply();
    }
}
