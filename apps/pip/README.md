# Pip

A cheerful offline voice assistant for the **Raspberry Pi 4, Pi 400, Pi 5 and Pi 500**. Talk through a headset; Pip listens, thinks and answers out loud, all on the Pi.

![Pip](screenshot.png)

- **Ears:** Whisper (`base.en`, via faster-whisper)
- **Brain:** Ollama — `llama3.2:3b` by default, `llama3.2:1b` for quicker replies
- **Voice:** Piper (Amy, Joe, Alba)
- **Push to talk / Hands-free** toggle, talk-over-to-interrupt, headset picker, editable personality

## Works on
One copy of Pip runs on all of these. It recognizes the board when it starts and picks the right setup by itself.

| Board | RAM | Setup Pip uses | Brain | Ears | First download |
|---|---|---|---|---|---|
| **Raspberry Pi 500** | 8 GB | Standard | `llama3.2:3b` (+ `llama3.2:1b`) | Whisper `base.en` | ~3.5 GB |
| **Raspberry Pi 5** | 4, 8 or 16 GB | Standard | `llama3.2:3b` (+ `llama3.2:1b`) | Whisper `base.en` | ~3.5 GB |
| **Raspberry Pi 400** | 4 GB | Slim | `llama3.2:1b` (+ `qwen2.5:0.5b`) | Whisper `tiny.en` | ~1.6 GB |
| **Raspberry Pi 4** | 4 GB | Slim | `llama3.2:1b` (+ `qwen2.5:0.5b`) | Whisper `tiny.en` | ~1.6 GB |
| **Raspberry Pi 4** | 8 GB | Standard | `llama3.2:3b` (+ `llama3.2:1b`) | Whisper `base.en` | ~3.5 GB |

- Needs **64-bit Raspberry Pi OS** and a **USB headset** (or USB mic + speakers). Boards with 2 GB or less aren't supported.
- The **slim setup** (`profiles/pi4.json`) also uses the Amy *low* voice and a smaller memory window, and the installer raises swap to 2 GB if it's lower.
- **Speed:** a Pi 5 or Pi 500 starts answering in a few seconds; a Pi 4 or Pi 400 is slower, so pick the smallest brain in the model picker for the snappiest replies.
- See what Pip picked with `python3 appconfig.py`. To force a setup, add `"profile": "pi4"` (slim) or `"profile": "none"` (standard) to `config.json`.

## Web address
The address is shown at the top of the window with Copy buttons. Flip **Share on Wi-Fi** to get an address
other devices on your home network can open (the first visit shows a certificate warning; choose Advanced → Continue).

## Install (64-bit Raspberry Pi OS)
```bash
git clone https://github.com/hardwaremack-prog/pip.git ~/Pip
cd ~/Pip
./install.sh
```
Needs internet once (about 3.5 GB on a Pi 5 / Pi 500, about 1.6 GB on a 4 GB Pi 4 / Pi 400). Then use the **Pip** icon on the desktop or in the menu.

## Updating
Pip tells you when a new version is out. Update with any of:
- **Gear menu → Updates → Update now**
- the **Update Pip** icon on the desktop
- `~/Pip/update.sh`

Models, voices, settings, chats and your `config.json` changes are kept. Pip restarts itself.

**First time on an older Pip** (before 1.2.0), run once:
```bash
cd ~/Pip && curl -fsSL https://raw.githubusercontent.com/hardwaremack-prog/pip/main/update.sh | bash
```

## Use
- Hold **Space** (or the button) to talk, or switch to **Hands-free** and just talk.
- **Esc** stops Pip mid-sentence. The gear menu sets mic, voice, speed and sensitivity.

See `README.txt` for troubleshooting. Log: `~/Pip/pip.log`.
