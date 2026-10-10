package com.roomform.studio;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import androidx.activity.result.ActivityResult;
import com.getcapacitor.*;
import com.getcapacitor.annotation.*;
import com.google.ar.core.ArCoreApk;
import java.io.File;
import java.io.FileInputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;

@CapacitorPlugin(name = "RoomScanner", permissions = @Permission(alias = "camera", strings = Manifest.permission.CAMERA))
public class RoomScannerPlugin extends Plugin {
    private boolean scanning;

    @PluginMethod public void capabilities(PluginCall call) {
        ArCoreApk.getInstance().checkAvailabilityAsync(getContext(), availability -> {
            JSObject result = new JSObject();
            result.put("platform", "android"); result.put("supported", availability.isSupported());
            result.put("mode", availability.isSupported() ? "arcore-guided" : "manual");
            result.put("reason", availability.isSupported() ? "ARCore available. Depth support is checked when scanning starts." : "ARCore is unavailable on this device. Use manual room entry.");
            call.resolve(result);
        });
    }
    @PluginMethod public void scan(PluginCall call) {
        if (scanning) { call.reject("A scan is already in progress."); return; }
        Double height = call.getDouble("height", 2.7);
        if (height == null || !Double.isFinite(height) || height < 0.5 || height > 12) { call.reject("Enter a wall height between 0.5 and 12 meters."); return; }
        scanning = true;
        if (getPermissionState("camera") != PermissionState.GRANTED) requestPermissionForAlias("camera", call, "cameraResult");
        else launch(call);
    }
    @PermissionCallback private void cameraResult(PluginCall call) {
        if (getPermissionState("camera") == PermissionState.GRANTED) launch(call);
        else { scanning = false; call.reject("Camera access is required. Allow it in Android Settings > Apps > Roomform > Permissions."); }
    }
    private void launch(PluginCall call) {
        Intent intent = new Intent(getActivity(), RoomScanActivity.class);
        intent.putExtra("height", call.getDouble("height", 2.7));
        startActivityForResult(call, intent, "scanResult");
    }
    @ActivityCallback private void scanResult(PluginCall call, ActivityResult result) {
        scanning = false;
        if (call == null) return;
        Intent data = result.getData();
        if (result.getResultCode() != Activity.RESULT_OK || data == null) {
            call.reject(data != null && data.hasExtra("error") ? data.getStringExtra("error") : "Scan cancelled"); return;
        }
        try {
            String path = data.getStringExtra("capturePath");
            if (path == null) throw new IllegalArgumentException("Capture result is missing.");
            File file = new File(path).getCanonicalFile();
            File root = new File(getContext().getFilesDir(), "captures").getCanonicalFile();
            if (!file.getPath().startsWith(root.getPath() + File.separator) || file.length() > 5 * 1024 * 1024) throw new IllegalArgumentException("Invalid capture file.");
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            try (FileInputStream input = new FileInputStream(file)) {
                byte[] chunk = new byte[8192]; int count;
                while ((count = input.read(chunk)) != -1) { bytes.write(chunk, 0, count); if (bytes.size() > 5 * 1024 * 1024) throw new IllegalArgumentException("Capture is too large."); }
            }
            JSObject capture = new JSObject(new String(bytes.toByteArray(), StandardCharsets.UTF_8));
            JSObject response = new JSObject(); response.put("capture", capture); call.resolve(response);
        } catch (Exception e) { call.reject("Could not read the captured room: " + e.getMessage()); }
    }
    @PluginMethod public void shareOriginal(PluginCall call) { call.reject("USDZ is available for original iPhone captures. Export GLB for this room."); }
}
