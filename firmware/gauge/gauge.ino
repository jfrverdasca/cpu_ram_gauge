// Needle gauge driven by a VDO/Siemens 91 255 008 instrument-cluster stepper.
//
// Only the physical part: pc/gauge.py decides what the needle and the LCD show and
// what a click means. Serial input (protocol 2): "N45.3\n" needle %, sent every 50 ms
// (also the sign gauge.py is alive), "T0<text>\n" LCD row 0 or 1, "G0<16 hex digits>\n"
// custom LCD character 0-7 (8 rows, top first), "B0".."B255" backlight (0 = LCD off,
// sent when the computer's screen sleeps). "P\n" asks to park. "Z<UTC seconds>
// <standard offset, minutes> <0/1 summer time>\n" sets the clock. "M<focus> <break>
// <long break> <cycles>\n" Pomodoro minutes, "S<Hz> <volume 0-100>\n" piezo; both
// saved to EEPROM.
// Output: "READY 2" at boot, "PARKED", "CLICK", "POMODORO ON"/"POMODORO OFF", "TEST".
//
// Button, acting on release (the LCD shows what releasing will do while held):
// < 1 s sends "CLICK", or in the Pomodoro starts/pauses it or starts the next phase;
// 1-3 s enters or leaves the Pomodoro; >= 3 s lamp test: a 0 -> 100 -> 0 sweep, 1 s at
// the top and 1 s at the bottom, every LCD pixel on and a beep.
// Hold it while powering up to force a full homing (that release is ignored).
//
// Pomodoro: focus, break, long break after every few focus sessions. The needle runs
// from 100% to 0% as each phase's time runs out; at the end the piezo beeps and the
// next phase waits for a click. gauge.py's needle and text are ignored meanwhile, and
// clicks don't reach it.
//
// With no data for 5 s (or on "P") the needle parks at 0% and its position is saved
// to EEPROM, so the next boot doesn't need to grind against the end stop. After a
// minute with no data the LCD and its backlight turn off; a short press then only
// wakes it for 10 s.
//
// With a DS3231 clock module (I2C, A4/A5) and no data, the needle shows the minutes
// (0-60%) and the LCD the time and date; only its light turns off, and a short press
// lights it for 10 s. Not while gauge.py keeps saying the computer's screen sleeps
// (brightness 0): then the needle parks at 0% and the LCD turns off as before. The
// clock keeps UTC; summer time follows the EU rule. The parked
// position goes to the clock's alarm registers (battery-backed, no write limit) instead
// of the EEPROM, as the needle then moves every minute.
//
// Bench-measured motor parameters (see NOTES.md): half-step, 290 steps stop to stop,
// 2 ms/step is reliable going up, the downward direction needs more margin.

#include <EEPROM.h>
#include <LiquidCrystal.h>
#include <Wire.h>

const uint8_t COIL_A1 = 2;
const uint8_t COIL_A2 = 3;
const uint8_t COIL_B1 = 8;
const uint8_t COIL_B2 = 9;
const uint8_t BUTTON = 4;       // to GND
const uint8_t PIEZO = 13;       // passive piezo, driven from Timer2 (bit 5 of PORTB)
const uint8_t LCD_RS = 7;
const uint8_t LCD_E = 12;       // LCD D4-D7 on A0-A3, RW to GND
const uint8_t LCD_LIGHT = 10;   // backlight (PWM), through a resistor
const uint8_t RTC_ADDR = 0x68;  // DS3231
const uint8_t RTC_PARK_REG = 0x07;  // alarm 1: 4 bytes, holds the parked record

const int TRAVEL = 290;         // half-steps between end stops
const int POS_MIN = 12;         // 0%
const int POS_MAX = 242;        // 100%: 50% points straight up

const unsigned long START_US = 5000;      // first step from rest
const unsigned long MIN_UP_US = 2000;
const unsigned long MIN_DOWN_US = 3000;
const unsigned long HOMING_US = 5000;
// Phase whose rest position is just below the zero stop (calibrate with step_counter).
// Ending the homing on it always seats the rotor against the stop; any other phase can
// leave it up to half an electrical cycle (4 half-steps) above. -1 = not calibrated.
const int8_t HOME_PHASE = 6;              // bench-calibrated, direct drive (3 runs)
const uint8_t RAMP = 12;                  // steps from START_US to full speed
const uint8_t DEAD_ZONE = 2;

const unsigned long DATA_TIMEOUT_MS = 5000;
const unsigned long RELEASE_MS = 100;     // de-energize coils once stopped
// Hold the current phase this long before a move starts: from rest the rotor needs
// it to settle, and right after a stop it is still ringing. Starting during the ringing
// loses steps (going down most): a target falling ~5 steps every 50 ms lost dozens.
const unsigned long SETTLE_MS = 60;
// Caught up with the target, keep the phase and the direction this long before
// stopping: a smoothed metric (CPU) creeps on every 50 ms, and going on from here
// needs no settle. Stopping each time made it climb in jerks (35 stops 10% -> 90%).
const unsigned long FOLLOW_MS = 400;     // the slow end of a climb too
const unsigned long DEBOUNCE_MS = 50;
const unsigned long POMODORO_PRESS_MS = 1000;  // hold to enter/leave the Pomodoro
const unsigned long TEST_PRESS_MS = 3000;      // hold for the lamp test
const unsigned long TEST_HOLD_MS = 1000;  // the lamp test's needle stays at 100%, then 0%
const unsigned long LCD_REFRESH_MS = 250;
const unsigned long SCREEN_OFF_MS = 60000; // LCD off this long after the last data
const unsigned long WAKE_MS = 10000;      // LCD on after a press while it was off
const unsigned long SLEEP_MS = 15000;     // "B0" holds this long (gauge.py resends every 5 s)
const unsigned long SCRIPT_MS = 15000;    // gauge.py counts as running this long after a line
const unsigned long BEEP_MS = 150;        // each beep and each gap

const int8_t HALF_STEP[8][2] = {{1,0},{1,1},{0,1},{-1,1},{-1,0},{-1,-1},{0,-1},{1,-1}};

// Magic is the last field so it is written last. Change it to invalidate saved data.
struct Parked { int16_t pos; uint8_t phase; uint8_t magic; };
const uint8_t MAGIC = 0xA9;
struct Zone { int16_t stdMin; uint8_t summer; uint8_t magic; };  // from gauge.py
const int ZONE_ADDR = 8;        // EEPROM, after the parked record
// From gauge.py ("M", "S"): Pomodoro minutes, focus sessions before the long break,
// piezo tone and volume
struct Settings { uint8_t focus, brk, longBrk, cycles; uint16_t hz; uint8_t volume, magic; };
const int SETTINGS_ADDR = 12;   // EEPROM, after the zone

enum PomoPhase : uint8_t { FOCUS, BREAK, LONG_BREAK };
enum PomoState : uint8_t { RUNNING, PAUSED, OVER };   // OVER: waits for a click

struct Button { uint8_t pin; bool raw, down, skip; unsigned long changedMs, pressedMs; };

int pos = 0, target = 0;
int8_t dir = 0;
uint8_t speed = 0, phase = 0;
bool energized = false;
unsigned long lastStepUs = 0, stoppedMs = 0;   // stoppedMs: still on the current phase since
unsigned long caughtUpMs = 0;   // at the target while moving, since
bool caughtUp = false;

float needle = 0;               // % from gauge.py
// From where the needle was going when it came, the target slides to it over the
// 50 ms until the next: steps come evenly instead of in a burst per reading
float needleFrom = 0;
unsigned long needleMs = 0;
const unsigned long READING_MS = 50;      // gauge.py's INTERVAL
bool hasData = false;
unsigned long lastDataMs = 0;
unsigned long lastLineMs = 0;   // any line from gauge.py
char text[2][17];               // LCD rows from gauge.py
uint8_t glyphs[8][8];           // LCD custom characters from gauge.py
uint8_t glyphsChanged = 0;      // one bit per character, written by updateLcd()
bool parked = false;            // EEPROM holds the current position
// 0 = off, 1 = lamp test pending, 2 = going up, 3 = at the top, 4 = going down,
// 5 = at the bottom
uint8_t sweep = 0;
unsigned long sweepMs = 0;      // reached the top or the bottom
Button mainButton = {BUTTON};
uint8_t holding = 0;            // main button held: 0 = no, 1 = Pomodoro, 2 = lamp test
unsigned long wokeMs = 0;       // last press that woke the LCD
bool screenOn = true;
uint8_t brightness = 255;       // from gauge.py

bool rtcOk = false;             // clock module answers
bool timeOk = false;            // and its time is valid (set since the battery last ran out)
uint32_t utcNow = 0;            // seconds since 1970, read while the needle is still
Zone zone = {0, 1, MAGIC};
uint32_t syncUtc = 0;           // time from gauge.py, applied once the needle is still
unsigned long syncMs = 0;       // when it arrived; 0 = nothing pending

Settings settings = {25, 5, 15, 4, 2000, 50, MAGIC};
bool pomo = false;              // in the Pomodoro
PomoPhase pomoPhase = FOCUS;
PomoState pomoState = RUNNING;
uint8_t pomoCycle = 1;          // focus session, 1..settings.cycles
unsigned long pomoEndMs = 0;    // while running
unsigned long pomoLeftMs = 0;   // while paused or over
uint8_t beepsLeft = 0;          // beeps and gaps still to play
unsigned long beepMs = 0;
volatile uint8_t piezoHigh, piezoLow;  // Timer2 ticks (4 us) of each half wave, minus 1

LiquidCrystal lcd(LCD_RS, LCD_E, A0, A1, A2, A3);

// ---------------------------------------------------------------- motor

void setCoils(int8_t a, int8_t b) {
  digitalWrite(COIL_A1, a > 0);
  digitalWrite(COIL_A2, a < 0);
  digitalWrite(COIL_B1, b > 0);
  digitalWrite(COIL_B2, b < 0);
  energized = a || b;
}

void applyPhase() { setCoils(HALF_STEP[phase][0], HALF_STEP[phase][1]); }

bool stopped() { return dir == 0 && pos == target; }
// Stepping now. Caught up and waiting for the target (FOLLOW_MS) the rotor is still:
// slow work (LCD, I2C) can go then, or a slow, steady move would hold it off for good
bool stepping() { return dir != 0 && !caughtUp; }

float slidingNeedle() {
  unsigned long t = millis() - needleMs;
  return t >= READING_MS ? needle : needleFrom + (needle - needleFrom) * t / READING_MS;
}

// Non-blocking move with an acceleration ramp. If the target moves behind us,
// decelerate first and reverse afterwards.
void updateMotor() {
  unsigned long now = micros();

  if (dir == 0) {
    if (pos == target) {
      if (energized && millis() - stoppedMs > RELEASE_MS) setCoils(0, 0);
      return;
    }
    if (!energized) {           // let the rotor settle on the current phase first
      applyPhase();
      stoppedMs = millis();
    }
    if (millis() - stoppedMs < SETTLE_MS) return;
    if (parked) clearParked();
    dir = target > pos ? 1 : -1;
    speed = 0;
    caughtUp = false;
    lastStepUs = micros();
    return;
  }

  unsigned long minUs = dir > 0 ? MIN_UP_US : MIN_DOWN_US;
  if (now - lastStepUs < START_US - (START_US - minUs) * speed / RAMP) return;

  int ahead = (target - pos) * dir;
  if (ahead == 0) {             // wait a little for the target to move on
    if (!caughtUp) {
      caughtUp = true;
      caughtUpMs = millis();
      speed = 0;                // whatever comes next starts from the ramp's foot
    }
    if (millis() - caughtUpMs < FOLLOW_MS) return;
  }
  caughtUp = false;
  if (ahead == 0 || (ahead < 0 && speed == 0)) {   // reversing: stop and settle first
    dir = 0;
    stoppedMs = millis();
    return;
  }
  if (ahead < 0 || speed > ahead) speed--;
  else if (speed < ahead && speed < RAMP) speed++;

  phase = (phase + 8 + dir) % 8;
  applyPhase();
  pos += dir;
  lastStepUs = now;
}

void fullHoming() {
  clearParked();
  applyPhase();
  delay(SETTLE_MS);             // same settle as before any move
  for (int i = 0; i < TRAVEL + 10; i++) {
    phase = (phase + 7) % 8;
    applyPhase();
    delayMicroseconds(HOMING_US);
  }
  while (HOME_PHASE >= 0 && phase != HOME_PHASE) {
    phase = (phase + 7) % 8;
    applyPhase();
    delay(20);
  }
  pos = target = 0;
  stoppedMs = millis();
}

bool validParked(const Parked& p) {
  return p.magic == MAGIC && p.phase < 8 && p.pos >= 0 && p.pos <= TRAVEL;
}

bool restoreParked() {
  Parked p;
  if (!rtcOk || !rtcRead(RTC_PARK_REG, (uint8_t*)&p, sizeof(p)) || !validParked(p)) {
    EEPROM.get(0, p);
    if (!validParked(p)) return false;
  }
  pos = target = p.pos;
  phase = p.phase;
  applyPhase();
  stoppedMs = millis();
  parked = true;
  return true;
}

void saveParked() {
  Parked p = {(int16_t)pos, phase, MAGIC};
  if (rtcOk) rtcWrite(RTC_PARK_REG, (uint8_t*)&p, sizeof(p));
  else EEPROM.put(0, p);
  parked = true;
}

void clearParked() {
  uint8_t zero = 0;
  if (rtcOk) rtcWrite(RTC_PARK_REG + sizeof(Parked) - 1, &zero, 1);
  EEPROM.update(sizeof(Parked) - 1, 0);   // writes only if it was valid
  parked = false;
}

// ---------------------------------------------------------------- clock

uint8_t bcd(uint8_t v) { return v / 10 * 16 + v % 10; }
uint8_t unbcd(uint8_t v) { return v / 16 * 10 + v % 16; }

bool rtcRead(uint8_t reg, uint8_t* buf, uint8_t n) {
  Wire.beginTransmission(RTC_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission() != 0 || Wire.requestFrom(RTC_ADDR, n) != n) return false;
  for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
  return true;
}

void rtcWrite(uint8_t reg, const uint8_t* buf, uint8_t n) {
  Wire.beginTransmission(RTC_ADDR);
  Wire.write(reg);
  Wire.write(buf, n);
  Wire.endTransmission();
}

// Days since 1970-01-01 and back (Howard Hinnant's civil calendar algorithms).
int32_t daysFromCivil(int16_t y, uint8_t m, uint8_t d) {
  y -= m <= 2;
  uint16_t yoe = y % 400;
  uint16_t doy = (153 * (m > 2 ? m - 3 : m + 9) + 2) / 5 + d - 1;
  return (y / 400) * 146097L + yoe * 365L + yoe / 4 - yoe / 100 + doy - 719468;
}

void civilFromDays(int32_t z, int16_t& y, uint8_t& m, uint8_t& d) {
  z += 719468;
  int32_t era = z / 146097;
  uint32_t doe = z - era * 146097;
  uint32_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  uint32_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  uint32_t mp = (5 * doy + 2) / 153;
  d = doy - (153 * mp + 2) / 5 + 1;
  m = mp < 10 ? mp + 3 : mp - 9;
  y = yoe + era * 400 + (m <= 2);
}

int32_t lastSunday(int16_t y, uint8_t m) {
  int32_t d = daysFromCivil(y, m, 31);
  return d - (d + 4) % 7;       // 1970-01-01 was a Thursday
}

// EU summer time: from the last Sunday of March to the last Sunday of October, 01:00 UTC.
bool euSummer(uint32_t utc) {
  int16_t y;
  uint8_t m, d;
  civilFromDays(utc / 86400, y, m, d);
  return utc >= lastSunday(y, 3) * 86400UL + 3600 && utc < lastSunday(y, 10) * 86400UL + 3600;
}

uint32_t localNow() {
  return utcNow + zone.stdMin * 60L + (zone.summer && euSummer(utcNow) ? 3600 : 0);
}

void readClock() {
  uint8_t r[7], status;
  rtcOk = rtcRead(0x00, r, 7) && rtcRead(0x0F, &status, 1);
  timeOk = rtcOk && !(status & 0x80);   // oscillator-stopped flag
  if (!timeOk) return;
  int32_t days = daysFromCivil(2000 + unbcd(r[6]), unbcd(r[5] & 0x1F), unbcd(r[4]));
  utcNow = days * 86400UL + unbcd(r[2] & 0x3F) * 3600UL + unbcd(r[1]) * 60 + unbcd(r[0]);
}

void setClock(uint32_t utc) {
  int16_t y;
  uint8_t m, d;
  uint32_t days = utc / 86400, s = utc % 86400;
  civilFromDays(days, y, m, d);
  uint8_t r[7] = {bcd(s % 60), bcd(s / 60 % 60), bcd(s / 3600), (uint8_t)((days + 4) % 7 + 1),
                  bcd(d), bcd(m), bcd(y - 2000)};
  rtcWrite(0x00, r, 7);
  uint8_t status = 0;           // clears the oscillator-stopped flag, 32 kHz output off
  rtcWrite(0x0F, &status, 1);
  utcNow = utc;
  timeOk = true;
}

// I2C takes ~1 ms: only while the needle is still.
void updateClock() {
  static unsigned long lastMs = 0;
  if (stepping() || millis() - lastMs < LCD_REFRESH_MS) return;
  lastMs = millis();
  readClock();
  if (syncMs && rtcOk) {
    uint32_t utc = syncUtc + (millis() - syncMs) / 1000;
    if (!timeOk || utc > utcNow + 1 || utcNow > utc + 1) setClock(utc);
  }
  syncMs = 0;
}

// ---------------------------------------------------------------- piezo

// Square wave on D13 toggled by Timer2's compare interrupt (CTC, 4 us ticks). The
// volume is the duty cycle: 100 = 50% (loudest), lower is quieter. 1000-5000 Hz.
void piezoTone(bool on) {
  TIMSK2 = 0;
  PORTB &= ~_BV(5);
  if (!on || settings.volume == 0) return;
  uint8_t period = 250000UL / settings.hz;
  uint8_t high = max(2, period * settings.volume / 200);
  piezoHigh = high - 1;
  piezoLow = period - high - 1;
  TCCR2A = _BV(WGM21);          // CTC: counts up to OCR2A
  TCCR2B = _BV(CS22);           // clk/64
  TCNT2 = 0;
  OCR2A = piezoHigh;
  PORTB |= _BV(5);
  TIMSK2 = _BV(OCIE2A);
}

ISR(TIMER2_COMPA_vect) {
  if (PORTB & _BV(5)) {
    PORTB &= ~_BV(5);
    OCR2A = piezoLow;
  } else {
    PORTB |= _BV(5);
    OCR2A = piezoHigh;
  }
}

void beep(uint8_t n) {
  beepsLeft = n * 2;
  beepMs = millis() - BEEP_MS;
}

void updatePiezo() {
  if (!beepsLeft || millis() - beepMs < BEEP_MS) return;
  beepMs = millis();
  beepsLeft--;
  piezoTone(beepsLeft % 2);     // odd: a beep, even: a gap
}

// ---------------------------------------------------------------- pomodoro

unsigned long pomoTotalMs() {
  uint8_t m = pomoPhase == FOCUS ? settings.focus : pomoPhase == BREAK ? settings.brk : settings.longBrk;
  return m * 60000UL;
}

unsigned long pomoLeft() {
  if (pomoState != RUNNING) return pomoLeftMs;
  long left = pomoEndMs - millis();
  return left > 0 ? left : 0;
}

bool longBreakNext() { return pomoCycle % settings.cycles == 0; }

void startPhase(PomoPhase p) {
  pomoPhase = p;
  pomoState = RUNNING;
  pomoEndMs = millis() + pomoTotalMs();
}

void togglePomodoro() {
  pomo = !pomo;
  if (pomo) {
    pomoCycle = 1;
    startPhase(FOCUS);
  } else {
    beepsLeft = 0;
    piezoTone(false);
  }
  Serial.println(pomo ? F("POMODORO ON") : F("POMODORO OFF"));
}

// A click in the Pomodoro: pause, resume, or start the next phase once one is over.
void pomodoroClick() {
  if (pomoState == RUNNING) {
    pomoLeftMs = pomoLeft();
    pomoState = PAUSED;
  } else if (pomoState == PAUSED) {
    pomoEndMs = millis() + pomoLeftMs;
    pomoState = RUNNING;
  } else if (pomoPhase == FOCUS) {
    startPhase(longBreakNext() ? LONG_BREAK : BREAK);
  } else {
    pomoCycle = pomoCycle % settings.cycles + 1;
    startPhase(FOCUS);
  }
}

void updatePomodoro() {
  if (!pomo || pomoState != RUNNING || pomoLeft() > 0) return;
  pomoState = OVER;
  pomoLeftMs = 0;
  beep(3);
}

// ---------------------------------------------------------------- input

void saveSettings(const Settings& s) {
  if (memcmp(&s, &settings, sizeof(s)) == 0) return;
  settings = s;
  EEPROM.put(SETTINGS_ADDR, settings);
}

void handleLine(char* s) {
  lastLineMs = millis();
  char* end;
  switch (s[0]) {
    case 'N': {
      // The dead zone is on readings: one within it of the last is noise, left out.
      // The slide then moves one step at a time
      float n = atof(s + 1);
      int next = posFor(n);
      if (abs(next - posFor(needle)) >= DEAD_ZONE || next == POS_MIN || next == POS_MAX) {
        needleFrom = slidingNeedle();
        needle = n;
        needleMs = millis();
      }
      hasData = true;
      lastDataMs = millis();
      break;
    }
    case 'T': {
      uint8_t row = s[1] - '0';
      if (row <= 1) strlcpy(text[row], s + 2, sizeof(text[0]));
      break;
    }
    case 'P':
      hasData = false;
      if (pomo) Serial.println(F("POMODORO ON"));   // the needle stays with the Pomodoro
      else if (parked) Serial.println(F("PARKED"));
      break;
    case 'G': {
      uint8_t n = s[1] - '0';
      if (n > 7 || strlen(s) != 18) return;
      uint8_t g[8];
      for (uint8_t i = 0; i < 8; i++) {
        char hex[3] = {s[2 + 2 * i], s[3 + 2 * i], '\0'};
        g[i] = strtoul(hex, NULL, 16);
      }
      if (memcmp(g, glyphs[n], 8) != 0) {
        memcpy(glyphs[n], g, 8);
        glyphsChanged |= 1 << n;
      }
      break;
    }
    case 'Z': {
      syncUtc = strtoul(s + 1, &end, 10);
      syncMs = millis() | 1;
      Zone z = {(int16_t)strtol(end, &end, 10), (uint8_t)(strtol(end, NULL, 10) != 0), MAGIC};
      if (memcmp(&z, &zone, sizeof(z)) != 0) {
        zone = z;
        EEPROM.put(ZONE_ADDR, zone);
      }
      break;
    }
    case 'B':
      brightness = constrain(atoi(s + 1), 0, 255);
      break;
    // constrain() is a macro that evaluates its argument more than once: parse first
    case 'M': {
      long focus = strtol(s + 1, &end, 10), brk = strtol(end, &end, 10),
           longBrk = strtol(end, &end, 10), cycles = strtol(end, NULL, 10);
      Settings m = settings;
      m.focus = constrain(focus, 1, 99);
      m.brk = constrain(brk, 1, 99);
      m.longBrk = constrain(longBrk, 1, 99);
      m.cycles = constrain(cycles, 1, 9);
      saveSettings(m);
      if (pomoCycle > settings.cycles) pomoCycle = settings.cycles;
      break;
    }
    case 'S': {
      long hz = strtol(s + 1, &end, 10), volume = strtol(end, NULL, 10);
      Settings m = settings;
      m.hz = constrain(hz, 1000, 5000);
      m.volume = constrain(volume, 0, 100);
      saveSettings(m);
      break;
    }
  }
}

void readSerial() {
  static char buf[32];
  static uint8_t n = 0;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n') {
      buf[n] = '\0';
      n = 0;
      handleLine(buf);
    } else if (ch != '\r' && n < sizeof(buf) - 1) {
      buf[n++] = ch;
    }
  }
}

// Debounced; true when the button was just pressed or released.
bool debounce(Button& b) {
  bool now = digitalRead(b.pin) == LOW;
  if (now != b.raw) {
    b.raw = now;
    b.changedMs = millis();
  }
  if (millis() - b.changedMs < DEBOUNCE_MS || b.raw == b.down) return false;
  b.down = b.raw;
  if (b.down) b.pressedMs = millis();
  return true;
}

bool scriptRunning() { return lastLineMs && millis() - lastLineMs < SCRIPT_MS; }

void startTest() {
  sweep = 1;
  beep(1);
  Serial.println(F("TEST"));
}

// The main button acts on release, by how long it was held.
void readButtons() {
  Button& b = mainButton;
  if (debounce(b) && !b.down) {
    unsigned long held = millis() - b.pressedMs;
    if (b.skip) b.skip = false;   // the press that forced the homing
    else if (held >= TEST_PRESS_MS) startTest();
    else if (held >= POMODORO_PRESS_MS) togglePomodoro();
    else if (pomo) pomodoroClick();
    else if (!screenOn) wokeMs = millis();
    else if (scriptRunning()) Serial.println(F("CLICK"));
  }
  unsigned long held = millis() - b.pressedMs;
  holding = !b.down || b.skip ? 0 : held >= TEST_PRESS_MS ? 2 : held >= POMODORO_PRESS_MS ? 1 : 0;
}

// ---------------------------------------------------------------- logic

bool online() { return hasData && millis() - lastDataMs <= DATA_TIMEOUT_MS; }

// The computer's screen sleeps: gauge.py is still there, sending brightness 0.
bool pcAsleep() { return brightness == 0 && millis() - lastLineMs < SLEEP_MS; }

bool clockMode() { return !online() && timeOk && !pcAsleep(); }

int posFor(float percent) {
  return POS_MIN + (int)((POS_MAX - POS_MIN) * constrain(percent, 0.0, 100.0) / 100.0 + 0.5);
}

void updateTarget() {
  if (sweep == 1) return;       // the needle finishes its move, then updateLcd() goes on
  // Up, a second at the top, down, a second at the bottom
  if (sweep == 2 || sweep == 4) {
    target = sweep == 2 ? POS_MAX : POS_MIN;
    if (stopped()) {
      sweep++;
      sweepMs = millis();
    }
    return;
  }
  if (sweep == 3 || sweep == 5) {
    if (millis() - sweepMs >= TEST_HOLD_MS) sweep = sweep == 3 ? 4 : 0;
    return;
  }

  if (pomo) {
    target = posFor(pomoLeft() * 100.0 / pomoTotalMs());
    // Like the clock: only to the clock module, as the EEPROM would wear out
    if (rtcOk && !parked && stopped()) saveParked();
    return;
  }

  if (!online()) {
    target = clockMode() ? posFor(localNow() / 60 % 60) : POS_MIN;
    if (!parked && stopped()) {
      saveParked();
      Serial.println(F("PARKED"));
    }
    return;
  }

  target = posFor(slidingNeedle());
}

// ---------------------------------------------------------------- display

// Pads to the full width, so old text is overwritten without lcd.clear().
void printRow(uint8_t row, const char* text) {
  static char shown[2][17];
  char line[17];
  snprintf(line, sizeof(line), "%-16s", text);
  if (strcmp(line, shown[row]) == 0) return;
  strcpy(shown[row], line);
  lcd.setCursor(0, row);
  lcd.print(line);
}

void printRow(uint8_t row, const __FlashStringHelper* text) {
  char line[17];
  strlcpy_P(line, (const char*)text, sizeof(line));
  printRow(row, line);
}

// Lamp test for the sweep: every pixel on.
void showAllPixels() {
  char full[17];
  memset(full, 0xFF, 16);       // 0xFF: the full 5x8 block in the LCD's character ROM
  full[16] = '\0';
  printRow(0, full);
  printRow(1, full);
}

// "     14:37"; empty without the clock module.
void timeRow(char* row) {
  uint32_t t = localNow();
  if (timeOk) snprintf_P(row, 17, PSTR("     %02u:%02u"), (unsigned)(t / 3600 % 24), (unsigned)(t / 60 % 60));
  else row[0] = '\0';
}

// "Focus 2/4  24:13" / "Paused", or "Break time!" once a phase is over.
void showPomodoro() {
  char row[17], label[11];
  if (pomoState == OVER) {
    printRow(0, pomoPhase != FOCUS ? F("Focus time!") : longBreakNext() ? F("Long break time!") : F("Break time!"));
  } else {
    if (pomoPhase == LONG_BREAK) strcpy_P(label, PSTR("Long break"));
    else snprintf_P(label, sizeof(label), PSTR("%S %u/%u"), pomoPhase == FOCUS ? PSTR("Focus") : PSTR("Break"),
                    pomoCycle, settings.cycles);
    unsigned long s = (pomoLeft() + 999) / 1000;
    snprintf_P(row, sizeof(row), PSTR("%-10s %2lu:%02lu"), label, s / 60, s % 60);
    printRow(0, row);
  }
  if (pomoState == PAUSED) {
    printRow(1, F("Paused"));
  } else {
    timeRow(row);
    printRow(1, row);
  }
}

// Writing to the LCD blocks for a few ms, which could stall the motor mid-move,
// so the display only updates while the needle is still.
void updateLcd() {
  static unsigned long lastMs = 0;
  // A pending lamp test goes out as soon as the needle is still
  if (stepping() || (millis() - lastMs < LCD_REFRESH_MS && sweep != 1)) return;
  lastMs = millis();

  for (uint8_t n = 0; n < 8; n++) {
    if (glyphsChanged & (1 << n)) lcd.createChar(n, glyphs[n]);
  }
  glyphsChanged = 0;

  // Off a minute after the last data, or when gauge.py sets 0. A press wakes it
  // anyway. gauge.py's brightness only while it sends data; full otherwise.
  static uint8_t backlight = 255;
  bool on = sweep || holding || pomo || (brightness > 0 && millis() - lastDataMs < SCREEN_OFF_MS) ||
            millis() - wokeMs < WAKE_MS;
  uint8_t level = !on ? 0 : (sweep || brightness == 0 || !online()) ? 255 : brightness;
  if (level != backlight) {
    backlight = level;
    analogWrite(LCD_LIGHT, level);
  }
  screenOn = on;
  // The clock stays readable without light
  static bool shown = true;
  bool show = on || clockMode();
  if (show != shown) {
    shown = show;
    if (show) lcd.display();
    else lcd.noDisplay();
  }
  if (sweep) {
    if (sweep == 1) {
      showAllPixels();
      sweep = 2;                // now the needle can start
    }
    return;
  }
  if (holding) {
    printRow(0, holding == 2 ? F("Test") : pomo ? F("Exit pomodoro") : F("Start pomodoro"));
    printRow(1, F(""));
    return;
  }
  if (pomo) {
    showPomodoro();
    return;
  }

  if (clockMode()) {
    static const char DAYS[] = "SunMonTueWedThuFriSat";
    uint32_t t = localNow();
    int32_t days = t / 86400;
    int16_t y;
    uint8_t m, d;
    civilFromDays(days, y, m, d);
    char row[17];
    timeRow(row);
    printRow(0, row);
    snprintf(row, sizeof(row), " %.3s %02u/%02u/%d", DAYS + (days + 4) % 7 * 3, d, m, y);
    printRow(1, row);
    return;
  }
  printRow(0, online() ? text[0] : "No data");
  printRow(1, online() ? text[1] : "");
}

// ----------------------------------------------------------------

void setup() {
  pinMode(COIL_A1, OUTPUT);
  pinMode(COIL_A2, OUTPUT);
  pinMode(COIL_B1, OUTPUT);
  pinMode(COIL_B2, OUTPUT);
  pinMode(LCD_LIGHT, OUTPUT);
  pinMode(PIEZO, OUTPUT);
  pinMode(BUTTON, INPUT_PULLUP);
  digitalWrite(LCD_LIGHT, HIGH);
  lcd.begin(16, 2);
  Serial.begin(115200);
  Wire.begin();
  Wire.setWireTimeout(3000, true);   // never hang on a bad I2C bus
  readClock();
  Zone z;
  EEPROM.get(ZONE_ADDR, z);
  if (z.magic == MAGIC) zone = z;
  Settings st;
  EEPROM.get(SETTINGS_ADDR, st);
  if (st.magic == MAGIC) settings = st;

  delay(DEBOUNCE_MS);
  if (digitalRead(BUTTON) == LOW || !restoreParked()) {
    if (digitalRead(BUTTON) == LOW) {
      // Still held after the homing: its release must not count as a press
      mainButton.raw = mainButton.down = mainButton.skip = true;
    }
    lcd.print(F("Homing..."));
    fullHoming();
  }
  while (Serial.available()) Serial.read();  // drop anything sent during homing
  Serial.println(F("READY 2"));             // protocol version, checked by gauge.py
}

void loop() {
  readSerial();
  readButtons();
  updateClock();
  updatePomodoro();
  updateTarget();
  updateMotor();
  updatePiezo();
  updateLcd();
}
