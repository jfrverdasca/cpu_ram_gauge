#pragma once
#include "Arduino.h"

// Keeps what a 16x2 LCD would show.
class LiquidCrystal {
 public:
  char rows[2][17];
  bool on = true;               // display() / noDisplay()

  LiquidCrystal(uint8_t, uint8_t, uint8_t, uint8_t, uint8_t, uint8_t) {
    memset(rows, ' ', sizeof(rows));
    rows[0][16] = rows[1][16] = '\0';
  }
  void begin(int, int) {}
  void setCursor(int c, int r) { col = c; row = r; }
  void print(const char* s) {
    while (*s && col < 16) rows[row][col++] = *s++;
  }
  void print(const __FlashStringHelper* s) { print(reinterpret_cast<const char*>(s)); }
  void createChar(uint8_t, uint8_t*) {}
  void display() { on = true; }
  void noDisplay() { on = false; }

  // A row without the trailing spaces.
  std::string text(int r) const {
    std::string t(rows[r]);
    return t.erase(t.find_last_not_of(' ') + 1);
  }

 private:
  int col = 0, row = 0;
};
