# Casebook

<img src="assets/app-icon_256.png" width="96" align="right" alt="Casebook Icon">

**A local engineering data workbench that turns Aspen Plus simulation results into searchable, comparable, and exportable deliverables.**

With an active Aspen Plus simulation open, click **"Read Current Case"** to inspect process-wide streams, equipment blocks, tray-by-tray column profiles, and interactive flowsheet topologies in a local web UI—complete with **multi-case comparison**, **pipe and pump sizing assistance**, **one-click material & energy balance reports**, and **structured AI-ready exports**.

[简体中文](README.md) | **English**

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Version: v1.6.0](https://img.shields.io/badge/version-v1.6.0-0F766E)](https://github.com/DonkeyHeng/Casebook/releases/tag/v1.6.0)
[![Install Guide](https://img.shields.io/badge/Install%20Guide-docs%2Finstall.md-4D6BFE)](docs/install.md)

> **Disclaimer & Privacy Notice**
> 1. **Sample Data**: The bundled sample database originates from the author's personal coursework and competition designs (sanitized). It is provided solely to demonstrate UI features and diagnostic capabilities, may contain engineering inaccuracies, and **must never be used as a basis for actual engineering design**. Always rely on your own validated models.
> 2. **Software & Standards Licensing**: Casebook is an independent open-source utility unaffiliated with AspenTech. It does not bundle Aspen Plus software or copyrighted national standard texts (GB/T). A valid Aspen Plus license is required for live COM extraction.
> 3. **Network & Privacy**: All case snapshots, standard lookup tables, and configurations are stored locally under `data/` and are **never uploaded to the cloud**. The only outbound network action in the entire application is the manual **"Check for Updates"** button in the sidebar, which sends a single `HEAD` request to `github.com` to read the latest release tag without transmitting local data or downloading/overwriting files.

---

## Pain Points & Design Principles

While Aspen Plus excels at process simulation, **data inspection, anomaly tracing, and report compilation** remain tedious: checking stream compositions, comparing duties across cases, and compiling column profiles often require manual cell-by-cell copying. When convergence warnings or block errors occur, diagnostic messages are scattered across convergence blocks, unit messages, and tear streams, making it difficult to tell at a glance whether a run is trustworthy and which streams are affected.

Casebook is built on four core principles:

- **Non-Intrusive Read-Only Attachment**: Connects directly to an already open Aspen Plus document via Windows COM file monikers, calling only `FindNode` and `.Value`. It never spawns a new session, consumes an extra license seat, or modifies/re-runs/saves your simulation file.
- **Local Snapshots & Content-Hash Identity**: Every read appends a new immutable snapshot to a local SQLite database (`data/cases.db`) without overwriting history, identifying models via `sha256` file content hashes so manual engineering inputs carry over cleanly across cases without cross-model contamination.
- **Strict Null-Safety—No Guessing**: Unit conversions are evaluated per field from each node's native `UnitString`. Unrecognized units or missing properties (such as vapor pressure $p_v$) are left blank and flagged in diagnostics rather than substituted with `0` or heuristic guesses.
- **Fact-Based Diagnostics with Explicit Boundaries**: Automatically performs three-state convergence judgment, downstream/upstream error impact tracing, equipment-wide enthalpy balance checks, and code validations (`V1–V14`), reporting objective engineering facts alongside transparent calculation scopes and limitations.

---

## Quick Start

### 1. Download & Launch

| Method | Steps |
| :--- | :--- |
| **Standalone Windows Bundle (Recommended)** | Download `Casebook-windows-x64.zip` (~40 MB) from [Releases](https://github.com/DonkeyHeng/Casebook/releases), extract the `Casebook` folder, and double-click `Casebook-Desktop.exe`. It opens in its **own window (WebView2)** and does not rely on your browser. |
| **Run from Source (Python 3.10+)** | Clone or download the repository and double-click **`start.cmd`** in the root folder (checks dependencies, starts the local server, opens the app window, and stops the server when the window closes). |
| **Troubleshooting** | Refer to **[docs/install.md](docs/install.md)** (plain-text version in [`docs/install-text.txt`](docs/install-text.txt)). |

**Launch via CLI:**
```bash
git clone [https://github.com/DonkeyHeng/Casebook.git](https://github.com/DonkeyHeng/Casebook.git)
cd Casebook
python -m pip install -r requirements.txt
python -m app.api   # Automatically opens the app window (default: [http://127.0.0.1:8000](http://127.0.0.1:8000))
```

### 2. Three-Step Workflow

1. **Run Simulation**: Open your `.apw` model in Aspen Plus and complete a simulation run.
2. **Read Case**: Click **"Read Current Case"** in the Casebook sidebar and wait for the staged progress bar to finish (~15–40 s for 100 streams).
3. **Inspect & Export**: Browse streams, equipment tabs, flowsheets, pipe sizing, or balance reports in the sidebar; export multi-sheet Excel workbooks, Word reports, or AI analysis packages from the top toolbar.
   > *Tip: Before using Pipe Sizing for the first time, click **"Standard Tables"** in the top-right corner of the Piping page to import your self-supplied GB/T CSV tables (see FAQ #3). Custom material and corrosion allowance rules can be configured under **"Piping Classes"**.*

### 3. Offline Demo (No Aspen Plus Required)

On first launch, the local database is empty and the home page displays an onboarding prompt. The repository bundles a sanitized real-world abnormal simulation snapshot (**121 streams, 77 blocks, 214 connections, 401 column stage rows, and 7 pumps**, featuring a dry tray in `T0202` and component non-convergence in `T0205`):

- **Using the desktop bundle**: Double-click `Casebook-Desktop.exe` and click **"Load Sample Case"** on the home screen (or run `Casebook-Desktop.exe --sample`). Any existing database is safely backed up to `cases.db.bak`.
- **Using Source Code**:
  ```bash
  cp data/cases.db data/cases.local.db   # Back up existing local data if present
  cp samples/case-23-error.db data/cases.db
  python -m app.api
  ```
  *See [`samples/README.md`](samples/README.md) for sample details.*

---

## Architecture & Feature Overview (8 Layers · 61 Capabilities)

Casebook spans an end-to-end **Read → Store → View → Compute → Report → Export → Operate → Verify** architecture, with all views and exports driven from a single local data source:

| Layer | Scope | Core Responsibility |
| :--- | :--- | :--- |
| **1. Data Acquisition & Judgment** | Read-only COM attachment, full-flow extraction, background progress, unit normalization, three-state judgment, error impact tracing | Answers *"Where does the data come from, is it trustworthy, and which streams are affected by errors?"* |
| **2. Local Storage & Identity** | SQLite multi-case snapshots, `sha256` model content hashing, offline historical recomputation & migration | Answers *"Where is data stored, how are models isolated across cases, and how do old cases upgrade?"* |
| **3. UI Exploration & Visualization** | Home summary cards, stream & composition drawers, 9 equipment-family tabs, column profiles, interactive flowsheet, multi-case comparison | Answers *"How can engineers inspect flowsheets and pinpoint anomalies quickly?"* |
| **4. Engineering Calculations** | Pump parameters & $\text{NPSH}_a$, design head & line friction, pump selection ledger, 10-step pipe sizing, `V1–V14` code checks | Converts raw simulation outputs into transparent sizing parameters and code validation findings |
| **5. Balances & Reports** | Process section (battery-limit) confirmation, two-tier mass balance checks, energy summary, dual-volume Word reports | Produces print-ready Material & Energy Balance reports aligned with engineering documentation standards |
| **6. Exports & Integration** | 7/8-sheet Excel workbook, family-specific Excel export, AI analysis pack (`.zip`), 10-column pipe schedule Word, CLI interface | Supports archival deliverables, spreadsheet workflows, and LLM-assisted pump selection/analysis |
| **7. Runtime & Security** | Zero-build HTTP server, localhost request guards, single-source versioning & manual update check, path isolation, single-file packaging | Ensures safe local execution, zero cloud leakage, and lossless upgrades |
| **8. Tooling & Quality Assurance** | 825 offline regression self-tests (including COM node-tree fixture replay), headless dual-theme UI sweep, pre-release secret scanner, Aspen COM probes, column diagnostics | Verifies calculation/judgment changes offline and prevents accidental data leaks before release |

### Key Capabilities in Detail

#### Data Acquisition, Diagnostics & Storage
- **Background Extraction with Live Progress**: Dedicated worker threads initialize COM independently and extract data in stages (`Streams → Blocks → Judgment`), reporting live object counts and current tags (~15–40 s for 100 streams; ~76 s for 1,000 streams).
- **Unit Normalization & Gap Diagnostics**: Normalizes values to `℃ / bar / kg·h⁻¹ / kmol·h⁻¹ / m³·h⁻¹ / kW / m²` while preserving raw Aspen values and `UnitString` metadata. Unrecognized units remain blank and are explicitly itemized in completion banners and exports.
- **Single-Entry Three-State Judgment & Impact Analysis**: Consolidates run status codes, block-level `BLKSTAT`, and tear-stream status into `OK / Warning / Error` verdicts with raw Aspen diagnostic messages (non-converged runs are stored and highlighted in yellow, and excluded from default comparisons/statistics). Automatically traces block errors across the process topology to flag affected inlet/outlet streams as unusable.
- **Equipment-Wide Enthalpy Balance Self-Check**: Evaluates $\Delta H = \Sigma H_{\text{out}} - \Sigma H_{\text{in}}$ per equipment family—heaters/flash drums against heat duty (`1%` relative AND `0.5 kW` absolute tolerance), two-stream heat exchangers on the cold side, pumps/compressors via $\Delta H / W_{\text{shaft}}$ ratios, columns against `Q_cond + Q_reb`, reactors against heat of reaction, and adiabatic units against `0.5%` of max stream enthalpy.
- **CAS Component Registry & On-Demand Composition**: Parses `COMPONENTS MAIN` and `DSET CASN` directly from model files to index components by CAS number (preventing ID mismatches across models), displaying bilingual labels (`Chinese Name + CAS`) with manual entry for custom components. Stream compositions load on demand (~264 ms per stream, 54 COM calls) and cache locally, or can be read in bulk via the *"Read all stream compositions"* toggle.
- **Content-Hash Identity & Offline Migration**: Identifies models by file `sha256` hash to safely inherit manual $p_v$ entries and section boundaries across runs. When rules evolve, `python -m tools.repair_cases --apply` recomputes verdicts, enthalpy checks, and derived parameters offline without re-opening Aspen.

#### Visualization & Multi-Case Comparison
- **Nine Equipment-Family Tabs**: Groups dozens of Aspen block types into **Columns, Heat Exchangers, Separators, Reactors, Pumps, Turbines, Fluid Movers, Mixers/Splitters, and Others** (distinguishing pumps `P*` from turbines `K*`), sorted by `Error → Warning → OK` by default.
- **Tray-by-Tray Column Profiles & Interactive Flowsheet**: Displays interactive temperature, pressure, vapor/liquid flow, and duty profiles synchronized with tray data tables to spot dry trays or temperature jumps. Auto-generates layered process topologies with zoom/pan, two-hop upstream/downstream focus, keyboard shortcuts (`+ / − / 0 / Esc`), and vector **SVG** / high-DPI **PNG** export.
- **Multi-Case Comparison**: Compares any two historical cases side by side (`Base / Compare / Delta`) by stream or block, highlighting status transitions and enthalpy balance changes while leaving missing entries blank rather than zero-padded.
- **Themes & Accessibility**: Supports System / Light / Dark themes, persistent window geometry, automatic server shutdown on window close, ARIA landmarks, arrow-key table scrolling, and `Esc` focus restoration.

#### Engineering Calculations (Pumps & Piping)
- **Pump Parameters & $\text{NPSH}_a$**: Extracts flow, head (`HEAD_CAL` preferred), efficiency, shaft power, viscosity, and nozzle conditions; computes available $\text{NPSH}_a = (p_{\text{in}} - p_v)\times 10^5 / (\rho g)$ independently (ignoring unreliable native `NPSH-AVAIL` values and skipping two-phase inlets with explanations) and matches suggested motor ratings (`0.95` efficiency, `1.10` margin). Vapor pressure $p_v$ supports automatic extraction (compatible with V11 and V12.1+ layouts), manual entry, and cross-case inheritance.
- **Design Head, Line Friction & Selection Ledger**: Separates Aspen pressure-rise head from **Design Head** $H = \Delta p/(\rho g) + \Delta Z + \Sigma h_f$ and rounded selection head $\lceil 1.1 H \rceil$. Pulls pipe ID from the Piping module and inlet viscosity to compute friction loss $\Sigma h_f$ via Colebrook iteration, Gu Yuzhen correlation, or manual $\lambda$. The Selection Ledger persists user-chosen pump models, materials, motors, and $\text{NPSH}_r$ while dynamically re-evaluating adequacy against the active case.
- **Vendor Lookup & AI Pump Selection Prompt**: Provides one-click vendor catalog launch (sending zero local data), clipboard parameter copy, and a unified **AI Pump Selection Prompt** shared across the UI button, family Excel sheet, and AI pack (`pump_prompt.md`).
- **10-Step Pipe Sizing & `V1–V14` Code Checks**: Executes phase detection → service code → velocity selection → ID sizing & DN rounding (`DN15–DN1000`, welded pipe above `DN600`, `5%` tolerance at `DN1000`) → design P/T (`T_op + 20 ℃`; `-H` insulation at or above `65 ℃`, `-C` cold insulation for cryogenics) → allowable stress lookup → GB/T 20801.3 pressure wall thickness + corrosion allowance, mill tolerance, and process minimum thickness floor → HG/T 20519.6 piping class code. Runs **`V1–V14` engineering checks** covering pump suction/cavitation limits, vacuum (`< 95 kPa` abs) external buckling screening, two-phase homogeneous dynamic head limits & stratified flow warnings, high-P/T stainless steel `R1E` upgrades, non-standard tags, and zero-flow lines.

#### Material & Energy Balances & Multi-Format Exports
- **Process Battery Limits & Two-Tier Mass Balance**: Infers draft process sections from equipment tag prefixes; once confirmed by the engineer, validates both **Battery-Limit Mass Balance** ($\Sigma \text{In} = \Sigma \text{Out}$ per section, primary table) and **Block-Level Mass Balance** (auxiliary table) at `0.5%` relative tolerance (exempting trace flows `< 1 kg/h`), generating stream-column wide tables and annualized summaries (`8,000 h/a`, `t/a`).
- **Dual-Volume Word Balance Reports**: Exports standalone `.docx` reports for **Material Balance** (cool blue theme) and **Energy Balance** (warm brick-red theme), each with independent cover pages, TOCs, headers/footers, three-line tables, light-red highlighting for out-of-tolerance rows, and figure placement notes.
- **Versatile Export Formats**:
  - **Full Excel Workbook**: 7 worksheets (`Summary, Streams, Blocks, Pumps, Connections, Column Profiles, Pipe Calculation Sheet`, plus `Unsized Streams` when applicable).
  - **Equipment-Family Excel**: Matches the active UI tab columns; Pump export includes full selection columns plus an `AI Pump Prompt` worksheet.
  - **10-Column Pipe Schedule Word**: Formatted to design-institute appendix standards with a dedicated `Verification Remarks` column (`--audit` CLI flag prints item-by-item check details).
  - **AI Analysis Pack (`.zip`)**: Bundles `README.md` (data dictionary & run summary), `metrics.csv`, `streams.csv`, `blocks.csv`, `case.json`, plus conditional `column_profiles.csv`, `composition.csv`, `pumps.csv`, `pump_prompt.md`, and `pipe_schedule.csv`—strictly preserving empty strings for nulls and original Aspen units for full auditability.

---

## Engineering Scopes & Known Limitations

When referencing Casebook outputs in coursework, competitions, or engineering studies, keep the following boundaries and prerequisites in mind:

- **Read-Only Analysis Tool**: Does not perform closed-loop flowsheet optimization or write parameters back to Aspen Plus.
- **Prerequisites**:
  - Pipe sizing requires importing three user-supplied CSV tables extracted from **GB/T 17395-2024** and **GB/T 20801.1-2025**;
  - Pump $\text{NPSH}_a$ requires configuring a `pv` property set in Aspen Plus (or entering $p_v$ manually in the UI);
  - Component mass flows in Material Balance reports require checking *"Read all stream compositions"* prior to extraction (otherwise molar flows are reported).
- **Pipe Sizing Boundaries**: Supports nominal diameters up to **DN1000** (larger streams are flagged for parallel lines or custom design). Vacuum buckling (`V11`) provides a long-cylinder elastic buckling screening floor and **does not replace formal external-pressure vessel or pipe stress analysis**. Friction head loss $\Sigma h_f$ is currently evaluated per pump suction/discharge circuit rather than across a plant-wide hydraulic network. Two-phase sizing uses an `8 m/s` baseline velocity plus homogeneous dynamic head limits, yielding conservative diameters.
- **Pump Selection Boundaries**: Computes system-available $\text{NPSH}_a$ only; vendor-required $\text{NPSH}_r$ must be obtained from manufacturer curves and entered into the selection ledger (default `0.5 m` margin). Suggested motor sizes are rounded up from standard engineering motor ratings (`0.95` efficiency, `1.10` margin) and are not mandatory code selections; reverse-operation pumps (`shaft power ≤ 0`) are excluded from motor matching.
- **Validation Coverage**: COM extraction and pump calculations have been verified against Aspen Plus V15 under `MET` / `METCBAR` unit sets; `ENG` unit conversions are derived by definition and have not yet been tested on a live `ENG` model.

---

## System Requirements & Local Data Governance

### Runtime Environment
- **Operating System**: Windows 10 / 11 (requires Windows COM; macOS and Linux are not supported).
- **Simulation Software**: Aspen Plus **V15** (tested release; node paths compatible with V11 / V12.1+ layouts), licensed separately.
- **Desktop Session**: Must run inside an **interactive local desktop session** at the **same privilege level** (standard vs. administrator) as Aspen Plus.
- **Browser**: Modern Chromium-based browser (Edge / Chrome; zero-build frontend, no Node.js required).

### Local Data & Security Guards
- **Path Isolation**: Read-only web assets are separated from writable user data under `data/` (`data/cases.db`, standard CSVs under `data/preview/std/docx/`, piping classes under `data/config/piping-classes.json`, and sections under `data/config/sections.json`). **All `data/` files are git-ignored—preserve `data/` when upgrading.**
- **Local Server Hardening**: Binds exclusively to localhost, enforces `Host` allowlisting against DNS rebinding, requires custom headers and `Origin` checks on state-mutating requests, and caps payload sizes (`256 KB` for JSON, `16 MB` for standard table imports) so malformed requests never wipe user configurations.

---

## Frequently Asked Questions (FAQ)

<details>
<summary><strong>1. "Read Current Case" fails to connect to Aspen Plus</strong></summary>

Check the following three items in order:
1. Ensure Aspen Plus is open with a simulation file (`.apw` / `.bkp`) loaded.
2. Ensure both programs run in an interactive local Windows desktop session (not a restricted sandbox or isolated remote session).
3. Ensure Casebook and Aspen Plus run at the same privilege level (do not run one as Administrator and the other as a standard user).
You can also run `python tools/probe_aspen.py` to diagnose COM moniker visibility.
</details>

<details>
<summary><strong>2. Why do stream compositions load only when clicked?</strong></summary>

Reading full stream composition (vapor/liquid mole fractions plus component mass flows) requires 54 COM calls per stream; reading 121 streams upfront increases capture time from seconds to tens of seconds. Casebook therefore loads compositions on demand (~264 ms first click, 0 ms cached).
- **Tip**: To include component mass flows in Material Balance reports, check **"Read all stream compositions"** beneath the sidebar read button before capturing, or run `python tools/fill_composition.py` while Aspen remains open.
</details>

<details>
<summary><strong>3. Why does Pipe Sizing report "Standard tables not loaded"?</strong></summary>

Pipe sizing relies on pipe OD/wall-thickness series from **GB/T 17395-2024** (Tables 1 & 4) and allowable stresses from **GB/T 20801.1-2025** (Table B.1). **Because national standard texts are copyrighted, these tables are not distributed with the repository or installer.**

**Three-step setup:**
1. Prepare three UTF-8 CSV files from your licensed standard texts: `t1-17395-table1.csv`, `t4-17395-table4.csv` (requiring `D_mm`, `S_mm` columns), and `t3-20801-tableB1.csv` (requiring `标准`, `牌号`, and `许用应力_<temp>` columns).
2. Click **"Standard Tables"** in the top-right corner of the Piping page and import the CSVs. Casebook validates schemas/row counts and hot-reloads them atomically without requiring a restart. Unsure about column names? The dialog offers **"Download CSV template"** (`samples/pipe-standards-template/`, headers plus 2 fictional rows only — not importable as-is) and **"Copy filling prompt"**.
3. See [`docs/pipe-standards-index.md`](docs/pipe-standards-index.md) for clause/page indices and [`docs/pipe-standards-extract-prompt.md`](docs/pipe-standards-extract-prompt.md) for table extraction prompts.
</details>

<details>
<summary><strong>4. Why are pump parameters or NPSHₐ shown as `—`?</strong></summary>

- **Entire table blank**: Likely an older snapshot captured before pump extraction was added; re-read the case or run `python -m tools.repair_cases --apply` to recompute from stored raw values.
- **Only $\text{NPSH}_a$ blank**: Requires inlet bubble-point vapor pressure $p_v$ (`PBUB`). Enter it manually in the table (or inherit it from an earlier case of the same model), or configure a `pv` property set in Aspen Plus including `PL` and `PBUB` and enable it in stream report options (see [`docs/pump-selection-plan.md`](docs/pump-selection-plan.md) §3.6).
- **Two-phase pump inlet**: Cavitation formulas do not apply to two-phase suction; Casebook automatically skips $\text{NPSH}_a$ and explains why in the tooltip.
</details>

<details>
<summary><strong>5. Why is the selected pipe wall thicker than the hoop-stress formula value?</strong></summary>

After computing pressure-retaining thickness per GB/T 20801.3 and adding corrosion allowance and negative mill tolerance, Casebook enforces a **process minimum wall thickness floor** (stiffness-based tiers, not standard values: `2.0 mm` for `DN10–25`, `3.0 mm` for `DN32–100`, `3.5 mm` for `DN125–150`, `4.5 mm` for `DN200–300`, `5.5 mm` for `DN350–600`, `10 mm` for `DN700–1000`) to ensure weldability and mechanical rigidity. Enable **"Show Calculation Details"** on the Piping page to inspect every intermediate thickness column.
</details>

<details>
<summary><strong>6. Other Questions (Material Selection, Default Velocities, Non-Converged Runs)</strong></summary>

- **Does Casebook auto-select pipe materials?** No. Corrosivity depends heavily on concentration, trace impurities (e.g., chlorides), and temperature. Casebook applies the material and corrosion allowance rules you define in `data/config/piping-classes.json` (defaulting to `S30408` when unconfigured). Available grades follow the imported Table B.1 — whatever grades you load become selectable, not just the five built-in ones.
- **Can default velocities be changed?** Yes, adjust them live in the top parameter bar of the Piping page (defaults: liquid `1.5 m/s`, gas `15 m/s`, two-phase `8 m/s`, pump suction `0.8 m/s`).
- **Can non-converged simulations be imported?** Yes. They are saved to the database with yellow/red warning banners, isolated from default statistics/comparisons, and retain all raw Aspen error text and stream impact flags.
</details>

---

## Repository Structure, CLI Reference & Documentation

```text
Casebook/
├── start.cmd            # Windows launcher (checks environment, starts server, opens app window)
├── requirements.txt     # Python dependencies (pywin32, openpyxl, python-docx, websocket-client)
├── app/                 # Core source: aspen (COM), store (SQLite/impact), design (engines), api (HTTP), web (UI), cli
├── tests/               # Regression suite (pytest): judgment, enthalpy balance, pipe sizing, pump, HTTP, DB consistency
├── tools/               # Maintenance & QA: selftest entry, repair_cases, ui_sweep, check_public_safe, COM probes, packagers
├── docs/                # Technical docs (installation, HTTP API, Aspen field maps, sizing/balance specs, UI style)
├── samples/             # Sanitized offline abnormal simulation snapshot for demo & testing
├── assets/              # Application icons and static resources
├── standards/           # [Local only] Self-supplied GB/T standard PDFs (git-ignored)
├── model/               # [Local only] Personal Aspen simulation models (git-ignored)
└── data/                # [Local only] SQLite database, imported CSV standards, and JSON configs (git-ignored)
```

### CLI Tools Quick Reference

| Command | Description |
| :--- | :--- |
| `python -m app.cli --list` / `--read` / `--cases` / `--show N` | Headless CLI to list open Aspen models, capture the active case, or inspect saved cases (supports `--composition`) |
| `python -m tools.selftest [--db data/cases.db]` | Runs **848** standalone offline regression tests (**855** with `--db` database consistency checks), no Aspen or internet required |
| `python -m pytest tests/ -q` | The same suite under pytest (cases live in `tests/`: run one domain at a time, filter with `-k`) |
| `python -m tools.repair_cases [--apply]` | Recomputes verdicts, enthalpy checks, and derived parameters offline after rule updates (dry-run by default) |
| `python -m tools.balance_report --case N` | Batch-generates Material & Energy Balance Word reports for case `N` from the command line |
| `python -m tools.make_pipe_schedule --case N [--audit]` | Exports the 10-column Pipe Selection Schedule `.docx` (`--audit` prints itemized engineering checks) |
| `python tools/fill_composition.py` / `python -m tools.column_diag` | Batch-fills stream compositions while Aspen stays open / diagnoses column temperature jumps, flow oscillations, and dry trays |
| `python -m tools.ui_sweep` / `python -m tools.check_public_safe` | Headless Chromium Light/Dark UI sweep across all tabs / pre-release secret and internal-path scanner |
| `python -m tools.build_exe` / `python -m tools.make_release` | Packages standalone `Casebook.exe` and generates source + Windows release archives with SHA-256 hashes |

### Documentation Index
- **Getting Started**: [`docs/install.md`](docs/install.md) (Installation & Troubleshooting) · [`docs/README.md`](docs/README.md) (Documentation Index)
- **Engineering Methods**: [`docs/pipe-selection-plan.md`](docs/pipe-selection-plan.md) (Pipe Sizing) · [`docs/pump-selection-plan.md`](docs/pump-selection-plan.md) (Pump & $\text{NPSH}_a$) · [`docs/balance-report.md`](docs/balance-report.md) (Balance Reports)
- **Standards & Interfaces**: [`docs/pipe-class-codes.md`](docs/pipe-class-codes.md) (HG/T 20519.6 Codes) · [`docs/aspen-field-map.md`](docs/aspen-field-map.md) (Aspen Field Paths) · [`docs/api.md`](docs/api.md) (Local HTTP API)
- **Contributor Guides**: [`app/README.md`](app/README.md) (Module Layering) · [`docs/ui-style.md`](docs/ui-style.md) (UI Guidelines) · [`docs/conventions.md`](docs/conventions.md) (Project Conventions)

---

## Release History

**Latest Release: [v1.6.0](https://github.com/DonkeyHeng/Casebook/releases/tag/v1.6.0)** (See [Releases](https://github.com/DonkeyHeng/Casebook/releases) for binaries and full changelogs)

- **v1.6.0 (Native Desktop Window & Model-Identity Fix)**: Added a desktop shell `app/desktop.py` built on WebView2 — the app now opens in **its own window with no browser dependency and no console window**; startup/shutdown follows the window lifecycle, logs always go to `data/desktop.log`, geometry is stored in `data/window.json`, in-app exports are written by the host process to `data/exports/` (folder opened automatically) while external links are handed to the system browser, and the bottom-left "Quit" now closes the window right after confirmation. New `--data-dir` flag lets the bundled build and the source build share one `data/` folder (standard tables and case database); a missing standard table now reports "cannot compute ID" instead of silently leaving the pump ID blank. Model identity now falls back between **content hash and file name**, so hand-filled CAS numbers and pump selection records stay attached after the model file is modified. The bundled sample case was refreshed from the latest full-flowsheet read (121 streams / 77 blocks / 214 connections / 401 stage rows / 2178 compositions / 7 pumps with complete $p_v$, 18 component CAS entries) and its machine-specific model hash cleared so the sample stays portable; fixed the empty-state "Load Sample Case" button and the column-name bug in the sample builder.
- **v1.5.1 (Repo-Wide Architecture Refactor & Test Infrastructure, behavior and verdict rules unchanged)**: API routes moved to declarative tables split by domain, export layer extracted to `app/export/`, ten-step pipe sizing split into `app/design/pipe/` submodules, frontend migrated to native ES Modules with six per-page view modules, and regression cases migrated to `tests/` under pytest; CI added `ruff check` / `ruff format` gates plus a frontend module-dependency static check. The standards import page gained "Copy fill-in prompt / Download template CSV" entries and rejects importing the template verbatim; allowed material grades now follow Table B.1 (no longer limited to 5 preset grades); added Aspen COM node-tree fixtures with offline regression so reader node-path drift is caught by tests; probe scripts' `--help` no longer requires pywin32.
- **v1.5.0 (Third-Round Pipe Validation Revisions & Pump Selection Assistance)**: Switched vacuum detection to `< 95 kPa` absolute pressure, added long-cylinder external buckling screening, unified high-pressure/high-temperature stainless steel wall thickness under GB/T 20801.3 (eliminating temperature-thickness inversion), excluded pump discharge lines from cavitation velocity limits, and set thermal insulation threshold at `65 ℃` design temperature; added validation codes **`V10–V14`**, filtered non-material streams, and added a `5%` rounding tolerance at `DN1000`; expanded the Word Pipe Schedule to 10 columns with a dedicated `Verification Remarks` column. On the pump side, fixed bubble-point $p_v$ extraction under Aspen V11 node layouts (`PBUB\MIXED\LIQUID`), added a `Viscosity (cP)` column, and introduced the unified **AI Pump Selection Prompt**.
- **v1.4.0 (Code-Aligned Pipe Sizing & Dual-Volume Balance Reports)**: Derived design pressure from gauge operating pressure, enforced process minimum wall thickness floors, added homogeneous dynamic head limits and upstream liquid ID constraints for two-phase lines, extended nominal diameters to `DN1000` (welded pipe above `DN600`), and appended `-C` suffixes for cryogenic lines. Separated the Pipe Schedule into a standalone 3-line Word appendix, split Material and Energy Balance reports into independent `.docx` volumes, elevated convergence judgment as the home screen's primary focus, added ARIA accessibility landmarks, and introduced manual update checks.
- **v1.3.0 (Design Head, Line Friction & Pump Selection Ledger)**: Persisted pump piping inputs (pipe length, $\Sigma\zeta$, fitting counts, property overrides, $\lambda$ method) and selection ledger entries per model content hash, dynamically recalculating adequacy against the active case; supported Colebrook, Gu Yuzhen, and manual friction factor methods; redesigned the pump drawer into three collapsible panels sharing one scrollbar and added headless UI sweeps plus automatic legacy DB migration.
- **v1.2.1 (Write-Path Hardening)**: Prevented malformed payloads from clearing piping classes, switched sample loading to verified backups with atomic replacement, introduced versioned directory switching for standard tables, rejected `NaN`/`Inf` numeric inputs, and ensured secret-scanner errors fail explicitly.
- **v1.2.0 (Material & Energy Balance Reports)**: Added Word exports for Material and Energy Balance reports with draft section inference, manual battery-limit confirmation, two-tier mass conservation checks, and column/reactor enthalpy balance validation.
- **Early Milestones (Phase 1 & Phase 2)**: Delivered read-only COM attachment, SQLite multi-case storage, stream/9-family block/column profile/flowsheet views, multi-case comparison, baseline pipe sizing, pump $\text{NPSH}_a$ calculations, and Dark Mode.

<details>
<summary><strong>Click to view legacy version mapping (2026-11 version normalization)</strong></summary>

| Legacy Tag | Normalized Tag | Notes |
| :--- | :--- | :--- |
| `v1.0.0` | `v1.0.0` | Retained (initial release under former name) |
| `v1.0.1` | `v1.0` | — |
| `v1.0.2` + `v1.0.3` | `v1.0.1` | — |
| `v1.1` | `v1.1` | Unchanged |
| `v1.2` | `v1.1.1` | — |
| `v1.3` | `v1.1.2` | — |
| `v1.4` | `v1.1.3` | — |

</details>

---

## Contributing & Contact

Issues and Pull Requests are welcome:

1. Read [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md), [`app/README.md`](app/README.md), and [`docs/conventions.md`](docs/conventions.md) before contributing.
2. After modifying judgment rules, unit conversions, or engineering calculations, keep all five gates green: `ruff check .`, `ruff format --check .`, `python -m pytest tests/ -q` (equivalent entry point: `python -m tools.selftest`), and `node tools/check_render.mjs`; run `python -m tools.check_public_safe` before committing.
3. Never commit `.apw` / `.bkp` simulation files, copyrighted GB/T standard PDFs/CSVs, or local `data/` files.
4. Report security vulnerabilities privately via [`.github/SECURITY.md`](.github/SECURITY.md).

- **Contact**: QQ `2655972249` / WeChat `User_DonkeyHeng` (GitHub Issues are preferred for technical discussions).
- **License**: Distributed under the [Apache License 2.0](LICENSE).