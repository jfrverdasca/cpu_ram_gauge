// CPU/RAM gauge driven by a VDO/Siemens 91 255 008 instrument-cluster stepper.
//
// Serial input (from pc/gauge.py): "C45.3 R72.1\n" readings, "T0C<text>\n" LCD text
// (row 0 or 1, for when the needle shows CPU or RAM), "G0<16 hex digits>\n" custom
// LCD character 0-7 (8 rows, top first), "B0".."B255" backlight (0 = LCD off, sent
// when the computer's screen sleeps). "P\n" asks to park. "Z<UTC seconds> <standard
// offset, minutes> <0/1 summer time>\n" sets the clock.
// Button: short press toggles CPU/RAM; hold 1 s for a 0 -> 100 -> 0 sweep, with every
// LCD pixel on (and full light) as a lamp test.
// Hold the button while powering up to force a full homing.
// The 16x2 LCD shows the text gauge.py sends for the current needle metric. After a
// button press it shows which one the needle has for 3 s.
//
// With no data for 5 s (or on "P") the needle parks at 0% and its position is saved
// to EEPROM, so the next boot doesn't need to grind against the end stop. After a
// minute with no data the LCD and its backlight turn off; a short press then only
// wakes it for 10 s instead of toggling CPU/RAM.
//
// With a DS3231 clock module (I2C, A4/A5) and no data, the needle shows the minutes
// (0-60%) and the LCD the time and date; only its light turns off, and a short press
// lights it for 10 s. Not while gauge.py keeps saying the computer's screen sleeps
// (brightness 0): then the needle parks at 0% and the LCD turns off as before. The clock keeps UTC; summer time follows the EU rule. The parked
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
const uint8_t LCD_RS = 7;
const uint8_t LCD_E = 12;       // LCD D4-D7 on A0-A3, RW to GND
const uint8_t LCD_LIGHT = 10;   // backlight (PWM), through a resistor
const uint8_t RTC_ADDR = 0x68;  // DS3231
const uint8_t RTC_PARK_REG = 0x07;  // alarm 1: 4 bytes, holds the parked record

const int TRAVEL = 290;         // half-steps between end stops
const int POS_MIN = 12;         // 0%
const int POS_MAX = 278;        // 100%

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
const unsigned long SETTLE_MS = 20;       // hold the current phase before a move starts
const unsigned long DEBOUNCE_MS = 50;
const unsigned long LONG_PRESS_MS = 1000;
const unsigned long LCD_REFRESH_MS = 250;
const unsigned long NOTICE_MS = 3000;     // "Needle: ..." after a button press
const unsigned long SCREEN_OFF_MS = 60000; // LCD off this long after the last data
const unsigned long WAKE_MS = 10000;      // LCD on after a press while it was off
const unsigned long SLEEP_MS = 15000;     // "B0" holds this long (gauge.py resends every 5 s)

const int8_t HALF_STEP[8][2] = {{1,0},{1,1},{0,1},{-1,1},{-1,0},{-1,-1},{0,-1},{1,-1}};

// Magic is the last field so it is written last. Change it to invalidate saved data.
struct Parked { int16_t pos; uint8_t phase; uint8_t magic; };
const uint8_t MAGIC = 0xA9;
struct Zone { int16_t stdMin; uint8_t summer; uint8_t magic; };  // from gauge.py
const int ZONE_ADDR = 8;        // EEPROM, after the parked record

int pos = 0, target = 0;
int8_t dir = 0;
uint8_t speed = 0, phase = 0;
bool energized = false;
unsigned long lastStepUs = 0, stoppedMs = 0;

bool showRam = false;
float cpu = 0, ram = 0;
bool hasData = false;
unsigned long lastDataMs = 0;
unsigned long lastLineMs = 0;   // any line from gauge.py
char text[2][2][17];            // LCD text from gauge.py: [row][needle shows RAM]
uint8_t glyphs[8][8];           // LCD custom characters from gauge.py
uint8_t glyphsChanged = 0;      // one bit per character, written by updateLcd()
bool parked = false;            // EEPROM holds the current position
uint8_t sweep = 0;              // 0 = off, 1 = lamp test pending, 2 = going up, 3 = going down
unsigned long switchedMs = 0;   // last CPU/RAM toggle (0 = also shown at boot)
unsigned long wokeMs = 0;       // last press that woke the LCD
bool screenOn = true;
uint8_t brightness = 255;       // from gauge.py

bool rtcOk = false;             // clock module answers
bool timeOk = false;            // and its time is valid (set since the battery last ran out)
uint32_t utcNow = 0;            // seconds since 1970, read while the needle is still
Zone zone = {0, 1, MAGIC};
uint32_t syncUtc = 0;           // time from gauge.py, applied once the needle is still
unsigned long syncMs = 0;       // when it arrived; 0 = nothing pending

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

// Non-blocking move with an acceleration ramp. If the target moves behind us,
// decelerate first and reverse afterwards.
void updateMotor() {
  unsigned long now = micros();

  if (dir == 0) {
    if (pos == target) {
      if (energized && millis() - stoppedMs > RELEASE_MS) setCoils(0, 0);
      return;
    }
    if (parked) clearParked();
    dir = target > pos ? 1 : -1;
    speed = 0;
    applyPhase();               // let the rotor settle on the current phase first
    delay(SETTLE_MS);
    lastStepUs = micros();
    return;
  }

  unsigned long minUs = dir > 0 ? MIN_UP_US : MIN_DOWN_US;
  if (now - lastStepUs < START_US - (START_US - minUs) * speed / RAMP) return;

  int ahead = (target - pos) * dir;
  if (ahead == 0 || (ahead < 0 && speed == 0)) {
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
  delay(SETTLE_MS);
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
  if (dir != 0 || millis() - lastMs < LCD_REFRESH_MS) return;
  lastMs = millis();
  readClock();
  if (syncMs && rtcOk) {
    uint32_t utc = syncUtc + (millis() - syncMs) / 1000;
    if (!timeOk || utc > utcNow + 1 || utcNow > utc + 1) setClock(utc);
  }
  syncMs = 0;
}

// ---------------------------------------------------------------- input

void handleLine(char* s) {
  lastLineMs = millis();
  if (strcmp(s, "P") == 0) {
    hasData = false;
    if (parked) Serial.println(F("PARKED"));
    return;
  }
  if (s[0] == 'G') {
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
    return;
  }
  if (s[0] == 'Z') {
    char* end;
    syncUtc = strtoul(s + 1, &end, 10);
    syncMs = millis() | 1;
    Zone z = {(int16_t)strtol(end, &end, 10), (uint8_t)(strtol(end, NULL, 10) != 0), MAGIC};
    if (memcmp(&z, &zone, sizeof(z)) != 0) {
      zone = z;
      EEPROM.put(ZONE_ADDR, zone);
    }
    return;
  }
  if (s[0] == 'B') {
    brightness = constrain(atoi(s + 1), 0, 255);
    return;
  }
  if (s[0] == 'T') {
    uint8_t row = s[1] - '0';
    if (row > 1 || (s[2] != 'C' && s[2] != 'R')) return;
    strlcpy(text[row][s[2] == 'R'], s + 3, sizeof(text[0][0]));
    return;
  }
  char* c = strchr(s, 'C');
  char* r = strchr(s, 'R');
  if (!c || !r) return;
  cpu = atof(c + 1);
  ram = atof(r + 1);
  hasData = true;
  lastDataMs = millis();
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

void readButton() {
  static bool down = false, raw = false, longDone = false;
  static unsigned long changedMs = 0, pressedMs = 0;

  bool now = digitalRead(BUTTON) == LOW;
  if (now != raw) {
    raw = now;
    changedMs = millis();
  }
  if (millis() - changedMs < DEBOUNCE_MS) return;

  if (raw != down) {
    down = raw;
    if (down) {
      pressedMs = millis();
      longDone = false;
    } else if (!longDone && !screenOn) {
      wokeMs = millis();
    } else if (!longDone) {
      showRam = !showRam;
      switchedMs = millis();
    }
  }
  if (down && !longDone && millis() - pressedMs >= LONG_PRESS_MS) {
    longDone = true;
    sweep = 1;
  }
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
  if (sweep) {
    target = sweep == 2 ? POS_MAX : POS_MIN;
    if (stopped()) sweep = sweep == 2 ? 3 : 0;
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

  int next = posFor(showRam ? ram : cpu);
  if (abs(next - target) >= DEAD_ZONE || next == POS_MIN || next == POS_MAX) target = next;
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

// Lamp test for the sweep: every pixel on.
void showAllPixels() {
  char full[17];
  memset(full, 0xFF, 16);       // 0xFF: the full 5x8 block in the LCD's character ROM
  full[16] = '\0';
  printRow(0, full);
  printRow(1, full);
}

// Writing to the LCD blocks for a few ms, which could stall the motor mid-move,
// so the display only updates while the needle is still.
void updateLcd() {
  static unsigned long lastMs = 0;
  // A pending lamp test goes out as soon as the needle is still
  if (dir != 0 || (millis() - lastMs < LCD_REFRESH_MS && sweep != 1)) return;
  lastMs = millis();

  for (uint8_t n = 0; n < 8; n++) {
    if (glyphsChanged & (1 << n)) lcd.createChar(n, glyphs[n]);
  }
  glyphsChanged = 0;

  // Off a minute after the last data, or when gauge.py sets 0. A press wakes it
  // anyway. gauge.py's brightness only while it sends data; full otherwise.
  static uint8_t backlight = 255;
  bool on = sweep || (brightness > 0 && millis() - lastDataMs < SCREEN_OFF_MS) ||
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

  const char* top = text[0][showRam];
  char notice[17];
  if (clockMode()) {
    static const char DAYS[] = "SunMonTueWedThuFriSat";
    uint32_t t = localNow();
    int32_t days = t / 86400;
    int16_t y;
    uint8_t m, d;
    civilFromDays(days, y, m, d);
    char date[17];
    snprintf(notice, sizeof(notice), "     %02u:%02u", (unsigned)(t / 3600 % 24), (unsigned)(t / 60 % 60));
    snprintf(date, sizeof(date), " %.3s %02u/%02u/%d", DAYS + (days + 4) % 7 * 3, d, m, y);
    printRow(0, notice);
    printRow(1, date);
    return;
  }
  if (millis() - switchedMs < NOTICE_MS) {
    snprintf(notice, sizeof(notice), "Needle: %s", showRam ? "RAM" : "CPU");
    top = notice;
  } else if (!online()) {
    top = "No data";
  }
  printRow(0, top);
  printRow(1, online() ? text[1][showRam] : "");
}

// ----------------------------------------------------------------

void setup() {
  pinMode(COIL_A1, OUTPUT);
  pinMode(COIL_A2, OUTPUT);
  pinMode(COIL_B1, OUTPUT);
  pinMode(COIL_B2, OUTPUT);
  pinMode(LCD_LIGHT, OUTPUT);
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

  delay(DEBOUNCE_MS);
  if (digitalRead(BUTTON) == LOW || !restoreParked()) {
    lcd.print(F("Homing..."));
    fullHoming();
  }
  while (Serial.available()) Serial.read();  // drop anything sent during homing
  Serial.println(F("READY"));
}

void loop() {
  readSerial();
  readButton();
  updateClock();
  updateTarget();
  updateMotor();
  updateLcd();
}
