package com.roomform.studio;

import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;
import android.content.Intent;
import android.graphics.Color;
import android.media.Image;
import android.opengl.GLES11Ext;
import android.opengl.GLES20;
import android.opengl.GLSurfaceView;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.*;
import com.google.ar.core.*;
import com.google.ar.core.exceptions.NotYetAvailableException;
import org.json.*;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import javax.microedition.khronos.egl.EGLConfig;
import javax.microedition.khronos.opengles.GL10;

/** Native AR camera capture. Guided corners work without depth. Automatic convex fitting is experimental. */
public class RoomScanActivity extends ComponentActivity implements GLSurfaceView.Renderer {
    private GLSurfaceView surface;
    private TextView status;
    private Session session;
    private boolean installRequested, depthEnabled;
    private volatile boolean resumed;
    private volatile boolean finishing;
    private int width = 1, height = 1, texture, program;
    private long lastDepthTimestamp, lastSampleTime, lastStatus;
    private double roomHeight;
    private Anchor floorAnchor;
    private final ArrayList<Anchor> corners = new ArrayList<>();
    private final ArrayList<DepthBatch> batches = new ArrayList<>();
    private int pointCount;
    private final float[] quad = {-1,-1, 1,-1, -1,1, 1,1};
    private final float[] uv = new float[8];
    private final FloatBuffer vertices = buffer(8), textureCoords = buffer(8);
    private static class DepthBatch {
        final Anchor anchor; final ArrayList<float[]> points;
        DepthBatch(Anchor anchor, ArrayList<float[]> points) { this.anchor = anchor; this.points = points; }
    }
    private static FloatBuffer buffer(int count) { return ByteBuffer.allocateDirect(count * 4).order(ByteOrder.nativeOrder()).asFloatBuffer(); }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override public void handleOnBackPressed() { cancel("Scan cancelled"); }
        });
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        roomHeight = getIntent().getDoubleExtra("height", 2.7);
        FrameLayout root = new FrameLayout(this);
        surface = new GLSurfaceView(this); surface.setEGLContextClientVersion(2); surface.setPreserveEGLContextOnPause(true);
        surface.setRenderer(this); root.addView(surface);
        LinearLayout top = new LinearLayout(this); top.setOrientation(LinearLayout.VERTICAL); top.setPadding(20, 20, 20, 16); top.setBackgroundColor(0xC0182B24);
        status = new TextView(this); status.setTextColor(Color.WHITE); status.setTextSize(16); status.setText("Starting camera…"); top.addView(status);
        TextView guide = new TextView(this); guide.setTextColor(Color.WHITE); guide.setText("Move slowly. Aim + at a floor corner, then add each corner in order around the room. Mark the floor first for a depth estimate."); top.addView(guide);
        top.addView(button("Cancel scan", v -> cancel("Scan cancelled")));
        FrameLayout.LayoutParams topParams = new FrameLayout.LayoutParams(-1,-2,Gravity.TOP); root.addView(top, topParams);
        TextView cross = new TextView(this); cross.setText("+"); cross.setTextSize(38); cross.setTextColor(Color.WHITE); cross.setGravity(Gravity.CENTER);
        FrameLayout.LayoutParams crossParams = new FrameLayout.LayoutParams(80,80,Gravity.CENTER); root.addView(cross, crossParams);
        LinearLayout bottom = new LinearLayout(this); bottom.setOrientation(LinearLayout.VERTICAL); bottom.setPadding(16,10,16,12); bottom.setBackgroundColor(0xD0182B24);
        LinearLayout actions = new LinearLayout(this);
        actions.addView(button("Set floor", v -> surface.queueEvent(() -> mark(false))), new LinearLayout.LayoutParams(0,-2,1));
        actions.addView(button("Add corner", v -> surface.queueEvent(() -> mark(true))), new LinearLayout.LayoutParams(0,-2,1));
        actions.addView(button("Undo", v -> surface.queueEvent(() -> { if (!corners.isEmpty()) corners.remove(corners.size()-1).detach(); })), new LinearLayout.LayoutParams(0,-2,1));
        bottom.addView(actions);
        bottom.addView(button("Finish capture", v -> surface.queueEvent(this::complete)));
        TextView note = new TextView(this); note.setTextColor(Color.WHITE); note.setTextSize(12); note.setText("3+ corners: guided room. No corners: experimental depth estimate. Wall height: " + roomHeight + " m. Review every dimension."); bottom.addView(note);
        root.addView(bottom, new FrameLayout.LayoutParams(-1,-2,Gravity.BOTTOM));
        setContentView(root);
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(android.view.WindowInsets.Type.systemBars());
                v.setPadding(bars.left,bars.top,bars.right,bars.bottom);
            } else v.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());
            return insets;
        });
        root.requestApplyInsets();
    }
    private Button button(String text, View.OnClickListener action) { Button button = new Button(this); button.setText(text); button.setOnClickListener(action); return button; }
    private void message(String text) { runOnUiThread(() -> status.setText(text)); }
    private void cancel(String text) { if (finishing) return; finishing = true; setResult(RESULT_CANCELED, new Intent().putExtra("error",text)); finish(); }

    @Override public void onResume() {
        super.onResume();
        try {
            if (session == null) {
                if (ArCoreApk.getInstance().requestInstall(this, !installRequested) == ArCoreApk.InstallStatus.INSTALL_REQUESTED) { installRequested = true; return; }
                session = new Session(this);
                Config config = new Config(session); config.setPlaneFindingMode(Config.PlaneFindingMode.HORIZONTAL_AND_VERTICAL);
                depthEnabled = session.isDepthModeSupported(Config.DepthMode.AUTOMATIC);
                config.setDepthMode(depthEnabled ? Config.DepthMode.AUTOMATIC : Config.DepthMode.DISABLED);
                session.configure(config);
            }
            session.resume(); resumed = true; surface.onResume();
        } catch (Exception error) { cancel("AR camera could not start: " + error.getMessage()); }
    }
    @Override public void onPause() {
        surface.onPause(); if (session != null && resumed) { session.pause(); resumed = false; }
        super.onPause();
    }
    @Override public void onDestroy() {
        if (floorAnchor != null) floorAnchor.detach();
        for (Anchor a : corners) a.detach(); for (DepthBatch b : batches) b.anchor.detach();
        if (session != null) session.close(); super.onDestroy();
    }
    @Override public void onSurfaceCreated(GL10 gl, EGLConfig config) {
        GLES20.glClearColor(0.07f,0.12f,0.09f,1);
        int[] textures = new int[1]; GLES20.glGenTextures(1,textures,0); texture = textures[0];
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,texture);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,GLES20.GL_TEXTURE_MIN_FILTER,GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,GLES20.GL_TEXTURE_MAG_FILTER,GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,GLES20.GL_TEXTURE_WRAP_S,GLES20.GL_CLAMP_TO_EDGE);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,GLES20.GL_TEXTURE_WRAP_T,GLES20.GL_CLAMP_TO_EDGE);
        String vertex = "attribute vec2 aPosition; attribute vec2 aTex; varying vec2 vTex; void main(){gl_Position=vec4(aPosition,0.0,1.0);vTex=aTex;}";
        String fragment = "#extension GL_OES_EGL_image_external : require\nprecision mediump float; uniform samplerExternalOES uCamera; varying vec2 vTex; void main(){gl_FragColor=texture2D(uCamera,vTex);}";
        program = GLES20.glCreateProgram(); GLES20.glAttachShader(program,shader(GLES20.GL_VERTEX_SHADER,vertex)); GLES20.glAttachShader(program,shader(GLES20.GL_FRAGMENT_SHADER,fragment)); GLES20.glLinkProgram(program);
        int[] linked = new int[1]; GLES20.glGetProgramiv(program,GLES20.GL_LINK_STATUS,linked,0);
        if (linked[0] == 0) runOnUiThread(() -> cancel("Camera renderer could not be initialized."));
        vertices.put(quad).position(0);
    }
    private int shader(int type, String code) {
        int id = GLES20.glCreateShader(type); GLES20.glShaderSource(id,code); GLES20.glCompileShader(id); return id;
    }
    @Override public void onSurfaceChanged(GL10 gl,int w,int h) { width=w; height=h; GLES20.glViewport(0,0,w,h); }
    private Frame latest;
    @Override public void onDrawFrame(GL10 gl) {
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT | GLES20.GL_DEPTH_BUFFER_BIT);
        if (session == null || !resumed || finishing) return;
        try {
            session.setCameraTextureName(texture);
            session.setDisplayGeometry(getWindowManager().getDefaultDisplay().getRotation(),width,height);
            latest = session.update();
            if (latest.getTimestamp() == 0) return;
            latest.transformCoordinates2d(Coordinates2d.OPENGL_NORMALIZED_DEVICE_COORDINATES,quad,Coordinates2d.TEXTURE_NORMALIZED,uv);
            textureCoords.clear(); textureCoords.put(uv).position(0);
            GLES20.glDisable(GLES20.GL_DEPTH_TEST); GLES20.glUseProgram(program); GLES20.glActiveTexture(GLES20.GL_TEXTURE0); GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,texture);
            GLES20.glUniform1i(GLES20.glGetUniformLocation(program,"uCamera"),0);
            int p = GLES20.glGetAttribLocation(program,"aPosition"), t = GLES20.glGetAttribLocation(program,"aTex");
            GLES20.glEnableVertexAttribArray(p); GLES20.glEnableVertexAttribArray(t);
            GLES20.glVertexAttribPointer(p,2,GLES20.GL_FLOAT,false,0,vertices); GLES20.glVertexAttribPointer(t,2,GLES20.GL_FLOAT,false,0,textureCoords);
            GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP,0,4); GLES20.glDisableVertexAttribArray(p); GLES20.glDisableVertexAttribArray(t);
            long now = android.os.SystemClock.elapsedRealtime();
            boolean tracking = latest.getCamera().getTrackingState() == TrackingState.TRACKING;
            if (tracking && depthEnabled && now-lastSampleTime > 400) { collectDepth(latest); lastSampleTime = now; }
            if (now-lastStatus > 1800) {
                message(tracking ? corners.size()+" corners · "+pointCount+" depth points\n"+(floorAnchor == null ? "Floor not marked" : "Floor marked")+(depthEnabled ? " · Depth available" : " · Guided capture") : "Tracking paused. Move slowly toward a textured, well-lit surface."); lastStatus=now;
            }
        } catch (Exception error) { runOnUiThread(() -> cancel("Capture interrupted: " + error.getMessage())); }
    }
    private void mark(boolean corner) {
        if (latest == null || finishing || latest.getCamera().getTrackingState() != TrackingState.TRACKING) { message("Wait for camera tracking, then try again."); return; }
        for (HitResult hit : latest.hitTest(width/2f,height/2f)) {
            if (!(hit.getTrackable() instanceof Plane)) continue;
            Plane plane = (Plane)hit.getTrackable();
            if (plane.getType() != Plane.Type.HORIZONTAL_UPWARD_FACING || !plane.isPoseInPolygon(hit.getHitPose())) continue;
            if (floorAnchor != null && Math.abs(hit.getHitPose().ty()-floorAnchor.getPose().ty()) > 0.12f) { message("Aim at the same floor level, not a table or another surface."); return; }
            if (floorAnchor == null) floorAnchor = hit.createAnchor();
            if (corner) {
                if (corners.size() >= 128) { message("Maximum corner count reached."); return; }
                if (!corners.isEmpty()) { Pose previous = corners.get(corners.size()-1).getPose(); if (Math.hypot(previous.tx()-hit.getHitPose().tx(),previous.tz()-hit.getHitPose().tz()) < 0.1) { message("Move to the next corner."); return; } }
                corners.add(hit.createAnchor());
            }
            message(corner ? "Corner "+corners.size()+" added. Continue around the room in order." : "Floor marked. Scan every wall from several angles."); lastStatus=android.os.SystemClock.elapsedRealtime(); return;
        }
        message("No floor detected at +. Move the camera slowly over the floor, then aim again."); lastStatus=android.os.SystemClock.elapsedRealtime();
    }
    private void collectDepth(Frame frame) throws Exception {
        try (Image depth = frame.acquireRawDepthImage16Bits(); Image confidence = frame.acquireRawDepthConfidenceImage()) {
            if (depth.getTimestamp() == lastDepthTimestamp || depth.getTimestamp() != confidence.getTimestamp()) return;
            lastDepthTimestamp = depth.getTimestamp();
            CameraIntrinsics intrinsics = frame.getCamera().getTextureIntrinsics();
            int w=depth.getWidth(), h=depth.getHeight(); int[] dimensions=intrinsics.getImageDimensions();
            float[] focal=intrinsics.getFocalLength(), center=intrinsics.getPrincipalPoint();
            float fx=focal[0]*w/dimensions[0], fy=focal[1]*h/dimensions[1], cx=center[0]*w/dimensions[0], cy=center[1]*h/dimensions[1];
            Image.Plane d=depth.getPlanes()[0], c=confidence.getPlanes()[0]; ByteBuffer db=d.getBuffer().order(ByteOrder.LITTLE_ENDIAN), cb=c.getBuffer();
            ArrayList<float[]> points=new ArrayList<>(); int step=Math.max(2,(int)Math.sqrt(w*h/180.0));
            for (int y=step/2;y<h;y+=step) for (int x=step/2;x<w;x+=step) {
                int certainty=cb.get(y*c.getRowStride()+x*c.getPixelStride()) & 0xff; if(certainty<180) continue;
                float meters=(db.getShort(y*d.getRowStride()+x*d.getPixelStride()) & 0xffff)/1000f; if(meters<0.5f || meters>5f) continue;
                points.add(new float[]{meters*(x-cx)/fx,meters*(cy-y)/fy,-meters});
            }
            if (!points.isEmpty()) {
                if (batches.size() >= 100) { DepthBatch oldest=batches.remove(0); pointCount-=oldest.points.size(); oldest.anchor.detach(); }
                batches.add(new DepthBatch(session.createAnchor(frame.getCamera().getPose()),points)); pointCount+=points.size();
            }
        } catch (NotYetAvailableException ignored) { /* Depth warms up while the camera moves. */ }
    }
    private void complete() {
        if (finishing) return;
        if (floorAnchor == null || floorAnchor.getTrackingState()!=TrackingState.TRACKING) { message("Mark a tracked floor first."); return; }
        if (!corners.isEmpty() && corners.size()<3) { message("Add at least 3 corners in order, or undo them all to try depth estimation."); return; }
        if (corners.isEmpty() && (!depthEnabled || pointCount<150)) { message("Not enough depth data. Mark floor corners to create the room."); return; }
        try {
            JSONArray cornerJson=new JSONArray(), pointJson=new JSONArray();
            for(Anchor anchor:corners) {
                if(anchor.getTrackingState()!=TrackingState.TRACKING) { message("Some corners lost tracking. Move back toward the room before finishing."); return; }
                Pose p=anchor.getPose(); cornerJson.put(new JSONObject().put("x",p.tx()).put("z",p.tz()));
            }
            HashSet<String> occupied=new HashSet<>();
            for(DepthBatch batch:batches) {
                if(batch.anchor.getTrackingState()!=TrackingState.TRACKING) continue;
                Pose pose=batch.anchor.getPose();
                for(float[] local:batch.points) {
                    float[] p=pose.transformPoint(local); String key=Math.round(p[0]/0.05f)+":"+Math.round(p[1]/0.05f)+":"+Math.round(p[2]/0.05f);
                    if(occupied.add(key) && pointJson.length()<20000) pointJson.put(new JSONObject().put("x",p[0]).put("y",p[1]).put("z",p[2]));
                }
            }
            JSONObject capture=new JSONObject().put("kind","arcore").put("corners",cornerJson).put("points",pointJson).put("height",roomHeight).put("floorY",floorAnchor.getPose().ty()).put("depth",depthEnabled);
            File folder=new File(getFilesDir(),"captures"); if(!folder.exists() && !folder.mkdirs()) throw new IllegalStateException("Cannot create capture folder.");
            File file=new File(folder,UUID.randomUUID()+".json");
            try(FileOutputStream output = new FileOutputStream(file)) { output.write(capture.toString().getBytes(StandardCharsets.UTF_8)); }
            finishing=true; runOnUiThread(() -> { setResult(RESULT_OK,new Intent().putExtra("capturePath",file.getAbsolutePath())); finish(); });
        } catch(Exception error) { runOnUiThread(() -> cancel("Could not save capture: "+error.getMessage())); }
    }
}
