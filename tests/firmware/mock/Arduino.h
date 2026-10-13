// Host-side stand-in for the Arduino core, just enough to run gauge.ino on the
// computer: simulated time, pins, serial port, Timer2 registers and PROGMEM.
#pragma once

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <sys/wait.h>
#include <unistd.h>

#define OUTPUT 1
#define INPUT_PULLUP 2
#define LOW 0
#define HIGH 1
#define A0 14
#define A1 15
#define A2 16
#define A3 17

#define constrain(x, lo, hi) ((x) < (lo) ? (lo) : ((x) > (hi) ? (hi) : (x)))
#define max(a, b) ((a) > (b) ? (a) : (b))

// ---------------------------------------------------------------- time

inline uint64_t simUs = 0;
inline unsigned long millis() { return simUs / 1000; }
inline unsigned long micros() { return simUs; }
inline void delay(unsigned long ms) { simUs += ms * 1000; }
inline void delayMicroseconds(unsigned int us) { simUs += us; }

// ---------------------------------------------------------------- pins

inline bool pressed[20];        // buttons to ground: pressed reads LOW
inline uint8_t level[20];
inline int pwm[20];
inline void pinMode(uint8_t, uint8_t) {}
inline void digitalWrite(uint8_t pin, uint8_t v) { level[pin] = v; }
inline int digitalRead(uint8_t pin) { return pressed[pin] ? LOW : HIGH; }
inline void analogWrite(uint8_t pin, int v) { pwm[pin] = v; }

// ---------------------------------------------------------------- Timer2

inline volatile uint8_t TIMSK2, PORTB, TCCR2A, TCCR2B, TCNT2, OCR2A;
#define _BV(bit) (1 << (bit))
#define WGM21 1
#define CS22 2
#define OCIE2A 1
#define ISR(vector) void vector()

// ---------------------------------------------------------------- PROGMEM

class __FlashStringHelper;
#define F(s) (reinterpret_cast<const __FlashStringHelper*>(s))
#define PSTR(s) (s)
#define strcpy_P strcpy
#define strlcpy_P strlcpy

// avr-libc's %S is a string in flash; on the computer it's a plain %s.
inline int snprintf_P(char* buf, size_t n, const char* fmt, ...) {
  std::string f(fmt);
  for (size_t i = f.find("%S"); i != std::string::npos; i = f.find("%S", i)) f[i + 1] = 's';
  va_list args;
  va_start(args, fmt);
  int r = vsnprintf(buf, n, f.c_str(), args);
  va_end(args);
  return r;
}

// ---------------------------------------------------------------- serial

struct SimSerial {
  std::string in, out;          // in: what the PC sent; out: what the sketch printed
  void begin(long) {}
  int available() { return in.size(); }
  int read() {
    if (in.empty()) return -1;
    uint8_t c = in[0];
    in.erase(0, 1);
    return c;
  }
  void print(const char* s) { out += s; }
  void println(const char* s) { out += s; out += "\n"; }
  void print(const __FlashStringHelper* s) { print(reinterpret_cast<const char*>(s)); }
  void println(const __FlashStringHelper* s) { println(reinterpret_cast<const char*>(s)); }
};
inline SimSerial Serial;
