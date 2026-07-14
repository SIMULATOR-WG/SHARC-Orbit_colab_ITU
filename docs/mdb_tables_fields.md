# Tables and fields used from the SRS and MASK MDBs

Document generated from source code (`src/srs_reader.py`,
`src/mask_converter.py`, `streamlit_app/lib/srs_inspect.py`). Lists the tables
and fields actually read from each Microsoft Access database (`.mdb`) via
the pure-Python `access-parser` library.

Reading conventions:

- Every row is read as text and converted by `_parse_int` / `_parse_float` /
  `_parse_bool`. Quotes and whitespace are stripped (`.strip().strip('"')`).
- `f_*` flags use `_parse_bool`: `Y/YES/TRUE/1` → true.
- Periods come in separate columns (day/hour/min/sec) and are summed to seconds.

---

## 1. SRS MDB (ITU/SRS orbital data)

Main NGSO filing database. Tables used: `non_geo`, `orbit`, `phase`,
`sat_oper`, `mask_info`, `grp`, `mask_lnk1`.

### 1.1 `non_geo` — NGSO system/notice

Functions: `read_srs_mdb`, `list_non_geo_systems`.

| Field | Parsed type | Use |
|-------|-------------|-----|
| `ntc_id` | str | Notice identifier; join key across tables |
| `sat_name` | str | Satellite/system name |
| `adm` / `admin` | str | Administration (country) |
| `ref_body` | str | Reference body (`T` = Earth) |
| `nbr_plane` | int | Number of orbital planes |
| `nbr_sat_td` | int | Total active satellites (density field; ≠ sats/plane) |
| `density` | float | Density (sats/km²) |
| `avg_dist` | float | Average distance between satellites |
| `f_x_zone` | bool | Exclusion zone flag |
| `x_zone` | float | Exclusion zone angle (°) |
| `f_constell` | bool | Constellation flag |
| `d_rcv`, `d_not`, `date_rcv`, `rcv_date`, `ntc_date`, `notice_date`, `d_sub`, `d_ntc`, `d_latest` | date | Date column to sort notices most-recent-first (first present/parseable one is used) |

### 1.2 `orbit` — orbital planes

Function: `read_srs_mdb`. Filtered by `ntc_id`.

| Field | Parsed type | Use |
|-------|-------------|-----|
| `ntc_id` | str | System filter |
| `orb_id` | int | Orbital plane identifier |
| `nbr_sat_pl` | int | Satellites per plane (real sats/plane) |
| `right_asc` | float | RAAN — right ascension of ascending node (°) |
| `inclin_ang` | float | Inclination (°) |
| `prd_ddd`, `prd_hh`, `prd_mm` | float | Orbital period (day, hour, min) → seconds |
| `apog`, `apog_exp` | float, int | Apogee = `apog × 10^apog_exp` (km altitude) |
| `perig`, `perig_exp` | float, int | Perigee = `perig × 10^perig_exp` (km altitude) |
| `perig_arg` | float | Argument of perigee (°) |
| `op_ht`, `op_ht_exp` | float, int | Operational altitude = `op_ht × 10^op_ht_exp` (km) |
| `f_stn_keep` | bool | Station keeping flag |
| `rpt_prd_dd`, `rpt_prd_hh`, `rpt_prd_mm`, `rpt_prd_ss` | float | Ground-track repeat period → seconds |
| `f_precess` | bool | Precession flag |
| `precession` | float | Precession rate (°/day) |
| `long_asc` | float | Longitude of ascending node (°) |
| `keep_rnge` | float | Station keeping range (°) |
| `f_sunsynch` | bool | Sun-synchronous flag |

### 1.3 `phase` — initial phases per satellite

Function: `read_srs_mdb`. Optional; absence → uniform phase per plane.

| Field | Parsed type | Use |
|-------|-------------|-----|
| `ntc_id` | str | System filter |
| `orb_id` | int | Orbital plane |
| `orb_sat_id` | int | Satellite index within plane |
| `phase_ang` | float | Initial phase (°), normalized mod 360 |
| `d_ref`, `t_ref` | int | Reference date/time; duplicate tie-break (largest wins) |

### 1.4 `sat_oper` — MAX_CO_FREQ per latitude band

Function: `read_sat_oper` (S.1503-4 Annex D, Step 19).

| Field | Parsed type | Use |
|-------|-------------|-----|
| `ntc_id` | str | System filter |
| `lat_fr` | float | Band start latitude (°), default −90 |
| `lat_to` | float | Band end latitude (°), default +90 |
| `nbr_op_sat` | int | Max simultaneous co-frequency satellites |
| `min_dur` / `min_duration` | float | MIN_DURATION; if ≠ 0 → `NotImplementedError` (§D.5.1.4.2 not implemented) |

### 1.5 `mask_info` — mask metadata

Functions: `read_mask_info`, `read_mask_assignment_all` (`f_mask` filter).

| Field | Parsed type | Use |
|-------|-------------|-----|
| `ntc_id` | str | System filter |
| `mask_id` | int | Mask identifier |
| `freq_min` | float | Minimum frequency (GHz) |
| `freq_max` | float | Maximum frequency (GHz) |
| `f_mask` | str | Type: `P` = PFD, `E` = EIRP, `S` = other |
| `f_mask_type` | str | Subtype: `A` = alpha, `O` = other |

### 1.6 `grp` — emission/reception groups

Functions: `read_group_for_mask`, `read_mask_assignment_*`, `srs_inspect`.

| Field | Parsed type | Use |
|-------|-------------|-----|
| `ntc_id` | str | System filter |
| `grp_id` | int | Group identifier |
| `emi_rcp` | str | Emission/reception (`E` = transmit); prioritizes mask link |
| `beam_name` | str | Beam name |
| `freq_min` | float | Minimum frequency (MHz → GHz) |
| `freq_max` | float | Maximum frequency (MHz → GHz) |
| `elev_min` | float | Minimum elevation (°) |

### 1.7 `mask_lnk1` — orbit/satellite → mask link

Functions: `read_mask_assignment`, `read_mask_assignment_all`,
`read_mask_assignment_per_sat`, `read_group_for_mask`.

| Field | Parsed type | Use |
|-------|-------------|-----|
| `ntc_id` | str | System filter |
| `grp_id` | int | Associated group (cross-references `grp.emi_rcp` for priority) |
| `orb_id` | int | Orbital plane; `-1` = wildcard (applies to all) |
| `sat_orb_id` | int / blank | Specific satellite; blank/0 = all sats of the orbit |
| `mask_id` | int | Linked mask |
| `seq_no` | int | Sequence; ordering tie-break |

---

## 2. MASK MDB (PFD masks)

Separate database holding the XML content of the masks. Table used: `masks`.

### 2.1 `masks` — PFD/EIRP masks

Functions: `read_pfd_mask_xml_from_mdb`, `load_pfd_masks_for_ids`,
`list_pfd_masks_from_mask_mdb`.

| Field | Parsed type | Use |
|-------|-------------|-----|
| `ntc_id` | str | System filter |
| `mask_id` | int | Mask identifier |
| `f_mask` | str | Type; listing keeps only `P` (PFD) |
| `f_mask_type` | str | Mask subtype |
| `sat_name` | str | Satellite name (label) |
| `mask` | BLOB (OLE) | Binary payload: ZIP (`PK\x03\x04`) containing the `<pfd_mask>` XML; falls back to raw XML |

The `mask` payload is decoded as latin-1, the ZIP is opened in memory, and the
first internal `.xml` is extracted and parsed by `load_pfd_mask_from_xml_content`.

---

## Appendix — EPFD limits MDB (separate)

Not SRS nor MASK, but read through the same path (`read_epfd_limits_from_mdb`,
`suggest_services_from_limits_mdb`). Tables: `rf_epfd_mask`
(`mask_argmt`, `rr_service`, `link_direction`, `rf_band_wdth`, `freq_min`,
`freq_max`, `rf_diam`, `mask_id`, `rr_reference`) and `epfd_time`
(`mask_id`, `mask_step`, `epfd`, `time_percent`, `rr_reference`).
