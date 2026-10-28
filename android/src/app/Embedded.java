package app.prismix.band;

import android.content.Context;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;

import javax.crypto.Cipher;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * A member invite baked into a locally built APK. build.sh fills KEY and ships the encrypted invite as an asset only
 * when files/secrets/band_member.env exists, so published builds carry nothing. This keeps the invite out of plain
 * sight in the APK; it is not a vault, which is why only a read-only, revocable member invite may ever go here.
 */
final class Embedded {
    private static final String KEY = "@@KEY@@";

    static String invite(Context ctx) {
        if (KEY.length() != 32) return null;
        try (InputStream in = ctx.getAssets().open("cloud.bin")) {
            java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream();
            byte[] chunk = new byte[1024];
            for (int n; (n = in.read(chunk)) > 0; ) buf.write(chunk, 0, n);
            byte[] all = buf.toByteArray();
            byte[] k = new byte[16];
            for (int i = 0; i < 16; i++) k[i] = (byte) Integer.parseInt(KEY.substring(i * 2, i * 2 + 2), 16);
            Cipher c = Cipher.getInstance("AES/CBC/PKCS5Padding");
            c.init(Cipher.DECRYPT_MODE, new SecretKeySpec(k, "AES"), new IvParameterSpec(all, 0, 16));
            return new String(c.doFinal(all, 16, all.length - 16), StandardCharsets.UTF_8).trim();
        } catch (Exception e) {
            return null;
        }
    }
}
