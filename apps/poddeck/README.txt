PODDECK - a control panel for Podman containers on the Raspberry Pi
=====================================================================

WHAT IT DOES
  See your Pi's CPU, memory, temperature and storage, and start, stop,
  restart or remove containers with a click. Watch their logs live, pull
  new images, and launch new ones from a simple form or quick-starts
  (web server, Uptime Kuma, File Browser, IT Tools).

FIRST TIME
  Double-click the PodDeck icon on the Desktop, or in a Terminal:
      bash ~/Desktop/"My Apps"/apps/poddeck/install.sh
  It installs Podman, asks you to pick a password, and sets PodDeck to
  start every time the Pi boots.

OPEN IT
  On the Pi:            http://localhost:8070
  From a phone/laptop:  http://<the Pi's IP address>:8070

GOOD TO KNOW
  - Needs 64-bit Raspberry Pi OS (Bookworm or newer). Works on Pi 4, 400, 5, 500.
  - Images have to be made for ARM (arm64). The quick-starts are.
  - Use ports 1024 and up (8080 instead of 80).
  - Short names like "nginx" work (looked up on Docker Hub).
  - After a reboot, "Always" and "Unless I stop it" containers come back.
  - Change the password: run  bash install.sh  again.
  - Remove it with:  bash uninstall.sh   (your containers stay)
