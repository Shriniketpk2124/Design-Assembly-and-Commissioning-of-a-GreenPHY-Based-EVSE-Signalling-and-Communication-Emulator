# Design, Assembly and Commissioning of a GreenPHY-Based EVSE Signalling and Communication Emulator

This repository documents an engineering prototype for EVSE-side control-pilot
(CP) signalling, HomePlug Green PHY communication and ISO 15118 message
observation with a real electric vehicle.

The project combines a custom mixed-signal PCB, an Arduino Nano, a Raspberry
Pi, a Devolo Green PHY modem and a hardware-adapted EcoG `iso15118` SECC.

> [!CAUTION]
> This is a laboratory communication emulator, not a charger. It has no
> authorized high-voltage DC source, verified insulation-monitoring system,
> pre-charge power stage or energy-transfer path. Never connect an HV/DC source
> or use this project to charge a vehicle.

## Demonstrated scope

The validated setup demonstrated:

- IEC 61851 CP generation and feedback monitoring;
- 1 kHz CP PWM, including the 5% HLC duty cycle;
- EV detection through the CP resistor states;
- EVSE-side SLAC using the `plc-utils` `evse` process;
- IPv6 link-local communication and SDP;
- ISO 15118-2 application-message exchange with external payment and no TLS;
- controlled termination before any physical energy transfer.

The final communication experiment used an explicitly enabled, protocol-only
PreCharge probe. It emulated the protocol status needed to pass CableCheck,
reached `PreChargeReq`, rejected the requested voltage/current command and
latched an external STOP. No DC output was produced.

## Repository layout

| Path | Contents |
| --- | --- |
| `hardware/kicad/PWM_Schematic/` | KiCad schematic, PCB, project-local symbols and footprints, and ERC/DRC reports |
| `hardware/bom/` | Bill of materials exported from the final schematic |
| `software/arduino/` | Arduino Nano CP controller and feedback-monitor firmware |
| `software/raspberry-pi/` | Raspberry Pi console, cockpit, test-day and evidence-collection tools |
| `software/josev-modifications/` | Hardware-controller additions for the EcoG ISO 15118 SECC |
| `software/configuration/` | Public configuration templates without local identifiers or secrets |
| `validation/procedures/` | Bench and real-EV validation procedures |
| `validation/selected-results/` | Sanitized results selected for publication |
| `docs/` | Architecture and setup documentation |
| `thesis/` | Publication status only; the thesis report is not distributed here |

Raw packet captures, complete run folders, local environment files, vehicle
identifiers and PLC network keys are intentionally excluded.

## System overview

1. The Arduino generates the CP waveform and publishes comparator feedback over
   a serial connection.
2. The Raspberry Pi console is the sole owner of the Arduino serial connection
   and the two CP-path relay GPIOs.
3. The console writes an atomic state snapshot under `/run/evse-monday`.
4. The modified SECC reads that snapshot instead of controlling serial or GPIO
   hardware from inside the container.
5. The `plc-utils` `evse` process is the sole EVSE-side SLAC owner.
6. The Devolo modem carries HomePlug Green PHY traffic between the PCB coupling
   network and the vehicle.
7. The operations script records packet captures, application logs, controller
   state and checksums for each test attempt.

The word **Monday** remains in several tested script, class and configuration
names. It was the internal name of the final real-EV test workflow; it is not a
separate product or protocol.

## Hardware interface

The final KiCad PCB assigns the four-position vehicle-interface terminal as:

| Terminal | Net |
| --- | --- |
| `J2.1` | `EVSE_CP_OUT` |
| `J2.2` | `EVSE_PP_OUT` |
| `J2.3` | `ISO_GND` |
| `J2.4` | Not connected |

Verify these assignments against the supplied schematic and PCB before making
any physical connection. Wiring changes must be performed with all power
removed.

## Software provenance

The Josev modifications were developed against:

- upstream repository: `https://github.com/EcoG-io/iso15118.git`
- recorded base commit: `76bf85be572d16adbcd009a985d605622c3b7227`

The repository contains the project-specific additions and operational files,
not a claim of ownership over the upstream project. Retain all upstream license
and attribution notices when constructing a working tree.

## Replication order

This project is not a one-command charger build. A new user should proceed in
the following order:

1. Read this README and the safety limits.
2. Open `hardware/kicad/PWM_Schematic/PWM_Schematic.kicad_pro` and inspect the
   schematic, PCB, connector assignments and ERC/DRC reports.
3. Review the BOM and confirm every fitted component and polarity against the
   physical assembly.
4. Compile the Arduino sketch in `software/arduino/evse_cp_feedback_led/` for an
   Arduino Nano. The final sketch was verified with Arduino IDE and used 6102
   bytes of program storage and 448 bytes of global SRAM.
5. Prepare a Raspberry Pi with Docker Compose, Python 3, `tmux`, `tcpdump`,
   `plc-utils` and the required GPIO/serial permissions.
6. Check out the recorded upstream `iso15118` revision and apply the files from
   `software/josev-modifications/` using the Raspberry Pi installation guide.
7. Copy the public templates from `software/configuration/` to local,
   git-ignored configuration files and replace every `CHANGE_ME` value.
8. Run the hardware-independent tests and the operations-script preflight.
9. Validate State A, State B and 5% HLC feedback using bench loads before a
   vehicle is connected.
10. Only after the bench checks pass, follow the hardware-specific procedure in
    `validation/procedures/REAL_EV_COMMUNICATION_TEST_RUNBOOK.md`.

## Operating modes

`MONDAY_PRECHARGE_PROBE` materially changes the protocol boundary:

| Value | Behaviour |
| --- | --- |
| `0` | Default fail-safe mode. Reports `No_IMD`/`EVSE_NotReady` and requests STOP when CableCheck cannot be truthfully completed. |
| `1` | Test-only protocol probe. Emulates CableCheck readiness to observe entry into PreCharge, but still blocks the requested charging command and requests STOP. |

The probe does not add an IMD, contactor, pre-charge circuit or voltage source.
Do not enable it on a system containing an HV/DC energy path.

## Evidence and privacy

Complete test archives can contain MAC addresses, PLC network keys, EVCC
identifiers, hostnames, operator names and packet payloads. They are private
laboratory evidence and are not suitable for direct publication. Publish only
purpose-selected, sanitized extracts.

## Project status

The repository captures a thesis prototype and its validated laboratory
workflow. It is intended for research, review and reproducibility work by
qualified users. It is not production EVSE firmware and has not been certified
for charging, electrical safety or standards conformance.

The thesis report is withheld until publication permission is confirmed.
