package app.prismix.band;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.drawable.Icon;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.media.MediaMetadata;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import java.io.InputStream;
import java.net.URL;

import javax.net.ssl.HttpsURLConnection;

/**
 * Keeps the app alive with the screen off and shows the song in the notification shade and on the lock screen
 * (MediaStyle, so MIUI and the other skins draw their media card). Buttons go back to the page, which owns playback.
 */
public class MediaService extends Service {
    private static final String CHANNEL = "playback";
    private static final int ID = 7;
    private static final String PLAY = "app.prismix.band.PLAY", PAUSE = "app.prismix.band.PAUSE",
            BACK = "app.prismix.band.BACK", FWD = "app.prismix.band.FWD";

    private MediaSession session;
    private PowerManager.WakeLock lock;
    private android.net.wifi.WifiManager.WifiLock wifi;
    private Bitmap art;
    private String title = "", artist = "", artUrl = "";
    private boolean playing, started;
    private long pos, dur;
    private float rate = 1f;
    private int artGen;
    private AudioManager audio;
    private AudioFocusRequest focusReq;
    private boolean focused;
    private final AudioManager.OnAudioFocusChangeListener onFocus = change -> {
        // another app took over (a call, Spotify, YouTube…): stop, do not fight for it
        if (change == AudioManager.AUDIOFOCUS_LOSS || change == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT) {
            focused = false;
            Main.js("pause", 0);
        }
    };


    /** Buttons on the lock screen, headsets and the notification shade. */
    private static class Keys extends MediaSession.Callback {
        @Override public void onPlay() { Main.js("play", 0); }
        @Override public void onPause() { Main.js("pause", 0); }
        @Override public void onStop() { Main.js("pause", 0); }
        @Override public void onSeekTo(long ms) { Main.js("seek", ms / 1000.0); }
        @Override public void onSkipToNext() { Main.js("step", 10); }
        @Override public void onSkipToPrevious() { Main.js("step", -10); }
        @Override public void onFastForward() { Main.js("step", 10); }
        @Override public void onRewind() { Main.js("step", -10); }
    }

    @Override
    public IBinder onBind(Intent i) { return null; }

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Playback", NotificationManager.IMPORTANCE_LOW));
        }
        session = new MediaSession(this, "Prismix");
        session.setCallback(new Keys());
        session.setActive(true);
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        lock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "prismix:media");
        lock.setReferenceCounted(false);
        android.net.wifi.WifiManager wm = (android.net.wifi.WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        wifi = wm.createWifiLock(android.net.wifi.WifiManager.WIFI_MODE_FULL_HIGH_PERF, "prismix:media");
        wifi.setReferenceCounted(false);
    }

    @Override
    public int onStartCommand(Intent i, int flags, int startId) {
        String a = i == null ? null : i.getAction();
        if (PLAY.equals(a)) Main.js("play", 0);
        else if (PAUSE.equals(a)) Main.js("pause", 0);
        else if (BACK.equals(a)) Main.js("step", -10);
        else if (FWD.equals(a)) Main.js("step", 10);
        else if (i != null && i.hasExtra("title")) {
            title = i.getStringExtra("title");
            artist = i.getStringExtra("artist");
            playing = i.getBooleanExtra("playing", false);
            pos = i.getLongExtra("pos", 0);
            dur = i.getLongExtra("dur", 0);
            rate = i.getFloatExtra("rate", 1f);
            String url = i.getStringExtra("art");
            if (url != null && !url.isEmpty() && !url.equals(artUrl)) { artUrl = url; art = null; fetchArt(url); }
            show();
        }
        return START_NOT_STICKY;
    }

    private PendingIntent act(String a, int code) {
        Intent i = new Intent(this, MediaService.class).setAction(a);
        return PendingIntent.getService(this, code, i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private Notification.Action action(int icon, String label, PendingIntent p) {
        return new Notification.Action.Builder(Icon.createWithResource(this, icon), label, p).build();
    }

    private void show() {
        MediaMetadata.Builder md = new MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE, title)
                .putString(MediaMetadata.METADATA_KEY_ARTIST, artist)
                .putString(MediaMetadata.METADATA_KEY_ALBUM, "Prismix")
                .putLong(MediaMetadata.METADATA_KEY_DURATION, dur);
        if (art != null) md.putBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART, art);
        session.setMetadata(md.build());
        long acts = PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE | PlaybackState.ACTION_PLAY_PAUSE
                | PlaybackState.ACTION_SEEK_TO | PlaybackState.ACTION_SKIP_TO_NEXT | PlaybackState.ACTION_SKIP_TO_PREVIOUS
                | PlaybackState.ACTION_FAST_FORWARD | PlaybackState.ACTION_REWIND;
        session.setPlaybackState(new PlaybackState.Builder().setActions(acts)
                .setState(playing ? PlaybackState.STATE_PLAYING : PlaybackState.STATE_PAUSED, pos, playing ? rate : 0f).build());

        Notification.Builder n = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        n.setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle(title)
                .setContentText(artist)
                .setLargeIcon(art)
                .setOngoing(playing)
                .setShowWhen(false)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setContentIntent(PendingIntent.getActivity(this, 0, new Intent(this, Main.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
                        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE))
                .addAction(action(android.R.drawable.ic_media_rew, "Back 10 s", act(BACK, 1)))
                .addAction(playing ? action(android.R.drawable.ic_media_pause, "Pause", act(PAUSE, 2))
                        : action(android.R.drawable.ic_media_play, "Play", act(PLAY, 3)))
                .addAction(action(android.R.drawable.ic_media_ff, "Forward 10 s", act(FWD, 4)))
                .setStyle(new Notification.MediaStyle().setMediaSession(session.getSessionToken()).setShowActionsInCompactView(0, 1, 2));
        Notification note = n.build();
        if (!started) {
            if (Build.VERSION.SDK_INT >= 29) startForeground(ID, note, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
            else startForeground(ID, note);
            started = true;
        } else {
            ((NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE)).notify(ID, note);
        }
        if (playing) grabFocus(); else dropFocus();
        if (playing) {
            lock.acquire(6 * 60 * 60 * 1000L);
            if (!wifi.isHeld()) wifi.acquire();
        } else {
            if (lock.isHeld()) lock.release();
            if (wifi.isHeld()) wifi.release();
        }
    }

    /** Playing here silences every other app's audio (Spotify, YouTube…): ask for the media focus, they pause on losing it. */
    private void grabFocus() {
        if (focused) return;
        if (audio == null) audio = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
        int r;
        if (Build.VERSION.SDK_INT >= 26) {
            if (focusReq == null) {
                focusReq = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
                        .setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build())
                        .setOnAudioFocusChangeListener(onFocus)
                        .build();
            }
            r = audio.requestAudioFocus(focusReq);
        } else {
            r = audio.requestAudioFocus(onFocus, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN);
        }
        focused = r == AudioManager.AUDIOFOCUS_REQUEST_GRANTED;
    }

    private void dropFocus() {
        if (audio == null || !focused) return;
        focused = false;
        if (Build.VERSION.SDK_INT >= 26) { if (focusReq != null) audio.abandonAudioFocusRequest(focusReq); }
        else audio.abandonAudioFocus(onFocus);
    }

    /** The cover comes from the host the app is connected to, whose certificate is self-signed; a song whose file is missing there falls back to YouTube's. */
    private void fetchArt(final String url) {
        final int gen = ++artGen;
        new Thread(() -> {
            Bitmap b = load(url);
            if (b == null) {
                java.util.regex.Matcher m = java.util.regex.Pattern.compile("/media/([\\w-]{11})/thumb\\.jpg").matcher(url);
                if (m.find()) b = load("https://i.ytimg.com/vi/" + m.group(1) + "/mqdefault.jpg");
            }
            final Bitmap got = b;
            Main.post(() -> {
                if (gen != artGen || !url.equals(artUrl)) return;
                if (got != null) { art = got; if (started) show(); }
                else artUrl = ""; // not there yet (the host was busy): the next update asks again
            });
        }).start();
    }

    private Bitmap load(String url) {
        try {
            URL u = new URL(url);
            Cloud.Credential cred = Main.cloud();
            java.net.HttpURLConnection c;
            if (cred != null && u.getHost().equals(cred.host())) {
                c = (java.net.HttpURLConnection) u.openConnection();
                c.setRequestProperty("Authorization", "Bearer " + cred.token);
            } else if (u.getHost().equals(Main.hostName())) {
                HttpsURLConnection lan = (HttpsURLConnection) u.openConnection();
                lan.setSSLSocketFactory(Main.tls());
                lan.setHostnameVerifier((h, s) -> true);
                c = lan;
            } else if ("https".equals(u.getProtocol()) && u.getHost().endsWith("ytimg.com")) {
                c = (java.net.HttpURLConnection) u.openConnection();
            } else return null;
            c.setConnectTimeout(4000);
            c.setReadTimeout(6000);
            try (InputStream in = c.getInputStream()) { return BitmapFactory.decodeStream(in); }
            finally { c.disconnect(); }
        } catch (Exception e) { return null; }
    }

    @Override
    public void onDestroy() {
        dropFocus();
        if (lock != null && lock.isHeld()) lock.release();
        if (wifi != null && wifi.isHeld()) wifi.release();
        if (session != null) { session.setActive(false); session.release(); }
        super.onDestroy();
    }
}
