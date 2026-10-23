# PON Lab hardware guide

How to build a Pi that cuts power to Itron Riva (AMI 2.0) meters, so you can
measure what you're after:

1. **PON / last gasp.** Does the meter send its outage notice, and how fast?
2. **Mesh routing on cap power.** Does it keep routing neighbours' traffic, and for how long?
3. **Supercap holdup.** When does the radio finally go dark?
4. **Restore.** How long until the restore notice comes in and the meter rejoins the mesh?

![Wiring diagram](docs/wiring.png)

## The big idea

**The Pi's relay never carries meter voltage.** It only switches the 24 VAC
coil of a proper **contactor**, and the contactor switches both 240 V legs to
the meter socket. That gives you:

- **A clean, real outage.** Both L1 and L2 open together in about 10–20 ms, the
  way a real outage looks to the meter. That makes your PON timing meaningful.
- **Safety.** The 240 V side uses parts rated for it. The Pi side stays Class 2
  low voltage.
- **Fail-safe behaviour.** The coil is wired through the relay's **NC**
  contact, so normally the contactor is *held closed* and the meter has power.
  If the Pi crashes, reboots, loses power or is unplugged, the relay drops and
  the meter stays powered. The meter only loses power while PON Lab is actively
  cutting it.

## Parts list (per Pi)

| Qty | Part | What to look for | Notes |
|---|---|---|---|
| 1 | Raspberry Pi 4 or 5 (a 3B+ works) | 40-pin header, wired Ethernet | Ethernet gives steadier timing than Wi-Fi |
| 1 | Pi power supply | Official supply for that Pi | |
| 1 | **8-channel relay HAT** | **Waveshare RPi Relay Board (B)**: 8 relays, opto-isolated, 5 A / 250 VAC contacts, active-low | Up to 8 meters per Pi. PON Lab ships `config.waveshare-8ch.json` with its pins (GPIO 5, 6, 13, 16, 19, 20, 21, 26) |
| 1 per meter | **2-pole definite-purpose contactor** | 2-pole, 30–40 A, **24 VAC coil**, normally-open poles (for example Eaton C25DND240T, 40 A 2P 24 VAC) | The standard HVAC condenser contactor, easy to find at supply houses |
| 1 | **24 VAC Class 2 transformer** | 40 VA for up to 4 contactors, 75 VA for up to 8 | Add up your contactors' sealed coil VA and leave some headroom |
| 1 | 2-pole breaker + enclosure | 15–20 A, sized for your test board | Feeds the contactors |
| 1 per meter | Meter socket / test board | Matches the meter form (often 2S for residential Riva) | Bench test board, not a customer service |
| — | 18 AWG thermostat wire, ferrules, DIN rail, terminal blocks | | Keep 24 V and 240 V wiring physically apart |
| optional, 1 per meter | AC voltage detection module (opto-isolated, 220–240 V input, logic output) | Its output goes to a free GPIO as `sense_gpio` | Logs the exact moment the line went dead, which confirms the contactor really opened |

**Cheaper 4-meter option:** a generic 4-channel 5 V opto-isolated relay module
(the blue SRD-05VDC type) on GPIO 17/27/22/23, which is PON Lab's default
`config.json`. Take the **JD-VCC jumper off**: feed **VCC from the Pi's 3.3 V**
and **JD-VCC from 5 V**. That way the Pi's 3.3 V signal switches the module
fully on and off.

**For 16 meters:** use two Pis with 8-channel HATs, or four Pis with 4 channels
each spread across different spots in the mesh. Then control them all together
from **PON Fleet**.

## Wiring, step by step (one meter channel)

### Low-voltage side (24 VAC Class 2)

1. **Transformer R** (hot) → relay **COM**.
2. Relay **NC** → contactor coil **A1**.
3. Contactor coil **A2** → **transformer C** (common).
4. Leave the relay's NO terminal empty.
5. Repeat for each channel. COM on every relay can share the R wire from the
   transformer.

### Line side (240 V, done by a qualified meter tech or electrician)

6. 2-pole breaker **L1 → contactor L1** and **L2 → contactor L2**.
7. Contactor **T1 / T2 → meter socket line-side lugs**.
8. **Neutral goes straight through** to the socket. Never switch it.
9. *(Optional)* AC sense module across T1–T2. Its output goes to the Pi GPIO
   you set as `sense_gpio` for that channel.

### Pi setup

```bash
cd ponlab
sudo ./install.sh waveshare-8ch     # or plain  sudo ./install.sh  for the 4-ch module
```

The installer does three things:
- It sets the clock to sync over NTP.
- It sets the relay pins to **drive HIGH from the first moment of boot**
  (`gpio=…=op,dh` in `config.txt`). Active-low boards would otherwise click on
  during boot and briefly cut the meters.
- It starts PON Lab on every boot.

Then edit `config.json`:
- `node_name` is the site name, for example "Elm St pole 14".
- `meter` for each channel is the meter number or ID.
- `api_token` is a password your fleet server will send.

Restart with `sudo systemctl restart ponlab`.

### Test before you trust it

1. With the breaker **off**, click **Cut power** on a channel. You should hear
   the relay click and the contactor drop. Click **Restore** and the contactor
   pulls back in.
2. Unplug the Pi's power. The contactor should **pull in**, meaning the meter is
   powered. That's the fail-safe working.
3. Breaker on, one meter installed: do a 5 s cut and check that the meter
   display goes out and comes back.

## How to get the results you want

| You want to know | How to run it | What to watch |
|---|---|---|
| Does it send a PON, and how fast? | **Sustained 5 min** | When the head-end/collector shows the outage, press **PON received** on that meter. The time is stamped in seconds after the cut |
| Does it avoid a PON on a blip? | **Momentary blip (1 s)** and **Flicker ×10** | No PON should show up (depends on the meter's outage-delay setting) |
| How long does it keep routing? | Pick a meter that **neighbours route through**, and cut it for 30 min | When neighbours re-route or the meter disappears from the mesh, press **Dropped from mesh** |
| Supercap holdup | **Cap holdup ladder** (5 s → 5 min) | The longest outage where it still sent a PON or stayed meshed. Press **Radio dark** at the last sign of RF from it |
| Restore behaviour | Any outage | Press **Restore notice** and **Rejoined mesh** after power comes back |
| Mass outage (many meters at once) | PON Fleet: select up to 16 meters → **Cut selected** | Whether PONs from many meters all make it through the collector/mesh at the same moment |

**Tips**
- A **spectrum analyzer or RTL-SDR** tuned to the meter's RF band is the most
  reliable way to see when the radio really goes quiet. Use it alongside the
  **Radio dark** button.
- Let meters sit powered long enough to **fully charge their caps** between
  long tests. The ladder preset has recovery time built in.
- Write down meter firmware and outage-detection settings in the note box.
  PON delay settings change results a lot.

## Safety

- Meter sockets carry service voltage with high available fault current.
  The line side must be built and tested by a **qualified meter technician or
  electrician**, on a **bench test board** with proper fusing and an enclosure.
- **Never** put meter voltage through the Pi relay board's contacts.
- Only run tests on meters and services your utility has authorised for
  testing.
