# Casebook

<img src="assets/app-icon_256.png" width="96" align="right" alt="Casebook Icon">

**Turn Aspen Plus simulation results into searchable, comparable, and exportable local engineering data.**

Open a solved Aspen Plus model, click **"Read Current Case"**, and inspect all process streams, equipment parameters, column tray profiles, and process flow diagrams in a local web UI—complete with **multi-case comparison**, **automated pipe sizing**, **one-click mass & energy balance reports**, and structured data exports.

**English** | [简体中文](README.md)

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Installation Guide](https://img.shields.io/badge/Install%20Guide-docs%2Finstall.md-4D6BFE)](docs/install.md)

> **Disclaimer & Copyright Notice**
> 1. **Sample Data**: The sample database bundled in this repository comes from the author's personal coursework and process design competitions. It is provided strictly for demonstration purposes and may contain engineering inaccuracies. **Do NOT use it as a basis for actual engineering design.** Always rely on your own validated models.
> 2. **Software License**: This is an independent personal project and is not affiliated with, endorsed by, or sponsored by Aspen Technology, Inc. It contains no Aspen software. A valid, licensed installation of Aspen Plus is required prior to use.

---

## Why Casebook?

Aspen Plus excels at rigorous process simulation, yet **reviewing results and compiling engineering reports** remains tedious: checking stream compositions, comparing heat duties across design iterations, or extracting stage-by-stage column profiles typically requires manual cell-by-cell clicking. Worse, when a simulation encounters issues, warnings are scattered across convergence blocks, unit operation messages, and tear streams—making it difficult to tell at a glance whether a run is trustworthy.

Casebook does one thing well: **it extracts existing simulation results into clean, standardized, and offline-searchable structured data.**

- **Non-Intrusive Read-Only Attachment**: Hooks directly into an already open Aspen Plus instance via COM. Reads node values without launching new sessions, consuming extra license tokens, or modifying/re-running your flowsheet.
- **Local Offline Snapshots**: Saves full-flowsheet snapshots into a local SQLite database with one click, enabling offline browsing and exporting anytime.
- **Smart Convergence & Error Diagnostics**: Scans hard errors and convergence statuses automatically. Non-converged models are **still imported and flagged clearly in yellow** to prevent accidental misuse.
- **Multi-Format Deliverables**: Generates ready-to-submit Word (`.docx`) Mass & Energy Balance Reports, multi-sheet Excel workbooks, or structured JSON/CSV packages for downstream scripts and AI analysis.

---

## Quick Start

### 1. Download & Launch

| Setup Option | Instructions |
| :--- | :--- |
| **Standalone Executable (Recommended)** | Download `Casebook-windows-x64.zip` (~30 MB) from [Releases](https://github.com/DonkeyHeng/Casebook/releases), extract it, and double-click `Casebook.exe` (includes `安装说明.txt` in the root folder). |
| **Run from Source (Python 3.10+)** | Clone or download the repository and double-click **`start.cmd`** in the root directory (opens a console window for live logs). |
| **Troubleshooting** | Refer to **[docs/install.md](docs/install.md)** (plain-text version available at [`docs/install-text.txt`](docs/install-text.txt)). |

**Launch via CLI:**
```bash
git clone https://github.com/DonkeyHeng/Casebook.git
cd Casebook
python -m pip install -r requirements.txt
python -m app.api   # Open http://127.0.0.1:8000 in your browser
```

### 2. Three-Step Workflow

1. **Run Simulation**: Open your `.apw` file in Aspen Plus and run a complete calculation.
2. **Read Case**: Click the blue **"Read Current Case"** button in the top-left corner of Casebook and wait for the progress bar to finish.
3. **Inspect, Compare & Export**: Navigate through Streams, Equipment, Flowsheet, and Piping tabs via the left sidebar, or export reports from the top-right menu.
   > *Tip: For automated pipe sizing, configure default flow velocities and pipe material (default: `S30408`) in the top bar of the **Piping** tab. To assign specific materials to individual streams, click **"Pipe Classes"** in the top-right corner.*

### 3. Try Without Aspen (Sample Data)

On first launch, the local database is empty (`data/cases.db` is excluded from Git) and the home page will display *"No cases yet"*. The repository includes a real-world **failed/abnormal** case snapshot (121 streams, 77 unit blocks, and 7 pumps, featuring a dry-out column `T0202` and non-converged components in `T0205`) so you can test the UI and diagnostic logic offline:

- **Using `Casebook.exe`**: Click **"Load Sample Case"** directly on the home page (or run `Casebook.exe --sample`). Any existing database is automatically backed up to `cases.db.bak`.
- **Using Source Code**:
  ```bash
  cp data/cases.db data/cases.local.db   # Back up existing local DB if present
  cp samples/case-23-error.db data/cases.db
  python -m app.api
  ```
  *See [`samples/README.md`](samples/README.md) for sample details.*

---

## Core Features

| Module | Capabilities |
| :--- | :--- |
| **Case Reader** | Triggerable from any page. Attaches to Aspen → extracts streams/blocks/topology/trays → saves to SQLite → switches to the new case (~15–40 s for 100+ streams, with progress bar and stall detection). |
| **Issue Banner** | Displays an executive verdict on the first line (e.g., *"2 blocks reported errors; not recommended for yield calculations"*), backed by collapsible raw error details. |
| **Streams & Compositions** | Standardized to `℃ / bar / kg·h⁻¹ / kW`. Click any stream to view liquid ($x$) and vapor ($y$) mole fractions. Compositions are fetched on demand and cached locally; check **"Read All Stream Compositions"** in the sidebar to fetch component mass flows in bulk. |
| **Equipment & Enthalpy Check** | Categorized tabs (Columns / Heat Exchangers / Separators / Reactors / Pumps / Fluid Movers / Mixers-Splitters / Others), sorted by `Error → Warning → OK` with raw Aspen messages. Runs automatic enthalpy balance self-checks across all equipment (including columns and reactors) and supports per-category Excel export. |
| **Pump Sizing & Cavitation** | Extracts flow rate, head, efficiency, shaft power, recommended motor rating, and suction/discharge conditions. Vapor pressure ($p_v$) defaults to the suction stream's bubble-point pressure to compute $\text{NPSH}_a$ (editable if missing), with one-click links to pump vendor selection pages. |
| **Interactive Flowsheet** | Auto-layout hierarchical process flow diagram supporting zoom/pan, 2-hop upstream/downstream node highlighting on click, and SVG/PNG export for reports. |
| **Multi-Case Comparison** | Select any two cases to compare `Case A / Case B / Delta` by stream or equipment, highlighting status changes and enthalpy deviations. |
| **Column Tray Profiles** | Stage-by-stage temperature, pressure, liquid/vapor flowrates, and duty profiles with synchronized interactive charts and tables. |
| **Automated Pipe Sizing** | Real-time calculation pipeline: `Phase Detection → Velocity Selection → ID Sizing → Standard DN Rounding → Design P/T & Allowable Stress → Wall Thickness (with mill tolerance & minimum process thickness floor) → Pipe Class Code`. Includes V1–V9 engineering rule checks (velocity limits, vacuum, high temp, pump suction, etc.). |
| **Mass & Energy Balance Reports** | Exports standalone or combined `.docx` reports for **Mass Balance** and **Energy Balance**. Features wide-format mass balance tables (streams as columns, properties/components as rows), annual consumption/output (`t/a`), plant-wide energy summaries, column duty breakdowns, manual battery-limit (section) confirmation, and $\Sigma\text{In} = \Sigma\text{Out}$ conservation checks. Also supports CLI batch export via `python -m tools.balance_report --case N`. |
| **Multi-Format Exports** | Exports an 8-sheet Excel workbook (`Summary / Streams / Equipment / Pumps / Connections / Tray Profiles / Piping / Piping Calculation Sheet`) or a structured `.zip` package (`README.md` + CSVs + JSON). |

> **Quality Assurance**: Includes **465** offline regression unit tests (493 with `--db` database consistency checks). Run `python -m tools.selftest` to verify core logic without Aspen Plus installed.

---

## Release History

**Current Version: [v1.2.1](https://github.com/DonkeyHeng/Casebook/releases/tag/v1.2.1)** (See [Releases](https://github.com/DonkeyHeng/Casebook/releases) for full changelogs)

- **Phase 1 (Core Extraction & Sizing)**: Snapshots, streams/equipment/tray profiles/flowsheet viewer, multi-case comparison, pipe sizing, and data exports.
- **Phase 2 (Engineering Depth & UX)**: Categorized equipment views, pump hydraulics & $\text{NPSH}_a$ calculation, category-filtered Excel exports, and dark mode (strictly read-only).
- **v1.2.0 (Balance Reports)**: Added Word `.docx` Mass & Energy Balance Reports, interactive section/battery-limit boundary confirmation, plant-wide conservation checks, and enthalpy balance validation for columns and reactors.
- **v1.2.1 (Robustness fixes)**: Hardened write paths found by a code review — malformed requests no longer clear the piping-class table, sample install uses a consistent backup plus atomic replace, standards tables switch via versioned directories, numeric inputs reject NaN/Inf, and the safety scan fails closed. Four long functions were split up.

<details>
<summary><strong>Legacy Version Number Mapping (Renumbered in Nov 2026)</strong></summary>

| Legacy Tag | Current Version | Notes |
| :--- | :--- | :--- |
| `v1.0.0` | `v1.0.0` | Retained (initial build under former project name) |
| `v1.0.1` | `v1.0` | — |
| `v1.0.2` + `v1.0.3` | `v1.0.1` | — |
| `v1.1` | `v1.1` | Unchanged |
| `v1.2` | `v1.1.1` | — |
| `v1.3` | `v1.1.2` | — |
| `v1.4` | `v1.1.3` | — |

</details>

---

## System Requirements & Data Storage

### Environment Requirements
- **OS**: Windows 10 / 11 (required for Windows COM automation; macOS and Linux are not supported).
- **Simulator**: Aspen Plus **V15** (tested version; other versions are unverified) with a valid license.
- **Session**: Must run inside an **interactive desktop session** (remote/service sessions lack COM access) at the **same privilege level** (Standard vs. Administrator) as Aspen Plus.
- **Browser**: Modern browser such as Edge or Chrome (zero-build frontend; Node.js is not required).

### Local Data & Privacy
- **Local-Only Storage**: Case snapshots (`data/cases.db`), piping standard tables (`data/preview/std/docx/*.csv`), and pipe class configs (`data/config/piping-classes.json`) stay strictly local under `data/` and are ignored by Git.
- **Unit Handling**: Internal displays use standardized SI/metric engineering units while preserving raw Aspen values and units in exports. Unmapped units remain blank and are logged in the top **"Unit Diagnostics"** panel rather than silently dropped.

---

## FAQ

<details>
<summary><strong>1. "Cannot connect to Aspen" when clicking "Read Current Case"?</strong></summary>

Check the following three items:
1. Ensure Aspen Plus is open with a loaded flowsheet.
2. Ensure Casebook is running in a local interactive desktop session.
3. Verify both Casebook and Aspen Plus are running under the same user privilege level (do not run one as Administrator and the other as a standard user).
If issues persist, run `python tools/probe_aspen.py` to diagnose COM connectivity.
</details>

<details>
<summary><strong>2. Why are stream compositions loaded only when clicked?</strong></summary>

Fetching complete composition data (liquid $x$, vapor $y$, and component mass flows) requires ~54 COM calls per stream, turning a few seconds into nearly a minute for 120+ streams. Therefore, Casebook fetches compositions on demand and caches them locally.
- **Tip**: To include the *"Component Mass Flow"* section in the **Mass Balance Report**, check **"Read All Stream Compositions"** below the read button before importing; otherwise, only molar flows are exported.
</details>

<details>
<summary><strong>3. Why does Pipe Sizing say "Standard Tables Missing" and how do I add them?</strong></summary>

Pipe sizing relies on dimensional and allowable stress tables from **GB/T 17395-2024** (Tables 1 & 4) and **GB/T 20801.1-2025** (Table B.1). **Because Chinese national standards are copyright-protected, these CSV tables cannot be distributed with the repository.**

**Setup in 3 steps:**
1. Prepare three UTF-8 CSV files from your licensed copies of the standards: `t1-17395-table1.csv`, `t4-17395-table4.csv` (requiring `D_mm`, `S_mm` columns), and `t3-20801-tableB1.csv` (requiring `标准`, `牌号`, and `许用应力_<Temp>` columns).
2. Click the **"Standard Tables"** button in the top-right corner of the **Piping** tab to upload them. Built-in validation checks headers and row counts; once passed, changes **take effect immediately without restarting**.
3. See [`docs/pipe-standards-index.md`](docs/pipe-standards-index.md) for exact page numbers, or use the ready-made extraction prompt in [`docs/pipe-standards-extract-prompt.md`](docs/pipe-standards-extract-prompt.md) with a vision-capable AI to digitize the tables from screenshots.
</details>

<details>
<summary><strong>4. Why are Pump parameters or NPSHₐ showing `—`?</strong></summary>

- **Entire table blank**: The case was likely imported prior to Phase 2. Re-read the case from Aspen, or run `python -m tools.repair_cases --apply` to recalculate from stored raw values.
- **Only $\text{NPSH}_a$ blank**: Requires suction vapor pressure ($p_v$), read from the suction stream's bubble-point pressure (`PBUB`). You can enter $p_v$ manually in the UI, or configure Aspen to output it in 5 steps (Create property set `pv` → add `PL` & `PBUB` in `bar` → Report Options → Stream → add `pv` to selected property sets → reset/re-run → re-read case; see [`docs/pump-selection-plan.md`](docs/pump-selection-plan.md) §3.6).
- **Two-phase suction**: Cavitation margin formulas do not apply to two-phase inlets; $\text{NPSH}_a$ is intentionally omitted with an explanatory tooltip.
</details>

<details>
<summary><strong>5. Why is the calculated pipe wall thickness thicker than the hoop-stress formula value?</strong></summary>

After computing internal pressure thickness per GB/T 20801.1 and adding corrosion allowance, Casebook enforces a **minimum process wall thickness floor** (`2.0 mm` for DN10–25; `3.0 mm` for DN32–100). For example, a DN15 pipe at 1.0 MPa requires only `0.07 mm` theoretically, which corresponds to instrument tubing rather than weldable process piping. Enable **"Show Calculation Steps"** in the UI to inspect the minimum thickness column.
</details>

<details>
<summary><strong>6. Other Questions (Material Selection, Velocity Overrides, Non-Converged Runs)</strong></summary>

- **Does it auto-select pipe materials?** No. Corrosion depends on concentration, trace chlorides (ppm), water content, and temperature. Casebook applies materials and corrosion allowances strictly from your `data/config/piping-classes.json` (defaults to `S30408` if unconfigured).
- **Can I change default velocities?** Yes, edit them directly in the top bar of the **Piping** tab for live recalculation (defaults: Liquid `1.5 m/s`, Vapor `15 m/s`, Two-Phase `8 m/s`, Pump Suction `0.8 m/s`; max size capped at `DN600`).
- **Can I import non-converged models?** Yes. Data is saved as-is, flagged in yellow, excluded from comparative statistics by default, and accompanied by raw module error messages.
</details>

---

## Engineering Scope & Known Limitations

When using Casebook outputs for coursework, design competitions, or engineering references, keep the following boundaries in mind:

- **Read-Only Tool**: Performs no closed-loop optimization and writes nothing back to Aspen Plus.
- **Piping Scope**: Supports nominal diameters up to **DN600** (larger streams are flagged for parallel runs or custom design); does not perform line pressure-drop calculations, external pressure buckling checks, or pipe stress analysis. Two-phase flow uses a conservative `8 m/s` velocity—cite this basis when quoting results.
- **Pump Scope**: Calculates system-side $\text{NPSH}_a$ only; pump-required $\text{NPSH}_r$ must be obtained from manufacturer curves. Recommended motor sizes are rounded up against standard motor power ratings as an engineering reference (skipped if shaft power $\le 0$).
- **Unit Set Coverage**: Pump extraction has been verified on `MET` and `METCBAR` unit sets; `ENG` conversions are derived analytically and have not yet been tested against live `ENG` models. Two-phase pump suction exclusion is currently verified via unit test fixtures only.

---

## Project Structure & Documentation

```text
Casebook/
├── start.cmd            # Windows one-click launcher (opens log console)
├── requirements.txt     # Python dependencies (pywin32, openpyxl, python-docx)
├── app/                 # Core app: aspen (COM reader), store (SQLite), design (pure math), api (HTTP), web (UI)
├── tools/               # Maintenance & CLI scripts: COM probe, selftest, DB repair, batch balance reports, packager
├── docs/                # Technical documentation (install, API, Aspen field maps, sizing & balance specs)
├── samples/             # Offline demo database containing real abnormal case snapshots
├── assets/              # Application icons and static assets
├── standards/           # [Local] Place your own standard PDFs here (git-ignored)
├── model/               # [Local] Place your own Aspen models here (git-ignored)
└── data/                # [Local] SQLite snapshots, standard CSVs, and pipe class configs (git-ignored)
```

### Documentation Index
- **Getting Started**: [`docs/install.md`](docs/install.md) (Installation & Troubleshooting) · [`docs/README.md`](docs/README.md) (Full Doc Index)
- **Engineering Basis**: [`docs/pipe-selection-plan.md`](docs/pipe-selection-plan.md) (Pipe Sizing) · [`docs/pump-selection-plan.md`](docs/pump-selection-plan.md) (Pump Hydraulics & $\text{NPSH}_a$) · [`docs/balance-report.md`](docs/balance-report.md) (Balance Reports)
- **Standards & API**: [`docs/pipe-class-codes.md`](docs/pipe-class-codes.md) (HG/T 20519.6 Codes) · [`docs/aspen-field-map.md`](docs/aspen-field-map.md) (Aspen COM Paths) · [`docs/api.md`](docs/api.md) (Local HTTP API)
- **Developer Guides**: [`app/README.md`](app/README.md) (Architecture) · [`docs/ui-style.md`](docs/ui-style.md) (UI Tokens) · [`docs/conventions.md`](docs/conventions.md) (Repo Conventions)

---

## Contributing & Contact

Issues and Pull Requests are welcome!

1. Please review [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md) and [`app/README.md`](app/README.md) before contributing.
2. Ensure all offline regression tests pass before submitting a PR: `python -m tools.selftest`.
3. Never commit `.apw` simulation files, copyrighted standard PDFs, or local `data/` databases.
4. Report security vulnerabilities privately via [`.github/SECURITY.md`](.github/SECURITY.md) rather than opening a public issue.

- **Contact**: QQ `2655972249` / WeChat `User_DonkeyHeng` (Please open a GitHub Issue first; add on QQ/WeChat if there is no reply after a while).
- **License**: Licensed under the [Apache License 2.0](LICENSE).
