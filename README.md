# Spectral Characteristics (calc_sp_ch)

A QGIS plugin that measures how well each vector feature matches the spectral
signature of its class on a raster image.

It samples the raster pixels inside every polygon, turns them into spectral
characteristics, builds a reference profile for each class, and scores every
feature against the profile of its own class. The verdict is a **typicality**
of 1 (typical), 2 (doubtful) or 3 (atypical), stored in the project and
exportable to JSON and CSV.

Documentation in Russian: [README.ru.md](README.ru.md)

---

## What it is for

The usual case is checking a map against imagery: forest stands, land parcels,
water bodies or crop fields have already been delineated and classified, and you
want to know which of them actually look like their class on the image. Features
that come back atypical are the ones worth re-checking: a stand that has been
felled, a parcel whose use has changed, a polygon drawn in the wrong place.

Unlike a classification, the plugin does not assign classes. It takes the class
you already have and asks how plausible it is.

## Requirements

- QGIS 3.22 or newer (developed and tested on 3.44 LTR)
- `numpy` — ships with every QGIS build
- `matplotlib` — present in the OSGeo4W/Windows and macOS builds, but not in
  every Linux distribution package. Without it the plugin still loads and
  everything except the plots keeps working; the plot area says so. On Debian or
  Ubuntu: `sudo apt install python3-matplotlib`.

The vector layer and the raster may be in different coordinate systems: geometry
is reprojected into the raster CRS before sampling. A reprojection failure is
reported rather than ignored.

## Installation

**From a ZIP.** Download an archive of this repository, then in QGIS:
*Plugins → Manage and Install Plugins → Install from ZIP*.

**From source.** Clone the repository into the QGIS plugin directory so that the
folder is named `calc_sp_ch`:

```bash
git clone https://github.com/bes-leak/calc_sp_ch.git
```

| System | Plugin directory |
|---|---|
| Windows | `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins` |
| Linux | `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins` |
| macOS | `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins` |

Then enable *Spectral Characteristics* in the plugin manager. The panel opens
from the toolbar button or *Vector → Spectral Characteristics*.

---

## Quick start

1. Load the raster and the vector layer, and make the vector layer active.
2. Open the plugin panel. Pick the raster in the combo box at the top: only
   **visible** raster layers are listed, in layer-tree order.
3. In **class:** choose the attribute that holds the class of a feature (for
   example `type_name`). If the layer is already styled with a categorized
   renderer, the field is picked up from the style automatically.
4. In **key:** choose a stable identifier field. The default key is the feature
   id, which shifts whenever the layer is re-saved; a real identifier field
   survives that. Values must be unique.
5. Select the features of one class on the map and press **★**. That builds the
   class profile: mean spectrum, covariance and per-band histograms. Repeat for
   every class.
6. Select a single feature and press **⚙ Auto-assess** to see the full
   calculation, or press **⚙ Batch over layer** to score the whole layer at once.
7. Press **💾 JSON** to export everything, or **▦ Separability matrix** to check
   how well the classes can be told apart in the first place.

## The panel

| Control | What it does |
|---|---|
| raster combo box | the raster the spectra are read from; only visible layers |
| `polygon` / `bbox` | sample the pixels inside the outline, or its whole bounding box |
| `bar` / `line` / `step` | plot style for the distribution |
| 🔍 Analyse | plot the spectrum of the selected feature |
| 💾 JSON | export the feature characteristics (see below) |
| **profile:** | the class profile library held in the project |
| ★ | build or update a profile from the selected features |
| 💾 / 📂 | save the profiles to a file, load them back |
| ↻ | recompute every profile against the raster now selected |
| ✕ | delete the selected profile |
| **class:** | the attribute holding a feature's class |
| **key:** | the attribute used as the feature key |
| 1 / 2 / 3 / not set | set the typicality of the selected feature by hand |
| ⤓ to field | also mirror the assessments into an attribute of the layer |
| ⚙ Auto-assess | assess one feature, showing every metric and its contribution |
| ⚙ Batch over layer | assess every feature whose class has a profile |
| ⬇ Import assessments | read assessments back from a JSON or CSV file |
| ▦ Separability matrix | score every feature against every profile |

Assessments and profiles live in the project properties, so they are saved with
the `.qgz` and need no editable vector layer. Nothing is written to your source
data unless you ask for it with **⤓ to field**.

### Mirroring the assessments into an attribute

Keeping the assessments in the project leaves your data untouched, but it also
puts them out of reach of everything QGIS does with attributes. Tick **⤓ to
field** and they are mirrored into a real integer field as well — `typicality`,
ten characters, which is exactly what a shapefile allows:

```
"typicality" = 3                          -- filter the atypical ones
"typicality" IS NULL                      -- what has not been assessed yet
```

From there the field behaves like any other: categorized styling, labels,
*Select by expression*, joins, export to any format.

- Turning it on asks first, creates the field if it is missing, and fills it
  with everything assessed so far.
- After that every assessment keeps it in step — by hand, single, batch or
  imported.
- A feature with no assessment gets NULL, not zero: a stale value from an
  earlier run is worse than an empty cell.
- Turning it off only stops further writes. The values already in the layer
  stay, because deleting a column nobody asked to delete would be worse.
- If you have the layer open for editing, the changes join your edit buffer and
  your undo stack, and you decide when to commit. Otherwise they are saved
  straight away.

The project remains the source of truth; the field is a copy of it. If the two
drift apart — because the field was off for a while, or someone edited it by
hand — switching the checkbox off and on again rewrites the whole column from
the project.

---

## How the assessment works

### Sampling

The feature outline is reprojected into the raster CRS and the sampling window
is snapped to the pixel grid. This matters: asking a provider for an arbitrary
rectangle returns *resampled* values, shifted by up to half a pixel, rather than
the image's own pixels. Pixels whose **centre** falls inside the outline are
kept, by an even-odd ray-casting test.

All bands share one validity mask, so row *i* of the resulting matrix is the
same pixel in every band — which is what makes covariance, SAM and Mahalanobis
meaningful. The window is read in stripes of 2M pixels, so peak memory stays in
the tens of megabytes whatever the size of the feature. Above 300 000 pixels the
sample is thinned uniformly, and that fact is recorded in the export.

### The class profile

From the pixels of a class the plugin computes the mean vector (the spectral
signature) and the covariance matrix. A sample covariance is singular when there
are few pixels relative to the number of bands, so it is always shrunk towards a
scaled identity matrix:

    S' = (1 - l) * S + l * (tr S / k) * I,    l = min(1, k / n)

The shrinkage coefficient `l` is stored in the profile: the higher it is, the
less the covariance can be trusted, and the panel warns when it gets large.

### The metrics

Every metric returns a raw value in its own units and a similarity in [0, 1].

| Metric | What it measures | Scaled by |
|---|---|---|
| Spectral angle (SAM) | angle between the mean vectors | `exp(-a / 0.15)` |
| Mahalanobis | distance of the feature mean from the profile | chi-square p-value, k d.f. |
| Per-pixel SAM | share of pixels within 0.10 rad of the signature | the share itself |
| Inlier fraction | share of pixels inside the 95% ellipsoid | the share itself |
| Jeffries-Matusita | separability of the two distributions | `1 - JM / 2` |
| Histogram intersection | per-band overlap on the profile's bins | the overlap itself |

All six are always computed, but the last two carry a weight of zero by default:
they are shown for interpretation and can be given weight in the dialog. The
four that do count are split evenly, two per group.

Two design decisions are worth knowing about:

- **SAM is scaled exponentially, not linearly.** Real angles between land cover
  classes fall in 0.02–0.3 rad, so a linear `1 - a/(pi/2)` would give 0.98 for
  any pair and distinguish nothing. `exp(-a/0.15)` gives 0.5 at 0.10 rad, the
  usual "same class" threshold in remote sensing.
- **Mahalanobis is measured against the spread of the class, not the standard
  error of the mean.** With thousands of pixels the latter would drive the
  p-value to zero for any feature at all. The question asked here is "is this
  feature plausibly one more realization of the class?", not "are the means
  statistically distinguishable?".

### The verdict

Similarities are folded into one score. Weights are split evenly between two
kinds of evidence — metrics on the mean spectrum and metrics on individual
pixels — so that two correlated mean-based metrics cannot outvote the pixel-based
ones and let a heterogeneous feature through as typical.

    score = sum(w_i * sim_i) / sum(w_i)          (sum mode, the default)
    score = exp(sum(w_i * ln sim_i) / sum(w_i))  (product mode, stricter)

A metric that could not be computed is excluded with a note and its weight is
redistributed; nothing is silently guessed. The score becomes a verdict:
`>= 0.66` is 1, `>= 0.33` is 2, otherwise 3. Weights, thresholds and the mode
are adjustable in the auto-assess dialog and are remembered between runs; the
batch run uses whatever was set there.

---

## Output

**JSON (v2)** — one record per feature: attributes, centroid, outline in the
raster CRS (as rings and as WKT), per-band statistics inside the feature and in
the background, the mean vector and covariance, and the typicality assessment.
The mean and covariance are there so the metrics can be recomputed outside QGIS.
Features with no pixels are kept, marked with `n_pixels: 0`, so the export stays
a complete copy of the layer.

**Batch CSV** — one row per feature with the score, the verdict and every
metric's raw value, similarity and weight. Written as UTF-8 with BOM and `;`
separators so that a localized Excel opens it without an encoding dance. In
pandas: `pd.read_csv(path, sep=';')`.

**Separability matrix** — two files: a detailed one (a row per feature, a column
per profile) and a summary with the median score for each (feature class ×
profile) pair, plus the share of features whose own profile scored best. If a
class does not win against itself, its profile or its delineation is the problem,
not the features.

Both JSON and batch CSV can be read back in through **⬇ Import assessments**.
The import refuses to proceed if the keys in the file match no feature of the
layer, and suggests which field they do look like.

---

## Development

The tests cover the pure-logic core — metrics, profiles, the library, the batch
run and binning — and need no QGIS, only `numpy`:

```bash
python -m unittest discover -s test -t .
```

On Windows, run them with the Python that ships with QGIS (`OSGeo4W Shell`), or
with any Python that has numpy installed.

The plugin is organised as:

| Path | What lives there |
|---|---|
| `calc_sp_ch.py` | the controller: QGIS wiring, dialogs, actions |
| `calc_sp_ch_dockwidget.py` | the panel and its signals |
| `raster_utils/` | raster sampling, grid snapping, histograms, mask overlay |
| `typicality/` | metrics, profiles, the library, the store, batch runs, dialogs |
| `export_json.py` | JSON v2 export and import |
| `vector_utils.py` | geometry reprojection and categorized-renderer helpers |
| `test/` | the test suite |

`typicality/` and `raster_utils/histogram.py` deliberately import nothing from
QGIS, which is what keeps them testable.

### Translations

The interface is in English. The loader looks for
`i18n/calc_sp_ch_<locale>.qm`, so a translation can be added without touching
the code: extract the strings with `pylupdate5`, translate the `.ts` in Qt
Linguist, compile it with `lrelease` and drop the `.qm` into `i18n/`.

## Licence

GNU General Public License, version 2 or (at your option) any later version.
See [LICENSE](LICENSE). QGIS itself is GPL-licensed, so a plugin that imports
PyQGIS has to be GPL-compatible.

Copyright (C) 2026 Evgeny Belousov.
