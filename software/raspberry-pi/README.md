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

## Installation and first startup

These instructions create a separate EcoG `iso15118` checkout and install the
project additions into it. They do not turn the prototype into a charger.
Keep the vehicle disconnected and keep every HV/DC source absent during the
installation and software checks.

The validated host was a Raspberry Pi 3B running a Debian-based Raspberry Pi
OS. The tested hardware names are fixed in the operational scripts:

- Green PHY interface: `eth0`;
- Arduino serial device: `/dev/ttyUSB0`;
- relay GPIOs: GPIO17 and GPIO27.

If the target uses different names or GPIOs, update and revalidate the scripts
before connecting a vehicle.

### 1. Install and verify host tools

On a Debian-based Raspberry Pi OS, install the ordinary command-line
dependencies with:

```bash
sudo apt update
sudo apt install -y \
  git make openssl python3 python3-serial \
  tmux tcpdump iproute2 util-linux coreutils psmisc default-jre
```

Install Docker Engine with the Docker Compose plugin using the instructions for
the installed Raspberry Pi OS release. Install Qualcomm Atheros `plc-utils`
and ensure that the Raspberry Pi `pinctrl` command is available. Package names
for Docker, `plc-utils` and `pinctrl` differ between OS releases, so verify the
result directly:

```bash
python3 --version
docker --version
sudo docker compose version
command -v tmux tcpdump ip ss script stdbuf sha256sum
command -v evse plctool plcstat pinctrl
```

Do not continue until every command returns successfully.

### 2. Clone this project and the recorded upstream revision

The project-specific Josev files were developed against EcoG `iso15118` commit
`76bf85be572d16adbcd009a985d605622c3b7227`. Use a separate checkout so another
simulation or development tree is not overwritten:

```bash
export PROJECT_CHECKOUT="$HOME/greenphy-evse-emulator"
export MONDAY_HOME="$HOME/thesis-monday-real-ev"
export MONDAY_REPO="$MONDAY_HOME/iso15118-real-ev"

git clone \
  https://github.com/Shriniketpk2124/Design-Assembly-and-Commissioning-of-a-GreenPHY-Based-EVSE-Signalling-and-Communication-Emulator.git \
  "$PROJECT_CHECKOUT"

mkdir -p "$MONDAY_HOME"
git clone https://github.com/EcoG-io/iso15118.git "$MONDAY_REPO"
git -C "$MONDAY_REPO" checkout --detach \
  76bf85be572d16adbcd009a985d605622c3b7227
git -C "$MONDAY_REPO" rev-parse HEAD
```

The final command must print the recorded commit exactly.

### 3. Build the SECC dependency image

The runtime Compose file expects an image named `iso15118-secc:latest`. Build
that image from the recorded upstream checkout:

```bash
cd "$MONDAY_REPO"
make generate_v2_certs
cp template.Dockerfile iso15118/secc/Dockerfile
sudo docker build \
  --tag iso15118-secc:latest \
  --file iso15118/secc/Dockerfile \
  .
sudo docker pull redis:6.2.6-alpine
```

Confirm both images before continuing:

```bash
sudo docker image inspect iso15118-secc:latest >/dev/null
sudo docker image inspect redis:6.2.6-alpine >/dev/null
```

The image provides the upstream Python dependencies and Java EXI runtime. The
project's Compose file later mounts the installed `iso15118` source tree into
the container.

### 4. Create the three private configuration files

Create local copies from the public templates:

```bash
cp "$PROJECT_CHECKOUT/software/configuration/.env.example" \
  "$PROJECT_CHECKOUT/software/configuration/.env.monday-real-ev"
cp "$PROJECT_CHECKOUT/software/configuration/monday-ops.env.example" \
  "$PROJECT_CHECKOUT/software/configuration/monday-ops.env"
cp "$PROJECT_CHECKOUT/software/configuration/slac/evse-monday.example.ini" \
  "$PROJECT_CHECKOUT/software/configuration/slac/evse-monday.ini"

chmod 600 \
  "$PROJECT_CHECKOUT/software/configuration/.env.monday-real-ev" \
  "$PROJECT_CHECKOUT/software/configuration/monday-ops.env" \
  "$PROJECT_CHECKOUT/software/configuration/slac/evse-monday.ini"
```

Edit all three files and replace every `CHANGE_ME`. Use `eth0` as
`NETWORK_INTERFACE` for the published operational scripts. The SLAC profile
must contain the local, authorized PLC parameters for the adapter and test
environment; do not copy identifiers or keys from published captures.

After editing the SLAC profile, calculate its checksum:

```bash
sha256sum \
  "$PROJECT_CHECKOUT/software/configuration/slac/evse-monday.ini"
```

Copy the resulting 64-character value into `MONDAY_PROFILE_SHA256` in
`monday-ops.env`. Also enter the local QCA7000/Devolo adapter address as
`DEVOLO_MAC`.

Keep `MONDAY_PRECHARGE_PROBE=0` for the default fail-safe installation. Confirm
that no placeholders remain:

```bash
grep -RInE '=[[:space:]]*CHANGE_ME[[:space:]]*$' \
  "$PROJECT_CHECKOUT/software/configuration/.env.monday-real-ev" \
  "$PROJECT_CHECKOUT/software/configuration/monday-ops.env" \
  "$PROJECT_CHECKOUT/software/configuration/slac/evse-monday.ini"
```

The final command must produce no output. These local files are ignored by
Git. Never commit adapter addresses, PLC network material or other identifiers.

### 5. Install the project additions

Tell the installer where the private files and upstream checkout are located:

```bash
export MONDAY_ENV_FILE="$PROJECT_CHECKOUT/software/configuration/.env.monday-real-ev"
export MONDAY_OPS_ENV_FILE="$PROJECT_CHECKOUT/software/configuration/monday-ops.env"
export MONDAY_SLAC_PROFILE="$PROJECT_CHECKOUT/software/configuration/slac/evse-monday.ini"

"$PROJECT_CHECKOUT/software/raspberry-pi/install_monday.sh"
```

The installer:

- verifies the recorded upstream commit;
- backs up previously installed project files;
- installs the Josev controller additions and Compose file;
- installs the host console, status and operations tools;
- installs the three private configuration files with mode `0600`;
- compiles the Python files and runs the included hardware-independent tests.

It does not install OS packages, build the Docker image, flash the Arduino or
perform any physical safety check.

### 6. Load the operations configuration and run preflight

Connect the Arduino and Devolo adapter, but keep the vehicle disconnected.
Confirm that `eth0` and `/dev/ttyUSB0` are the intended devices:

```bash
ip -br link
ls -l /dev/ttyUSB0
```

Load the local operations settings and run the read-only preflight:

```bash
set -a
. "$MONDAY_HOME/monday-ops.env"
set +a

"$MONDAY_HOME/verify_monday.sh"
```

The preflight checks the required files and commands, Ethernet carrier, IPv6
link-local address, Arduino device, local QCA7000 response, SLAC-profile hash,
Compose configuration, Docker images, running processes and required ports.
Do not connect a vehicle if any item fails.

### 7. Start and stop the software

After the hardware-independent tests, preflight and bench CP checks have
passed, start the integrated cockpit with:

```bash
"$MONDAY_HOME/monday_ops.sh" test-day
```

At the `bench>` prompt, establish the safe initial state one command at a
time:

```text
reset
remote on
a
status
```

Do not enter `hlc` until the approved bench procedure or real-vehicle runbook
requires it and stable State B has been confirmed. If the SSH connection is
lost, reconnect and run:

```bash
"$MONDAY_HOME/monday_ops.sh" cockpit
```

For a normal shutdown, use the guarded cockpit menu or run from a separate SSH
session:

```bash
set -a
. "$MONDAY_HOME/monday-ops.env"
set +a
"$MONDAY_HOME/monday_ops.sh" shutdown
```

The evidence path can then be displayed with:

```bash
"$MONDAY_HOME/monday_ops.sh" paths
```

The run directory is private evidence and can contain vehicle and network
identifiers. Do not commit it.

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

## Hardware-independent checks

The installer compiles the deployed Python files and runs the three included
test programs. They verify that:

- state snapshots become unusable when stale or malformed;
- unsupported comparator combinations decode to `UNKNOWN`;
- the STOP command is latched;
- charging commands cannot silently pass through the controller;
- only one process owns the serial/GPIO interface;
- only one EVSE-side SLAC process is active.

After those tests pass, load `monday-ops.env` and run:

```bash
"$MONDAY_HOME/monday_ops.sh" preflight
```

Do not connect a vehicle if any preflight item fails.

## Final operating workflow

The final integrated workflow uses `monday_ops.sh`; the older manual sequence of
separate terminals, a separately started packet capture and unresolved SLAC
ownership is obsolete.

Start the environment with:

```bash
"$MONDAY_HOME/monday_ops.sh" test-day
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
"$MONDAY_HOME/monday_ops.sh" cockpit
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
| 5% HLC PWM | `10` | B2 |
| 5% HLC PWM | `11` | C2 |

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
"$MONDAY_HOME/monday_ops.sh" shutdown
```

The workflow verifies or requests the safe CP state, stops packet capture,
collects logs and final state, stops services and creates checksums.

Complete run directories are private by default. They may contain EVCC IDs,
MAC addresses, PLC network keys, host details and packet payloads. Publish only
sanitized, purpose-selected results.

## Real-vehicle procedure

Do not derive a vehicle test from this README alone. Follow the
[real-EV communication test runbook](../../validation/procedures/REAL_EV_COMMUNICATION_TEST_RUNBOOK.md)
after the bench checks and preflight have passed.
