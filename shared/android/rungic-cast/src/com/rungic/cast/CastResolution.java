// SPDX-License-Identifier: MIT
package com.rungic.cast;

import android.util.AtomicFile;
import android.view.Display;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.concurrent.TimeUnit;
import org.json.*;

/** Exact video mode policy shared by all frontends. No phone or receiver model lists.
 * The installed backend is capability-probed. Online change is preferred; a new
 * session is the fallback. Requested modes are never presented as actual modes.
 */
final class CastResolution {
    private static final File DIR = new File("/data/adb/rungic-wfd");
    private static final File VENDOR = new File("/vendor/etc/wfdconfig.xml");
    private static final File CONFIG = new File(DIR, "wfdconfig.xml");
    private static final File BASE = new File(DIR, "wfdconfig-default.xml");
    private static final File PREFS = new File(DIR, "resolution-preferences.json");
    private static final File APPLIED = new File(DIR, "run/resolution-applied");
    static String preference(String address) throws Exception {
        if (!PREFS.exists() || address == null) return "auto";
        return new JSONObject(new String(new AtomicFile(PREFS).readFully(), StandardCharsets.UTF_8))
                .optString(address.toLowerCase(java.util.Locale.ROOT), "auto");
    }
    static void save(String address, String value) throws Exception {
        JSONObject all = PREFS.exists() ? new JSONObject(new String(new AtomicFile(PREFS).readFully(), StandardCharsets.UTF_8)) : new JSONObject();
        all.put(address.toLowerCase(java.util.Locale.ROOT), value);
        AtomicFile file = new AtomicFile(PREFS);
        FileOutputStream stream = file.startWrite();
        try { stream.write(all.toString().getBytes(StandardCharsets.UTF_8)); file.finishWrite(stream); }
        catch (Exception e) { file.failWrite(stream); throw e; }
    }
    static WfdFormats.Mode mode(String value) {
        if ("auto".equals(value)) return null;
        WfdFormats.Mode mode = WfdFormats.find(value);
        if (mode == null || mode.interlaced) throw new IllegalArgumentException("Unknown progressive video mode");
        return mode;
    }
    private static JSONArray candidates(String address) throws Exception {
        JSONArray choices=new JSONArray();
        choices.put(new JSONObject().put("id","auto").put("label","自动"));
        JSONObject peer=WfdCapture.read(address);
        if(peer==null)return choices;
        java.util.List<WfdFormats.Codec> codecs=WfdFormats.parse(peer.getString("offered"),peer.getBoolean("r2"));
        WfdSession backend;
        try { backend=new WfdSession(); } catch(Exception unavailable) { return choices; }
        for(WfdFormats.Mode m:WfdFormats.TABLE) if(backend.canSet(m) && backend.compatible(m,codecs))
            choices.put(new JSONObject().put("id",m.id()).put("label",m.label()).put("width",m.width).put("height",m.height).put("fps",m.fps));
        return choices;
    }
    static boolean supported(String address,String value) throws Exception {
        if (!BASE.exists()) return false;
        JSONArray choices=candidates(address);
        for(int i=0;i<choices.length();i++)if(value.equals(choices.getJSONObject(i).getString("id")))return true;
        return false;
    }
    static JSONObject modes(Object displayInfo, String address) throws Exception {
        JSONObject out = new JSONObject().put("adjustable", BASE.exists() && VENDOR.exists())
                .put("selection_kind", "video-mode").put("receiver_modes_known", WfdCapture.read(address)!=null)
                .put("address", address == null ? JSONObject.NULL : address)
                .put("requested", preference(address)).put("switch_policy", "online-rate-reconnect-size")
                .put("options",new JSONArray());
        JSONObject peer = WfdCapture.read(address);
        if (peer != null) out.put("capability_source",peer.getString("source")).put("capabilities_observed_at",peer.getLong("observed_at"));
        if (displayInfo != null) {
            Display.Mode[] modes = (Display.Mode[]) displayInfo.getClass().getField("supportedModes").get(displayInfo);
            int id = displayInfo.getClass().getField("modeId").getInt(displayInfo);
            for (Display.Mode mode : modes) if (mode.getModeId() == id) {
                out.put("width", mode.getPhysicalWidth()).put("height", mode.getPhysicalHeight())
                        .put("display_refresh_rate", mode.getRefreshRate())
                        .put("actual", mode.getPhysicalWidth()+"×"+mode.getPhysicalHeight());
            }
        }
        if (displayInfo != null) try {
            int[] video = negotiatedVideo();
            if (video != null) out.put("video_width",video[0]).put("video_height",video[1]).put("video_fps",video[2])
                    .put("actual",video[0]+"×"+video[1]+" · "+video[2]+" fps");
        } catch (Exception ignored) { /* Optional vendor ABI; never invent a video rate from the display rate. */ }
        JSONArray available=out.getJSONArray("options");
        int unchecked=0,rejected=0;
        ModeHistory history=ModeHistory.open(address,peer);
        JSONObject records=history==null?new JSONObject():history.results();
        String current=out.optInt("video_width")+"x"+out.optInt("video_height")+"@"+out.optInt("video_fps");
        JSONArray candidates=BASE.exists() && VENDOR.exists()?candidates(address):new JSONArray();
        for(int i=0;i<candidates.length();i++) {
            JSONObject option=candidates.getJSONObject(i);String id=option.getString("id");
            JSONObject record=records.optJSONObject(id);
            String state=record==null?"unverified":record.optString("state","unverified");
            boolean isCurrent=id.equals(current) && out.optInt("width")==out.optInt("video_width") && out.optInt("height")==out.optInt("video_height");
            if(id.equals("auto"))state="automatic";
            else if(isCurrent && !state.equals("passed"))state="current";
            option.put("validation",state);
            if(record!=null)option.put("last_result",record);
            if(state.equals("passed")||state.equals("current")||state.equals("automatic"))available.put(option);
            else if(state.equals("unverified"))unchecked++;
            else rejected++;
        }
        out.put("validation_scope","negotiated-mode-and-display-size")
                .put("excluded_unverified_count",unchecked).put("excluded_failed_count",rejected);
        return out;
    }
    /** Check every sample after the switch; merely accepting a request is insufficient. */
    static WfdFormats.Mode verify(String requested)throws Exception {
        WfdSession backend=new WfdSession();WfdFormats.Mode expected=backend.current();
        if(expected==null || (!requested.equals("auto")&&!expected.id().equals(requested)))return null;
        long until=android.os.SystemClock.elapsedRealtime()+5000;
        while(android.os.SystemClock.elapsedRealtime()<until) {
            WfdFormats.Mode actual=backend.current();
            if(actual==null || !actual.id().equals(expected.id()) || !Main.displayMatches(expected))return null;
            Thread.sleep(500);
        }
        return expected;
    }
    private static int[] negotiatedVideo() throws Exception {
        WfdFormats.Mode current=new WfdSession().current();
        return current==null?null:new int[]{current.width,current.height,current.fps};
    }
    static boolean applyOnline(String address,String selection)throws Exception {
        if(selection.equals("auto"))return false;
        WfdFormats.Mode requested=mode(selection);
        // This session ABI changes the encoder but does not resize the Android display.
        // A fresh display is needed for geometry changes; same-size rate changes can stay online.
        if(!Main.displayMatches(requested))return false;
        JSONObject peer=WfdCapture.read(address);
        if(peer==null)throw new IOException("Receiver capability data unavailable; reconnect to refresh it");
        WfdSession backend=new WfdSession();
        if(!backend.canSet(requested))return false;
        backend.set(requested,WfdFormats.parse(peer.getString("offered"),peer.getBoolean("r2")));
        long deadline=android.os.SystemClock.elapsedRealtime()+12000,stable=0;
        while(android.os.SystemClock.elapsedRealtime()<deadline) {
            WfdFormats.Mode actual=backend.current();
            if(Main.connectedDisplayId()<0)return false;
            if(actual!=null && actual.id().equals(selection) && Main.displayMatches(requested)) {
                if(stable==0)stable=android.os.SystemClock.elapsedRealtime();
                if(android.os.SystemClock.elapsedRealtime()-stable>=2000)return true;
            }else stable=0;
            Thread.sleep(200);
        }
        return false;
    }
    /** Rebuild from the stock file, never from a previous (possibly 720p) override. */
    static void configure(String value) throws Exception {
        WfdFormats.Mode requested = mode(value);
        if (!VENDOR.exists()) {
            if (requested != null) throw new IOException("Resolution negotiation control is unavailable");
            return;
        }
        DIR.mkdirs(); new File(DIR,"run").mkdirs();
        try (RandomAccessFile lock = new RandomAccessFile(new File(DIR, "config.lock"), "rw");
                java.nio.channels.FileLock held = lock.getChannel().lock()) {
            boolean mounted = new String(Files.readAllBytes(new File("/proc/1/mountinfo").toPath()), StandardCharsets.UTF_8)
                    .contains(" " + VENDOR + " ");
            if (mounted && (!CONFIG.exists() || !ns("stat","-c","%d:%i",CONFIG.toString()).equals(ns("stat","-c","%d:%i",VENDOR.toString()))))
                throw new IOException("WFD configuration belongs to another component");
            byte[] previous = CONFIG.exists() ? Files.readAllBytes(CONFIG.toPath()) : null;
            if (mounted) ns("umount", VENDOR.toString());
            boolean rebound=false;
            try {
                File stock = new File(DIR, "wfdconfig-stock.xml");
                ns("cp", VENDOR.toString(), stock.toString());
                WfdConfig.generate(stock, BASE);

                JSONObject report = new JSONObject(WfdConfig.generate(stock, CONFIG, requested));

                if (report.getBoolean("changed")) {
                    Main.command("chcon", "u:object_r:vendor_configs_file:s0", CONFIG.toString());
                    ns("mount", "--bind", CONFIG.toString(), VENDOR.toString());
                    rebound=true;
                }
                Files.write(APPLIED.toPath(), value.getBytes(StandardCharsets.UTF_8));
            } catch (Exception error) {
                if (rebound) ns("umount",VENDOR.toString());
                if (mounted && previous != null) {
                    Files.write(CONFIG.toPath(), previous);
                    Main.command("chcon", "u:object_r:vendor_configs_file:s0", CONFIG.toString());
                    ns("mount", "--bind", CONFIG.toString(), VENDOR.toString());
                }
                throw error;
            }
        }
    }
    static void restoreIdle() throws Exception {
        if (APPLIED.exists() && !new String(Files.readAllBytes(APPLIED.toPath()), StandardCharsets.UTF_8).equals("auto")) configure("auto");
    }
    private static String ns(String... args) throws Exception {
        String[] command = new String[args.length+6];
        String[] prefix = {RootProvider.busybox(),"nsenter","-t","1","-m","--"};
        System.arraycopy(prefix,0,command,0,6); System.arraycopy(args,0,command,6,args.length);
        // All commands produce small output (stat only); the files are copied by nsenter.
        Process p = new ProcessBuilder(command).redirectErrorStream(true).start();
        if (!p.waitFor(5,TimeUnit.SECONDS)) { p.destroyForcibly(); throw new IOException("WFD configuration command timed out"); }
        String output = new String(p.getInputStream().readAllBytes(),StandardCharsets.UTF_8).trim();
        if (p.exitValue()!=0) throw new IOException(output);
        return output;
    }
}
