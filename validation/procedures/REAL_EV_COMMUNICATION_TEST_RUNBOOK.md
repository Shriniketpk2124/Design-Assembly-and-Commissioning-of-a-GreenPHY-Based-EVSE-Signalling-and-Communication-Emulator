# Real-EV communication test runbook

This is the validated hardware-specific procedure for the GreenPHY EVSE
signalling and communication emulator. The tested scripts retain the internal
name **Monday**.

> [!CAUTION]
> This procedure authorizes CP, PLC, SLAC and ISO 15118 communication only.
> It does not authorize an HV/DC source, physical pre-charge stage, insulation
> monitor, DC output contactor or energy transfer. Keep every HV/DC source
> physically disconnected and disabled.

## 1. Required configuration

Before using this runbook:

- complete the PCB assembly inspection;
- obtain clean ERC and DRC results for the committed KiCad revision;
- compile and upload the committed Arduino sketch;
- verify the State-A, State-B and 5% HLC feedback paths with bench loads;
- install and test the Raspberry Pi software;
- configure the correct serial device, GPIOs and network interface;
- configure exactly one EVSE-side SLAC owner: the `plc-utils` `evse` process
  started by `monday_ops.sh`;
- create the local environment, operations and SLAC configuration files;
- confirm that no raw run directory or local secret will be added to Git.

The final PCB vehicle-interface mapping is:

| Terminal | Function |
| --- | --- |
| `J2.1` | `EVSE_CP_OUT` to charging-cable CP |
| `J2.2` | `EVSE_PP_OUT` to the documented PP connection, when used |
| `J2.3` | `ISO_GND` to charging-cable PE/reference |
| `J2.4` | Not connected |

Confirm the cable pinout independently. Do not infer connector orientation from
wire colour.

## 2. Select the protocol boundary

The local `.env.monday-real-ev` file contains:

```text
MONDAY_PRECHARGE_PROBE=0
```

Use `0` for the normal fail-safe mode. In that mode, reaching CableCheck and
receiving an automatic external STOP is a successful communication result.

The final thesis experiment used `MONDAY_PRECHARGE_PROBE=1` to observe the
vehicle's first `PreChargeReq`. That option emulates CableCheck readiness only
at the protocol layer. It performs no isolation measurement, closes no physical
DC contactor and produces no output voltage. The first requested charging
target is rejected and triggers a latched STOP.

Enabling the probe is permitted only on the documented communication-only rig
with every HV/DC source absent. Record the chosen value in the lab notes before
the test.

## 3. Physical configuration with all power removed

1. Remove the State-B and State-C breadboard loads. The real EV supplies the CP
   load.
2. Leave PCB `JP2` open so the designed PLC coupling capacitors remain in the
   circuit.
3. Connect the Devolo coupling pair to PCB `J1.1` (PLC signal) and `J1.2`
   (`ISO_GND`/return). Confirm the Devolo terminal orientation from its
   documentation or a verified continuity check.
4. Connect PCB `J2.1` (`EVSE_CP_OUT`) to the cable CP conductor.
5. Connect PCB `J2.2` (`EVSE_PP_OUT`) only according to the documented PP
   arrangement for the test cable.
6. Connect PCB `J2.3` (`ISO_GND`) to the cable PE/reference conductor.
7. Keep the Raspberry-Pi-to-Devolo Ethernet connection installed. It is
   independent of the two-wire PLC coupling pair.
8. Add strain relief and inspect for loose strands or unintended shorts.
9. Confirm that every HV/DC source is absent and disabled.

Recommended unpowered checks include:

- continuity from PCB `J1.2` to `J2.3`;
- no hard short from `J1.1` to `J1.2`;
- no hard short from `J2.1` to `J2.3`;
- the cable CP, PP and PE conductors reach the intended J2 terminals.

With `JP2` open, do not expect DC continuity through the series PLC coupling
capacitors. Keep the J3-to-J1 coupling pair short and twisted.

## 4. Start the low-voltage test environment

Power only the PCB low-voltage electronics, Raspberry Pi, Arduino and Devolo
modem. Connect the operator laptop to the Pi management network.

In one SSH session, run:

```bash
ssh <pi-user>@<pi-host>

set -a
. /path/to/monday-ops.env
set +a

/path/to/monday_ops.sh test-day
```

`test-day` performs the software/network preflight, creates a timestamped
evidence directory, starts packet capture, Redis and the SECC, waits for UDP
15118 and opens the four-pane cockpit.

If preflight fails, keep the EV disconnected. Correct the reported problem and
run the preflight again.

| Pane | Function |
| --- | --- |
| Large left | Interactive console and sole serial/GPIO owner |
| Upper right | Decoded CP state and freshness monitor |
| Middle right | Sole `plc-utils` EVSE SLAC owner |
| Lower right | Live SECC application log |

Select a pane by clicking it or with `Ctrl+b` followed by an arrow key. Use
`Ctrl+b`, then `z`, to zoom or restore a pane. Use `Ctrl+b`, then uppercase
`M`, for the guarded operator menu.

If SSH disconnects, reconnect and run:

```bash
/path/to/monday_ops.sh cockpit
```

## 5. Establish State A

At the `bench>` prompt, enter one command at a time:

```text
reset
remote on
a
status
```

Before connecting the EV, require:

```text
CP_MODE=A_POSITIVE_DC
REMOTE=ON
FB_PAIR=00
FB_STABLE=YES
Pi relay command: ON
STOP latch: OFF
```

The CP monitor must repeatedly show usable `A1`. Do not enter `HLC` yet.

The SLAC pane must show a successful `CM_SET_KEY` result and then the
unoccupied/listening state. A repeating `CM_SLAC_PARAM.REQ ?` line means the
listener is waiting; it does not prove that an EV has transmitted a request.

## 6. Connect the EV and verify State B

1. Keep the console in State A and the SLAC listener running.
2. Connect the charging connector to the EV.
3. Wait for the CP state to become stable.
4. Require both views to agree before starting HLC.

Console:

```text
FB_PAIR=10 FB_STABLE=YES
```

CP monitor:

```text
CP=B1 usable=True
```

If B1 is not stable, use the operator menu to save a snapshot and request
fail-safe STOP. Do not change wiring while powered.

## 7. Start HLC once

Only after stable B1, enter:

```text
hlc
status
```

Require:

```text
CP_MODE=PWM
CP_POSITIVE_DUTY=5.0%
REMOTE=ON
FB_PAIR=10
FB_STABLE=YES
```

and:

```text
CP=B2 usable=True
```

The validated feedback mapping may later change to `FB_PAIR=11`, decoded C2,
when the vehicle requests the ready-to-charge CP state. This is a normal
vehicle-controlled transition; it is not the initial State-B acceptance check.

Observe the evidence in this order:

1. HomePlug Green PHY/SLAC frames (`0x88e1`);
2. a remote PLC station;
3. IPv6 and SDP;
4. TCP connection;
5. SupportedAppProtocol negotiation;
6. ISO 15118 application messages;
7. external STOP at the selected protocol boundary.

Expected boundary by mode:

| Probe value | Expected result |
| --- | --- |
| `0` | `No_IMD`/`EVSE_NotReady`; STOP when CableCheck cannot be completed |
| `1` | Test-only CableCheck status; first PreCharge target blocked; latched STOP |

The validated probe session received a target of 303.2 V and 1.0 A, blocked it,
returned a failed PreCharge response and restored the host to the safe state.
Those values are protocol observations, not emulator output ratings.

## 8. Stop conditions

Request STOP immediately for any of the following:

- stale, unstable or `UNKNOWN` CP feedback;
- disagreement between physical indications and console state;
- unexpected relay behaviour;
- a second serial/GPIO owner or second SLAC owner;
- missing capture or SECC logging;
- loose wiring, abnormal heat, odor, smoke or arcing;
- any connected HV/DC source;
- progression beyond the selected test boundary without the expected STOP.

If the console cannot confirm relay OFF within eight seconds, remove PCB
low-voltage power.

## 9. Normal shutdown

At the console:

```text
session stop
status
exit
```

Confirm `Pi relay command: OFF`. Open the operator menu and select final
shutdown, or run the shutdown command from a separate management shell if the
validated procedure requires it:

```bash
/path/to/monday_ops.sh shutdown
```

The shutdown procedure requests or verifies the safe CP state, stops capture,
collects state and logs, stops services and creates `SHA256SUMS`.

Display the evidence paths with:

```bash
/path/to/monday_ops.sh paths
```

Copy the complete run folder to private storage before another attempt. Record
the probe value, EV model, visible vehicle message, operator observations and
connection/disconnection times in the laboratory notes.

Do not commit the raw run folder. It can contain vehicle identifiers, MAC
addresses, PLC keys, host information and packet payloads.

## 10. Optional final bench rehearsal

This rehearsal checks the integrated cockpit against the previously verified
State-B load. It is not a substitute for component qualification.

1. Complete shutdown and remove all low-voltage power.
2. Disconnect the EV and the Devolo coupling pair.
3. Connect only the verified State-B bench load between `J2.1` and `J2.3`.
4. Attach the oscilloscope using the validated measurement points.
5. Start `monday_ops.sh test-day` and establish State A.
6. Require stable `FB_PAIR=10`, decoded B1 and the expected State-B plateau.
7. Enter HLC once and require 1 kHz/5%, stable `FB_PAIR=10` and decoded B2.
8. Save a snapshot and perform the confirmed safe shutdown.
9. Remove power before changing the bench load or restoring the real-EV wiring.
