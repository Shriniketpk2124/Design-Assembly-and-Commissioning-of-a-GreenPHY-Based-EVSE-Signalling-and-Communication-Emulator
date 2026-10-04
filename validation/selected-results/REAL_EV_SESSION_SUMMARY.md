# Sanitized real-EV session summary

This document summarizes the four integrated vehicle sessions used for the
communication results in Chapter 6 of the thesis. Session numbering follows the
thesis: Sessions 2–5 were recorded on 25 September 2026.

The raw packet captures and complete cockpit archives are deliberately not part
of the repository. They contain link-layer and application identifiers, PLC
network material, host details and packet payloads. The source evidence is kept
in private storage; only the sanitized outcome matrix is published here.

## Outcome matrix

Times below are local laboratory time (Europe/Berlin, UTC+02:00).

| Thesis session | Capture label | Actual captured traffic | HomePlug frames | TCP payload segments | Highest confirmed application result | Termination |
| --- | --- | --- | ---: | ---: | --- | --- |
| 2 | `20260925T152722` | 15:28:00–15:28:19 | 39 | 18 | `PreChargeReq` received and failed `PreChargeRes` sent | SECC safety logic blocked the requested target and ended the session |
| 3 | `20260925T161404` | 16:15:32–16:15:44 | 39 | 4 | `SessionSetupRes` sent; SECC entered `ServiceDiscovery` | TCP peer closed the connection before `ServiceDiscoveryReq` |
| 4 | `20260925T163003` | 16:30:17–16:30:36 | 39 | 19 | `PreChargeReq` received and failed `PreChargeRes` sent | SECC safety logic blocked the requested target and ended the session |
| 5 | `20260925T163915` | 16:41:08–16:41:26 | 39 | 19 | `PreChargeReq` received and failed `PreChargeRes` sent | SECC safety logic blocked the requested target and ended the session |

The 19 TCP payload segments in Sessions 4 and 5 include a retransmitted
`PreChargeRes`; the SECC application log records one generated response per
session.

## Findings

- EVSE-side SLAC completed in all four recorded sessions.
- SDP selected TCP without TLS in all four sessions.
- ISO 15118-2 was successfully negotiated in all four sessions.
- Three independent sessions reached the thesis endpoint: the first
  `PreChargeReq` followed by the deliberately failed `PreChargeRes`.
- Session 3 was not a PreCharge failure. The vehicle-side TCP peer closed the
  connection after `SessionSetupRes`, while the SECC was waiting in
  `ServiceDiscovery`.
- No physical pre-charge or energy transfer took place in any session.

In the three endpoint sessions, the vehicle requested approximately 303.2 V
and 1.0 A. The controller rejected the target, requested the host fail-safe
STOP and returned the failed protocol response. These values are observations
from the vehicle request, not electrical output ratings of the emulator.

## Private evidence correlation

The following files are retained outside Git. Their hashes allow the private
evidence supplied for review to be checked against the files used to produce
this summary.

| Session | Private packet capture | SHA-256 | Private cockpit archive | SHA-256 |
| --- | --- | --- | --- | --- |
| 2 | `precharge_pilot_20260925T152722.pcap` | `b926a9a125a0aac29f2084cd59858d7af2c89f49af7d701558f798a328f45740` | `monday-test-20260925-152950.tar.gz` | `4fedf52a68d74bf341bb6c673713d6923a8859c7f63e90b1219200d06884a1d7` |
| 3 | `precharge_pilot_20260925T161404.pcap` | `45d7ad0859d58865deb4f4b94dea07223c49f4df7540d0afb662204b4f28fc8d` | `monday-test-20260925-161749.tar.gz` | `3f93a36922e5305bafa8b1cbb8ae0c9e5b1029f38511b1d5d62b66d1785990e3` |
| 4 | `precharge_pilot_20260925T163003.pcap` | `6a407a9bff8a7918118347ce86c75b32878c168209e383a3ed9be6e82611df8d` | `monday-test-20260925-163138.tar.gz` | `7ff62f4aaf49cdba5bfe0b28580cfeb34804f572e0c05715cf147b1fcd07da6e` |
| 5 | `precharge_pilot_20260925T163915.pcap` | `4f29650ac94579b679a4e29ccea3770ceb56abb5c53ed57421af419ac350f0be` | `monday-test-20260925-164910.tar.gz` | `7b7fb2766953a7b684dc6882cd47f8e9b735cc236d40057cc3326895bf83a2fe` |

An earlier cockpit archive ending in `151558` represents a separate preliminary
attempt. It is not one of the four sessions summarized in Chapter 6 and is not
included in the outcome matrix above.

## Publication boundary

Do not commit the raw PCAP files, raw cockpit archives, unredacted SECC logs or
state snapshots. If packet-level evidence is required for a reviewer, provide
it privately or create a separately reviewed and sanitized extract. Removing a
raw capture after committing it does not remove it from Git history.
