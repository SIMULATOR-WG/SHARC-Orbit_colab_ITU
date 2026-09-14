# NEXT101 — S.1503-4 test case with operating-parameter masks

Test case for the S.1503-4 examination data format: a notice whose operating
regime differs per frequency band, including the new **minimum tracking
duration** feature — something the pre-S.1503-4 database schema cannot
express. Built by converting a renumbered copy of a real MEO filing
(original ntc_id 123520182 / O3BNEXT1) to the structure prescribed by the
EPFD Project Specifications (EPS) V41.

## Identity

| | |
|---|---|
| ntc_id | 127520101 |
| sat_name | NEXT101 |
| Bands examined | down 17.8–18.6 and 19.7–20.2 GHz; up 27.5–28.6 and 29.5–30 GHz |

## Constellation

168 satellites at 8062 km circular (MEO), 11 planes:

| planes | inclination | LAN | sats/plane |
|---|---|---|---|
| 4 | 90° | 0/45/90/135° | 16 |
| 1 | 0° (equatorial) | 0° | 32 |
| 6 | 45° | 0/60/120/180/240/300° | 12 |

All planes are **station-kept** (`f_stn_keep='Y'`, repeat period
**0d 23h 56m 04s** — one sidereal day, ≈ five revolutions at this altitude —
keep_rnge 0.1°, no admin precession) — i.e. S.1503-4 orbit model **Case 2**:
repeating ground track, W_delta node sweep, repeating-orbit run duration.

Corrected source-data error: the original filing copy carried
rpt_prd = 0d 1h 54m, which is physically impossible (less than half of the
~4h48m orbital period at 8062 km); set to the sidereal-day repeat on all
planes. Also note the mixed inclinations (0/45/90°) preclude a strict common
J2 repeat without per-plane precession — the declared repeat is the design
intent held by station-keeping, an approximation the Case-2 model covers.

## Scenarios and operating-parameter sets

Two examination scenarios (epfd_param/epfd_freq), resolving to three
operating-parameter masks (f_mask='R') by frequency containment:

| scenario | bands | R-set | regime |
|---|---|---|---|
| 1 "Classic 17.8-18.6 & 27.5-30.0 GHz" | ↓17.8–18.6, ↑27.5–28.6, ↑29.5–30 | param 8 (17800–18600), param 9 (27500–30000) | classic; the two sets carry identical values |
| 2 "Downlink 19.7-20.2 GHz track duration" | ↓19.7–20.2 | param 7 (19700–20200) | **min_duration = 2400 s** → track-duration algorithm |

All three sets share: exclusion zone 5° (all orbits, `c="0"`), min elevation
10°, max_co_freq (Nco) 3, es_density 2.8182e-7 /km², es_distance 1883 km.
The only difference is min_duration in set 7. Sets 8 and 9 are value-identical
duplicates because a set has a single contiguous frequency range and one
spanning 17.8–30 would swallow the 19.7–20.2 band that belongs to set 7.

The old scenario schema is retired per the EPS: epfd_param's operating fields
are NULL (the XML is the authority), sat_oper is empty (superseded by the
max_co_freq array), non_geo's duplicated operating fields are NULL.

## Files in this package

| file | content |
|---|---|
| `127520101 SRS.MDB` | SRS in the EPS-prescribed structure: 2 scenarios, mask registry (mask_info in MHz, incl. the three R rows), mask_lnk1/2/3, orbit/phase with the enhanced columns (act_code, orbit_set_id) |
| `127520101 Masks.MDB` | 7 mask blobs: pfd 1 (17.8–18.6), pfd 4 (19.7–20.2), eirp SS 3 (17.8–18.4), eirp ES 6 (27.5–30), and R sets 7/8/9 (zip-compressed XML, entry `mask ntc_id ... mask_id ... MHz.xml`) |
| `Mask_param_id_{7,8,9}_OP_NEXT101.xml` | the operating-parameter masks as plain files (same content as the blobs) |
| `123520182 Masks.MDB` | the original v10-format masks data, kept for reference (pre-conversion identity; no operating-parameter masks) |
| `EPFD Project Specifications_Section_6.docx` | extract of EPS V41 section 6 — the database structure this case conforms to |
| `S1503-4_Database_Guide.md` | background: what S.1503-4 adds to the database, what the project database adds beyond the Recommendation, and practical notes |

## Notes for whoever runs or extends this case

- param_ids 7/8/9 (not 1/2/3): operating-parameter masks share the
  (ntc_id, mask_id) key space with pfd/e.i.r.p. masks, and low ids were taken.
- There is no scenario→set link table: each examined band resolves to the set
  whose range contains it (EPS validations: no overlapping set ranges; every
  examined band inside exactly one set).
- epfd_freq's emi_rcp is station-centric: 'E' = space station emits
  (downlink), 'R' = receives (uplink).
- max_co_freq_sat is deliberately omitted (uplink per-satellite ES cap):
  absence = no cap, which preserves the old-schema behaviour and is the
  conservative reading.
- Latitude arrays are single-point (a="0") — with nearest-latitude semantics
  that covers −90…+90.
