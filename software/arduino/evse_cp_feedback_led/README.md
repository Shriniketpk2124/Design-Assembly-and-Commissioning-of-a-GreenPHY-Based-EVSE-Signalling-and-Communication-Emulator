\# Arduino Control-Pilot Controller



This Arduino Nano firmware generates the control-pilot command waveform and

monitors two digital comparator-feedback channels for the Green PHY EVSE

signalling and communication emulator.



\## Target



\- Arduino Nano

\- ATmega328P

\- 16 MHz

\- Arduino AVR core

\- No additional Arduino libraries required



\## Build verification



Verified with Arduino IDE on 2026-10-04.



\- Program storage: 6,102 of 30,720 bytes (19%)

\- Global variables: 448 of 2,048 bytes (21%)

\- Remaining dynamic memory: 1,600 bytes



\## Pin assignment



| Nano pin | Function |

|---|---|

| D9 | Timer1 control-pilot output through U3 |

| D2 | Comparator feedback input |

| D3 | Comparator feedback input |

| D4 | D2 diagnostic indication |

| D5 | D3 diagnostic indication |

| D6 | Feedback-settling diagnostic indication |



D2 and D3 require the external pull-ups provided by the validated hardware and

are always configured as inputs.



\## Serial interface



\- Baud rate: 115200

\- Line ending: newline

\- Maximum command length: 80 characters



Supported commands include:



\- `HELP`

\- `STATUS`

\- `A`

\- `HLC`

\- `PWM <duty>`

\- `SESSION START`

\- `SESSION STOP`

\- `DISABLE`

\- `FAULT <text>`

\- `RESET`

\- `REMOTE ON`

\- `REMOTE OFF`

\- `HB`

\- `PING <text>`



\## Operational behavior



U3 inverts the Arduino D9 output. A 5% positive control-pilot duty therefore

requires D9 to remain high for approximately 95% of the period.



Remote supervision uses a three-second timeout. Any received nonempty command

refreshes the host-activity timestamp, including `HB`.



\## Safety scope



The firmware does not control the Raspberry Pi relay outputs and does not

measure contactor feedback. `CONTACTORS=OFF` is retained only as a serial

protocol status field.



Comparator bit pairs and diagnostic LEDs are not proof of a valid control-pilot

voltage, connected vehicle or permission to transfer energy.



This firmware belongs to a communication-only research prototype and must not

be used as a certified EVSE controller or high-voltage safety function.
