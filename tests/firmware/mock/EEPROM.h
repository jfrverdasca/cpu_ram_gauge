#pragma once
#include "Arduino.h"

struct SimEEPROM {
  uint8_t data[1024];
  SimEEPROM() { memset(data, 0xFF, sizeof(data)); }   // erased
  template <class T> T& get(int addr, T& t) { memcpy(&t, data + addr, sizeof(T)); return t; }
  template <class T> const T& put(int addr, const T& t) { memcpy(data + addr, &t, sizeof(T)); return t; }
  void update(int addr, uint8_t v) { data[addr] = v; }
};
inline SimEEPROM EEPROM;
