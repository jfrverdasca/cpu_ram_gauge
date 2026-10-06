// CPU/RAM gauge driven by a VDO/Siemens 91 255 008 instrument-cluster stepper.
//
// Serial input (from pc/gauge.py): "C45.3 R72.1\n" readings, "T0C<text>\n" LCD text
// (row 0 or 1, for when the needle shows CPU or RAM), "G0<16 hex digits>\n" custom
// LCD character 0-7 (8 rows, top first), "B0".."B255" backlight (0 = LCD off, sent
// when the computer's screen sleeps). "P\n" asks to park.
// Button: short press toggles CPU/RAM; hold 1 s for a 0 -> 100 -> 0 sweep.
// Hold the button while powering up to force a full homing.
// The 16x2 LCD shows the text gauge.py sends for the current needle metric. After a
// button press it shows which one the needle has for 3 s.
//
// With no data for 5 s (or on "P") the needle parks at 0% and its position is saved
// to EEPROM, so the next boot doesn't need to grind against the end stop. After a
// minute with no data the LCD and its backlight turn off; a short press then only
// wakes it for 10 s instead of toggling CPU/RAM.
//
// Bench-measured motor parameters (see NOTAS.md): half-step, 290 steps stop to stop,
// 2 ms/step is reliable going up, the downward direction needs more margin.

#include <EEPROM.h>
#include <LiquidCrystal.h>

const uint8_t COIL_A1 = 2;
const uint8_t COIL_A2 = 3;
const uint8_t COIL_B1 = 8;
const uint8_t COIL_B2 = 9;
const uint8_t BUTTON = 4;       // to GND
const uint8_t LCD_RS = 7;
const uint8_t LCD_E = 12;       // LCD D4-D7 on A0-A3, RW to GND
const uint8_t LCD_LIGHT = 10;   // backlight (PWM), through a resistor

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

const int8_t HALF_STEP[8][2] = {{1,0},{1,1},{0,1},{-1,1},{-1,0},{-1,-1},{0,-1},{1,-1}};

// Magic is the last field so it is written last. Change it to invalidate saved data.
struct Parked { int16_t pos; uint8_t phase; uint8_t magic; };
const uint8_t MAGIC = 0xA9;

int pos = 0, target = 0;
int8_t dir = 0;
uint8_t speed = 0, phase = 0;
bool energized = false;
unsigned long lastStepUs = 0, stoppedMs = 0;

bool showRam = false;
float cpu = 0, ram = 0;
bool hasData = false;
unsigned long lastDataMs = 0;
char text[2][2][17];            // LCD text from gauge.py: [row][needle shows RAM]
uint8_t glyphs[8][8];           // LCD custom characters from gauge.py
uint8_t glyphsChanged = 0;      // one bit per character, written by updateLcd()
bool parked = false;            // EEPROM holds the current position
uint8_t sweep = 0;              // 0 = off, 1 = going up, 2 = going down
unsigned long switchedMs = 0;   // last CPU/RAM toggle (0 = also shown at boot)
unsigned long wokeMs = 0;       // last press that woke the LCD
bool screenOn = true;
uint8_t brightness = 255;       // from gauge.py

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
    if (parked) {
      EEPROM.update(sizeof(Parked) - 1, 0);
      parked = false;
    }
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
  EEPROM.update(sizeof(Parked) - 1, 0);
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

bool restoreParked() {
  Parked p;
  EEPROM.get(0, p);
  if (p.magic != MAGIC || p.phase >= 8 || p.pos < 0 || p.pos > TRAVEL) return false;
  pos = target = p.pos;
  phase = p.phase;
  applyPhase();
  stoppedMs = millis();
  parked = true;
  return true;
}

// ---------------------------------------------------------------- input

void handleLine(char* s) {
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

void updateTarget() {
  if (sweep) {
    target = sweep == 1 ? POS_MAX : POS_MIN;
    if (stopped()) sweep = sweep == 1 ? 2 : 0;
    return;
  }

  if (!online()) {
    target = POS_MIN;
    if (!parked && stopped()) {
      Parked p = {(int16_t)pos, phase, MAGIC};
      EEPROM.put(0, p);
      parked = true;
      Serial.println(F("PARKED"));
    }
    return;
  }

  float v = constrain(showRam ? ram : cpu, 0.0, 100.0);
  int next = POS_MIN + (int)((POS_MAX - POS_MIN) * v / 100.0 + 0.5);
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

// Writing to the LCD blocks for a few ms, which could stall the motor mid-move,
// so the display only updates while the needle is still.
void updateLcd() {
  static unsigned long lastMs = 0;
  if (dir != 0 || millis() - lastMs < LCD_REFRESH_MS) return;
  lastMs = millis();

  for (uint8_t n = 0; n < 8; n++) {
    if (glyphsChanged & (1 << n)) lcd.createChar(n, glyphs[n]);
  }
  glyphsChanged = 0;

  // Off a minute after the last data, or when gauge.py sets 0. A press wakes it
  // anyway, at full brightness if gauge.py set 0.
  static uint8_t backlight = 255;
  bool on = (brightness > 0 && millis() - lastDataMs < SCREEN_OFF_MS) ||
            millis() - wokeMs < WAKE_MS;
  uint8_t level = !on ? 0 : brightness > 0 ? brightness : 255;
  if (level != backlight) {
    backlight = level;
    analogWrite(LCD_LIGHT, level);
  }
  if (on != screenOn) {
    screenOn = on;
    if (on) lcd.display();
    else lcd.noDisplay();
  }

  const char* top = text[0][showRam];
  char notice[17];
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
  updateTarget();
  updateMotor();
  updateLcd();
}
