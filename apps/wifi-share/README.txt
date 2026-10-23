WiFi Share - share the Pi's Wi-Fi through its Ethernet port
============================================================

Turns on internet sharing from the Pi's Wi-Fi to its Ethernet port with
one button, and lists the devices plugged in: name, IP address, MAC
address, online/offline and last seen. Refreshes every 15 seconds.

NEEDS
  Raspberry Pi OS Bookworm or newer (it uses NetworkManager).
  On an older Pi OS, turn NetworkManager on with:
      sudo raspi-config  ->  Advanced Options  ->  Network Config  ->  NetworkManager
  then reboot.

START IT
  Double-click the WiFi Share icon on the Desktop (added by
  Set Up My Apps.sh), or find it in the Pi menu under Internet.
  Or double-click start-wifi-share.sh here, or type:
      python3 wifi_share.py
  No sudo needed.

USING IT
  - Click Start sharing, then plug the other device into the Pi's
    Ethernet port. It gets an address like 10.42.0.x.
  - Double-click a device in the list to copy its IP address.
  - "Start sharing automatically at boot" keeps sharing on after a restart.
  - Stop sharing gives the Ethernet port back to its normal setup.

CAREFUL
  Don't plug the Pi's Ethernet into your home router while sharing is
  on. The Pi would hand out addresses that clash with the router's.
  Click Stop sharing first.

WITHOUT THE DESKTOP (over SSH)
  nmcli connection up WiFi-Share      start sharing
  nmcli connection down WiFi-Share    stop sharing
  (works once WiFi Share has been used at least once)
