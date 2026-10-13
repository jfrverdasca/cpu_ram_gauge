#!/bin/sh
# Runs the tests on this computer, no Arduino needed: pc/gauge.py against a fake
# serial port, and firmware/gauge/gauge.ino against a simulated Arduino.
set -e
cd "$(dirname "$0")/.."

python3 -m unittest discover -s tests -p "test_*.py"

# arduino-cli adds the function prototypes the Arduino IDE adds; it ships inside
# the IDE on macOS.
CLI=${ARDUINO_CLI:-$(command -v arduino-cli || echo "/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli")}
BUILD=tests/firmware/build
mkdir -p "$BUILD"
# LiquidCrystal, from the IDE's libraries folder (only for the include check: the
# tests use mock/LiquidCrystal.h)
"$CLI" compile --fqbn arduino:avr:nano --libraries "$HOME/Library/Arduino15/libraries" \
  --preprocess firmware/gauge > "$BUILD/gauge.cpp"
c++ -std=c++17 -I tests/firmware/mock -I "$BUILD" -o "$BUILD/test_firmware" tests/firmware/test_firmware.cpp
"$BUILD/test_firmware"
