# Raspberry Pi communication-controller workflow

This directory contains the host-side controller, test-day cockpit and evidence
collection tools used by the GreenPHY EVSE signalling and communication
emulator.

The historical name **Monday** is retained in filenames and runtime paths
because those names belong to the tested integration. It refers to the final
real-EV workflow, not to a protocol or separate software product.

> [!CAUTION]
> The software is for a communication-only laboratory rig. It must not control
> an HV/DC source or be treated as a production EVSE controller.

## Responsibilities

| Component | Responsibility |
| --- | --- |
| `evse_monday_console.py` | Sole owner of the Arduino serial connection and CP-path relay GPIOs |
| `monday_status.py` | Decodes and displays the current host state snapshot |
| `monday_ops.sh` | Preflight, service startup, cockpit, SLAC, capture, diagnosis, STOP, evidence collection and shutdown |
| `install_monday.sh` | Installs the project-specific files into a prepared upstream working tree |
| `verify_monday.sh` | Compatibility wrapper that invokes the final `monday_ops.sh preflight` checks |
| `tests/` | Hardware-independent parser, state and fail-safe tests |

The modified SECC does not open the Arduino serial port or directly control the
Pi GPIOs. It exchanges small JSON state/command files with the host under
`/run/evse-monday`.

## Prerequisites

The validated setup used:

- Raspberry Pi 3B running a Debian-based Raspberry Pi OS;
- Python 3;
- Docker with the Compose plugin;
- `tmux`, `tcpdump`, `ip`, `ss`, `script`, `stdbuf` and `sha256sum`;
- Qualcomm Atheros `plc-utils`, including `evse`, `plctool` and `plcstat`;
- access to the Arduino serial device;
- access to the selected Pi GPIOs;
- a Devolo/QCA7000 Green PHY interface on the configured network interface;
- the EcoG `iso15118` source at the recorded compatible revision.

The original validated defaults were `eth0`, `/dev/ttyUSB0`, GPIO 17 and GPIO
27. Treat them as rig-specific values and verify them on the target system.

## Upstream compatibility

The project-specific Josev files were based on:

```text
repository: https://github.com/EcoG-io/iso15118.git
base commit: 76bf85be572d16adbcd009a985d605622c3b7227
```

Use a separate upstream checkout for this experiment. Do not overwrite another
simulation or development checkout.

## Public and local configuration

The committed files under `../configuration/` are templates. Create local,
git-ignored copies before running the workflow:

| Public template | Local file |
| --- | --- |
| `.env.example` | `.env.monday-real-ev` |
| `monday-ops.env.example` | `monday-ops.env` |
| `slac/evse-monday.example.ini` | `slac/evse-monday.ini` |

Replace every `CHANGE_ME`. Do not commit adapter MAC addresses, PLC network
keys, profile hashes, hostnames or captured vehicle identifiers.

For the operations-script values, export the local file into the current shell
before invoking `monday_ops.sh`:

```bash
set -a
. /path/to/monday-ops.env
set +a
```

The environment/Compose configuration must also identify the correct network
interface, protocol selection, authentication mode and TLS policy.

## PreCharge-probe setting

Keep this disabled for the default fail-safe configuration:

```text
MONDAY_PRECHARGE_PROBE=0
```

With the value `0`, the controller reports `No_IMD` and `EVSE_NotReady` and
requests host STOP when the stack reaches a boundary that cannot be truthfully
completed by the rig.

The value `1` enables the thesis experiment's test-only protocol probe. After
CableCheck begins, the software emulates the protocol status needed to observe
entry into PreCharge. It does not close a physical DC contactor, perform an
isolation measurement or generate DC voltage. A received voltage/current target
is blocked and causes a latched host STOP.

Never enable the probe on hardware containing an HV/DC source or energy path.

## Installation outline

1. Prepare a separate checkout of the compatible upstream `iso15118` revision.
2. Review `install_monday.sh` and set its deployment paths for the target Pi.
3. Install the files from `software/josev-modifications/` into the corresponding
   locations in the upstream tree.
4. Place the local environment and Compose files beside the upstream checkout.
5. Place the local SLAC profile at the path expected by `monday_ops.sh`.
6. Build or obtain the compatible `iso15118-secc:latest` container image.
7. Run the hardware-independent tests.
8. Run `monday_ops.sh preflight` with the EV disconnected.

The installer and operations script should be reviewed before use because the
validated Pi directory was `~/thesis-monday-real-ev`; a different installation
location must be configured consistently.

## Hardware-independent checks

From the deployed working tree, compile the Python files and run the included
tests before connecting hardware. At minimum, verify:

- state snapshots become unusable when stale or malformed;
- unsupported comparator combinations decode to `UNKNOWN`;
- the STOP command is latched;
- charging commands cannot silently pass through the controller;
- only one process owns the serial/GPIO interface;
- only one EVSE-side SLAC process is active.

Then run:

```bash
/path/to/monday_ops.sh preflight
```

Do not connect a vehicle if any preflight item fails.

## Final operating workflow

The final integrated workflow uses `monday_ops.sh`; the older manual sequence of
separate terminals, a separately started packet capture and unresolved SLAC
ownership is obsolete.

Start the environment with:

```bash
/path/to/monday_ops.sh test-day
```

The command performs the preflight, creates a timestamped run directory,
starts packet capture, Redis and the SECC, waits for UDP 15118 and opens a
four-pane `tmux` cockpit:

| Pane | Function |
| --- | --- |
| Large left | Interactive console and sole serial/GPIO owner |
| Upper right | Decoded CP state and freshness monitor |
| Middle right | Sole `plc-utils` EVSE SLAC owner |
| Lower right | Live SECC application log |

Reconnect to an existing cockpit with:

```bash
/path/to/monday_ops.sh cockpit
```

Use the operator menu for status, diagnosis, snapshots, fail-safe STOP and
confirmed shutdown. CP-changing commands remain deliberate console actions.

## CP mapping used by the controller

A snapshot is accepted only when it is fresh, stable, fault-free and internally
consistent. With the CP-path relays commanded on, heartbeat supervision must
also be active.

| Arduino output | `FB_PAIR` | Decoded state |
| --- | ---: | ---: |
| Relays commanded off | any | A1 |
| Positive DC | `00` | A1 |
| Positive DC | `10` | B1 |
| Positive DC | `01` | C1 |
| 5% HLC PWM | `00` | A2 |
| 5% HLC PWM | `11` | B2 |
| 5% HLC PWM | `01` | C2 |

Every other combination is `UNKNOWN`.

## SLAC ownership

The final workflow uses exactly one `plc-utils` `evse` process as the EVSE-side
SLAC owner. `monday_ops.sh test-day` starts it in the cockpit after the SECC is
available. Do not start `pyslac`, a second `evse` process or another SLAC owner
on the same interface.

Before the EV is connected, the normal listener output includes a successful
`CM_SET_KEY` result followed by the unoccupied/listening state. Ethernet carrier
or an IPv6 link-local address alone does not prove successful SLAC.

## Safe shutdown and evidence

Use the console to request session stop, then use the guarded operations menu or
run:

```bash
/path/to/monday_ops.sh shutdown
```

The workflow verifies or requests the safe CP state, stops packet capture,
collects logs and final state, stops services and creates checksums.

Complete run directories are private by default. They may contain EVCC IDs,
MAC addresses, PLC network keys, host details and packet payloads. Publish only
sanitized, purpose-selected results.

## Real-vehicle procedure

Do not derive a vehicle test from this README alone. Follow
`validation/procedures/REAL_EV_COMMUNICATION_TEST_RUNBOOK.md` after the bench
checks and preflight have passed.
