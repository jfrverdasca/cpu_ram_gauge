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
- **A single needle**, with a **button** toggling between CPU and RAM (a
  click, handled by `gauge.py`; see "Script-driven firmware").
  Buttons on `INPUT_PULLUP` to ground, 50 ms software debounce.
- **0→100→0 sweep** as a self-test on a 3 s press, 1 s at the top and 1 s
  at zero before going back to what it shows, with
  every LCD pixel on (and full light) and a beep as a lamp test.
- **The LCD replaces the mode lights:** it shows the metric the needle isn't
  showing; when toggling, it shows for 3 s which one is on the needle.
  - **Row 1 is contextual** (2026-10-09): uptime and network normally; with
    the needle's metric (system total) above 60% CPU / 80% RAM, the app using
    the most of it and that app's own share, in the same layout as row 0
    (`▣ 38% Code`), until it drops below 50% CPU / 75% RAM. For RAM, the
    app's size instead of its share (2026-10-15): `▣ 2.6G Firefox`, `850M`
    below 1 GB, one letter as the network rate's to leave room for the name.
    It's the summed RSS, not Activity Monitor's "Memory" (footprint, with
    compressed memory: for Firefox 6.7 GB against 2.6 GB RSS).
    The icon is an app window (2026-10-10), not the metric's: with the same
    layout on both rows, a metric icon on both made the PC's total and the
    app's share easy to mix up. The metric is known (the needle's). Tried an
    app grid (four 2×2 blocks) too: nicer, but the window reads better on
    the LCD. Try the grid again once it's mounted in the case.
    The app's name is noise at low usage; the threshold is on the total
    because a single app rarely reaches 60% of RAM.
    - RAM higher because macOS sits at 60-70% used on a normal day (62% at
      rest on 2026-10-09): at 60% the row never left the app.
    - **Apps scanned every 1 s while high** (CPU or RAM past its threshold;
      either, since the Arduino picks the needle), and at once when one
      crosses it. At 5 s the row switched with the name from before the rise
      (VS Code with 8 `yes` loading the CPU), and a new process reads 0% on
      its first scan, so it took up to ~10 s. Now ~2.5 s, and the first scan
      after the CPU rises only primes the new processes: the row keeps
      uptime and network until the next one, 1 s later. Before that it
      showed the old app for that second (VS Code at 3%).
- **Warning lights** (the cluster's pink bulb holders) become real warnings,
  normally off: sustained CPU > 90%, end of a Pomodoro phase, etc. 5 V LEDs;
  the original bulbs (~12 V / 1.2 W) would need a 12 V supply. Beware of
  "automotive" T5 LEDs: many are for 12 V.
- **Pomodoro** (implemented 2026-10-13; button and piezo still to be wired
  and tested): on the Arduino, so it works without the PC. Entered and left
  with a 1 s press on the main button; inside it a click starts/pauses the
  time, or starts the next phase. Until 2026-10-14 that was a second,
  dedicated button (D11): one button is enough, since clicks had no other use
  in the Pomodoro. Work and breaks
  both run the needle 100%→0% as the time runs out (it was: break rising
  back to 100%); alert via a **passive piezo** (D13).
  - Entering starts the first focus at once.
  - At the end of a phase the piezo beeps 3 times and the next phase waits
    for a click (the needle stays at 0%). Long break after every
    `cycles` focus sessions.
  - Durations (focus, break, long break, cycles before the long one) and the
    piezo's tone and volume are set at the top of `gauge.py` and saved on
    the Arduino (EEPROM), so it works without the PC. Defaults 25/5/15 min,
    long break every 4, 2000 Hz, volume 50.
  - Clicks don't reach the script while in Pomodoro (they start/pause it);
    the 1 s press exits, the 3 s press runs the lamp test, which then goes
    back to the Pomodoro. The script's needle and text are ignored meanwhile.
  - The LCD stays lit (the script's brightness, full without the script or
    with the computer asleep).
  - The needle position goes to the clock module as in clock mode; without
    it, it isn't saved (the EEPROM would wear out: a step every ~6 s), so a
    power cut mid-Pomodoro means a homing on the next boot.
  - LCD (to try on the real display):

    | State | Row 0 | Row 1 |
    |---|---|---|
    | running | `Focus 2/4  24:13` / `Break 2/4   4:59` / `Long break 14:59` | time of day (empty without the clock module) |
    | paused | same | `Paused` (steady, not blinking) |
    | phase over | `Break time!` / `Long break time!` / `Focus time!` | time of day |

  - **Piezo volume:** `tone()` only sets the pitch, so the firmware drives D13
    from Timer2 itself (compare interrupt, 4 µs ticks) with the volume as
    the duty cycle: 100 = 50% (loudest), lower is quieter, 0 silent.
    1000–5000 Hz (the timer's range; piezos sound best around 2–4 kHz). How
    loud it really gets depends on the piezo: tune `BEEP_HZ`/`BEEP_VOLUME`
    once it arrives. Timer2 is free: D3 and D11 don't use PWM. The Nano's
    on-board LED (D13) blinks with the beeps.
- **Script-driven firmware (decided and implemented 2026-10-13):** the
  Arduino is only the physical part; `gauge.py` holds the modes and decides
  what the needle and the LCD show. Rule: **the Arduino keeps what must work
  without the PC or protects the hardware; the script owns the meaning.** It
  makes new metrics and modes Python-only (no reflashing) and freed firmware
  RAM for the Pomodoro (833 → 800 bytes with it; flash 16.6 → 19.2 KB).
  - **Arduino:** motor (ramp, settle, dead zone), the scale (`POS_MIN`/
    `POS_MAX`: the script sends percentages, not steps), homing, parking,
    the data timeout (clock or park, LCD off), clock and date, Pomodoro,
    lamp test.
  - **Script:** what the needle shows, both LCD rows, glyphs, brightness,
    the needles (the plugins', in click order; see Plugins) and what clicks
    mean. The `Needle: ...` notice moved to the script. Everything safe to
    change is at the top of `gauge.py`.
  - **Main button**, classified by the Arduino on release, never on reaching
    a threshold (holding to 3 s must not trigger the 1 s action on the way).
    While held, the LCD shows what releasing will do:

    | Held | LCD while held | On release |
    |---|---|---|
    | < 1 s | — | `CLICK` to the script; with the LCD off, wakes it for 10 s instead; in the Pomodoro, start/pause or the next phase |
    | 1–3 s | `Start pomodoro` / `Exit pomodoro` | enter/leave Pomodoro (Arduino) |
    | ≥ 3 s | `Test` | lamp test (Arduino) |
    | at power-up | — | forced homing; that release is ignored |

  - **Clicks are raw:** the Arduino sends one `CLICK` per short press and
    interprets nothing. Double clicks etc. would be the script's: the ~300 ms
    wait to tell a single from a double click only exists once the script
    uses double clicks, so a plain click acts at once.
  - **The Arduino reports its local actions too** (`POMODORO ON`/`OFF`,
    `TEST`); the script only logs them, as the Arduino ignores its needle and
    text while they last.
  - **LCD off** (no data for a minute, or the computer asleep): a click only
    wakes it for 10 s, showing "No data" or the clock, as before. The script
    couldn't show anything then anyway. Only with the LCD on and the script
    running (a line in the last 15 s) does the click go to the script.
  - **Heartbeat:** the needle line `N`, every reading (50 ms); while the
    computer sleeps, `B0` every 5 s.
  - **Protocol version:** `READY 2` at boot. `gauge.py` waits for it on
    connecting (up to 10 s, homing included) and exits on a mismatch (also
    an old firmware's bare `READY`), as firmware and script no longer work
    without each other. Without any `READY` it warns and carries on.
  - **Sent only when needed:** Pomodoro and piezo settings, brightness and
    time on connecting and on every `READY` (the Arduino restarted, which
    before went unnoticed and needed resending every 5 s); brightness also
    when the computer's screen sleeps or wakes, the time hourly, the LCD rows
    when they change, glyphs when a row needs one that isn't loaded
    (2026-10-14; before, all of them on connecting).
  - **No thread:** `gauge.py` reads the port on every 50 ms reading, without
    waiting, so a click is answered within ~50 ms.
  - **During the Pomodoro `gauge.py` almost rests** (2026-10-13): from
    `POMODORO ON` to `POMODORO OFF` it reads every 250 ms instead of 50 ms
    and sends only the needle (4 lines a second instead of 20 plus text),
    with no app scans, network readings or LCD text. The needle still goes,
    though the Arduino ignores it: with nothing at all it would count as
    no data after 5 s, start parking when the Pomodoro ends and take the
    LCD to full brightness. On `POMODORO OFF` it resends both rows (the LCD
    still has the text from before) and rescans apps and network (their
    figures would span the Pomodoro).
- **Clock:** ZS-042 module (DS3231 + AT24C32, CR2032 cell) on I2C: VCC,
  GND, SDA → A4, SCL → A5 (the other pins are unused). On the bench
  2026-10-08: clock mode, `gauge.py` setting the time and parking, and the
  parked position surviving a USB unplug (no homing) work; the
  screen-asleep rule below was tested on 2026-10-09. With no data the needle
  shows the minutes (0–60%, 1% per minute) and the LCD the time and date
  (`     14:37` / ` Thu 08/10/2026`). Without the module (or with its time
  invalid) everything works as before (`No data`, needle at 0%).
  - **Not while the computer's screen sleeps:** `gauge.py` then keeps sending
    `B0` (every 5 s) and the needle parks at 0% with the LCD off, as before.
    `B0` counts for 15 s after the last line received, so if the script
    dies or the PC turns off while asleep, the clock shows after 15 s.
  - **On the Mac the clock shows about a minute after the screen sleeps**
    (tested 2026-10-09): the Mac goes to idle sleep ~65 s after the display
    turns off (`pmset` `sleep 1`), the script stops with it, and 15 s of
    silence look like the PC being off. Kept on purpose: the alternative
    (`B0` holding until the script says it is leaving) would leave the gauge
    dark if the script died without saying so.
  - **The text stays on, only the light turns off** a minute after the last
    data (the backlight LED fades with hours of use; in daylight it reads
    without light). A short press lights it for 10 s.
  - The RTC keeps **UTC**. `gauge.py` sends the time every 5 s
    (`Z<UTC seconds> <standard offset, minutes> <0/1>`); the firmware applies
    it once the needle is still, only if it differs by more than 1 s. Never
    set by hand.
  - **Summer time with the EU rule** (last Sunday of March and of October,
    01:00 UTC), computed by the firmware, so it changes even with the PC off.
    The zone (standard offset + whether it has summer time) goes to the
    EEPROM at address 8, written only when it changes. Calendar maths
    checked against Python's `zoneinfo` (Europe/Lisbon) from 2000 to 2100.
  - **The parked position goes to the DS3231's alarm 1 registers** (4 bytes,
    battery-backed, no write limit) instead of the EEPROM: the needle now
    moves every minute, and 2 EEPROM writes per minute would wear it out in
    weeks. Without the module, the EEPROM as before. At boot, the RTC record
    first, then the EEPROM.
  - I2C (~1 ms per read) only while the needle is still, every 0.25 s.
    `Wire.setWireTimeout` so a bad bus never hangs the loop.
  - **The ZS-042 charges the cell** through a 200 Ω resistor (`201`) and a
    diode: with a CR2032 (not rechargeable) **remove the `201` resistor**.
  - **Power is the real limit:** with the PC off most motherboards cut USB
    5V, and the Arduino goes off with it. The coin cell only keeps the RTC
    counting. Works while the PC is on without the script, asleep, or off
    with USB power kept on (BIOS "USB power in S5" / ErP off; check by
    charging a phone with the PC off). Otherwise a separate 5 V supply.
  - Firmware with the clock: 16.5 KB of 30 KB flash, 833 of 2048 bytes of
    RAM (before: 10.5 KB and 535 bytes). The DS3231's temperature (±3 °C,
    0x11–0x12) would be cheap to add; left out for now.
- **Margins on the scale:** 0% = 12 half-steps above the end stop
  (`POS_MIN = 12`). It can go down to 4–6 if the scale needs it; less than
  that and the needle hits. **`POS_MAX = 242`** (2026-10-13, was 278): 50%
  (127) has the needle pointing straight up and 100% mirrors 0% on the other
  side. Measured with `step_counter`: `h`, `+12`, `z`, then `+115` to
  vertical and `+115` to 100%; the scale spans 230 half-steps from
  `POS_MIN`. Redo it if `HOME_PHASE` changes.

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
  and wait (`SETTLE_MS`). Without this, the self-test and the homing
  couldn't go down from the top. With the wait, going down got to 3 ms per
  step and homing to 5 ms. Self-test, 100% load and parking on timeout
  tested and working.
- **The wait also counts from the last stop: 60 ms** (2026-10-09; it was a
  fixed 20 ms `delay()` on each start). A heavy Spotlight search left the
  needle well above zero (up to ~90° with another motor of the same model):
  steps lost going down. Reproduced with a steady ramp (10% → 80% → 10%):
  at 1% per reading (~19 ms/step) and 4% (~5 ms/step) it stayed exact, at
  **2% per reading (~9 ms/step, like the CPU falling after a search)** it
  lost dozens of steps every run. At that rate each reading moves the target
  ~5 steps: 20 ms wait, 5 quick steps, stop, and the next start falls while
  the rotor is still ringing from the stop. With 60 ms since the last stop:
  exact. The wait doesn't block (serial and LCD keep running) and costs
  nothing after a longer pause. Then 3 heavy Spotlight searches (needle near 100%):
  back at zero every time. That other motor is the one mounted now: its
  `HOME_PHASE` is the old motor's, kept (to-do 13).
- **Following a creeping target** (2026-10-15): the CPU climbed in jerks, RAM
  smoothly. RAM jumps to its value at once; the CPU is smoothed in
  `gauge.py`, so its target creeps up every 50 ms, less each time. The motor
  is faster: it caught up, stopped, waited the 60 ms and started from the
  ramp's foot, ~every 100 ms. In the simulator, 10% → 90%: 35 stops and 3.1 s
  standing still out of 3.9 s (RAM: 1 stop). Two changes:
  - Caught up, it keeps the phase and the direction up to `FOLLOW_MS`
    before stopping; a target moving on in the same direction goes on with
    no settle. Reversing and starting from rest still settle 60 ms.
  - The target slides from where it was going to each new reading over the
    50 ms until the next (`slidingNeedle()`), so the steps of a reading come
    evenly instead of in one burst.
  Simulator: 0 stops during the climb. The 60 ms came from steps lost
  falling at ~5 steps a reading, which this brings back in a milder form (no
  hard stop): `tests/hardware/needle_cycles.py` climbs and falls like the CPU
  50 times, then the needle has to sit exactly on 0%. It did.
  On the gauge the middle of a move was smooth, the ends still jerked (the
  end most): the dead zone, on the sliding target, let it move in pairs, and
  with 150 ms it stopped (settle, coils off and back on) as the climb slowed.
  So the dead zone moved to the readings (one within 2 steps of the last is
  left out; the slide then goes a step at a time) and `FOLLOW_MS` to 400 ms.
  The LCD and the clock only update while the needle is still: caught up
  counts as still (`stepping()`), or a slow, steady move (the Pomodoro's)
  would freeze the LCD. On the gauge: no more jerks at the ends, and 50
  cycles again ended exactly on 0%, and the needle is much quieter.
  After review: the settle before reversing counts from the last step (still
  at the target for a while, it reverses at once); and reaching the target
  above the ramp's foot (a reading pulling it back mid-climb, an abrupt
  stop) settles 60 ms before going on, in either direction. 50 cycles: on
  0% again.
- **`HOME_PHASE = 6`** (2026-10-03, direct drive, 3 identical measurements;
  `MAGIC` changed to `0xA9`). With this, homing always ends in the same
  place, even after pulling the plug.

## Code
- `firmware/step_counter/`: bench sketch (commands over serial, 19200 baud).
- `firmware/gauge/`: final sketch.
- `pc/gauge.py`: PC script (`pip install -r pc/requirements.txt`).
  Runs on macOS and Linux; also starts on Windows (without screen detection).
  `pc/plugins/`: what it shows (see Plugins); `pc/lcd.py`: LCD helpers they
  share (scrolling, accents, glyphs).
- `tests/run.sh` (2026-10-13): `tests/test_gauge.py` runs `gauge.py` against a
  fake serial port (unittest); `tests/firmware/test_firmware.cpp` runs
  `gauge.ino` on the computer, with `tests/firmware/mock/` standing in for the
  Arduino core, EEPROM, LCD and I2C (no clock module), each test in its own
  process so it starts from a fresh boot. `arduino-cli --preprocess` adds the
  function prototypes the IDE adds, so the sketch compiles unchanged. They
  caught `constrain(strtol(...))` reading too many numbers: `constrain()` is a
  macro that evaluates its argument more than once.
- Compiling without the IDE: `arduino-cli` ships inside Arduino IDE.app;
  pass `--libraries ~/Library/Arduino15/libraries` so it finds
  LiquidCrystal.

### Serial protocol (version 2, 2026-10-13)
PC → Arduino:
| Line | Meaning |
|---|---|
| `N45.3` | needle, % (every 50 ms; also the sign the script is alive) |
| `T<row><text>` | LCD row `0`/`1` (when it changes) |
| `G<0-7><16 hex>` | 5×8 custom character, top row first; in text use `chr(n)`, except slot 0, which is `chr(8)` (byte 0 would end the line). Don't use `8 + n` for the others: 10 and 13 are `\n` and `\r` |
| `B<0-255>` | lighting brightness (PWM on D10), used only while data arrives (255 during the sweep and with no data; the script sends 255 while a plugin notifies); `0` turns the LCD off (when the screen sleeps or wakes; `B0` every 5 s while asleep) |
| `P` | park; the Arduino replies `PARKED` (`POMODORO ON` in the Pomodoro: the needle stays) |
| `Z<UTC seconds> <offset> <0/1>` | time for the clock module: UTC, standard offset in minutes, whether the zone has (EU) summer time (hourly) |
| `M<focus> <break> <long break> <cycles>` | Pomodoro minutes (1–99) and focus sessions before the long break (1–9); EEPROM |
| `S<Hz> <volume>` | piezo tone (1000–5000) and volume (0–100); EEPROM |

`G`, `M`, `S`, `B` and `Z` also go on connecting and on every `READY`.

Arduino → PC: `READY 2` (boot, after any homing), `PARKED`, `CLICK`,
`POMODORO ON`, `POMODORO OFF`, `TEST`.

Version 1 (until 2026-10-13): `C45.3 R72.1` readings and `T<row><C/R><text>`
for each metric; the Arduino picked the metric and showed `Needle: ...`.

Everything that isn't a reading goes into a queue and is sent **one line per
reading** (≤ ~32 bytes every 50 ms). The Arduino's receive buffer is only 64
bytes and the firmware stops reading for ~7–10 ms (LCD redraw; until
2026-10-09 also 20 ms when starting the motor); at 115200 baud ~11.5 bytes/ms
arrive. When everything was sent at once (~94 bytes every 0.25 s), bytes were
lost and lines got glued together (garbled text, and a `T` line missing its
start could be read as `C0 R0`).

`gauge.py` opens the port **exclusively** (2026-10-09). macOS lets two programs
open the same port, and during the bench tests a test script and `gauge.py`
both wrote readings: the needle swung between the two values. A second
`gauge.py` now gets "Could not exclusively lock port" and retries every 2 s.
The lock is advisory (`flock`): the Arduino IDE's Serial Monitor or any program
that doesn't ask for it can still open the port.

If the Arduino resets with the port open (reset button, brownout), the LCD
loses its glyphs. Until 2026-10-13 `G`, `B` and the text were resent every few
seconds for that; now `gauge.py` reads every line and resends them on `READY`.

### Behaviour
- **LCD text composed in `gauge.py`** by the plugins (`pc/plugins/`): to
  change what is shown, only the Python needs touching.
  Today: row 1 with the icon of the other metric (chip / RAM stick; or a
  fixed letter C/R), the % and the top app (`▣ 5% Code`); row 2 with a power
  icon and the uptime as `HH:MMh` or, from 24 h on, `DD:HHd` (fixed width),
  and on the right the network speed (`⏻ 03:26h   ↓1.2M`). Download and
  upload don't both fit in 16 characters with the uptime, so it shows the
  faster of the two, with ↓ or ↑ (download on a tie).
  Long app name: only the name slides (icon and % stay still), done in
  Python, one letter every 0.5 s and 2 s still at each end. The LCD's own
  scroll doesn't work: it shifts both rows together.
  Even steps (2026-10-15): text updates were 0.25 s after the previous one,
  0.26-0.28 s with the 50 ms loop, and drifted against the 0.5 s steps:
  every ~4 s two letters moved 0.27 s apart. Updates now fall on multiples
  of 0.25 s of the clock (`lcd.next_update()`) and `scroll()` counts whole
  updates, `since` (an email's start) included, so a step is every second
  update.
  `No data`, `Homing...`, the clock and the Pomodoro screens come from the
  Arduino; everything else from `gauge.py`.
- **Top apps:** summed per app. On macOS a process counts towards the
  outermost `.app` bundle of its executable (2026-10-10): Firefox's
  `plugin-container`, Chrome's and VS Code's helpers. Outside a bundle, the
  command name: Claude Code's binary is named after its version (`2.1.295`)
  and shows as `claude`. Elsewhere, the process name. The app's name is
  the bundle's, so VS Code shows as `Visual Studio Code` (scrolls).
  Without root, ~270 system processes are left out; they're remembered and
  skipped on later scans. The script itself is left out (with the PC idle it
  always showed `Python`). Scan every 5 s, ~14 ms of CPU (earlier
  measurements: ~28 ms; ~44 ms every 3 s before that).
  **Whole script, measured 2026-10-08** (30 s of the real loop, fake serial
  port): ~1.1% of one core (≈0.14% of the PC with 8 cores; ~2.2% in an
  earlier measurement). Per second: top apps ~2.8 ms, CPU + RAM readings
  ~0.13 ms, network ~0.07 ms, interface check ~0.04 ms, LCD text ~0.02 ms;
  the rest is waking up 20×/s and the serial writes. Moving RAM to its own interval
  isn't worth it: on macOS it comes from the same call as CPU. After the
  plugins (2026-10-14, 20 s, email off): ~1.0%, the same.
- **Network:** counts only the interface that internet traffic goes through,
  chosen by route (a UDP `connect` to `1.1.1.1` sends nothing) and not by
  name, which changes from OS to OS (the Mac has 18 interfaces: `lo0`,
  `utun*`, `awdl0`…). With a VPN it counts the VPN's, only once. Interface
  rechecked every 5 s (Wi-Fi ↔ cable swap); speed read every 1 s (~70 µs).
  With no network or right after an interface change it shows `↓--`.
  Both directions read from the same counters, at no extra cost. Units
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

### Plugins (2026-10-14)
`main()` used to hold CPU, RAM, apps, network and their state together; each
new feature added more. Now what the needle and the LCD show comes from
plugins, and `gauge.py` keeps the serial port, the loop, screen sleep, the
Pomodoro, clicks, the `Needle: ...` notice and putting the rows together.
- **One list, `PLUGINS`**, at the top of `gauge.py`. Its order is both the
  needles' click order and the LCD priority: each row shows the first plugin
  with something for it (`None` leaves it to the next). Considered a second
  list for the needles; one is simpler, and the two orders rarely clash
  (email has no needle, CPU and RAM come from one plugin).
- **Interface** (`pc/plugins/__init__.py`, all optional): `needles` (name →
  %), `update()` every `interval` s, `rows()` every 0.25 s, `start()`,
  `resume()` after a pause. No plugin runs while the screen sleeps or in
  the Pomodoro, so they cost nothing then; `resume()` lets them drop figures
  that would span the pause. A plugin hidden by the ones before it isn't
  asked for its rows, so what it would show waits.
- **Notifications** (2026-10-15): `notifying()` (`False` by default; email:
  while a message shows) puts the LCD's light at 255 meanwhile, then back to
  `BRIGHTNESS`. Fixed at 255 for every plugin, no setting. `B` goes only when
  the level changes; entering the Pomodoro brings it back at once (the
  Arduino uses the script's brightness there too).
- **Plugins:** `system` (CPU and RAM needles, the other metric and top app
  on row 0, the hot app on row 1; moved unchanged), `status` (uptime and
  network on row 1), `email`.
- **Glyphs on demand** (`lcd.Glyphs`): plugins register any number of 5×8
  characters (`lcd.glyph()` gives a private-use Unicode character to put in
  text); the script loads into the LCD's 8 slots only those the rows use,
  just before the rows, keeping loaded ones where they are. A new one takes
  an empty slot, else one no row needs, preferably not on the LCD: a slot
  rewritten changes on the LCD at once. Limit: 8 different ones on screen.
  The slots (CGRAM) are RAM in the LCD's controller: no wear from rewriting,
  but lost on a reset (resent after `READY`).
- **Accents:** `lcd.ascii()` drops them (João → Joao); the LCD's ROM has
  none of the Portuguese ones. Plugins call it before fitting a row: after,
  the length would change ("™" → "TM"). Whatever else isn't ASCII reaches
  the LCD as one `?` each (`text_line()`).
- **Email** (`plugins/email.py`): IMAP every 60 s in a thread (the loop never
  waits), mailbox opened read-only and headers fetched with `BODY.PEEK`, so
  nothing is marked read. The first poll only notes the last UID (no flood of
  old unread mail); a poll with nothing new and nothing waiting is just a
  `STATUS`. Each new unread message shows until its longest row has scrolled
  through once (at least 5 s), one at a time; one read elsewhere meanwhile is
  dropped. Mail that comes during the Pomodoro or sleep shows afterwards: the
  3 newest one by one. Layout by templates (`{icon}`, `{count}`, `{sender}`,
  `{address}`, `{subject}`). Default (2026-10-15): `3 new emails` on row 0
  and `sender - subject` scrolling on row 1, so email always takes both rows
  (before, a `+N emails` summary on row 0 left row 1 to the hot app). The
  count is the batch's (mail since the LCD had none, left-out ones included,
  read-elsewhere ones still counted) and only changes between messages; it
  replaces the `+N emails` summary. Or `sender - subject` on row 1 only,
  leaving row 0 to CPU/RAM. What comes before
  the first field other than `{icon}` stays still. The account goes in
  `.env` (out of git; `.env.example` lists the names), read by `load_env()`
  in `gauge.py` without python-dotenv; real environment variables win. The
  macOS Keychain was considered: `.env` is simpler and works everywhere,
  at the cost of the (app) password in plain text on disk.

### EEPROM
- One 4-byte record at address 0: position (2), phase (1), `MAGIC` (1,
  written last).
- Written when parking; `MAGIC` goes to 0 on the first move afterwards and at
  the start of a homing. Read only at boot.
- Uses `update`: in practice only `MAGIC` gets rewritten (2 writes per
  park/move cycle, ~100,000 per byte → more than 10 years).
- With the clock module the parked record goes to the RTC instead (see
  Clock). Address 8: the time zone from `gauge.py` (4 bytes). Address 12:
  Pomodoro and piezo settings from `gauge.py` (8 bytes, written only when
  they change).

## Final version electronics

Full wiring diagram: [docs/wiring.svg](docs/wiring.svg)
(regenerate with `python3 docs/wiring.py docs/wiring.svg`).
Perfboard layout: [docs/perfboard.svg](docs/perfboard.svg) (`docs/perfboard.py`,
which also checks that no traces cross and every net is connected). Driver
module chip side up, on headers like the Nano. The motor pads are labelled
by the bench-test wire (B1 = the one on D8, etc.), so the firmware is
unchanged.

### Pin map (Nano)
| Pin | Function |
|---|---|
| D2, D3 | motor, coil A (via driver) |
| D8, D9 | motor, coil B (via driver) |
| D4 | the button (needle, Pomodoro, lamp test), to ground |
| D5, D6 | warning lights (PWM, LED + 330 Ω straight to the pin) |
| D7, D12 | LCD RS, E |
| A0–A3 | LCD D4–D7 |
| D10 | lighting: LCD + needle + scale (PWM, via transistor) |
| D11 | free (was the Pomodoro button until 2026-10-14) |
| D13 | passive piezo, driven from Timer2 (in the firmware; not wired yet) |
| A4, A5 | RTC clock (DS3231), I2C SDA/SCL |
| A6, A7 | free (analog input only) |
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
| RTC module (future) | ~1–5 mA |
| Piezo (future, only while beeping) | ~10–30 mA |
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
| Push button (needle, Pomodoro, self-test, forced homing) | 1 | have | panel mount for the final version |
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
| RTC module with backup battery | 1 | clock with no data (have; confirm model) |
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
11. Clock: remove the module's `201` resistor.
    Later: Pomodoro, warning lights, temperature (`°` = the LCD's 0xDF).
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
13. ✅ `HOME_PHASE` for the motor mounted on 2026-10-09 (2026-10-14): kept
    at 6, the old motor's. With another motor a homing can end up to 4
    half-steps above the stop, but always in the same place, and the scale
    (`POS_MIN`, `POS_MAX`) was measured after a homing on this motor, so it
    already includes that offset. Checked: several forced homings, same zero
    every time. Recalibrate (procedure in 6), then `POS_MAX`, only if the
    motor or its wiring changes.
14. **Keep the Pomodoro across a reset** (noted 2026-10-13, not done).
    Opening the serial port resets the Arduino (DTR), so starting
    `gauge.py` (or its reconnecting after a port error) loses a Pomodoro
    running without it. Plan: save the state only when it changes (enter,
    pause, resume, phase over, next phase, exit: ~10 writes per session),
    with the phase's **end time in UTC** rather than the time left, and on
    boot work out what's left from the clock. Paused: the time left, resumed
    as it was. Needs the clock module (without it there's no way to know how
    long it was off).
    - Where: the DS3231's registers don't wear out (battery-backed), but only
      3 bytes are free (alarm 2; alarm 1 holds the parked needle) and the
      state needs ~7 (phase, state, cycle, 4-byte end time, magic).
    - **Recommended (not decided yet): the module's AT24C32 EEPROM**
      (ZS-042, I2C 0x57, 4 KB, ~1,000,000 writes per byte): room for
      everything, never wears out at ~10 writes a session, same I2C code as
      the clock.
    - Other options: packing it into the DS3231's 3 bytes (end time modulo
      65,536 s ≈ 18 h, no magic: off for over 18 h mid-Pomodoro, ~9% chance
      of resuming a phase with a meaningless time); the Arduino's EEPROM
      (~100,000 writes, ~7 years at 4 Pomodoros a day).
    - Alternative to the whole thing: stop the reset on opening the port
      (10 µF between RESET and GND; uploading then needs the reset button).
      Doing it in pyserial isn't reliable across systems and USB chips.
