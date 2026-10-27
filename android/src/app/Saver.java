package app.prismix.band;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.net.URLDecoder;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import javax.net.ssl.HttpsURLConnection;

/** Saves a song from the host into the phone's Music folder (Music/Prismix), one file at a time. */
final class Saver {
    private static final ExecutorService QUEUE = Executors.newSingleThreadExecutor();

    private Saver() { }

    static void save(final Context ctx, final String url, final String folder) {
        QUEUE.execute(() -> {
            String name = "Prismix.m4a";
            try {
                URL u = new URL(url);
                if (!u.getHost().equals(Main.hostName())) throw new IllegalStateException("not the connected host");
                HttpsURLConnection c = (HttpsURLConnection) u.openConnection();
                c.setSSLSocketFactory(Main.tls());
                c.setHostnameVerifier((h, s) -> true);
                c.setConnectTimeout(8000);
                c.setReadTimeout(10 * 60 * 1000); // the host encodes before it answers
                if (c.getResponseCode() != 200) throw new IllegalStateException("host said " + c.getResponseCode());
                name = fileName(c.getHeaderField("Content-Disposition"), name);
                try (InputStream in = c.getInputStream(); OutputStream out = open(ctx, folder, name)) {
                    byte[] buf = new byte[64 * 1024];
                    int n;
                    while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                }
                c.disconnect();
                done(ctx, name, null);
            } catch (Exception e) {
                done(ctx, name, e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
            }
        });
    }

    private static void done(final Context ctx, final String name, final String err) {
        Main.post(() -> Toast.makeText(ctx, err == null ? "Saved: " + name : "Could not save " + name + " (" + err + ")", Toast.LENGTH_LONG).show());
    }

    private static String fileName(String disposition, String fallback) {
        if (disposition != null) {
            int i = disposition.indexOf("filename*=UTF-8''");
            if (i >= 0) {
                try { return clean(URLDecoder.decode(disposition.substring(i + 17).split(";")[0].trim(), "UTF-8")); } catch (Exception e) { /* use the fallback */ }
            }
        }
        return fallback;
    }

    private static String clean(String s) {
        s = s.replaceAll("[\\\\/:*?\"<>|]+", " ").trim();
        return s.isEmpty() ? "Prismix.m4a" : s;
    }

    private static OutputStream open(Context ctx, String folder, String name) throws Exception {
        String dir = clean(folder);
        if (Build.VERSION.SDK_INT >= 29) {
            ContentResolver r = ctx.getContentResolver();
            ContentValues v = new ContentValues();
            v.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
            v.put(MediaStore.MediaColumns.MIME_TYPE, "audio/mp4");
            v.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_MUSIC + "/Prismix/" + dir);
            Uri uri = r.insert(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, v);
            if (uri == null) throw new IllegalStateException("no storage");
            return r.openOutputStream(uri);
        }
        File d = new File(ctx.getExternalFilesDir(Environment.DIRECTORY_MUSIC), "Prismix/" + dir);
        if (!d.isDirectory() && !d.mkdirs()) throw new IllegalStateException("no storage");
        return new FileOutputStream(new File(d, name));
    }
}
