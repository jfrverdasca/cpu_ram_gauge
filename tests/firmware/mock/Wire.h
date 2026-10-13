#pragma once
#include "Arduino.h"

// No clock module: every transmission goes unanswered.
struct SimWire {
  void begin() {}
  void setWireTimeout(unsigned long, bool) {}
  void beginTransmission(uint8_t) {}
  size_t write(uint8_t) { return 1; }
  size_t write(const uint8_t*, size_t n) { return n; }
  uint8_t endTransmission() { return 2; }   // address not acknowledged
  uint8_t requestFrom(uint8_t, uint8_t) { return 0; }
  int read() { return 0; }
};
inline SimWire Wire;
