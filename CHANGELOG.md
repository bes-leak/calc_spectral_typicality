# Changelog

## 0.3.0

### Added
- "⤓ to field": mirror the typicality assessments into an integer attribute of
  the layer (`typicality`), so they can be filtered, styled and labelled by.
  Turning it on asks first and backfills everything assessed so far; afterwards
  manual, single, batch and imported assessments all keep it in step. Features
  with no assessment are set to NULL. Turning it off stops further writes and
  leaves the existing values alone.

### Fixed
- An empty string in the identifier field produced the key `""` instead of
  falling back to the feature id, so every such feature shared one key and their
  assessments overwrote one another. File-based layers return NULL and were
  never affected; memory and GeoPackage layers could be.

## 0.2.0

### Added
- Library of class profiles: several classes are held in the project at once and
  the right one is picked by the feature's class field.
- Typicality assessment of a feature against its class profile: spectral angle,
  Mahalanobis distance with a chi-square p-value, per-pixel SAM, inlier
  fraction, Jeffries-Matusita distance and histogram intersection, folded into a
  verdict of 1 / 2 / 3 with adjustable weights, thresholds and aggregation mode.
- Batch run over a whole layer, with a preview of what will be processed, a
  progress dialog that can be cancelled, and a CSV of the full breakdown.
- Class separability matrix: every feature scored against every profile, with a
  summary of median scores per (class × profile) pair.
- JSON v2 export: one record per feature, with attributes, outline, per-band
  statistics, mean vector, covariance and the typicality assessment. Assessments
  can be imported back from JSON or from a batch CSV.
- Recomputation of the stored profiles against a different raster, using the
  outlines they were built from.
- Highlighting on the map of the features a profile was built from.
- Test suite covering the metrics, profiles, library, batch run and binning.
- Documentation in English and Russian.

### Changed
- The interface is now in English.
- Raster windows are read in stripes of 2M pixels and thinned above 300k pixels,
  so a large feature no longer exhausts memory.
- Layer-wide histograms use a decimated sample instead of loading the raster in
  full, and only counts/edges are cached.
- Assessments are written to the project in one flush instead of one per record,
  which removes the quadratic cost on large layers.
- Settings and assessments follow a layer by its source, so reopening a layer no
  longer loses them when QGIS regenerates its id.
- New plugin icon.

### Fixed
- The toolbar button was blank: the icon was loaded through a Qt resource that
  was never imported.
- `unload()` could raise and leave the plugin unremovable until QGIS restarted.
- The plugin crashed at load time on a fresh profile, where the locale setting
  is unset.
- Metric scaling: the linear SAM scale made a verdict of 3 unreachable in
  practice.
- The separability run raised when thresholds were left unset.
- Importing assessments now checks that the keys match the layer, instead of
  reporting success and filing the records under keys nothing will ever read.

## 0.1

- Initial version: per-feature spectra and band histograms.
