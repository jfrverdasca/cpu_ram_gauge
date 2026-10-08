# Project: needle gauge (car instrument cluster) for CPU/RAM

## Goal
Reuse a needle motor from a car instrument cluster to show the PC's CPU or
RAM usage, driven by an Arduino over serial.

## Identified hardware
- **Motor:** VDO/Siemens **91 255 008** (long-shaft version; the short-shaft
  one is the 91 255 005; compatible with the 91 255 112). Body marking:
  `91 255 008 04 00`. Salvaged from a board marked `B 3682 / KW-16`.
- **Type:** bipolar stepper motor with a reduction gearbox. Confirmed by
  hand: it has detents and end stops, doesn't turn 360°, and **doesn't
  return to a rest position** when let go (rules out an air-core).
- **Pins:** 4, with the pairs **crossed**.
- **Measured coils:** 135 Ω and 142 Ω (200 Ω range).
- **No public datasheet**: it's a repair part, not a catalogue component.
  Every parameter has to be found empirically.
- **Arduino:** Uno on the bench; a classic Nano (ATmega328P) works the same
  (clones may need "ATmega328P (Old Bootloader)"; over USB the 5V rail sits
  at ~4.6–4.7 V, slightly less torque).
- **16×2 LCD** (HD44780), wired in 4-bit mode.

## Wiring decisions
- 5 V / 135 Ω ≈ 37 mA per coil. ATmega absolute pin limit = 40 mA.
- **Direct drive from the Arduino: bench tests only.** Spread the 4 pins
  over different ports (e.g. D2, D3, D8, D9) because of the 100 mA per-port
  limit. Don't leave it energized while idle for long periods.
- **Final version: motor driver.** The L293D works, but it's bipolar and
  drops ~1–2 V at the outputs: at 5 V the coils would get much less torque,
  and going down is already the tightest move. **Prefer a TB6612FNG
  module** (MOSFET, negligible drop, same 4 control pins). As a last resort,
  4 × 100 Ω in series (≈21 mA, loses almost half the torque).
- External diodes are unnecessary at these currents.
- **Changing the motor wiring requires recalibrating `HOME_PHASE`.**

## Software (history)
- The plan was the **SwitecX25** library; it was replaced by our own
  half-step motor control (see bench results).
- **Homing** against the end stop in `setup()`. Stalling against the stop is
  electrically safe (current = V/R, it doesn't rise with the rotor stalled),
  but it's mechanical wear: limit it to a few tenths of a second.
- **Never turn the shaft by hand** (it damages the internal gearbox,
  according to the part's sellers). Not even to push the needle to zero.
- **Python with `psutil`**, sending both values over serial every cycle
  (e.g. `C45 R72\n`); the Arduino picks which one to show, so the button
  responds immediately.
- `psutil.cpu_percent()` is already normalized to 0–100 regardless of the
  number of cores. The 0–800% scale from `top` only shows up in
  **per-process** measurements.
- **Exponential filter for CPU:** `s = s*0.96 + new*0.04`, readings every
  50 ms (time constant ≈ 1.2 s, same as the old 0.15 at 200 ms; moved to
  50 ms so the needle moves in smaller steps). Measured cost: ~7 µs of CPU
  per reading, ≈ 0.015% of one core. **RAM needs no filter.**
- **Dead zone** of 2 steps before commanding a move, to remove jitter.
- Tried and reverted (2026-10-02): keeping the coils energized and spreading
  small steps over the interval between readings. The version with readings
  every 50 ms stayed.

## Decided design
- **A single needle**, with a **button** toggling between CPU and RAM.
  Button on `INPUT_PULLUP` to ground, 50 ms software debounce.
- **0→100→0 sweep** as a self-test on a long press.
- **The LCD replaces the mode lights:** it shows the metric the needle isn't
  showing; when toggling, it shows for 3 s which one is on the needle.
- **Warning lights** (the cluster's pink bulb holders) become real warnings,
  normally off: sustained CPU > 90%, end of a Pomodoro phase, etc. 5 V LEDs;
  the original bulbs (~12 V / 1.2 W) would need a 12 V supply. Beware of
  "automotive" T5 LEDs: many are for 12 V.
- **Pomodoro (future):** on the Arduino, so it works without the PC. Second
  button (short = start/pause, long = exit); work 100%→0%, break rises back
  to 100%; LCD with the exact time and count; alert via a **passive piezo**.
- **Margins on the scale:** 0% = 12 half-steps above the end stop
  (`POS_MIN = 12`), 100% = 12 below the other (`POS_MAX = 278`). They can go
  down to 4–6 if the scale needs it; less than that and the needle hits.

## Bench results (2026-10-02, direct drive D2/D3/D8/D9, sequence s0)
- The motor responds to SwitecX25's X25 sequence (6 states).
- Full travel ≈ **220 steps** with s0 (6 states) and **290** with s2
  (half step, 8 states). It adds up: 220 × 8/6 ≈ 293, i.e. ≈ 36.5
  electrical cycles from stop to stop. **290 confirmed again on
  2026-10-03.**
- Maximum speed without a ramp: **6 ms/step**. Below that it goes up fine,
  but going down from the top it gets stuck at the start.
- Confirmed it isn't desynchronization at the end stop: at 4 ms, between 0
  and 200 (without touching the stops), it goes up fine and stalls going
  down. The cause is the lack of a ramp, and the counter-clockwise direction
  (down) is the most demanding.
- **With s2 (half step) it works at 1 ms/step in both directions**, versus
  6 ms with s0. Full sweep in ≈ 0.3 s. This motor must be a regular 2-phase
  bipolar, and the X25 sequence (uneven 45° and 90° steps) doesn't suit it
  well.
- Away from the stops (from 150 to 50 and back), at 1 ms it stalls going
  down. **At 2 ms it ran 10 cycles without losing steps.** The safe minimum
  with a fixed interval is 2 ms.
- **Decision:** use s2 with 290 steps and our own motor control, instead of
  SwitecX25. The ramp starts at about 5 ms and reaches at most 2 ms.
  Full sweep in ≈ 0.6 s. +300 against the end stop makes the needle jump:
  only during homing, slowly.
- **Before each move, the rotor has to settle:** energize the current phase
  and wait 20 ms (`SETTLE_MS`). Without this, the self-test and the homing
  couldn't go down from the top. With the wait, going down got to 3 ms per
  step and homing to 5 ms. Self-test, 100% load and parking on timeout
  tested and working.
- **`HOME_PHASE = 6`** (2026-10-03, direct drive, 3 identical measurements;
  `MAGIC` changed to `0xA9`). With this, homing always ends in the same
  place, even after pulling the plug.

## Code
- `firmware/step_counter/`: bench sketch (commands over serial, 19200 baud).
- `firmware/gauge/`: final sketch.
- `pc/gauge.py`: PC script (`pip install -r pc/requirements.txt`).
  Runs on macOS and Linux; also starts on Windows (without screen detection).
- Compiling without the IDE: `arduino-cli` ships inside Arduino IDE.app;
  pass `--libraries ~/Library/Arduino15/libraries` so it finds
  LiquidCrystal.

### Serial protocol (PC → Arduino)
| Line | Meaning |
|---|---|
| `C45.3 R72.1` | CPU and RAM readings (every 50 ms) |
| `T<row><needle><text>` | LCD text: row `0`/`1`, for when the needle shows CPU (`C`) or RAM (`R`) (every 0.25 s) |
| `G<0-7><16 hex>` | 5×8 custom character, top row first; in text use `chr(n)`, except slot 0, which is `chr(8)` (byte 0 would end the line). Don't use `8 + n` for the others: 10 and 13 are `\n` and `\r` (every 5 s) |
| `B<0-255>` | lighting brightness (PWM on D10); `0` turns the LCD off (every 5 s) |
| `P` | park; the Arduino replies `PARKED` |

Everything that isn't a reading goes into a queue and is sent **one line per
reading** (≤ ~32 bytes every 50 ms). The Arduino's receive buffer is only 64
bytes and the firmware stops reading for up to 20 ms (`delay(SETTLE_MS)` when
starting the motor) or ~7–10 ms (LCD redraw); at 115200 baud ~11.5 bytes/ms
arrive. When everything was sent at once (~94 bytes every 0.25 s), bytes were
lost and lines got glued together (garbled text, and a `T` line missing its
start could be read as `C0 R0`).

`G`, `B` and the text are resent even without changes: if the Arduino resets
with the port open (reset button, brownout), the PC doesn't notice and the LCD
loses its glyphs. The firmware ignores identical glyphs, so the cost is almost
nil. Alternative not done: resend only on receiving `READY`.

### Behaviour
- **LCD text composed in `gauge.py`** (`lcd_rows()`, `GLYPHS`,
  `BRIGHTNESS`): to change what is shown, only the Python needs touching.
  Today: row 1 with the icon of the other metric (chip / RAM stick; or a
  fixed letter C/R), the % and the top app (`▣ 5% Code`); row 2 with a power
  icon and the uptime as `HH:MMh` or, from 24 h on, `DD:HHd` (fixed width),
  and on the right the download speed (`⏻ 03:26h   ↓1.2M`). Upload was left
  out: it doesn't fit in 16 characters with the uptime in this format.
  Long app name: only the name slides (icon and % stay still), done in
  Python, one letter every 0.5 s and 2 s still at each end. The LCD's own
  scroll doesn't work: it shifts both rows together.
  The Arduino only picks the variant; `Needle: ...`, `No data` and
  `Homing...` come from the Arduino.
- **Top apps:** summed per app ("Helper" processes count towards the app).
  Without root, ~270 system processes are left out; they're remembered and
  skipped on later scans. The script itself is left out (with the PC idle it
  always showed `Python`). Scan every 5 s, ~28 ms of CPU (previously ~44 ms
  every 3 s). The whole script used ~2.2% of one core (≈0.3% of the PC with
  8 cores); the 20 Hz loop alone is ~0.3%. Moving RAM to its own interval
  isn't worth it: on macOS it comes from the same call as CPU.
- **Network:** counts only the interface that internet traffic goes through,
  chosen by route (a UDP `connect` to `1.1.1.1` sends nothing) and not by
  name, which changes from OS to OS (the Mac has 18 interfaces: `lo0`,
  `utun*`, `awdl0`…). With a VPN it counts the VPN's, only once. Interface
  rechecked every 5 s (Wi-Fi ↔ cable swap); speed read every 1 s (~70 µs).
  With no network or right after an interface change it shows `↓--`. Units
  of 1000 bytes/s, like Activity Monitor. psutil doesn't give per-process
  traffic, so there's no "top app" for the network.
- **No data for 5 s** (or `P`): the needle parks at 0% and saves position and
  phase to EEPROM, so it doesn't home on the next boot.
- **No data for 1 minute:** LCD and lighting turn off. With the screen off,
  a short press only lights it for 10 s (doesn't toggle CPU/RAM).
- **Computer screen asleep:** the script sends `B0` and `P` and stops sending
  readings; it resumes when the screen wakes (checks every 3 s).
  Detection: macOS `CGDisplayIsAsleep` (ctypes); Linux `xset q` on X11,
  `/sys/class/drm/*/dpms` on Wayland/console (untested); other systems
  always assume on. Tested on the Mac (2026-10-06): the needle parks and
  the LCD text and lighting turn off.
- **The LCD is only written while the needle is still:** writing blocks for
  a few ms and could make the motor lose steps mid-move.

### EEPROM
- One 4-byte record at address 0: position (2), phase (1), `MAGIC` (1,
  written last).
- Written when parking; `MAGIC` goes to 0 on the first move afterwards and at
  the start of a homing. Read only at boot.
- Uses `update`: in practice only `MAGIC` gets rewritten (2 writes per
  park/move cycle, ~100,000 per byte → more than 10 years).

## Final version electronics

Full wiring diagram: [docs/wiring.svg](docs/wiring.svg)
(regenerate with `python3 docs/wiring.py docs/wiring.svg`).

### Pin map (Nano)
| Pin | Function |
|---|---|
| D2, D3 | motor, coil A (via driver) |
| D8, D9 | motor, coil B (via driver) |
| D4 | CPU/RAM button, to ground |
| D5, D6 | warning lights (PWM, LED + 330 Ω straight to the pin) |
| D7, D12 | LCD RS, E |
| A0–A3 | LCD D4–D7 |
| D10 | lighting: LCD + needle + scale (PWM, via transistor) |
| D11 | Pomodoro button (future) |
| D13 | piezo (future; `tone()` doesn't interfere with PWM on D10) |
| A4, A5, A6, A7 | free (A4/A5 = I2C; A6/A7 analog input only) |
| D0, D1 | **don't use**: they're serial/USB |

### LCD
- Pins: 1 GND, 2 5V, 3 V0 (contrast), 4 RS, 5 RW → GND, 6 E,
  11–14 D4–D7, 15 A (LED+), 16 K (LED−).
- **4-bit mode:** each byte goes in two halves over D4–D7; LCD pins 7–10
  (D0–D3) stay disconnected. Saves 4 Arduino pins; the cost is ~200 µs per
  character instead of ~100 µs. RW to ground: write only, the library uses
  fixed waits.
- **If pins run short:** an **I²C (PCF8574)** adapter soldered to the LCD.
  Uses only A4/A5 and frees 6 pins. Still 4-bit internally; slower (~0.5–1 ms
  per character, bearable with the `gauge.py` queue). It can only switch the
  backlight on/off: to keep brightness control, remove the jumper and leave
  it on D10. `LiquidCrystal_I2C` library.
- **JHD162A model, ROM A00** (Japanese), confirmed by the datasheet. ROM
  symbols that don't use slots: `0xDF` °, `0x7E` →, `0x7F` ←, `0xFF` full
  block, `0xA5` middle dot, `0xDB` □, `0xE4` µ, `0xF4` Ω, `0xE0` α,
  `0xE2` β, `0xF7` π, `0xF6` Σ, `0xE8` √, `0xF3` ∞, `0xFD` ÷, `0xE1` ä,
  `0xEF` ö, `0xF5` ü, `0xEE` ñ. **In ASCII, `\` shows as ¥ and `~` as →.**
- The LCD's own scroll shifts both rows together; there's no pixel-level
  drawing (only the 8 slots). That would need a graphic display (OLED
  SSD1306 or ST7920).
- **Backlight:** the module **has an on-board resistor** (confirmed
  2026-10-04), so it can be connected without an external resistor. If it's
  the usual "101" (100 Ω), straight to D10 gives ~20–30 mA: fine for tests,
  but close to the pin limit. In the final version it moves to the lighting
  transistor.
- **Contrast:** 10 kΩ trimpot ("103"), multi-turn (3296 type) with the
  screw on top: ends to 5V and GND, wiper to pin 3. Tune with everything
  assembled, at the final viewing angle. V0 accepts 0–5 V without damage
  (5 V = invisible text); never above 5 V. The breadboard has a **7 kΩ** one
  (2026-10-04): it works, the value isn't critical.
- **Contrast flickering when the needle moves:** motor noise on 5V, not a
  lack of current. Fix: 100 µF + 100 nF between 5V and GND next to the LCD
  (and next to the motor driver), LCD ground on its own wire to the Nano.

### Lighting
- **Colours (decided 2026-10-06):** scale numbers in **cool white** (like car
  instrument clusters); needle in **orange**, matching the LCD backlight.
- **LEDs salvaged from old speedometers** instead of bought: low SMD LEDs
  with two side terminals (probably PLCC-2 / 3528). The body is white
  whatever the light colour, so test each one before using it: multimeter
  in diode mode, or 5 V through 1 kΩ. Desolder with hot air or two irons;
  don't overheat them.
  - Take the scale's white LEDs from the same cluster, so the shade of white
    matches.
  - Orange for the needle: compare against the LCD with all of them lit and
    pick the closest by eye. Car clusters usually light the needle from the
    hub, so the needle LEDs of an old cluster are a good start.
  - The salvaged boards' resistors are sized for 12 V: don't reuse them,
    each LED gets its own 150/220 Ω (below).
- **The dial must be backlit:** numbers and marks translucent on an opaque
  background (dark ink on film or translucent paper), not black on white.
- **Lighting group** (LCD, acrylic needle LEDs next to the shaft, scale LEDs
  or strip): all on one **BC337** transistor driven by D10, so the script's
  brightness and off apply to everything.
  - 5V → resistor → LED → collector; emitter → GND; D10 → 1 kΩ → base;
    10 kΩ base → GND (off during reset).
  - **Each LED with its own resistor:** 150 Ω for white/blue, 220 Ω for
    red/amber/yellow (~13–15 mA each). **For now all 150 Ω** (2026-10-06):
    orange then runs at ~19 mA, within rating. If the needle is too bright
    against the scale, swap only its resistors for 220/270 Ω.
  - Handles 15–20 LEDs. More LEDs = more identical branches in parallel.
  - For the scale, consider a **5 V COB strip** (continuous light, no dots;
    already has resistors; connects to the transistor as if it were an LED).
    - **It must be 5 V**: many COB strips are 12/24 V and barely light at 5 V.
    - Width: 2.7 / 3 / 5 / 8 mm exist; thin 5 V ones are less common.
    - **High consumption:** typically 5–10 W/m at 5 V (1–2 A/m). Check the
      listing for W/m and the cut pitch. E.g. 15 cm at 2 A/m ≈ 300 mA max,
      which brings the total to ~450 mA, almost at the USB limit. Options:
      brightness below max (PWM reduces consumption) or a separate 5 V supply.
    - The BC337 handles 800 mA: enough for the strip plus the LEDs.
  - **Alternative (idea, untested): edge-lit light guide** for the scale.
    The dotted acrylic panel from an old LCD screen (laptop or monitor):
    LEDs shine into its edge, the dots send the light forward.
    - Stack, back to front: reflector sheet → light guide cut to the scale
      shape → diffuser sheet (hides the dots) → dial transparency. All of it
      comes out of the same screen, and so do the white LEDs on the edge strip.
    - 2 LEDs, one at each end of the arc, will likely leave the middle
      darker. Use 3–4, or shine in from the arc's inner edge (closer to the
      whole scale).
    - The dot pattern is denser far from the original LEDs; once cut, it
      no longer matches ours, so some unevenness is likely.
    - Light-entry edge sanded smooth (polished if possible); other edges
      with white or aluminium tape to keep the light inside.
    - Acrylic cracks easily: score several times and snap for straight
      cuts; coping saw / drill, slowly, for the arc and the shaft hole.
    - **Test first:** whole panel, 2 LEDs against the edge, transparency on
      top. If it isn't even enough, add LEDs before cutting.
    - If it works: fewer LEDs than the ~8 planned, no dots, and no COB strip.
- **Warning lights:** one LED per pin (D5/D6) with 330 Ω, no transistor.
- LED: long leg = + (anode); flat side/short leg = − (cathode). On SMD LEDs
  the cut corner usually marks the cathode; confirm in diode mode.
- WS2812/SK6812 (addressable LEDs, one pin for all) were left out: no RGB
  wanted.

### Current budget (all over USB, 500 mA)
| | approx. |
|---|---|
| Nano + LCD + backlight | ~40 mA |
| Motor (with driver) | ~80 mA |
| ~10 lighting LEDs | ~150 mA |
| 2 warnings | ~20 mA |
| **Total** | **~300 mA** |

It fits, but more current lowers the Nano's 5V (input diode) and the motor
loses torque: repeat the self-test with the lighting at maximum. If it falls
short, a separate 5 V supply for lighting and motor, with a common ground.

## Bill of materials
Status: **have**, **buy**, **salvaged** (from old speedometers), **later**.

**Main parts**
| Item | Qty | Status | Notes |
|---|---|---|---|
| VDO/Siemens 91 255 008 motor | 1 | have | |
| Arduino Nano V3 (ATmega328P) | 1 | have | has its own reset button |
| Mini-USB cable | 1 | have? | most Nano V3 clones are Mini-USB |
| **TB6612FNG** module | 1 | buy | motor driver; PWMA, PWMB and STBY to 5V |
| 16×2 LCD JHD162A | 1 | have | backlight resistor on board |
| Push button (CPU/RAM, self-test, forced homing) | 1 | have | panel mount for the final version |
| **10 kΩ multi-turn trimpot** (3296W) | 1 | buy | LCD contrast; the 7 kΩ from the breadboard also works |

**Lighting**
| Item | Qty | Status | Notes |
|---|---|---|---|
| Cool white SMD LEDs (scale) | ~8 | salvaged | |
| Orange SMD LEDs (needle) | ~2 | salvaged | |
| Warning LEDs, 2 colours (e.g. red/amber) | 2 | salvaged | |
| **BC337** transistor | 2 | buy | 1 spare; or BC639 / PN2222A (check pinout) |

**Resistors**
| Value | Qty | For |
|---|---|---|
| 150 Ω | 15 | lighting LEDs, all colours for now |
| 330 Ω | 2 | warning lights |
| 1 kΩ | 2 | transistor base (1 spare) |
| 10 kΩ | 2 | base to ground (1 spare) |

Check the ones at home first.

**Capacitors**
| Item | Qty | For |
|---|---|---|
| 100 µF electrolytic, ≥10 V | 3 | next to the LCD, the motor driver and the Nano's 5V |
| 100 nF ceramic ("104") | 4 | one beside each 100 µF + 1 spare |

**Assembly**
| Item | Qty | Notes |
|---|---|---|
| Perfboard (or Nano screw-terminal board) | 1 | |
| Female pin header strip | 2 × 15 | Nano removable |
| Male pin header strip | 1 × 40 | LCD and TB6612FNG, if they come without |
| Solid-core wire, a few colours | — | |
| Heat-shrink tubing | — | |
| 4-pin connector (JST/Dupont) | 1 | optional: motor detachable |

**Tools**
Soldering iron and solder, hot air (or a second iron) to desolder the SMD
LEDs, multimeter, flux, desoldering braid.

**Later**
| Item | Qty | For |
|---|---|---|
| 5 V COB strip, cool white | 1 m | only if the salvaged LEDs leave dots on the scale |
| Old LCD screen (laptop/monitor) | 1 | salvage light guide, diffuser, reflector and LEDs; alternative to the strip |
| Passive piezo | 1 | Pomodoro |
| Push button | 1 | Pomodoro |
| Transparency film for the printer | 1–2 sheets | backlit dial |
| Breadboard and jumper wires | — | testing the lighting, if not at hand |

## To do
1. ✅ Count the travel in steps: **290** (measured twice).
2. **Measure the real angle** between stops with a protractor (`-300` and
   `+300` in `step_counter`), to know the effective resolution
   (degrees/step).
3. **Draw the scale** after 2. Decide `POS_MIN`/`POS_MAX` then.
4. Generate the dial SVG (parameterized Python script: radius, sweep angle,
   number of divisions). Print at **100% / actual size**, with a 100 mm
   control line to check. The centre must be exact: 1 mm off ruins the
   reading.
5. ✅ Final sketch and Python script.
6. ✅ `HOME_PHASE = 6`. Procedure (repeat if the motor wiring changes):
   with `step_counter` (s2): `d20`, `-300`, `+20`; mark the needle tip;
   `-` one step at a time; the `phase` of the first `-` that no longer moves
   the needle is the `HOME_PHASE`. Repeat to confirm, put the value in
   `gauge.ino` and change `MAGIC` to force homing.
7. ✅ LCD backlight resistor: the module has one (2026-10-04).
8. Test the lighting on the breadboard: transistor + LCD + 1 LED under the
   acrylic needle; add LEDs until it looks right.
9. Move the motor to the TB6612FNG, add the capacitors and recalibrate
   `HOME_PHASE`; repeat the self-test with the lighting at maximum.
10. ✅ Screen sleeping on the Mac works (2026-10-06): needle parks, LCD and
    lighting off.
11. Later: Pomodoro, warning lights, temperature (`°` = the LCD's 0xDF).
    Temperature: only Linux has a generic way (`psutil.sensors_temperatures()`).
    On the M1 Mac (MacBook Air, fanless) only through a private API, without
    sudo, via `ctypes` (like Stats/macmon): fragile across chips and macOS
    versions. On Windows only with LibreHardwareMonitor running. Idea: a
    warning light above ~95 °C instead of using the LCD.
12. **Needle parking against the end stop** without homing (after a homing
    it parks 12 half-steps above, which is correct): the position in EEPROM
    is wrong. Test: forced homing (button held at power-up), several
    sessions under load and see where it parks. If it drops from session to
    session, it loses steps going up: go up slower (`MIN_UP_US`) or
    TB6612FNG.
