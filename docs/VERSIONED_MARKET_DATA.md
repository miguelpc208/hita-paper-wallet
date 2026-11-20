# Versioned point-in-time market replay

HITA ships two checked-in, checksummed synthetic market datasets. The catalog shows each version's manifest, parent, source contract, instrument IDs, and permitted project use.

| Version | Coverage |
| --- | --- |
| `v1` | Original fictional equity and benchmark on the 79-session synthetic calendar. This is the default. |
| `v2` | All prior records, a second fictional equity with 158 observations, and a later correction to the first equity's 13 February close. |

From the project folder in PowerShell:

```powershell
py -3.13 -m hita_paper market catalog

py -3.13 -m hita_paper market quote --market-version v2 --instrument EQ1_SYNTHETIC --at 2026-02-13T16:00:00Z --known-as-of 2026-04-03T12:09:59Z
py -3.13 -m hita_paper market quote --market-version v2 --instrument EQ1_SYNTHETIC --at 2026-02-13T16:00:00Z --known-as-of 2026-04-03T12:10:00Z

py -3.13 -m hita_paper market actions --market-version v2 --instrument EQ1_SYNTHETIC --start 2026-03-01T00:00:00Z --end 2026-04-15T00:00:00Z --known-as-of 2026-04-03T12:00:00Z
```

The two quotes return €108.10 and €108.25 respectively. The newer correction was published at 12:00 UTC and became usable at 12:10 UTC on 3 April. The result includes the selected observation's effective, publication, and availability times, and lists only revisions available by the requested cutoff. Missing and stale observations have no qualified price.

The action query shows notices available by the knowledge cutoff with effective times in the interval after `--start` and through `--end`. It preserves the split ratio, dividend amount and payable time, and the delisting notice. Quote lookups match a specific scheduled open or close. Use the catalog to find instrument IDs, including `EQ2_SYNTHETIC` in `v2`.

## Research runs and source provenance

```powershell
py -3.13 -m hita_paper research --market-version v1 --db market-research.sqlite
py -3.13 -m hita_paper research --market-version v2 --db market-research.sqlite
```

The baseline research experiment still models EQ1 against the synthetic benchmark. Reports identify the exact market manifest and retain distinct run and decision IDs per version. Both reports can be held in the same database and read by their original run IDs. Seeded random comparisons use the same candidate draws across fixture versions in this lineage. This keeps changes to observed data separate from changes in the chosen comparison trades.

Previously frozen decisions retain their source manifest and original information cutoff. Later corrections can update a newly requested retrospective observation or shadow evaluation. Simulated execution prices use the original auction publication window.

The catalog's source contract covers project-generated fictional data for synthetic research and paper replay. Future provider integration requires documented rights for retention, derived data, and intended distribution, as well as timestamped revisions, stable instrument and listing identities, corporate-action terms, and raw-source hashes.
