# Data manifest

One entry per dataset (source name, exact URL, retrieval date, checksum, licence, attribution text,
data kind: measured / modelled / reanalysis / satellite / static). Entries are added by the
data-processing scripts in `pipeline/`. This folder is tracked by git; `data/raw/` is not.

Current manifests:
- `zenodo_12720883.json`: Stage 1, the 13 files of Zenodo record 12720883 (checksums, licence, provenance).
- `embassy_processing.json`: Stage 2, extraction, cleaning rules, outputs and limitations for the US Embassy PM2.5 data.
- `gis_processing.json`: Stage 4, extraction, CRS decisions, grid integration method, column dictionary, outputs and licence notes for the GIS, CAMx and emissions data.
- `clarity_processing.json`: Stage 3, workbook structure, timestamp assumptions, cleaning rules, device inventory, outputs and limitations for the Clarity 2021 sensor data.
