<p align="center"><img src="icon.png" width="120"></p>

# PiMesh

A zero-setup mesh for your Raspberry Pis. Once it's installed on each Pi, the Pis
find each other automatically and share live stats, and **every** Pi serves the same
dashboard at `http://<pi-ip>:8470`.

![PiMesh dashboard](screenshots/dashboard-full.png)

| Mesh topology | Member history | Phone view |
|---|---|---|
| ![Topology](screenshots/topology.png) | ![History](screenshots/member-history.png) | ![Mobile](screenshots/mobile.png) |

## Install (do this on each Pi)

    unzip PiMesh.zip && cd PiMesh
    sudo ./install.sh

The installer asks for a **mesh name** and a **mesh key**. Use the same values on
every Pi. The key signs every packet, so only your Pis can join. After that, PiMesh
starts on boot and puts a "PiMesh Dashboard" icon on the Desktop.

## Try it first (any computer, no install)

    python3 pimesh.py --demo

Then open http://localhost:8470. It adds 6 simulated Pis, one of which keeps dropping off Wi-Fi.
On Windows, double-click **Try PiMesh (demo).bat**. On a Pi, run `./try-demo.sh`.

## What you get

- **Members**: name, model, IP, status (online / stale / offline), CPU, temperature,
  memory, disk, network speed, uptime, last seen
- **Mesh stats**: total cores, combined RAM and storage, hottest Pi, total throughput,
  and average link latency
- **Topology map**: each Pi measures latency to every other Pi, and lines are colored by speed
- **Coordinator**: one Pi is elected automatically (♛). If it goes down, another takes over
- **Activity log**: joins, drop-offs, rejoins, restarts
- **History**: click any Pi to see about 6 minutes of CPU, temperature and memory charts
- Each IP in the table links to that Pi's own dashboard

## How it works

Each Pi sends a signed heartbeat every 2 seconds by UDP broadcast on port 47474.
Pis also gossip the addresses they know about, so the mesh fills itself in. Pis that
broadcast can't reach (another subnet, a VPN, Tailscale) go in `"peers"` in
`/etc/pimesh.json`. Uses only built-in Python 3. Nothing needs to be downloaded.

## Files

- `pimesh.py`: the whole app (mesh, stats and web server)
- `dashboard.html`: the dashboard every Pi serves
- `install.sh` / `uninstall.sh`: set up or remove the background service on a Pi
- `start.sh`: run it in the foreground without installing
- `Try PiMesh (demo).bat` / `try-demo.sh`: demo with simulated Pis
- `Open PiMesh.html`: My Apps launcher page. It finds a running PiMesh or asks for a Pi's IP

## Handy commands

    journalctl -u pimesh -f          # live log
    sudo systemctl restart pimesh    # after editing /etc/pimesh.json
    sudo ./uninstall.sh              # remove

Config (`/etc/pimesh.json`): `mesh_name`, `secret`, `node_name` (blank = hostname),
`peers`, `mesh_port` (47474/udp), `web_port` (8470/tcp).
