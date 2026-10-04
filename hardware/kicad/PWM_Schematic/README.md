\# PWM and Control-Pilot PCB



This directory contains the KiCad source files for the mixed-signal PCB used by

the Green PHY EVSE signalling and communication emulator.



\## Requirements



\- KiCad 10

\- Standard KiCad symbol libraries

\- Standard KiCad footprint libraries

\- Standard KiCad 3D-model package, if 3D rendering is required



\## Opening the project



Open:



`PWM\_Schematic.kicad\_pro`



The custom symbol and footprint libraries required by the project are included

locally in the `symbols/` and `footprints/` directories. The corresponding

`sym-lib-table` and `fp-lib-table` files use `${KIPRJMOD}` paths, so the project

does not depend on machine-specific directories.



\## Included source files



\- `PWM\_Schematic.kicad\_pro` — KiCad project settings

\- `PWM\_Schematic.kicad\_sch` — schematic

\- `PWM\_Schematic.kicad\_pcb` — PCB layout

\- `PWM\_Schematic.kicad\_dru` — custom design rules

\- `symbols/` — project-local custom symbols

\- `footprints/` — project-local custom footprints

\- `reports/` — final ERC and DRC reports



\## Verification status



Final verification performed on 2026-10-04:



\- ERC errors: 0

\- ERC warnings: 0

\- DRC violations: 0

\- Unconnected pads: 0

\- Footprint errors: 0



The corresponding reports are stored in `reports/`.



\## 3D models



Standard components use the official KiCad 10 3D-model library where available.

Nonportable or incorrect custom 3D-model references were removed. Missing custom

3D models do not affect schematic connectivity, PCB routing, fabrication output

or design-rule verification.



\## Fabrication outputs



Historical Gerber and Aisler exports are not included because they predate the

final DRC-clean source revision. Fabrication outputs should be regenerated from

the checked PCB source when required.



\## Scope and safety



This PCB is a research prototype for control-pilot signalling, proximity-pilot

interfacing and communication-layer experimentation. It is not a certified EVSE

power stage and must not be treated as authorization for high-voltage or energy

transfer.



\## Third-party library data



Some project-local symbol and footprint definitions originated from manufacturer

or electronic-library-loader data and were adapted for this project. Applicable

redistribution terms must be reviewed before making the repository public.
