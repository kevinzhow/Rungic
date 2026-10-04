// SPDX-License-Identifier: MIT
package com.rungic.plasma;

/** Only these operations belong to the independently supervised hardware backend. */
final class DeviceOperations {
    private DeviceOperations() {}
    static boolean handles(String op) {
        switch(op) {
            case "network-get": case "wifi": case "network-wifi": case "bluetooth":
            case "telephony": case "sms": case "container-memory": case "screen-timeout":
            case "desktop-boost": case "device-status": return true;
            default: return false;
        }
    }
    static boolean topic(String topic) {
        return topic.equals("network") || topic.equals("bluetooth") || topic.equals("telephony");
    }
}
