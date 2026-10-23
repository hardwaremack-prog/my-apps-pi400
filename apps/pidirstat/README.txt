PiDirStat - see what's filling up your disk
===========================================

A WinDirStat-style disk usage viewer. One Python file, nothing to install
(just Python 3). Works on Raspberry Pi, Linux, Mac and Windows.

RASPBERRY PI / LINUX
  Double-click start-pidirstat.sh (choose "Execute in Terminal"), or type:
      sudo python3 pidirstat.py /
  Then open http://<pi-name>.local:8088 from your laptop or phone,
  or http://localhost:8088 on the Pi itself.
  Without sudo it still works, but skips folders only root can read.

WINDOWS
  Install Python from python.org if you don't have it.
  Double-click "Start PiDirStat (Windows).bat". It scans C: and your
  browser opens by itself. Right-click > Run as administrator to include
  protected folders.

IN THE VIEWER
  - Folder tree sorted by size, with a bar for each folder's share.
  - File-type list. Click one to highlight just those files in the map.
  - The shaded block map: click a block to select it, double-click to zoom
    into its folder, right-click to zoom back out.
  - Type a different folder in the box at the top and press Scan.

OPTIONS
  --cross-fs      also scan USB drives mounted inside the folder
  --apparent      count file sizes instead of space used on disk
  --html FILE     save a standalone report and exit
  --host 0.0.0.0  share the viewer on your network (Windows default is private)
  --port N        use a different port (default 8088)
