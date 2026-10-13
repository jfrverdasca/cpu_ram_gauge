// gauge.ino on the computer, against the simulated Arduino in mock/. Each test runs
// in its own process, so it starts from a fresh boot. Run with tests/run.sh.

#include "gauge.cpp"            // the sketch, preprocessed by arduino-cli (run.sh)

#include <vector>

// ---------------------------------------------------------------- harness

static int failures = 0;
#define CHECK(cond)                                                     \
  do {                                                                  \
    if (!(cond)) {                                                      \
      printf("    %s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #cond); \
      failures++;                                                       \
    }                                                                   \
  } while (0)

struct Test { const char* name; void (*fn)(); };
static std::vector<Test>& tests() { static std::vector<Test> t; return t; }
struct Register { Register(const char* n, void (*f)()) { tests().push_back({n, f}); } };
#define TEST(name)                                  \
  static void name();                               \
  static Register register_##name(#name, name);     \
  static void name()

static std::string feed;        // line the "script" sends every 50 ms ("" = none)
static uint64_t nextFeedUs = 0;
static int beeps = 0;           // times the piezo started sounding

// Runs the sketch for ms of simulated time, one loop() every 100 us.
static void run(unsigned long ms) {
  uint64_t end = simUs + ms * 1000ULL;
  while (simUs < end) {
    if (!feed.empty() && simUs >= nextFeedUs) {
      Serial.in += feed + "\n";
      nextFeedUs = simUs + 50000;
    }
    bool sounding = TIMSK2;
    loop();
    if (TIMSK2 && !sounding) beeps++;
    simUs += 100;
  }
}

static void send(const std::string& line) { Serial.in += line + "\n"; }

static bool printed(const std::string& line) {
  return ("\n" + Serial.out).find("\n" + line + "\n") != std::string::npos;
}

static void hold(uint8_t pin, unsigned long ms) {
  pressed[pin] = true;
  run(ms);
  pressed[pin] = false;
  run(100);
}

static void boot() {
  setup();
  run(10);
}

// Where the Pomodoro wants the needle now.
static int pomoPos() { return posFor(100.0 * pomoLeft() / pomoTotalMs()); }

// Booted, with the script sending the needle, and the Pomodoro entered.
static void startPomodoro(const char* minutes) {
  boot();
  feed = "N30";
  send(minutes);
  run(500);
  hold(BUTTON, 1200);
}

// ---------------------------------------------------------------- script

TEST(announces_protocol_2) {
  boot();
  CHECK(printed("READY 2"));
}

TEST(needle_follows_the_script) {
  boot();
  feed = "N50";
  run(2000);
  CHECK(posFor(50) == 127);
  CHECK(pos == 127);
  feed = "N100";
  run(2000);
  CHECK(pos == POS_MAX);
}

TEST(lcd_shows_the_script_rows) {
  boot();
  feed = "N50";
  send("T0Hello");
  send("T1World");
  run(1000);
  CHECK(lcd.text(0) == "Hello");
  CHECK(lcd.text(1) == "World");
}

TEST(no_data_parks_and_says_so) {
  boot();
  feed = "N50";
  run(2000);
  feed = "";
  run(7000);
  CHECK(pos == POS_MIN);
  CHECK(printed("PARKED"));
  CHECK(lcd.text(0) == "No data");
}

TEST(screen_asleep_parks_and_turns_the_lcd_off) {
  boot();
  feed = "N50";
  run(2000);
  feed = "";
  send("B0");
  send("P");
  for (int i = 0; i < 14; i++) {  // gauge.py resends B0 every 5 s while asleep
    run(5000);
    send("B0");
  }
  CHECK(pos == POS_MIN);
  CHECK(!screenOn);
  CHECK(!lcd.on);
  CHECK(pwm[LCD_LIGHT] == 0);
}

// ---------------------------------------------------------------- main button

TEST(short_press_sends_click) {
  boot();
  feed = "N50";
  run(500);
  hold(BUTTON, 300);
  CHECK(printed("CLICK"));
  CHECK(!pomo);
  CHECK(sweep == 0);
}

TEST(no_click_without_the_script) {
  boot();
  run(500);
  hold(BUTTON, 300);
  CHECK(!printed("CLICK"));
}

TEST(press_with_the_lcd_off_only_wakes_it) {
  boot();
  feed = "N50";
  run(500);
  feed = "";
  run(61000);
  CHECK(!screenOn);
  hold(BUTTON, 300);
  run(300);
  CHECK(!printed("CLICK"));
  CHECK(screenOn);
  CHECK(lcd.text(0) == "No data");
  run(10500);
  CHECK(!screenOn);
}

TEST(hold_1s_starts_the_pomodoro) {
  boot();
  feed = "N50";
  run(500);
  pressed[BUTTON] = true;
  run(1300);
  CHECK(lcd.text(0) == "Start pomodoro");
  pressed[BUTTON] = false;
  run(300);
  CHECK(printed("POMODORO ON"));
  CHECK(!printed("CLICK"));
  CHECK(pomo);
  run(1500);                    // the LCD waits for the needle to reach 100%
  CHECK(lcd.text(0).rfind("Focus 1/4  2", 0) == 0);   // "Focus 1/4  25:00" or 24:59
}

TEST(hold_3s_lamp_test_stays_1s_at_100_and_1s_at_0) {
  boot();
  feed = "N50";
  run(500);
  pressed[BUTTON] = true;
  run(1300);
  CHECK(lcd.text(0) == "Start pomodoro");
  run(2000);
  CHECK(lcd.text(0) == "Test");
  pressed[BUTTON] = false;
  run(60);
  CHECK(printed("TEST"));
  CHECK(!pomo);
  run(100);
  CHECK(lcd.text(0) == std::string(16, '\xFF'));   // every pixel on
  CHECK(beeps == 1);
  int ms = 0;
  while (pos != POS_MAX && ms < 3000) {
    run(1);
    ms++;
  }
  CHECK(pos == POS_MAX);
  run(900);
  CHECK(pos == POS_MAX);        // still at the top
  ms = 0;
  while (pos != POS_MIN && ms < 3000) {
    run(1);
    ms++;
  }
  CHECK(pos == POS_MIN);
  run(900);
  CHECK(pos == POS_MIN);        // still at zero
  run(2000);
  CHECK(sweep == 0);
  CHECK(pos == posFor(50));     // back to the script's needle
}

TEST(release_after_forced_homing_is_ignored) {
  pressed[BUTTON] = true;       // held while powering up
  boot();
  run(2500);
  pressed[BUTTON] = false;
  run(500);
  CHECK(!pomo);
  CHECK(sweep == 0);
  CHECK(!printed("POMODORO ON"));
  CHECK(!printed("TEST"));
  hold(BUTTON, 1200);           // the next press works
  CHECK(pomo);
}

// ---------------------------------------------------------------- pomodoro

TEST(pomodoro_needle_runs_from_100_to_0) {
  startPomodoro("M1 1 2 2");
  run(1000);
  CHECK(abs(pos - pomoPos()) <= 1);
  CHECK(pos > posFor(95));
  run(29000);
  CHECK(abs(pos - posFor(50)) <= 3);
  run(31000);
  CHECK(pomoState == OVER);
  CHECK(pos == POS_MIN);
  CHECK(beeps == 3);
  run(1000);
  CHECK(lcd.text(0) == "Break time!");
}

TEST(pomodoro_phases_wait_for_the_button) {
  startPomodoro("M1 1 2 2");
  run(61000);
  CHECK(lcd.text(0) == "Break time!");
  run(10000);
  CHECK(pomoState == OVER);     // waits
  hold(POMODORO_BUTTON, 200);
  run(500);
  CHECK(lcd.text(0).rfind("Break 1/2", 0) == 0);
  run(61000);
  CHECK(lcd.text(0) == "Focus time!");
  hold(POMODORO_BUTTON, 200);
  run(500);
  CHECK(lcd.text(0).rfind("Focus 2/2", 0) == 0);
  run(61000);
  CHECK(lcd.text(0) == "Long break time!");
  hold(POMODORO_BUTTON, 200);
  run(500);
  CHECK(lcd.text(0).rfind("Long break  ", 0) == 0);
  run(121000);
  CHECK(lcd.text(0) == "Focus time!");
  hold(POMODORO_BUTTON, 200);
  run(500);
  CHECK(lcd.text(0).rfind("Focus 1/2", 0) == 0);
}

TEST(pomodoro_button_pauses_and_resumes) {
  startPomodoro("M5 1 1 4");
  run(10000);
  hold(POMODORO_BUTTON, 200);
  CHECK(pomoState == PAUSED);
  unsigned long left = pomoLeft();
  run(20000);
  CHECK(pomoLeft() == left);
  CHECK(lcd.text(1) == "Paused");
  hold(POMODORO_BUTTON, 200);
  CHECK(pomoState == RUNNING);
  run(5000);
  CHECK(pomoLeft() < left);
}

TEST(pomodoro_button_does_nothing_outside_the_pomodoro) {
  boot();
  feed = "N50";
  run(500);
  hold(POMODORO_BUTTON, 200);
  CHECK(!pomo);
  CHECK(!printed("CLICK"));
}

TEST(pomodoro_ignores_clicks_and_the_script) {
  startPomodoro("M1 1 1 4");
  send("T0Hello");
  run(2000);
  hold(BUTTON, 300);
  CHECK(!printed("CLICK"));
  CHECK(abs(pos - pomoPos()) <= 1);   // the Pomodoro's, not the script's 30%
  CHECK(pos > posFor(90));
  CHECK(lcd.text(0).rfind("Focus", 0) == 0);
  send("P");
  run(100);
  CHECK(printed("POMODORO ON"));
}

TEST(hold_1s_leaves_the_pomodoro) {
  startPomodoro("M1 1 1 4");
  run(2000);
  pressed[BUTTON] = true;
  run(1300);
  CHECK(lcd.text(0) == "Exit pomodoro");
  pressed[BUTTON] = false;
  run(300);
  CHECK(printed("POMODORO OFF"));
  CHECK(!pomo);
  run(2000);
  CHECK(pos == posFor(30));     // back to the script's needle
}

TEST(lamp_test_goes_back_to_the_pomodoro) {
  startPomodoro("M5 1 1 4");
  hold(BUTTON, 3200);
  run(6000);
  CHECK(sweep == 0);
  CHECK(pomo);
  CHECK(abs(pos - pomoPos()) <= 1);
}

// ---------------------------------------------------------------- settings

TEST(settings_are_clamped_and_saved) {
  boot();
  send("M0 200 5 0");
  send("S9000 150");
  run(10);
  CHECK(settings.focus == 1 && settings.brk == 99 && settings.longBrk == 5 && settings.cycles == 1);
  CHECK(settings.hz == 5000 && settings.volume == 100);
  Settings saved;
  EEPROM.get(SETTINGS_ADDR, saved);
  CHECK(memcmp(&saved, &settings, sizeof(saved)) == 0);
  send("S500 -5");
  run(10);
  CHECK(settings.hz == 1000 && settings.volume == 0);
}

TEST(volume_is_the_duty_cycle) {
  boot();
  settings.hz = 2000;           // 125 ticks of 4 us
  settings.volume = 100;
  piezoTone(true);
  CHECK(piezoHigh + piezoLow + 2 == 125);
  CHECK(piezoHigh + 1 == 62);   // 50%
  settings.volume = 10;
  piezoTone(true);
  CHECK(piezoHigh + 1 == 6);
  CHECK(piezoHigh + piezoLow + 2 == 125);
  settings.volume = 0;
  piezoTone(true);
  CHECK(TIMSK2 == 0);           // silent
}

// ----------------------------------------------------------------

int main() {
  int failed = 0;
  for (const Test& t : tests()) {
    fflush(stdout);
    pid_t pid = fork();
    if (pid == 0) {
      t.fn();
      fflush(stdout);
      _exit(failures ? 1 : 0);
    }
    int status;
    waitpid(pid, &status, 0);
    bool ok = WIFEXITED(status) && WEXITSTATUS(status) == 0;
    printf("%s %s\n", ok ? "ok  " : "FAIL", t.name);
    failed += !ok;
  }
  printf("\n%zu firmware tests, %d failed\n", tests().size(), failed);
  return failed ? 1 : 0;
}
