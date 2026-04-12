# ICR Soundbank Analyzer

Analytical tool for validating, comparing and merging piano soundbank datasets for the [ICR](https://github.com/alchy/ICR) synthesis engine. Supports both **physical modeling** and **additive synthesis** parameter sets.

Loads soundbank JSON files directly from the ICR GitHub repository, runs multi-dimensional analysis, detects anomalous samples, and exports cleaned/merged banks.

## Installation

```bash
# Clone or extract the project
git clone <repo-url>
cd icrviz

# Install dependencies
npm install
```

Requires **Node.js 18+**.

## Running

```bash
# Development server (http://localhost:3000)
npm run dev

# Production build
npm run build

# Preview production build
npm run preview

# Type check
npm run lint
```

## Data Source

The app fetches soundbank data from `https://api.github.com/repos/alchy/ICR/contents`:

- **Physical banks** (`soundbanks-physical/`): 88 notes (MIDI 21-108), each with 16 physical parameters (`f0_hz`, `B`, `gauge`, `T60_fund`, `T60_nyq`, `exc_x0`, `K_hardening`, `p_hardening`, `n_disp_stages`, `disp_coeff`, `n_strings`, `detune_cents`, `hammer_mass`, `string_mass`, `output_scale`, `bridge_refl`)
- **Additive banks** (`soundbanks-additive/`): 88 notes x 8 velocity layers, each with up to 60 partials (`k`, `f_hz`, `A0`, `tau1`, `tau2`, `a1`, `beat_hz`, `phi`, `fit_quality`)

No API key needed - uses public GitHub API.

## Usage

### 1. Select Mode and Load Banks

Switch between **Physical** and **Additive** tabs. Check one or more banks from the repository list. The first bank is auto-loaded.

Set the **active bank** (green radio button) — this determines which bank is used as the source when manually adding anchor notes.

### 2. Set Anchor Points

Anchors are reference samples that define the "ideal" interpolation model. The deviation of every other sample is measured against this model.

- **AUTO-SELECT**: Automatically picks anchors every octave (every 12 MIDI notes), choosing the sample closest to the cross-bank average at each point.
- **Manual**: Type a MIDI number (e.g. `55`) or MIDI/velocity (e.g. `55/127`) and click ADD. The note is taken from the **active bank**. Without velocity, all velocity layers are added.
- **Import**: Load a previously saved anchor JSON file to restore/merge anchor sets across sessions.
- **Export**: Save current anchors to `anchor-export-YYMMDDhhmm.json` with full parameter data.

Anchor badges show MIDI number, velocity (if set), and bank name. Minimum 2 anchors required for meaningful analysis.

When the same MIDI+velocity appears from multiple banks, parameters are merged using a **weighted average**. For additive mode, missing partials are filled from anchor notes that have them.

### 3. Analyze Deviations

The **Deviation Overview** chart plots every sample's deviation from the interpolated model (X = MIDI note, Y = deviation). Points above the threshold line are flagged as anomalies.

Each sample is enriched with:
- **Deviation** - Weighted RMS error vs interpolated model. For additive mode, lower partials are weighted more heavily (w_j = 1/(1 + j/5), per Chabassier's damping model). For physical params, log-ratio is used for scale-invariant comparison.
- **Z-Score** - how many standard deviations from the mean deviation
- **Isolation Score** - average distance to 5 nearest neighbors in (midi, deviation) space

### 4. Explore Cluster Projections

Three projection methods for 2D visualization of the parameter space:

| Method | Description | Best for |
|--------|-------------|----------|
| **PCA** | Principal Component Analysis - projects high-dimensional data onto top 2 variance axes via power iteration | Seeing overall structure and outlier clusters |
| **Spectral** | Physical: log(f0) vs decay ratio. Additive: spectral centroid vs spectral spread | Acoustic feature relationships |
| **Params** | Direct plot of any two parameters (selectable) | Investigating specific parameter correlations |

After correction, the view splits into **Before** / **After** side-by-side comparison.

### 5. Tension Graph

Force-directed graph where nodes = samples and edges = k-nearest-neighbor similarity in the full parameter space. Clusters form naturally:
- Green nodes = anchors
- Blue nodes = good samples
- Red nodes = anomalies

Click any node to select it for detail view.

### 6. Apply Dataset Correction

Four correction methods available:

| Method | How it works | Parameter |
|--------|-------------|-----------|
| **Threshold** | Removes samples with deviation above the set threshold | Deviation threshold (0.5%-30%) |
| **Z-Score** | Removes samples beyond N standard deviations from mean | Z-score limit (0.5-4.0 sigma) |
| **IQR** | Removes samples outside Q1 - k*IQR .. Q3 + k*IQR (proper quartile interpolation) | IQR multiplier (0.5-3.0x) |
| **Interpolate** | Keeps all samples, replaces outlier values with model-interpolated values | None |

Click **Apply Correction** to see results. The cluster projection updates to show before/after comparison.

### 7. Export

Click **Export Merged Bank** to download a cleaned JSON file. The export contains:
- Anchor samples with their original values
- Good samples with original values
- Anomalous samples excluded (or replaced with interpolated values in `interpolate` mode)

Output format matches the ICR engine's expected structure:
- Physical mode: `notes.m021` (MIDI-keyed)
- Additive mode: `notes.m021_vel0` through `notes.m021_vel7` (MIDI + velocity layer keyed)

Exported file is saved to the browser's Downloads folder.

## Physics-Informed Interpolation

The interpolation engine is based on the piano acoustics research of **Chabassier, Chaigne & Joly** (INRIA/ENSTA) and **Simionato et al.** (University of Oslo):

### Monotone Cubic Spline (Fritsch-Carlson)
Instead of naive linear interpolation, all parameter curves use **monotone cubic Hermite splines** — guaranteeing C1 continuity and no overshooting between anchor points. Falls back to linear for 2-point cases.

### Log-Space Interpolation
Parameters that span orders of magnitude are interpolated in **logarithmic space**:
- `f0_hz`, `B`, `T60_fund`, `T60_nyq`, `K_hardening`, `hammer_mass`, `string_mass`, `output_scale`, `exc_x0`, `bridge_refl`

This reflects the physics: inharmonicity B scales as `π³Ed⁴/(64TL²)`, decay rates follow `σ = b₁ + b₃ω²`, and most acoustic parameters vary exponentially across the keyboard.

Linear-space splines are used for exponents and coefficients (`p_hardening`, `disp_coeff`, `detune_cents`, `gauge`). Integer params (`n_strings`, `n_disp_stages`) are rounded.

### Perceptual Weighting
Harmonic deviation uses physics-informed weighting: `w_j = 1/(1 + (j-1)/5)`. Lower partials (fundamental, 2nd, 3rd harmonic) receive higher weight since they are perceptually dominant and physically more stable (higher partials decay faster per Chaigne & Askenfelt's damping model).

### References
- Chabassier, J., Chaigne, A. & Joly, P. (2013). *Time domain simulation of a piano.* ESAIM: M2AN.
- Simionato, R., Fasciani, S. & Holm, S. (2024). *Physics-informed differentiable method for piano modeling.* Frontiers in Signal Processing.
- Bank, B. & Välimäki, V. (2003). *Robust loss filter design for digital waveguide synthesis.* IEEE SPL.
- Chaigne, A. & Askenfelt, A. (1994). *Numerical simulations of piano strings.* JASA.

## Architecture

```
src/
  App.tsx                         Main UI, state management, data loading
  types.ts                        Type definitions (Sample, PhysicalParams, etc.)
  lib/
    analysis.ts                   Parsing, anchor selection, interpolation, correction
    dimensionality.ts             PCA, spectral projection, tension graph edges
  components/
    DatasetOverview.tsx            MIDI vs Deviation scatter plot
    ClusterProjection.tsx          2D projection scatter with before/after
    TensionGraph.tsx               Force-directed k-NN similarity graph (canvas)
    HarmonicVisualizer.tsx         Bar chart for additive harmonic amplitudes
    PhysicalVisualizer.tsx         Bar chart for physical parameter comparison
    SystemGuide.tsx                Built-in documentation
    ui/                            shadcn/ui components (button, card, tabs, etc.)
```

## Tech Stack

- **React 19** + **TypeScript 5.8**
- **Vite 6** (build + dev server)
- **Tailwind CSS 4** + **shadcn/ui** (Base UI)
- **Recharts** (charts) + **Canvas API** (tension graph)
- **Lodash** (data manipulation)
