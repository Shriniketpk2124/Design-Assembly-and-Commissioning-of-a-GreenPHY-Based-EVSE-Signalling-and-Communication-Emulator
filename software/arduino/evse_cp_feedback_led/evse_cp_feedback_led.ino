/*
  EVSE control-pilot controller for Arduino Nano (ATmega328P, 16 MHz)

  Feedback-monitor bench build, September 2026.
  Q3 receiver: pin7 -> Nano D2, pin6 -> Nano D3, referenced to main GND.
  External pull-ups R28/R29 fitted; these pins are INPUT, never OUTPUT.
  Pi owns K1/K2 via GPIO17/27. CONTACTORS=OFF is retained only as a
  legacy protocol field, NOT measured Pi relay-contact feedback.
  Diagnostic LEDs: D4 mirrors sampled D2, D5 mirrors sampled D3,
  D6 is HIGH while feedback has not satisfied the 20 ms settling test.
  LEDs show sampled feedback only; fast transitions may not be visible.
  Power and isolated-rail configuration must match the validated bench setup.
  This monitor does not authorize charging or implement vehicle interlocks.

  PCB signal path:
    Arduino D9 -> R12 (330 ohm) -> U3 (VO2611) input LED -> main GND

  IMPORTANT POLARITY:
    U3 inverts D9. Therefore:
      D9 LOW       -> eventual CP +12 V (State A command)
      D9 HIGH      -> eventual CP -12 V (State F/disabled command)
      CP duty 5 %  -> D9 is HIGH for 95 % and LOW for 5 %

  Serial Monitor settings: 115200 baud, line ending "Newline".

  Commands:
    HELP
    STATUS
    A                 steady positive CP command (eventual +12 V)
    HLC               1 kHz, 5.0 % positive CP duty
    PWM <duty>        test/AC PWM: 5.0 or 10.0...96.0 percent CP duty
    SESSION START     same as HLC
    SESSION STOP      contactors off, return to State A command
    DISABLE           contactors off, steady negative command
    FAULT <text>      latch fault, contactors off, steady negative command
    RESET             clear fault and return to State A command
    REMOTE ON         enable 3 s host-heartbeat supervision
    REMOTE OFF        disable host-heartbeat supervision
    HB                host heartbeat
    PING <text>       replies ACK PING <text>

  State B/C/D/E are measured EV responses, not voltages generated directly
  by the Arduino. The EV resistor network pulls the positive CP peak from
  +12 V to approximately +9/+6/+3/0 V. Automatic feedback decoding will be
  assigned only after comparator thresholds and polarity are measured.
  STATUS appends FB_D2/FB_D3 (raw), FB_PAIR (settled D2,D3),
  FB_STABLE and FB_CHANGES. No unsolicited messages are emitted.
  A stable bit pair is NOT proof of a valid CP voltage or connected EV.
*/

#include <Arduino.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#if F_CPU != 16000000UL
#error "This Timer1 configuration requires a 16 MHz Arduino Nano."
#endif

namespace Hardware {
constexpr uint8_t CP_PWM_PIN = 9;       // OC1A
constexpr uint8_t FEEDBACK_D2_PIN = 2;
constexpr uint8_t FEEDBACK_D3_PIN = 3;
constexpr uint8_t INDICATOR_D4_PIN = 4;
constexpr uint8_t INDICATOR_D5_PIN = 5;
constexpr uint8_t INDICATOR_D6_PIN = 6;
}

namespace Timing {
constexpr uint16_t TIMER1_TOP = 1999;  // 16 MHz / 8 / (1999 + 1) = 1 kHz
constexpr uint32_t HEARTBEAT_TIMEOUT_MS = 3000;
}

enum class CpMode : uint8_t {
  STATE_A_POSITIVE,
  PWM_ACTIVE,
  STATE_F_NEGATIVE,
  FAULT_LATCHED
};

CpMode cpMode = CpMode::STATE_A_POSITIVE;
uint16_t cpDutyTenthPercent = 1000;  // 100.0 % positive for steady State A
bool faultLatched = false;
bool remoteSupervision = false;
uint32_t lastHeartbeatMs = 0;
char faultReason[33] = "NONE";

char commandBuffer[81];
uint8_t commandLength = 0;

void holdIndicatorsOff() {
  digitalWrite(Hardware::INDICATOR_D4_PIN, LOW);
  digitalWrite(Hardware::INDICATOR_D5_PIN, LOW);
  digitalWrite(Hardware::INDICATOR_D6_PIN, LOW);
}

void disconnectTimer1Output() {
  TCCR1A &= static_cast<uint8_t>(~(_BV(COM1A1) | _BV(COM1A0)));
}

// U3 is inverting, so D9 LOW eventually commands positive CP.
void commandSteadyPositive() {
  const uint8_t savedSreg = SREG;
  cli();
  disconnectTimer1Output();
  digitalWrite(Hardware::CP_PWM_PIN, LOW);
  TCNT1 = 0;
  SREG = savedSreg;

  cpMode = CpMode::STATE_A_POSITIVE;
  cpDutyTenthPercent = 1000;
}

// U3 is inverting, so D9 HIGH eventually commands negative CP.
void commandSteadyNegative(bool dueToFault) {
  const uint8_t savedSreg = SREG;
  cli();
  disconnectTimer1Output();
  digitalWrite(Hardware::CP_PWM_PIN, HIGH);
  TCNT1 = 0;
  SREG = savedSreg;

  cpMode = dueToFault ? CpMode::FAULT_LATCHED : CpMode::STATE_F_NEGATIVE;
  cpDutyTenthPercent = 0;
}

bool dutyIsAllowed(uint16_t dutyTenthPercent) {
  // 5.0 % is the HLC request. Conventional PWM is restricted here to
  // 10.0...96.0 %. Values between these regions are intentionally rejected.
  return dutyTenthPercent == 50 ||
         (dutyTenthPercent >= 100 && dutyTenthPercent <= 960);
}

void commandCpPwm(uint16_t positiveDutyTenthPercent) {
  // CP duty describes the eventual +12 V duration. Since U3 inverts D9,
  // the Arduino HIGH fraction is the complement of the requested CP duty.
  const uint32_t positiveCounts =
      (static_cast<uint32_t>(Timing::TIMER1_TOP + 1U) *
       positiveDutyTenthPercent + 500U) /
      1000U;
  const uint16_t d9HighCounts =
      static_cast<uint16_t>((Timing::TIMER1_TOP + 1U) - positiveCounts);

  const uint8_t savedSreg = SREG;
  cli();

  // Fast PWM, mode 14: TOP = ICR1, non-inverting OC1A, prescaler = 8.
  // At 5.0 % CP duty, OCR1A = 1900: D9 is HIGH for about 950 us and
  // LOW for about 50 us. U3 later converts this to +12 V for 50 us.
  TCCR1A = 0;
  TCCR1B = 0;
  TCNT1 = 0;
  ICR1 = Timing::TIMER1_TOP;
  OCR1A = d9HighCounts;
  TCCR1A = _BV(COM1A1) | _BV(WGM11);
  TCCR1B = _BV(WGM13) | _BV(WGM12) | _BV(CS11);

  SREG = savedSreg;

  cpMode = CpMode::PWM_ACTIVE;
  cpDutyTenthPercent = positiveDutyTenthPercent;
}

void copyFaultReason(const char *reason) {
  if (reason == nullptr || *reason == '\0') {
    reason = "UNSPECIFIED";
  }
  strncpy(faultReason, reason, sizeof(faultReason) - 1U);
  faultReason[sizeof(faultReason) - 1U] = '\0';
}

void latchFault(const char *reason) {
  faultLatched = true;
  copyFaultReason(reason);
  commandSteadyNegative(true);
  Serial.print(F("FAULT LATCHED: "));
  Serial.println(faultReason);
}

void clearFault() {
  faultLatched = false;
  copyFaultReason("NONE");
  remoteSupervision = false;
  commandSteadyPositive();
  Serial.println(F("ACK RESET; CP=A; CONTACTORS=OFF; REMOTE=OFF"));
}

const __FlashStringHelper *modeName() {
  switch (cpMode) {
    case CpMode::STATE_A_POSITIVE:
      return F("A_POSITIVE_DC");
    case CpMode::PWM_ACTIVE:
      return F("PWM");
    case CpMode::STATE_F_NEGATIVE:
      return F("F_NEGATIVE_DC");
    case CpMode::FAULT_LATCHED:
      return F("FAULT_NEGATIVE_DC");
  }
  return F("UNKNOWN");
}

void printDuty(uint16_t tenthPercent) {
  Serial.print(tenthPercent / 10U);
  Serial.print('.');
  Serial.print(tenthPercent % 10U);
}

// Commissioning filter only: two digital levels, not CP state decoding.
// This software cannot distinguish lost isolated power from some low-envelope
// conditions. Do not use FB_PAIR as a charging permission.
constexpr uint32_t FEEDBACK_SETTLE_MS = 20;
uint8_t feedbackRaw = 0;
uint8_t feedbackCandidate = 0;
uint8_t feedbackSettled = 0;
bool feedbackInitialized = false;
bool feedbackHasSettled = false;
uint32_t feedbackCandidateSince = 0;
uint32_t feedbackChanges = 0;

bool feedbackIsStable(uint32_t now) {
  return feedbackHasSettled && feedbackRaw == feedbackSettled &&
      static_cast<uint32_t>(now - feedbackCandidateSince) >= FEEDBACK_SETTLE_MS;
}

void serviceFeedback() {
  const uint32_t now = millis();
  feedbackRaw = (digitalRead(Hardware::FEEDBACK_D2_PIN) == HIGH ? 2U : 0U) |
                (digitalRead(Hardware::FEEDBACK_D3_PIN) == HIGH ? 1U : 0U);
  if (!feedbackInitialized || feedbackRaw != feedbackCandidate) {
    feedbackInitialized = true;
    feedbackCandidate = feedbackRaw;
    feedbackCandidateSince = now;
  }
  if (static_cast<uint32_t>(now - feedbackCandidateSince) >= FEEDBACK_SETTLE_MS &&
      (!feedbackHasSettled || feedbackSettled != feedbackCandidate)) {
    if (feedbackHasSettled) {
      ++feedbackChanges;
    }
    feedbackSettled = feedbackCandidate;
    feedbackHasSettled = true;
  }
  // Nonblocking diagnostics. Do not assign vehicle-state meanings to these LEDs.
  digitalWrite(Hardware::INDICATOR_D4_PIN, (feedbackRaw & 2U) ? HIGH : LOW);
  digitalWrite(Hardware::INDICATOR_D5_PIN, (feedbackRaw & 1U) ? HIGH : LOW);
  digitalWrite(Hardware::INDICATOR_D6_PIN, feedbackIsStable(now) ? LOW : HIGH);
}

void printFeedbackFields() {
  serviceFeedback();
  Serial.print(F(" FB_D2="));
  Serial.print((feedbackRaw & 2U) ? 1 : 0);
  Serial.print(F(" FB_D3="));
  Serial.print((feedbackRaw & 1U) ? 1 : 0);
  Serial.print(F(" FB_PAIR="));
  if (feedbackHasSettled) {
    Serial.print((feedbackSettled & 2U) ? 1 : 0);
    Serial.print((feedbackSettled & 1U) ? 1 : 0);
  } else {
    Serial.print(F("UNSET"));
  }
  Serial.print(F(" FB_STABLE="));
  const bool stable = feedbackIsStable(millis());
  Serial.print(stable ? F("YES") : F("NO"));
  Serial.print(F(" FB_CHANGES="));
  Serial.print(feedbackChanges);
}

void printStatus() {
  Serial.print(F("STATUS CP_MODE="));
  Serial.print(modeName());
  Serial.print(F(" CP_POSITIVE_DUTY="));
  printDuty(cpDutyTenthPercent);
  Serial.print(F("% D9_LOGIC_HIGH="));
  printDuty(static_cast<uint16_t>(1000U - cpDutyTenthPercent));
  Serial.print(F("% FAULT="));
  Serial.print(faultLatched ? F("YES") : F("NO"));
  Serial.print(F(" REASON="));
  Serial.print(faultReason);
  Serial.print(F(" CONTACTORS=OFF REMOTE="));
  Serial.print(remoteSupervision ? F("ON") : F("OFF"));
  printFeedbackFields();
  Serial.println();
}

void printHelp() {
  Serial.println(F("Commands: HELP, STATUS, A, HLC, PWM <5.0|10.0..96.0>,"));
  Serial.println(F("SESSION START, SESSION STOP, DISABLE, FAULT <text>, RESET,"));
  Serial.println(F("REMOTE ON, REMOTE OFF, HB, PING <text>"));
}

bool parseDutyTenthPercent(const char *text, uint16_t &result) {
  if (text == nullptr || *text == '\0') {
    return false;
  }

  // Parse directly as tenths of a percent instead of using strtof().
  // Some older Arduino AVR toolchains do not expose strtof(), and a fixed-
  // point value is sufficient for the 0.1 % resolution used by this sketch.
  while (*text == ' ') {
    ++text;
  }

  if (!isdigit(static_cast<unsigned char>(*text))) {
    return false;
  }

  uint16_t wholePercent = 0;
  while (isdigit(static_cast<unsigned char>(*text))) {
    wholePercent = static_cast<uint16_t>(wholePercent * 10U +
                                         static_cast<uint16_t>(*text - '0'));
    if (wholePercent > 100U) {
      return false;
    }
    ++text;
  }

  uint8_t tenthPercent = 0;
  if (*text == '.') {
    ++text;
    if (!isdigit(static_cast<unsigned char>(*text))) {
      return false;
    }

    tenthPercent = static_cast<uint8_t>(*text - '0');
    ++text;

    // Accept harmless extra zeroes (for example, 5.00), but reject values
    // requiring finer resolution (for example, 5.05).
    while (isdigit(static_cast<unsigned char>(*text))) {
      if (*text != '0') {
        return false;
      }
      ++text;
    }
  }

  while (*text == ' ') {
    ++text;
  }
  if (*text != '\0' || (wholePercent == 100U && tenthPercent != 0U)) {
    return false;
  }

  result = static_cast<uint16_t>(wholePercent * 10U + tenthPercent);
  return true;
}

void uppercaseCommand(char *text) {
  for (; *text != '\0'; ++text) {
    *text = static_cast<char>(toupper(static_cast<unsigned char>(*text)));
  }
}

void processCommand(char *line) {
  while (*line == ' ') {
    ++line;
  }
  if (*line == '\0') {
    return;
  }

  uppercaseCommand(line);
  lastHeartbeatMs = millis();

  char *argument = strchr(line, ' ');
  if (argument != nullptr) {
    *argument++ = '\0';
    while (*argument == ' ') {
      ++argument;
    }
  }

  // Diagnostic commands remain available while a fault is latched.
  if (strcmp(line, "PING") == 0) {
    Serial.print(F("ACK PING"));
    if (argument != nullptr && *argument != '\0') {
      Serial.print(' ');
      Serial.print(argument);
    }
    Serial.println();
    return;
  }
  if (strcmp(line, "HELP") == 0) {
    printHelp();
    return;
  }
  if (strcmp(line, "STATUS") == 0) {
    printStatus();
    return;
  }
  if (strcmp(line, "RESET") == 0) {
    clearFault();
    return;
  }
  if (strcmp(line, "FAULT") == 0) {
    latchFault(argument);
    return;
  }

  if (faultLatched) {
    Serial.println(F("ERR FAULT_LATCHED; use STATUS or RESET"));
    return;
  }

  if (strcmp(line, "A") == 0) {
    commandSteadyPositive();
    Serial.println(F("ACK A; eventual CP=+12V DC; D9=LOW"));
    return;
  }

  if (strcmp(line, "HLC") == 0) {
    commandCpPwm(50);
    Serial.println(F("ACK HLC; CP=1kHz/5.0%; D9=95.0% HIGH (inverted)"));
    return;
  }

  if (strcmp(line, "PWM") == 0) {
    uint16_t requestedDuty = 0;
    if (!parseDutyTenthPercent(argument, requestedDuty)) {
      Serial.println(F("ERR PWM syntax: PWM <5.0|10.0..96.0>"));
      return;
    }
    if (!dutyIsAllowed(requestedDuty)) {
      Serial.println(F("ERR PWM allowed: 5.0 or 10.0..96.0 percent CP duty"));
      return;
    }
    commandCpPwm(requestedDuty);
    Serial.print(F("ACK PWM CP_DUTY="));
    printDuty(requestedDuty);
    Serial.print(F("% D9_HIGH="));
    printDuty(static_cast<uint16_t>(1000U - requestedDuty));
    Serial.println('%');
    return;
  }

  if (strcmp(line, "SESSION") == 0) {
    if (argument != nullptr && strcmp(argument, "START") == 0) {
      commandCpPwm(50);
      Serial.println(F("ACK SESSION START; HLC 5.0%; CONTACTORS=OFF"));
      return;
    }
    if (argument != nullptr && strcmp(argument, "STOP") == 0) {
      commandSteadyPositive();
      remoteSupervision = false;
      Serial.println(F("ACK SESSION STOP; CP=A; CONTACTORS=OFF; REMOTE=OFF"));
      return;
    }
    Serial.println(F("ERR SESSION syntax: SESSION START|STOP"));
    return;
  }

  if (strcmp(line, "DISABLE") == 0) {
    commandSteadyNegative(false);
    remoteSupervision = false;
    Serial.println(F("ACK DISABLE; eventual CP=-12V DC; D9=HIGH"));
    return;
  }

  if (strcmp(line, "REMOTE") == 0) {
    if (argument != nullptr && strcmp(argument, "ON") == 0) {
      remoteSupervision = true;
      lastHeartbeatMs = millis();
      Serial.println(F("ACK REMOTE ON; heartbeat timeout=3000ms"));
      return;
    }
    if (argument != nullptr && strcmp(argument, "OFF") == 0) {
      remoteSupervision = false;
      Serial.println(F("ACK REMOTE OFF"));
      return;
    }
    Serial.println(F("ERR REMOTE syntax: REMOTE ON|OFF"));
    return;
  }

  if (strcmp(line, "HB") == 0) {
    lastHeartbeatMs = millis();
    Serial.println(F("ACK HB"));
    return;
  }

  Serial.println(F("ERR UNKNOWN_COMMAND; use HELP"));
}

void serviceSerial() {
  while (Serial.available() > 0) {
    const char incoming = static_cast<char>(Serial.read());

    if (incoming == '\r') {
      continue;
    }

    if (incoming == '\n') {
      commandBuffer[commandLength] = '\0';
      processCommand(commandBuffer);
      commandLength = 0;
      continue;
    }

    if (commandLength < sizeof(commandBuffer) - 1U) {
      commandBuffer[commandLength++] = incoming;
    } else {
      commandLength = 0;
      Serial.println(F("ERR COMMAND_TOO_LONG"));
    }
  }
}

void serviceHeartbeat() {
  if (!remoteSupervision || faultLatched) {
    return;
  }

  if (static_cast<uint32_t>(millis() - lastHeartbeatMs) >
      Timing::HEARTBEAT_TIMEOUT_MS) {
    remoteSupervision = false;
    latchFault("HEARTBEAT_TIMEOUT");
  }
}

void setup() {
  // Establish hardware-safe outputs before starting communication.
  pinMode(Hardware::CP_PWM_PIN, OUTPUT);
  pinMode(Hardware::INDICATOR_D4_PIN, OUTPUT);
  pinMode(Hardware::INDICATOR_D5_PIN, OUTPUT);
  pinMode(Hardware::INDICATOR_D6_PIN, OUTPUT);
  pinMode(Hardware::FEEDBACK_D2_PIN, INPUT);
  pinMode(Hardware::FEEDBACK_D3_PIN, INPUT);
  holdIndicatorsOff();
  serviceFeedback();
  commandSteadyPositive();

  Serial.begin(115200);
  delay(50);
  Serial.println(F("EVSE CP controller ready"));
  Serial.println(F("Startup: CP=A (+12V command), D9=LOW, CONTACTORS=OFF"));
  Serial.println(F("Bench feedback monitor: Q3 D2/D3; Pi owns K1/K2; no automatic EV interlocks"));
  Serial.println(F("LED diagnostics v1: D4=raw D2 HIGH; D5=raw D3 HIGH; D6=unsettled"));
  printHelp();
  printStatus();
}

void loop() {
  serviceFeedback();
  serviceSerial();
  serviceHeartbeat();
}
