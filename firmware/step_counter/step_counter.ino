// Bench test for the VDO/Siemens 91 255 008 stepper: drive it from the Serial
// Monitor and count its travel in steps.
//
// Direct drive from the Arduino pins is for bench tests only (~37 mA per coil,
// 40 mA absolute pin limit). Coils are released at the end of every move.
//
// Serial Monitor at 19200 baud, line ending "Newline". Type '?' for help.
//
// Scale calibration: 'h' homes exactly like gauge.ino, so position 0 here is the
// gauge's 0 (the end stop) and 0% is POS_MIN. Move the needle to point straight up,
// then 'm' takes that as 50% and prints the matching POS_MAX for gauge.ino.

const uint8_t COIL_A1 = 2;
const uint8_t COIL_A2 = 3;
const uint8_t COIL_B1 = 8;
const uint8_t COIL_B2 = 9;

struct Phase { int8_t a, b; };
const Phase SEQ_X25[]  = {{1,-1},{1,0},{0,1},{-1,1},{-1,0},{0,-1}};  // same as SwitecX25
const Phase SEQ_FULL[] = {{1,1},{-1,1},{-1,-1},{1,-1}};
const Phase SEQ_HALF[] = {{1,0},{1,1},{0,1},{-1,1},{-1,0},{-1,-1},{0,-1},{1,-1}};
const Phase* const SEQS[] = {SEQ_X25, SEQ_FULL, SEQ_HALF};
const uint8_t SEQ_LEN[] = {6, 4, 8};
const char* const SEQ_NAME[] = {"X25 / SwitecX25 (6 states)", "full step (4)", "half step (8)"};

// Same values as gauge.ino
const int TRAVEL = 290;
const int POS_MIN = 12;
const unsigned long HOMING_US = 5000;
const unsigned long SETTLE_MS = 60;
const int8_t HOME_PHASE = 6;

uint8_t seq = 2;
uint8_t phase = 0;
long count = 0;
unsigned int delayMs = 20;
bool verbose = true;
int posMax = 242;               // gauge.ino's POS_MAX until 'm' sets it

void setCoils(int8_t a, int8_t b) {
  digitalWrite(COIL_A1, a > 0);
  digitalWrite(COIL_A2, a < 0);
  digitalWrite(COIL_B1, b > 0);
  digitalWrite(COIL_B2, b < 0);
}

void applyPhase() { setCoils(SEQS[seq][phase].a, SEQS[seq][phase].b); }

// Any character received stops the move. The IDE 2 Serial Monitor doesn't send
// empty lines, so type a letter (e.g. "x") and Enter.
void move(long n) {
  int8_t dir = n > 0 ? 1 : -1;
  applyPhase();
  delay(delayMs);
  for (long i = labs(n); i > 0 && !Serial.available(); i--) {
    phase = (phase + SEQ_LEN[seq] + dir) % SEQ_LEN[seq];
    applyPhase();
    count += dir;
    if (verbose) {
      Serial.print(count);
      Serial.print(F("  phase "));
      Serial.println(phase);
    }
    delay(delayMs);
  }
  while (Serial.available()) Serial.read();
  setCoils(0, 0);
  Serial.print(F("Position: "));
  Serial.println(count);
}

// gauge.ino's fullHoming(): afterwards the count matches the gauge's positions.
void home() {
  if (seq != 2) {
    seq = 2;
    phase = 0;
    Serial.println(F("Sequence: half step"));
  }
  Serial.println(F("Homing..."));
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
  delay(SETTLE_MS);
  setCoils(0, 0);
  count = 0;
  Serial.println(F("Position: 0 (gauge zero)"));
}

int posFor(float percent) {
  return POS_MIN + (int)((posMax - POS_MIN) * percent / 100.0 + 0.5);
}

void printScale() {
  Serial.print(F("POS_MIN = ")); Serial.print(POS_MIN);
  Serial.print(F(", 50% = ")); Serial.print(posFor(50));
  Serial.print(F(", POS_MAX = ")); Serial.println(posMax);
}

void help() {
  Serial.println(F("\n--- VDO 91 255 008 bench test ---"));
  Serial.println(F("+N / -N  move N steps (no N = 1 step)"));
  Serial.println(F("z        reset the counter"));
  Serial.println(F("h        home like gauge.ino (position 0 = gauge zero)"));
  Serial.println(F("gN       go to N% of the scale (after 'h')"));
  Serial.println(F("m        current position is 50%: set and print POS_MAX"));
  Serial.println(F("p        print the position"));
  Serial.println(F("dN       delay between steps, ms"));
  Serial.println(F("sN       sequence: s0 X25, s1 full step, s2 half step"));
  Serial.println(F("v        toggle printing every step"));
  Serial.println(F("fN       hold phase N energized (to measure coil voltages)"));
  Serial.println(F("o        release the coils"));
  Serial.println(F("x+Enter  stop a move"));
  Serial.print(F("Sequence: ")); Serial.print(SEQ_NAME[seq]);
  Serial.print(F(" | delay: ")); Serial.print(delayMs);
  Serial.print(F(" ms | position: ")); Serial.println(count);
}

void setup() {
  pinMode(COIL_A1, OUTPUT);
  pinMode(COIL_A2, OUTPUT);
  pinMode(COIL_B1, OUTPUT);
  pinMode(COIL_B2, OUTPUT);
  setCoils(0, 0);
  Serial.begin(19200);
  help();
}

void loop() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;
  delay(5);
  while (Serial.available()) Serial.read();

  char cmd = line.charAt(0);
  long value = line.substring(1).toInt();

  switch (cmd) {
    case '+': move(max(value, 1L)); break;
    case '-': move(-max(value, 1L)); break;
    case 'z': count = 0; Serial.println(F("Counter reset.")); break;
    case 'h': home(); break;
    case 'g':
      if (seq != 2) Serial.println(F("Use 'h' first."));
      else move(posFor(constrain(value, 0L, 100L)) - count);
      break;
    case 'm':
      if (2 * count - POS_MIN > TRAVEL - 4) {
        Serial.println(F("Too high: 100% would hit the end stop."));
        break;
      }
      posMax = 2 * count - POS_MIN;
      printScale();
      break;
    case 'p': Serial.print(F("Position: ")); Serial.println(count); break;
    case 'd':
      if (value > 0) delayMs = value;
      Serial.print(F("Delay: ")); Serial.print(delayMs); Serial.println(F(" ms"));
      break;
    case 's':
      if (value >= 0 && value <= 2) {
        seq = value;
        phase = 0;
        Serial.print(F("Sequence: ")); Serial.println(SEQ_NAME[seq]);
        Serial.println(F("(the first step may jump; re-home before counting)"));
      }
      break;
    case 'v':
      verbose = !verbose;
      Serial.println(verbose ? F("Print every step: on") : F("Print every step: off"));
      break;
    case 'f':
      phase = value % SEQ_LEN[seq];
      applyPhase();
      Serial.print(F("Phase ")); Serial.print(phase);
      Serial.print(F(" energized: A=")); Serial.print(SEQS[seq][phase].a);
      Serial.print(F(" B=")); Serial.print(SEQS[seq][phase].b);
      Serial.println(F("  ('o' to release)"));
      break;
    case 'o': setCoils(0, 0); Serial.println(F("Coils released.")); break;
    case '?': help(); break;
    default: Serial.println(F("Unknown command. '?' for help."));
  }
}
