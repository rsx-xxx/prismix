package app.prismix.band;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.SurfaceTexture;
import android.hardware.Camera;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.Surface;
import android.view.TextureView;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.TextView;

import com.google.zxing.BinaryBitmap;
import com.google.zxing.DecodeHintType;
import com.google.zxing.PlanarYUVLuminanceSource;
import com.google.zxing.Result;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.QRCodeReader;

import java.util.Collections;

/** Scans the host's QR code. Returns the link as the result's data; Main treats it like a link opened from outside. */
public class ScanActivity extends Activity implements TextureView.SurfaceTextureListener, Camera.PreviewCallback {
    private Camera camera;
    private TextureView view;
    private TextView hint;
    private volatile boolean busy, done;
    private int w, h;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().setStatusBarColor(Color.BLACK);
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        view = new TextureView(this);
        view.setSurfaceTextureListener(this);
        root.addView(view, new FrameLayout.LayoutParams(-1, -1, Gravity.CENTER));
        hint = new TextView(this);
        hint.setTextColor(Color.WHITE);
        hint.setTextSize(16);
        hint.setGravity(Gravity.CENTER);
        hint.setText("Point the camera at the QR code on the host");
        hint.setPadding(48, 0, 48, 96);
        root.addView(hint, new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM));
        setContentView(root);
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] { Manifest.permission.CAMERA }, 2);
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        if (res.length > 0 && res[0] == PackageManager.PERMISSION_GRANTED) { if (view.isAvailable()) start(view.getSurfaceTexture()); }
        else hint.setText("Camera access is needed to scan. Allow it in the app settings, or pick the host from the list.");
    }

    @Override public void onSurfaceTextureAvailable(SurfaceTexture t, int width, int height) { start(t); }
    @Override public void onSurfaceTextureSizeChanged(SurfaceTexture t, int width, int height) { }
    @Override public boolean onSurfaceTextureDestroyed(SurfaceTexture t) { stop(); return true; }
    @Override public void onSurfaceTextureUpdated(SurfaceTexture t) { }

    private void start(SurfaceTexture t) {
        if (camera != null || t == null || checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) return;
        try {
            Camera.CameraInfo info = new Camera.CameraInfo();
            int id = 0;
            for (int i = 0; i < Camera.getNumberOfCameras(); i++) {
                Camera.getCameraInfo(i, info);
                if (info.facing == Camera.CameraInfo.CAMERA_FACING_BACK) { id = i; break; }
            }
            Camera.getCameraInfo(id, info);
            camera = Camera.open(id);
            Camera.Parameters p = camera.getParameters();
            Camera.Size best = null;
            for (Camera.Size s : p.getSupportedPreviewSizes()) {
                if (best == null || Math.abs(s.width * s.height - 1280 * 720) < Math.abs(best.width * best.height - 1280 * 720)) best = s;
            }
            if (best != null) p.setPreviewSize(best.width, best.height);
            if (p.getSupportedFocusModes().contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE)) p.setFocusMode(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE);
            camera.setParameters(p);
            w = p.getPreviewSize().width;
            h = p.getPreviewSize().height;
            int deg = 0;
            switch (getWindowManager().getDefaultDisplay().getRotation()) {
                case Surface.ROTATION_90: deg = 90; break;
                case Surface.ROTATION_180: deg = 180; break;
                case Surface.ROTATION_270: deg = 270; break;
                default: break;
            }
            int turn = (info.orientation - deg + 360) % 360;
            camera.setDisplayOrientation(turn);
            // fit the picture to the screen's width without stretching it
            boolean upright = turn % 180 == 90;
            view.post(() -> {
                int sw = ((View) view.getParent()).getWidth();
                view.setLayoutParams(new FrameLayout.LayoutParams(sw, Math.round(sw * (upright ? (float) w / h : (float) h / w)), Gravity.CENTER));
            });
            camera.setPreviewTexture(t);
            camera.setPreviewCallback(this);
            camera.startPreview();
        } catch (Exception e) {
            hint.setText("Could not start the camera.");
            stop();
        }
    }

    @Override
    public void onPreviewFrame(final byte[] data, Camera c) {
        if (busy || done) return;
        busy = true;
        final int fw = w, fh = h;
        new Thread(() -> {
            try {
                PlanarYUVLuminanceSource src = new PlanarYUVLuminanceSource(data, fw, fh, 0, 0, fw, fh, false);
                Result r = new QRCodeReader().decode(new BinaryBitmap(new HybridBinarizer(src)),
                        Collections.singletonMap(DecodeHintType.TRY_HARDER, Boolean.TRUE));
                final String text = r.getText();
                if (accept(text)) {
                    done = true;
                    runOnUiThread(() -> {
                        setResult(RESULT_OK, new Intent().setData(Uri.parse(text)));
                        finish();
                    });
                }
            } catch (Exception e) { /* no code in this frame */ }
            busy = false;
        }).start();
    }

    /** Only a Prismix link: a poster's QR must not send the app elsewhere. */
    private static boolean accept(String t) {
        if (t == null) return false;
        Uri u = Uri.parse(t);
        String s = u.getScheme();
        return ("prismix".equals(s) || "https".equals(s)) && u.getHost() != null && u.getPath() != null && u.getPath().startsWith("/remote");
    }

    private void stop() {
        if (camera != null) {
            camera.setPreviewCallback(null);
            camera.stopPreview();
            camera.release();
            camera = null;
        }
    }

    @Override
    protected void onPause() {
        stop();
        super.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (view.isAvailable()) start(view.getSurfaceTexture());
    }
}
