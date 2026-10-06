package com.rungic.plasma;

import android.app.*;
import android.content.*;
import android.os.Bundle;
import android.view.*;
import android.view.inputmethod.*;
import android.widget.*;
import android.util.Log;
import android.util.AtomicFile;
import android.graphics.Rect;
import com.winland.server.NativeBridge;
import java.io.*;
import java.util.concurrent.*;

public final class MainActivity extends Activity implements SurfaceHolder.Callback {
    private static final ExecutorService worker = Executors.newSingleThreadExecutor();
    private static volatile boolean initialized;
    private DisplayView display;
    private DisplayPacer pacer;
    private PlatformBridge platform;
    private MediaLink capture;   // the media backend's app side (docs/117)
    private CodecBridge codecs;
    private CastDesktop castDesktop;
    /** The assistant's screen: a 1920x1080 desktop output, on the TV or in a Linux floating window. */
    static final int[] AGENT_SCREEN_SIZE = {1920, 1080};
    private boolean desktopMode;
    /** The floating window's last report of showing the assistant's screen (docs/65). */
    private boolean agentScreenWatched = true;
    private String presenterOwner;
    private CastControls castControls;
    private StartupScreen loading;
    // The desktop's last frame, shown while the app comes back to the front until the desktop's
    // next one: the surface is gone while the app is away, and the session is not (docs/96).
    private android.widget.ImageView snapshot;
    private final java.util.concurrent.atomic.AtomicBoolean startupBusy=new java.util.concurrent.atomic.AtomicBoolean();
    private volatile int surfaceGeneration;
    private boolean awaitingFrame;
    private long frameTicket, frameDeadline;
    private int frameGeneration;
    private String notificationState="";
    private final Runnable installPoll=() -> {
        if(!isDestroyed() && this.started && display.getHolder().getSurface().isValid())
            surfaceCreated(display.getHolder());
    };
    private final Runnable framePoll=new Runnable() {
        @Override public void run() {
            if(isDestroyed() || !started || frameGeneration!=surfaceGeneration) { awaitingFrame=false; return; }
            if(NativeBridge.isPhoneFrameReady(frameTicket)) {
                awaitingFrame=false; loading.setVisibility(View.GONE); hideSnapshot(); notifyState(getString(R.string.state_running));
                Log.i("RungicWayland","desktop frame "+(android.os.SystemClock.uptimeMillis()-surfaceAt)+" ms after the surface");
            } else if(android.os.SystemClock.uptimeMillis()>frameDeadline) {
                awaitingFrame=false; NativeBridge.cancelPhoneFrame(frameTicket); hideSnapshot();
                showProblem(getString(R.string.display_unconfirmed), getString(R.string.display_unconfirmed_details), true);
            } else display.postDelayed(this,100);
        }
    };
    private volatile int bufferWidth = 720, bufferHeight = 1600;
    private FrameLayout frame;
    private boolean androidKeyboard;
    private volatile String displayMetrics;
    private String writtenDisplayMetrics;
    private volatile boolean accountReady;
    private volatile boolean accountPromptShowing;
    /** Cleared app data loses the install status; root republishes it once per process (docs/95). */
    private static volatile boolean installRepublishAsked;
    private android.window.OnBackInvokedCallback edgeBackCallback;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        controlTimeout = getString(R.string.control_timeout);
        if (getDisplay() != null && getDisplay().getDisplayId() != android.view.Display.DEFAULT_DISPLAY) {
            // Launched on a cast display (input focus had moved there): the desktop's
            // host window belongs on the phone; the TV gets its own window (docs/58).
            android.app.ActivityOptions options = android.app.ActivityOptions.makeBasic()
                .setLaunchDisplayId(android.view.Display.DEFAULT_DISPLAY);
            startActivity(new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), options.toBundle());
            finish();
            return;
        }
        getWindow().setDecorFitsSystemWindows(false);
        getWindow().setNavigationBarColor(android.graphics.Color.TRANSPARENT);
        getWindow().setStatusBarColor(android.graphics.Color.TRANSPARENT);
        getWindow().setNavigationBarContrastEnforced(false);
        WindowManager.LayoutParams attrs=getWindow().getAttributes();
        attrs.layoutInDisplayCutoutMode=WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS;
        getWindow().setAttributes(attrs);
        immersive();
        frame = new FrameLayout(this);
        PlatformBridge.applyOrientation(this,getPreferences(MODE_PRIVATE).getString("orientation","portrait"));
        display = new DisplayView();
        pacer = new DisplayPacer(display, () -> initialized,
                this::publishDisplayInfo, worker);
        capture = new MediaLink(this);
        codecs = new CodecBridge(this);
        platform = new PlatformBridge(this,capture);
        display.getHolder().addCallback(this);
        frame.addView(display, new FrameLayout.LayoutParams(-1, -1));
        loading=new StartupScreen(this,() -> {
            display.removeCallbacks(installPoll);
            if(display.getHolder().getSurface().isValid())surfaceCreated(display.getHolder());
        },() -> moveTaskToBack(true));
        loading.show(getString(R.string.state_preparing),getString(R.string.please_wait),true,false,"");
        snapshot=new android.widget.ImageView(this);
        snapshot.setScaleType(android.widget.ImageView.ScaleType.FIT_XY);
        snapshot.setVisibility(View.GONE);
        frame.addView(snapshot,new FrameLayout.LayoutParams(-1,-1));
        frame.addView(loading,new FrameLayout.LayoutParams(-1,-1));
        setContentView(frame);
        castControls = new CastControls(this, frame, this::setAndroidKeyboard);
        desktopMode = getPreferences(MODE_PRIVATE).getBoolean("desktop_mode", false);
        // "agent_screen" was the one switch before desktop mode and the assistant's screen split.
        assistantScreen = getPreferences(MODE_PRIVATE).getBoolean("assistant_screen",
            getPreferences(MODE_PRIVATE).getBoolean("agent_screen", false));
        assistantWorkspace = Math.max(1, getPreferences(MODE_PRIVATE).getInt("agent_workspace", 1));
        director = new Director(this);
        castDesktop = new CastDesktop(this, () -> initialized, () -> desktopMode ? AGENT_SCREEN_SIZE : null, bound -> {
            // A TV that goes away forgets what it showed: the next one shows the director (Director.bound).
            director.bound(bound, castDesktop.display());
            castControls.setAvailable(bound);
            castBoundAt = bound ? android.os.SystemClock.uptimeMillis() : 0;
            // The secondary home may have taken the focus before the TV got the desktop.
            if (bound) display.postDelayed(this::reclaimFocus, 400);
        });
        registerEdgeBack();
        display.setOnApplyWindowInsetsListener((v, insets) -> {
            captureDisplayInsets(insets);
            castControls.imeVisible(insets.isVisible(WindowInsets.Type.ime()));
            return insets;
        });
        display.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or_,ob) ->
            { resizeDisplay(l,t,r,b); captureDisplayInsets(display.getRootWindowInsets()); });
        display.requestApplyInsets();
        display.requestFocus();
        notifyState(getString(R.string.state_preparing));
    }

    @Override public void onDestroy() {
        if (pacer == null) { super.onDestroy(); return; } // finished before setup (onCreate)
        if(android.os.Build.VERSION.SDK_INT>=34 && edgeBackCallback!=null)
            getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(edgeBackCallback);
        pacer.stop();
        display.removeCallbacks(installPoll);
        display.removeCallbacks(framePoll);
        if(frameTicket!=0)NativeBridge.cancelPhoneFrame(frameTicket);
        surfaceGeneration++;
        if (idleInhibitFd != null) {
            android.os.Looper.getMainLooper().getQueue().removeOnFileDescriptorEventListener(idleInhibitFd.getFileDescriptor());
            try { idleInhibitFd.close(); } catch (IOException ignored) {}
        }
        castDesktop.release();
        capture.close();
        try { codecs.close(); } catch(IOException ignored) {}
        try { platform.close(); } catch(IOException ignored) {}
        super.onDestroy();
    }

    // FLAG_KEEP_SCREEN_ON has three owners: the Linux session (keep-awake op, e.g.
    // video playback through PowerDevil), a running cast, and Wayland idle inhibition
    // (KWin inhibits on its output surface while a window does, docs/72); the flag is the union.
    static final int AWAKE_LINUX = 1, AWAKE_CAST = 2, AWAKE_WAYLAND = 4;
    private int keepAwake;
    private android.os.ParcelFileDescriptor idleInhibitFd;
    private final android.os.MessageQueue.OnFileDescriptorEventListener idleInhibitChanged = (fd, events) -> {
        try { android.system.Os.read(fd, new byte[8], 0, 8); } catch (Exception e) { }
        setKeepAwake(AWAKE_WAYLAND, NativeBridge.idleInhibited());
        return android.os.MessageQueue.OnFileDescriptorEventListener.EVENT_INPUT;
    };

    /** Follow the host's idle inhibition from now on (main thread, once the host exists). */
    private void watchIdleInhibit() {
        if (idleInhibitFd != null) return;
        try { idleInhibitFd = android.os.ParcelFileDescriptor.fromFd(NativeBridge.idleInhibitFd()); }
        catch (IOException e) { Log.e("RungicWayland", "idle inhibition not followed", e); return; }
        android.os.Looper.getMainLooper().getQueue().addOnFileDescriptorEventListener(idleInhibitFd.getFileDescriptor(),
            android.os.MessageQueue.OnFileDescriptorEventListener.EVENT_INPUT, idleInhibitChanged);
        setKeepAwake(AWAKE_WAYLAND, NativeBridge.idleInhibited());
    }

    void setKeepAwake(int source, boolean on) {
        keepAwake = on ? keepAwake | source : keepAwake & ~source;
        if (keepAwake != 0) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }

    private boolean started, topResumed;
    private long focusReclaimWindowStart, castBoundAt;
    private int focusReclaims;
    // The vendor home takes the focus only while the cast display comes up.
    private static final long FOCUS_RECLAIM_AFTER_CAST_MS = 15000;

    // When a cast display connects, Android starts the vendor's secondary-display
    // home there and moves input focus to that display; the desktop host (still
    // visible on the phone) then loses focus, the platform bridge refuses requests
    // and phone input may go astray. Take the focus back on the phone (docs/58 step 4),
    // a few times at most so two parties never fight over it. Only right after the
    // cast display is bound: later the focus moves because the user left (home
    // gesture, recents, notifications), and pulling the app back broke going home.
    @Override public void onTopResumedActivityChanged(boolean top) {
        super.onTopResumedActivityChanged(top);
        topResumed = top;
        if (!top && castControls != null && castControls.available()) display.postDelayed(this::reclaimFocus, 400);
    }

    private void reclaimFocus() {
        if (!started || topResumed || !castControls.available()) return;
        long now = android.os.SystemClock.uptimeMillis();
        if (now - castBoundAt > FOCUS_RECLAIM_AFTER_CAST_MS) return;
        if (now - focusReclaimWindowStart > 30000) { focusReclaimWindowStart = now; focusReclaims = 0; }
        if (++focusReclaims > 3) return;
        Log.i("RungicCast", "taking input focus back from the cast display");
        startActivity(new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT),
            android.app.ActivityOptions.makeBasic().setLaunchDisplayId(android.view.Display.DEFAULT_DISPLAY).toBundle());
    }

    @Override public void onStart() {
        super.onStart();
        started = true;
        if(capture!=null)capture.setVisible(true);
        if(platform!=null)platform.desktopBoost(true);
        if(pacer!=null && display.getHolder().getSurface().isValid())pacer.start();
        if(display!=null) {
            if(awaitingFrame)display.post(framePoll);
            else if(!accountReady)display.post(installPoll);
        }
    }
    @Override public void onPause() {
        keepLastFrame();
        super.onPause();
    }
    @Override public void onStop() {
        started = false;
        if(display!=null) { display.removeCallbacks(installPoll); display.removeCallbacks(framePoll); }
        if(pacer!=null)pacer.stop();
        if(capture!=null)capture.setVisible(false);
        if(platform!=null)platform.desktopBoost(false);
        super.onStop();
    }
    @Override public void onRequestPermissionsResult(int code,String[] permissions,int[] grants) {
        super.onRequestPermissionsResult(code,permissions,grants);
        if(code==MediaLink.PERMISSION_REQUEST && capture!=null)capture.permissionResult(permissions,grants);
    }

    private void immersive() {
        WindowInsetsController controller=getWindow().getDecorView().getWindowInsetsController();
        if(controller!=null) {
            controller.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            controller.hide(WindowInsets.Type.systemBars());
        }
    }
    // A turn shows Android's status and navigation bars again over the Linux panels: hidden as when
    // the window gets the focus (docs/50).
    @Override public void onConfigurationChanged(android.content.res.Configuration config) {
        super.onConfigurationChanged(config);
        immersive();
        if(display!=null)display.requestApplyInsets();
    }
    @Override public void onWindowFocusChanged(boolean focus) {
        super.onWindowFocusChanged(focus);
        if(focus) { immersive(); if(display!=null)display.requestApplyInsets(); }
    }

    // Use SurfaceView coordinates, then convert to the actual Wayland buffer.
    // System bars are transient overlays; only permanent cutouts reserve space.
    private void captureDisplayInsets(WindowInsets insets) {
        if(insets==null || display.getWidth()==0 || display.getHeight()==0)return;
        float sx=(float)bufferWidth/display.getWidth(), sy=(float)bufferHeight/display.getHeight();
        int[] location=new int[2]; display.getLocationOnScreen(location);
        Rect topCutout=new Rect();
        int safeTop=0, safeLeft=0, safeRight=0, radius=0;
        DisplayCutout cutout=insets.getDisplayCutout();
        // Edge to edge in either orientation (docs/50): the desktop goes on under the camera hole, as
        // the user wants; Android's safe area is not reserved. The cutout is still reported below
        // (the Linux panel centres the status bar around it when upright).
        if(frame.getPaddingLeft()!=0 || frame.getPaddingRight()!=0)frame.setPadding(0,0,0,0);
        if(cutout!=null) {
            safeTop=Math.max(0,cutout.getSafeInsetTop()-location[1]);
            safeLeft=Math.max(0,cutout.getSafeInsetLeft()-location[0]);
            safeRight=Math.max(0,cutout.getSafeInsetRight()-frame.getPaddingRight());
            for(Rect bounds:cutout.getBoundingRects()) {
                Rect local=new Rect(bounds); local.offset(-location[0],-location[1]);
                if(local.intersect(0,0,display.getWidth(),display.getHeight()) && local.top<safeTop)
                    topCutout.union(local);
            }
        }
        if(android.os.Build.VERSION.SDK_INT>=31) {
            for(int position:new int[]{RoundedCorner.POSITION_TOP_LEFT,RoundedCorner.POSITION_TOP_RIGHT}) {
                RoundedCorner corner=insets.getRoundedCorner(position);
                if(corner!=null)radius=Math.max(radius,corner.getRadius());
            }
        }
        displayMetrics="[display]\nversion=1\nwidth="+bufferWidth+"\nheight="+bufferHeight+
            "\nsafe-top="+(int)Math.ceil(safeTop*sy)+
            "\nsafe-left="+(int)Math.ceil(safeLeft*sx)+
            "\nsafe-right="+(int)Math.ceil(safeRight*sx)+
            "\ncutout-left="+(int)Math.floor(topCutout.left*sx)+
            "\ncutout-right="+(int)Math.ceil(topCutout.right*sx)+
            "\ncutout-top="+(int)Math.floor(topCutout.top*sy)+
            "\ncutout-bottom="+(int)Math.ceil(topCutout.bottom*sy)+
            "\ncorner-radius="+(int)Math.ceil(radius*Math.max(sx,sy))+"\n";
        worker.execute(this::writeDisplayInsets);
    }

    private void writeDisplayInsets() {
        String metrics=displayMetrics;
        if(metrics==null || metrics.equals(writtenDisplayMetrics))return;
        File dir=new File(getFilesDir(),"tmp"); dir.mkdirs();
        AtomicFile file=new AtomicFile(new File(dir,"android-display.ini"));
        FileOutputStream stream=null;
        try {
            stream=file.startWrite();
            stream.write(metrics.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            file.finishWrite(stream); stream=null;
            android.system.Os.chmod(file.getBaseFile().getAbsolutePath(),0644);
            writtenDisplayMetrics=metrics;
            Log.i("RungicSafeArea",metrics.replace('\n',' '));
        } catch(Exception e) {
            if(stream!=null)file.failWrite(stream);
            Log.e("RungicSafeArea","Cannot publish display insets",e);
        }
    }

    private long surfaceAt;
    @Override public void surfaceCreated(SurfaceHolder holder) {
        surfaceAt=android.os.SystemClock.uptimeMillis();
        pacer.start();
        if(awaitingFrame || !startupBusy.compareAndSet(false,true))return;
        final int generation=surfaceGeneration;
        worker.execute(() -> {
            try {
                if (isDestroyed() || generation!=surfaceGeneration || !holder.getSurface().isValid()) return;
                File installSource=new File(getFilesDir(),"rungic-install-source.properties");
                File installStatus=new File(getFilesDir(),"rungic-install.properties");
                if(!installRepublishAsked && !installSource.exists() && !installStatus.exists()) {
                    installRepublishAsked=true;
                    try { control("install-publish"); }
                    catch(Exception e) {
                        if(showRemovalFailure(e,generation)) { installRepublishAsked=false; return; }
                        Log.w("RungicWayland","Install status not republished",e);
                    }
                }
                FirstBootState install=FirstBootState.readSource(installSource,
                    new File("/product/etc/rungic/seed.env"),installStatus);
                if(!install.ready) {
                    runOnUiThread(() -> {
                        if(isDestroyed() || generation!=surfaceGeneration)return;
                        loading.show(getString(install.failed?R.string.setup_attention_title:R.string.state_preparing),
                            StartupScreen.message(this,install),!install.failed && !install.attention,true,
                            StartupScreen.details(this,install));
                        notifyState(getString(install.failed?R.string.state_setup_attention:R.string.state_preparing));
                        display.removeCallbacks(installPoll);
                        if(started)display.postDelayed(installPoll,2000);
                    });
                    return;
                }
                new File(getFilesDir(), "tmp").mkdirs();
                if (!accountReady) {
                    if (accountPromptShowing) return;
                    org.json.JSONObject account=new org.json.JSONObject(control("account-status"));
                    if(account.optBoolean("pending",false)) {
                        runOnUiThread(()->{
                            if(isDestroyed() || generation!=surfaceGeneration)return;
                            showLoading(getString(R.string.account_pending));
                            display.removeCallbacks(installPoll);
                            if(started)display.postDelayed(installPoll,2000);
                        }); return;
                    }
                    accountReady=account.optBoolean("configured",false);
                    if (!accountReady) {
                        runOnUiThread(() -> showLoading(getString(R.string.account_preparing)));
                        control("account-prepare");
                        accountPromptShowing=true;
                        runOnUiThread(() -> {
                            if(isDestroyed() || generation!=surfaceGeneration) { accountPromptShowing=false; return; }
                            notifyState(getString(R.string.state_account));
                            AccountSetup.show(this,worker,payload -> {
                                try { control("account-setup",payload); }
                                catch(Exception failure) {
                                    // A timeout may have happened after the transaction committed.
                                    // A serialized status read must finish before another form is offered.
                                    if(!new org.json.JSONObject(control("account-status")).optBoolean("configured",false))throw failure;
                                }
                            },() -> {
                                accountReady=true; accountPromptShowing=false;
                                display.post(installPoll);
                            },failure -> {
                                accountPromptShowing=false;
                                showProblem(getString(R.string.account_incomplete), getString(R.string.account_incomplete_details), true);
                            });
                        });
                        return;
                    }
                }
                // Back to the front of a desktop this process already shows: the session is running
                // (it outlives the app, docs/96), so the desktop's next frame is all that is waited
                // for, behind its last one; the controller's checks run after it (resumeChecks).
                final boolean resume=initialized;
                runOnUiThread(() -> { if(resume)showSnapshot(); else showLoading(getString(R.string.starting_desktop)); });
                KeyboardAssets.ensure(getApplicationContext());
                new File(getFilesDir(), "tmp").mkdirs();
                platform.start();
                capture.start();
                codecs.start();
                boolean newServer = !initialized;
                if (!initialized) {
                    boolean ok = NativeBridge.initWaylandConnection(holder.getSurface(), getApplicationContext(), "ubuntu");
                    String error = NativeBridge.getLastNativeError();
                    if (!ok || error != null) throw new IOException(error == null ? "Wayland 初始化失败" : error);
                    initialized = true;
                    NativeBridge.setInputMode(1);
                    NativeBridge.setScale(1);
                    NativeBridge.setRefreshRate(display.getDisplay().getRefreshRate());
                    runOnUiThread(this::watchIdleInhibit);
                } else NativeBridge.rebindSurface(holder.getSurface());
                NativeBridge.resumeRendering();
                // Desktop mode first, so a TV connected before the desktop (re)started presents
                // it rather than making an output of its own.
                NativeBridge.presentWorkspace(0);
                if (desktopMode) NativeBridge.setAgentScreen(true, AGENT_SCREEN_SIZE[0], AGENT_SCREEN_SIZE[1], 60000);
                // A TV that was connected before the desktop (re)started gets it now.
                runOnUiThread(castDesktop::refresh);
                android.system.Os.chmod(new File(getFilesDir(), "tmp").getAbsolutePath(), 0755);
                if (!NativeBridge.startGpuAllocator(new File(getFilesDir(), "tmp/rungic-gpu-alloc").getAbsolutePath())) throw new IOException("GPU 缓冲服务启动失败");
                // Container releases from before the Rungic rename connect to the old name (docs/70, until phase D).
                try { android.system.Os.symlink("rungic-gpu-alloc", new File(getFilesDir(), "tmp/moto-gpu-alloc").getAbsolutePath()); }
                catch (android.system.ErrnoException e) { if (e.errno != android.system.OsConstants.EEXIST) throw new IOException(e); }
                updateSize();
                writeDisplayInsets();
                // A new host (this process started it) used to restart the whole desktop session.
                // KWin now keeps the session while the host is away and reconnects by itself, so
                // the windows survive the app being closed or killed; start only makes sure the
                // container and session run (the controller restarts a failed session).
                if(!resume)control("start");
                Log.i("RungicWayland", NativeBridge.getWaylandRuntimeStats());
                long ticket=NativeBridge.requestPhoneFrame();
                if(ticket==0)throw new IOException("无法请求显示状态");
                runOnUiThread(() -> {
                    if(isDestroyed() || generation!=surfaceGeneration) {
                        NativeBridge.cancelPhoneFrame(ticket); return;
                    }
                    if(!resume)showLoading(getString(R.string.waiting_frame));
                    frameTicket=ticket; frameGeneration=generation;
                    frameDeadline=android.os.SystemClock.uptimeMillis()+60000;
                    awaitingFrame=true;
                    if(started)display.post(framePoll);
                });
                if(resume)resumeChecks();
            } catch (Throwable e) {
                Log.e("RungicWayland", "Start failed", e);
                if(showRemovalFailure(e,generation))return;
                runOnUiThread(() -> {
                    if(!isDestroyed() && generation==surfaceGeneration)
                        showProblem(getString(R.string.start_failed), getString(R.string.start_failed_details), true);
                });
            } finally {
                startupBusy.set(false);
                if(generation!=surfaceGeneration && !isDestroyed())runOnUiThread(()->{ if(started)display.post(installPoll); });
            }
        });
    }

    /** Runs on a worker, after failure only; no extra root call on a normal return to the front. */
    private boolean showRemovalFailure(Throwable failure, int generation) {
        RemovalState.Result state=RemovalState.afterFailure(failure,()->control("removal-status"));
        if(state==RemovalState.Result.OTHER)return false;
        runOnUiThread(()->{
            if(isDestroyed() || generation!=surfaceGeneration)return;
            boolean unknown=state==RemovalState.Result.UNKNOWN;
            showProblem(getString(unknown?R.string.removal_status_unknown:R.string.removal_incomplete),
                getString(unknown?R.string.removal_status_unknown_details:R.string.removal_incomplete_details),unknown);
        });
        return true;
    }

    private void notifyState(String message) {
        if(!message.equals(notificationState)) { notificationState=message; DesktopService.update(this,message); }
    }
    /** What start does on a resume, off the display's path: the controller restarts a failed
     *  session and the hardware backends that stopped (a frame that does not come waits for it). */
    private void resumeChecks() {
        final int generation=surfaceGeneration;
        Thread checks=new Thread(() -> {
            try { control("start"); }
            catch(Exception e) {
                showRemovalFailure(e,generation);
                Log.w("RungicWayland","Checks after the return to the front failed",e);
            }
        },"rungic-resume-checks");
        checks.setDaemon(true);checks.start();
    }
    /** Keeps the desktop's last frame for the return to the front (the surface is still there). */
    private void keepLastFrame() {
        if(!initialized || display==null || !display.getHolder().getSurface().isValid())return;
        try {
            android.graphics.Bitmap bitmap=android.graphics.Bitmap.createBitmap(Math.max(1,display.getWidth()/2),
                Math.max(1,display.getHeight()/2),android.graphics.Bitmap.Config.ARGB_8888);
            android.view.PixelCopy.request(display,bitmap,result -> {
                if(result==android.view.PixelCopy.SUCCESS && !isDestroyed())snapshot.setImageBitmap(bitmap);
                else bitmap.recycle();
            },new android.os.Handler(android.os.Looper.getMainLooper()));
        } catch(RuntimeException e) { Log.w("RungicWayland","Last frame not kept",e); }
    }
    private void showSnapshot() {
        if(isDestroyed())return;
        if(snapshot.getDrawable()!=null)snapshot.setVisibility(View.VISIBLE);
        loading.setVisibility(View.GONE);
    }
    private void hideSnapshot() {
        snapshot.setVisibility(View.GONE);
        snapshot.setImageDrawable(null);
    }
    private void showLoading(String message) {
        if(isDestroyed())return;
        loading.show(getString(R.string.state_preparing),message,true,false,""); notifyState(getString(R.string.state_preparing));
    }
    private void showProblem(String message,String details,boolean retry) {
        if(isDestroyed())return;
        loading.show(getString(R.string.attention_title),message,false,retry,details); notifyState(getString(R.string.state_attention));
    }

    private void resizeDisplay(int l,int t,int r,int b) {
        int width=r-l,height=b-t;
        if(width<=0 || height<=0)return;
        Display.Mode physical=display.getDisplay().getMode();
        int shortEdge=getPreferences(MODE_PRIVATE).getInt("render_short_edge",Math.min(physical.getPhysicalWidth(),physical.getPhysicalHeight()));
        int w=width>height?Math.max(shortEdge,(int)Math.round(shortEdge*0.5*width/height)*2):shortEdge;
        int h=height>=width?Math.max(shortEdge,(int)Math.round(shortEdge*0.5*height/width)*2):shortEdge;
        if(w==bufferWidth && h==bufferHeight)return;
        bufferWidth=w;bufferHeight=h;
        display.getHolder().setFixedSize(w,h);
        worker.execute(this::updateSize);
        captureDisplayInsets(display.getRootWindowInsets());
        publishDisplayInfo();
    }

    org.json.JSONObject castDesktop(org.json.JSONObject request) throws Exception { return castDesktop.request(request); }
    /**
     * Two screens beside the phone's own (docs/65, docs/research/91), each in its Linux floating
     * window (in a window or fullscreen there, docs/research/97 §17) or on the TV:
     * - desktop mode ("desktop-mode"): the user's desktop gets a second output, a full desktop.
     *   {"enabled"} turns it on or off, {"watched"} is its floating window's report of showing its
     *   picture (false: tucked into the edge; it then gets frames at a low rate). A TV shows it by
     *   default: casting is desktop mode on the TV.
     * - the assistant's screen ("agent-screen"): the agent's own workspace (a KWin of its own).
     *   {"enabled"} shows or hides it, {"workspace"} which one, {"tv"} on the TV instead of the
     *   desktop.
     * The TV's presenter shows one of them at a time (bindPresenter). The app's own fullscreen
     * (AgentFullscreen, {"fullscreen"}) is gone since 2026-10-03: fullscreen is the Linux window's.
     */
    private boolean assistantScreen;
    private int assistantWorkspace = 1;
    private Director director;             // the assistant's screens together; what a TV shows (docs/58)
    org.json.JSONObject desktopMode(org.json.JSONObject request) throws Exception {
        if (request.has("enabled")) {
            desktopMode = request.getBoolean("enabled");
            // A floating window starts showing its picture; a new one reports otherwise.
            setAgentScreenWatched(true);
            getPreferences(MODE_PRIVATE).edit().putBoolean("desktop_mode", desktopMode).apply();
            if (initialized) NativeBridge.setAgentScreen(desktopMode, AGENT_SCREEN_SIZE[0], AGENT_SCREEN_SIZE[1], 60000);
        }
        if (request.has("watched")) setAgentScreenWatched(request.getBoolean("watched"));
        return new org.json.JSONObject().put("enabled", desktopMode)
            .put("width", AGENT_SCREEN_SIZE[0]).put("height", AGENT_SCREEN_SIZE[1])
            .put("tv", castControls.available() && !director.onTv())
            .put("watched", agentScreenWatched);
    }
    org.json.JSONObject agentScreen(org.json.JSONObject request) throws Exception {
        if (request.has("workspace")) {
            assistantWorkspace = Math.max(1, request.getInt("workspace"));
            getPreferences(MODE_PRIVATE).edit().putInt("agent_workspace", assistantWorkspace).apply();
        }
        if (request.has("enabled")) {
            assistantScreen = request.getBoolean("enabled");
            getPreferences(MODE_PRIVATE).edit().putBoolean("assistant_screen", assistantScreen).apply();
        }
        if (request.has("tv")) setTvSource(request.getBoolean("tv") ? assistantWorkspace : 0);
        if (request.length() > 1) HostEvents.bump(HostEvents.SCREENS);   // a change, not a query
        return new org.json.JSONObject().put("enabled", assistantScreen).put("workspace", assistantWorkspace)
            .put("width", AGENT_SCREEN_SIZE[0]).put("height", AGENT_SCREEN_SIZE[1])
            .put("tv", director.shown().contains(assistantWorkspace))
            .put("tvShown", new org.json.JSONArray(director.shown()))
            .put("tvHeard", director.onTv() && director.focus() != Director.BOARD ? director.focus() : -1)
            .put("directorFocus", director.focus());
    }
    Director director() { return director; }
    /**
     * "tv" (docs/58): what the TV shows. {"content": "director" | "desktop"} the director (the
     * assistant's screens) or the user's desktop in computer mode; {"button": true, "source": n}
     * the cast button of screen n (0 computer mode): with a TV the cast controls, else the TV
     * picker, the picked TV then showing the director (n in focus) or computer mode. A window's
     * button sends "content" with it (what that window shows); the cast quick setting does not,
     * and only opens the controls.
     */
    org.json.JSONObject tv(org.json.JSONObject request) throws Exception {
        boolean picking = false;
        if (request.has("content")) director.setTvDirector("director".equals(request.getString("content")));
        if (request.optBoolean("button")) picking = castButton(request.optInt("source", 0), false);
        return director.state().put("picking", picking);
    }
    /**
     * "director" (docs/58): the assistant's screens together. {"focus": n}, {"step": 1 | -1} the
     * next or previous in focus, {"level": 0 | 1 | 2} standard, enlarged, solo. Answers its state;
     * its "version" changes with every change (the phone's director window follows it).
     */
    org.json.JSONObject directorRequest(org.json.JSONObject request) throws Exception {
        if (request.has("focus")) director.setFocus(request.getInt("focus"));
        if (request.has("step")) director.step(request.getInt("step"));
        if (request.has("level")) director.setLevel(request.getInt("level"));
        if (request.has("background")) director.setBackground(android.util.Base64.decode(request.getString("background"), android.util.Base64.DEFAULT));
        if (request.has("member")) {
            org.json.JSONObject m = request.getJSONObject("member");
            director.setMember(m.getInt("slot"), m.optString("role"), m.optString("kind"), m.optString("text"));
        }
        if (request.has("board")) director.setBoard(request.getJSONObject("board"));
        if (request.has("alive")) director.alive(request.getInt("alive"));
        if (request.has("caption")) {
            org.json.JSONObject c = request.getJSONObject("caption");
            director.setCaption(c.getInt("slot"), c.optString("state", "working"), c.optString("text"));
        }
        return director.state();
    }
    /**
     * The cast button of screen `source`: with a TV the cast controls; else the TV picker. True:
     * picking. `inApp`: tapped in the app's own views; else asked over the bridge, which needs the
     * app in front to show anything.
     */
    boolean castButton(int source, boolean inApp) {
        // Workspace `source` in the director's focus, a TV already there too.
        if (source > 0) director.setFocus(source);
        if (castControls.available()) { castControls.openPanel(); return false; }
        if (!inApp && !hasWindowFocus()) throw new IllegalStateException("请先返回 Plasma Mobile");
        castControls.pickTv();
        return true;
    }
    /** The TV shows workspace `source` in the director's focus, or (0) computer mode. */
    private void setTvSource(int source) {
        if (source > 0) director.setFocus(source);
        director.setTvDirector(source > 0);
    }
    private void setAgentScreenWatched(boolean watched) {
        agentScreenWatched = watched;
        NativeBridge.setAgentScreenWatched(watched);
    }

    /**
     * The host's second presenter: the TV's window ("tv", CastDesktop). It releases it only while it
     * is the one bound (the app's own fullscreen, its other owner, is gone since 2026-10-03).
     */
    void bindPresenter(String owner, android.view.Surface surface, int width, int height, int refreshMhz, int rotation) {
        // The window's base, under the director's tiles: the wallpaper, blurred (docs/58).
        director.prepareBackground(width, height, rotation);
        // Its source first (the desktop, or a workspace), so the host makes no output for the other.
        NativeBridge.presentWorkspace(director.tvSource());
        NativeBridge.bindCastSurface(surface, width, height, refreshMhz, rotation);
        presenterOwner = owner;
        HostEvents.bump(HostEvents.SCREENS);
        NativeBridge.setPhoneCovered(false);
    }
    void releasePresenter(String owner) {
        if (!owner.equals(presenterOwner)) return;
        NativeBridge.releaseCastSurface();
        HostEvents.bump(HostEvents.SCREENS);
        presenterOwner = null;
        NativeBridge.setPhoneCovered(false);
    }
    org.json.JSONObject castControls(org.json.JSONObject request) throws Exception {
        if (request.has("mode")) castControls.setMode(CastControls.Mode.valueOf(request.getString("mode").toUpperCase()));
        return castControls.status();
    }

    /** Android keyboard on the phone; its keys and text go to the focused Linux window. */
    private void setAndroidKeyboard(boolean show) {
        InputMethodManager im=(InputMethodManager)getSystemService(INPUT_METHOD_SERVICE);
        if (show) {
            androidKeyboard=true;
            display.requestFocus();
            im.restartInput(display);
            im.showSoftInput(display, InputMethodManager.SHOW_IMPLICIT);
        } else {
            getWindow().getInsetsController().hide(WindowInsets.Type.ime());
            androidKeyboard=false;
        }
    }

    org.json.JSONObject displayInfo() throws org.json.JSONException {
        Display d=display.getDisplay();
        if(d==null)throw new IllegalStateException("Display unavailable");
        Display.Mode physical=d.getMode();
        android.util.DisplayMetrics metrics=new android.util.DisplayMetrics();
        d.getRealMetrics(metrics);
        int densityDpi=metrics.densityDpi,densityWidth=metrics.widthPixels,densityHeight=metrics.heightPixels;
        if(android.os.Build.VERSION.SDK_INT>=34) {
            // Maximum bounds describe the display reference, not the current Surface or a
            // split-screen window. Get density from that same immutable metrics snapshot.
            android.view.WindowMetrics reference=getWindowManager().getMaximumWindowMetrics();
            densityDpi=Math.round(reference.getDensity()*160);
            densityWidth=reference.getBounds().width();densityHeight=reference.getBounds().height();
        }
        int width=Math.max(1,display.getWidth()),height=Math.max(1,display.getHeight());
        int nativeEdge=Math.min(physical.getPhysicalWidth(),physical.getPhysicalHeight());
        org.json.JSONArray sizes=new org.json.JSONArray();
        for(int edge:new int[]{720,nativeEdge}) {
            if(edge==720 && edge>=nativeEdge)continue;
            int w=width>height?(int)Math.round(edge*0.5*width/height)*2:edge;
            int h=height>=width?(int)Math.round(edge*0.5*height/width)*2:edge;
            sizes.put(new org.json.JSONObject().put("width",w).put("height",h));
        }
        return new org.json.JSONObject().put("version",1).put("model",android.os.Build.MODEL)
            // Keep density and its pixel reference from one Android display snapshot. The
            // Surface buffer can be 720p while Android still uses the full display density.
            .put("densityDpi",densityDpi)
            .put("densityWidthPixels",densityWidth).put("densityHeightPixels",densityHeight)
            .put("physicalWidth",physical.getPhysicalWidth()).put("physicalHeight",physical.getPhysicalHeight())
            .put("physicalWidthMM",Math.round(physical.getPhysicalWidth()*25.4f/metrics.xdpi))
            .put("physicalHeightMM",Math.round(physical.getPhysicalHeight()*25.4f/metrics.ydpi))
            .put("renderWidth",bufferWidth).put("renderHeight",bufferHeight).put("renderModes",sizes)
            .put("refreshRates",pacer.supportedRates()).put("refreshPolicy",pacer.policy())
            .put("currentRefresh",d.getRefreshRate());
    }

    org.json.JSONObject setDisplayInfo(org.json.JSONObject request) throws org.json.JSONException {
        // Validate the entire request before changing preferences or buffers.
        org.json.JSONArray sizes=displayInfo().getJSONArray("renderModes");
        int shortEdge=-1;
        if(request.has("width") || request.has("height")) {
            int width=request.getInt("width"),height=request.getInt("height");
            for(int i=0;i<sizes.length();i++) {
                org.json.JSONObject size=sizes.getJSONObject(i);
                if(size.getInt("width")==width && size.getInt("height")==height)shortEdge=Math.min(width,height);
            }
            if(shortEdge<0)throw new IllegalArgumentException("Unsupported rendering resolution");
        }
        if(request.has("refreshPolicy"))pacer.setPolicy(request.getInt("refreshPolicy"));
        if(shortEdge>0) {
            getPreferences(MODE_PRIVATE).edit().putInt("render_short_edge",shortEdge).apply();
            resizeDisplay(0,0,display.getWidth(),display.getHeight());
        }
        publishDisplayInfo();
        return displayInfo().put("ok",true);
    }

    private String writtenDisplayInfo;
    private void publishDisplayInfo() {
        if(pacer==null || display==null || display.getWidth()==0)return;
        final String value;
        try { value=displayInfo().toString(); } catch(Exception e) { return; }
        worker.execute(() -> {
            if(value.equals(writtenDisplayInfo))return;
            File dir=new File(getFilesDir(),"tmp");dir.mkdirs();
            AtomicFile file=new AtomicFile(new File(dir,"android-display.json"));
            FileOutputStream stream=null;
            try {
                stream=file.startWrite();stream.write(value.getBytes(java.nio.charset.StandardCharsets.UTF_8));
                file.finishWrite(stream);stream=null;
                android.system.Os.chmod(file.getBaseFile().getAbsolutePath(),0644);
                writtenDisplayInfo=value;
            } catch(Exception e) { if(stream!=null)file.failWrite(stream);Log.w("RungicDisplay","Cannot publish modes",e); }
        });
    }
    private void updateSize() {
        if (initialized) {
            int w=bufferWidth,h=bufferHeight;
            NativeBridge.setResolution(w,h);
            NativeBridge.onSurfaceChanged(w,h,w>h?151:68,w>h?68:151);
        }
    }
    @Override public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) { worker.execute(this::updateSize); }
    @Override public void surfaceDestroyed(SurfaceHolder holder) {
        surfaceGeneration++; awaitingFrame=false; display.removeCallbacks(framePoll);
        if(frameTicket!=0)NativeBridge.cancelPhoneFrame(frameTicket);
        pacer.stop(); worker.execute(() -> { if(initialized)NativeBridge.suspendRendering(); });
    }

    /** Shown when a control request times out (the Toast of a menu action); set in onCreate. */
    private static volatile String controlTimeout = "System setup timed out";

    private static String control(String action) throws Exception {
        return control(action,null);
    }

    private static String control(String action, String payload) throws Exception {
        String command=action.equals("removal-status")?RemovalState.ROOT_COMMAND:"/data/adb/rungic-plasma/rungic-plasma " + action;
        ProcessBuilder b = new ProcessBuilder("/product/bin/su", "--mount-master", "-c", command);
        b.environment().put("PATH", "/product/bin:/system/bin:/system/xbin:/vendor/bin");
        b.environment().remove("LD_PRELOAD"); b.environment().remove("LD_LIBRARY_PATH");
        Process p = b.redirectErrorStream(true).start();
        try (OutputStream input=p.getOutputStream()) {
            if(payload!=null) input.write(payload.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        Thread reader = new Thread(() -> { try (InputStream in = p.getInputStream()) {
            byte[] data = new byte[4096]; int n; while ((n = in.read(data)) != -1) if (out.size() < 16384) out.write(data,0,n);
        } catch (IOException ignored) {} }); reader.start();
        if (!p.waitFor(action.equals("removal-status")?10:(action.equals("account-prepare")?240:90), TimeUnit.SECONDS)) {
            p.destroy(); throw new IOException(controlTimeout); }
        reader.join(2000);
        if (p.exitValue() != 0) throw new ControlException(action, p.exitValue(), out.toString("UTF-8"));
        return out.toString("UTF-8");
    }

    @Override public void onBackPressed() {
        if(castControls!=null && castControls.dismissOverlay()) return;
        WindowInsets insets=display.getRootWindowInsets();
        if(insets!=null && insets.isVisible(WindowInsets.Type.ime())) {
            getWindow().getInsetsController().hide(WindowInsets.Type.ime());
            androidKeyboard=false;
            return;
        }
        worker.execute(() -> {
            boolean hidden=false;
            try { hidden=control("hide-keyboard").contains("hidden"); }
            catch(Exception e) { Log.w("RungicWayland","Keyboard state unavailable",e); }
            if(!hidden)runOnUiThread(this::showDesktopMenu);
        });
    }

    private void registerEdgeBack() {
        if(android.os.Build.VERSION.SDK_INT<34) return;
        edgeBackCallback=new android.window.OnBackAnimationCallback() {
            private int edge=android.window.BackEvent.EDGE_LEFT;
            @Override public void onBackStarted(android.window.BackEvent event) {edge=event.getSwipeEdge();}
            @Override public void onBackProgressed(android.window.BackEvent event) {}
            @Override public void onBackCancelled() {edge=android.window.BackEvent.EDGE_LEFT;}
            @Override public void onBackInvoked() {
                boolean right=edge==android.window.BackEvent.EDGE_RIGHT;
                edge=android.window.BackEvent.EDGE_LEFT;
                if(accountPromptShowing) return;
                if(castControls!=null && castControls.dismissOverlay()) return;
                if(right) backInLinux(); else showDesktopMenu();
            }
        };
        getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
            android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,edgeBackCallback);
    }

    private void backInLinux() {
        WindowInsets insets=display.getRootWindowInsets();
        if(insets!=null && insets.isVisible(WindowInsets.Type.ime())) {
            getWindow().getInsetsController().hide(WindowInsets.Type.ime());
            androidKeyboard=false;
            return;
        }
        worker.execute(() -> {
            try {
                if(control("hide-keyboard").contains("hidden")) return;
                if(!initialized) return;
                // KDE StandardKey.Back and GTK/browser history both use Alt+Left.
                // The focused application decides whether it can navigate back.
                NativeBridge.sendKeyEvent(KeyEvent.KEYCODE_ALT_LEFT,true);
                try {
                    NativeBridge.sendKeyEvent(KeyEvent.KEYCODE_DPAD_LEFT,true);
                    NativeBridge.sendKeyEvent(KeyEvent.KEYCODE_DPAD_LEFT,false);
                } finally { NativeBridge.sendKeyEvent(KeyEvent.KEYCODE_ALT_LEFT,false); }
            } catch(Exception e) {
                runOnUiThread(() -> Toast.makeText(this,R.string.back_failed,Toast.LENGTH_SHORT).show());
            }
        });
    }

    private void showDesktopMenu() {
        new AlertDialog.Builder(this).setTitle("Rungic").setItems(new String[]{getString(R.string.menu_continue), getString(R.string.menu_home),
                getString(R.string.menu_keyboard), getString(R.string.menu_switch), getString(R.string.menu_permissions),
                getString(R.string.menu_stop), getString(R.string.menu_refresh)}, (d, i) -> {
            if (i == 1 && initialized) worker.execute(() -> {
                try { control("home"); }
                catch (Exception e) { runOnUiThread(() -> Toast.makeText(this, ControlException.userText(e), Toast.LENGTH_LONG).show()); }
            });
            if (i == 2) setAndroidKeyboard(true);
            if (i == 3) moveTaskToBack(true);
            if (i == 4) capture.requestPermissionsFromUser();
            if (i == 5) new AlertDialog.Builder(this).setTitle(R.string.stop_title)
                .setMessage(R.string.stop_message)
                .setNegativeButton(R.string.action_cancel,null).setPositiveButton(R.string.stop_confirm,(dialog,which)->worker.execute(() -> { try { control("stop"); NativeBridge.releaseWaylandConnection(); initialized=false;
                runOnUiThread(() -> { stopService(new Intent(this, DesktopService.class)); finish(); });
            } catch(Exception e) { runOnUiThread(() -> Toast.makeText(this, R.string.stop_failed, Toast.LENGTH_LONG).show()); } })).show();
            if(i==6) {
                org.json.JSONArray rates=pacer.supportedRates();
                String[] labels=new String[rates.length()+1];labels[0]=getString(R.string.auto);
                int checked=0;
                for(int n=0;n<rates.length();n++) {
                    labels[n+1]=rates.optInt(n)+" Hz";
                    if(rates.optInt(n)==pacer.policy())checked=n+1;
                }
                new AlertDialog.Builder(this).setTitle(R.string.refresh_title)
                    .setSingleChoiceItems(labels,checked,(choice,which)->{
                        pacer.setPolicy(which==0?0:rates.optInt(which-1));choice.dismiss();
                    }).setNegativeButton(R.string.action_cancel,null).show();
            }
        }).show();
    }

    private final class DisplayView extends SurfaceView {
        DisplayView() { super(MainActivity.this); setFocusable(true); setFocusableInTouchMode(true); getHolder().setFixedSize(720,1600); }
        @Override public boolean onTouchEvent(android.view.MotionEvent e) {
            if (!initialized) return true;
            pacer.touch();
            int action=e.getActionMasked(), index=e.getActionIndex();
            if (action==MotionEvent.ACTION_MOVE || action==MotionEvent.ACTION_CANCEL) {
                for(int i=0;i<e.getPointerCount();i++) send(e,action,i);
            } else send(e,action,index);
            return true;
        }
        private void send(MotionEvent e, int action, int i) {
            NativeBridge.sendTouchEvent(action,e.getPointerId(i),e.getX(i)*bufferWidth/getWidth(),e.getY(i)*bufferHeight/getHeight());
        }
        @Override public boolean onCheckIsTextEditor() { return androidKeyboard; }
        @Override public InputConnection onCreateInputConnection(EditorInfo info) {
            info.inputType=android.text.InputType.TYPE_CLASS_TEXT; info.imeOptions=EditorInfo.IME_FLAG_NO_EXTRACT_UI;
            return new BaseInputConnection(this,false) {
                @Override public boolean commitText(CharSequence text,int cursor) { if(initialized)NativeBridge.sendTextInput(text.toString()); return true; }
                @Override public boolean deleteSurroundingText(int before,int after) { for(int i=0;i<before;i++)key(KeyEvent.KEYCODE_DEL); return true; }
                @Override public boolean sendKeyEvent(KeyEvent event) { if(initialized)NativeBridge.sendKeyEvent(event.getKeyCode(),event.getAction()==KeyEvent.ACTION_DOWN); return true; }
            };
        }
        private void key(int k) { if(initialized) { NativeBridge.sendKeyEvent(k,true); NativeBridge.sendKeyEvent(k,false); } }
        @Override public boolean onKeyDown(int k,KeyEvent e) { if(k==KeyEvent.KEYCODE_BACK || k==KeyEvent.KEYCODE_VOLUME_UP || k==KeyEvent.KEYCODE_VOLUME_DOWN)return super.onKeyDown(k,e); if(initialized)NativeBridge.sendKeyEvent(k,true); return true; }
        @Override public boolean onKeyUp(int k,KeyEvent e) { if(k==KeyEvent.KEYCODE_BACK || k==KeyEvent.KEYCODE_VOLUME_UP || k==KeyEvent.KEYCODE_VOLUME_DOWN)return super.onKeyUp(k,e); if(initialized)NativeBridge.sendKeyEvent(k,false); return true; }
    }
}
