# Halo Studio

An easy, Photoshop-style image studio that runs on your own PC and is shared with everyone on your home network.
It is built to use the AMD Ryzen AI Max+ 395: the Radeon 8060S GPU and its large shared memory run the AI models,
and all 32 CPU threads handle batch work.

## Start it (Windows)

1. Double-click **Start-Halo-Studio.bat**. The first run sets everything up, which takes about a minute.
2. Your browser opens to `http://localhost:8395`.
3. To let other devices use it, double-click **Allow-On-Network.bat** once and accept the admin prompt.
   Then open the **Network** tab: it shows the address and a QR code for phones, tablets and Raspberry Pis.

**Raspberry Pi / Linux / Mac:** run `./start.sh`. Add `--ai` to also install background removal.

## What's inside

| Tab | What it does |
|---|---|
| **Edit** | Layers, blend modes, brush, eraser, fill bucket, text, selections, crop, resize, rotate, adjustments, presets, undo/redo. Drag and drop, paste with Ctrl+V. iPhone HEIC and JPEG XL open too. |
| **AI** (in Edit) | Remove background, Auto-fix, Upscale 2x/4x, **Generative fill** (select an area and describe it), **Erase object**, and text-to-image. AI results land on their own layer, so you can undo them easily. |
| **Batch** | Drop hundreds of photos or a whole folder and get every size you need in one zip. Options: crop or border, watermark, rename pattern, AVIF/JPEG XL/WebP output, and a contact sheet. |
| **Animate** | GIF, animated WebP, animated AVIF or APNG from images, a video clip (pick the start and end), or your editor layers. Includes boomerang mode, meme captions and a live preview. |
| **Compress** | Encodes one image as AVIF, JPEG XL, WebP, JPEG and PNG-8 side by side, with a before/after slider. **Hit a file size** mode finds the best quality under a target KB. |
| **3D Print** | Lithophanes (flat, curved or lamp), relief signs, **multi-color filament-swap prints** with a layer-by-layer swap list, keychains with a keyring hole, and cookie cutters. Exports watertight STL and 3MF with a live 3D preview. |
| **Network** | A shared gallery that everyone on the Wi-Fi sees, plus the address and QR code for joining from another device. |

Keyboard shortcuts: B brush, E eraser, M select, V move, G fill, T text, I picker, H pan (or hold Space),
[ and ] to change brush size, Ctrl+Z/Y to undo/redo, Ctrl+J for copy to layer, Ctrl+S to export, 0 to fit the image on screen.

## Turning on the full AI (generative fill and AI images)

Halo Studio drives **ComfyUI**, which runs the models on your Radeon GPU using AMD ROCm.

1. Install ComfyUI Desktop from comfy.org. Its Windows installer supports AMD ROCm on Ryzen AI Max.
2. Put a model in `ComfyUI/models/checkpoints`. For a fast start, use an SDXL Lightning or Turbo model.
   For best quality, use any good SDXL model. Your 96 GB lets you keep several models loaded at once.
3. Optional: put an upscaler such as 4x-UltraSharp in `ComfyUI/models/upscale_models` for true AI upscaling.
4. Start ComfyUI, then Halo Studio. The status pill turns green and shows **AI ready**.

Tip: in AMD Software (Adrenalin), give the GPU a large share of memory under Variable Graphics Memory.
Big models run much better with plenty of GPU memory.

For **background removal**, run **Install-AI-Extras.bat** once. It installs rembg with DirectML so it runs on the Radeon GPU.

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `HALO_PORT` | 8395 | Web port |
| `COMFY_URL` | http://127.0.0.1:8188 | Where ComfyUI is running |
| `HALO_NO_BROWSER` | (unset) | Set to 1 to stop the browser opening automatically |

Shared files are saved in the `shared` folder next to `server.py`.
There's no password: anyone on your network can use it, so keep it on your home network only.
