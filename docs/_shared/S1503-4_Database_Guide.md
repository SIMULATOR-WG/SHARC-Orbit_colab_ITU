# The S.1503-4 examination database: what is new and how it fits together

*July 2026. Reference: EPFD Project Specifications (EPS) V41; worked example: the NEXT101 test case (ntc_id 127520101, a renumbered copy of a real MEO filing).*

## Why the database changed

S.1503-4 lets a non-GSO operator describe *how the system actually operates* far more precisely than before: service rules that vary by latitude, per-orbit exclusion zones, and — new in -4 — **minimum tracking duration**, which switches the downlink examination to a different algorithm. The old database schema (one row of operating parameters per notice) cannot carry any of that, so the data model was rebuilt around two ideas:

1. **Operating-parameter masks** — XML files, one per frequency range, carrying the full operating regime (defined by the Recommendation itself).
2. **Examination scenarios** — a project extension grouping frequency ranges that share one operating regime, so a single notice can be examined under several regimes.

## What the Recommendation adds (the XML operating-parameter mask)

One XML file per frequency range, holding:

| Parameter | Varies by | Meaning |
|---|---|---|
| min_exclude | latitude, orbital plane | exclusion-zone angle (α₀) |
| min_elev | latitude, azimuth | minimum service elevation |
| max_co_freq | latitude | satellites one earth station uses co-frequency (Nco) |
| min_duration | latitude | minimum tracking time — non-zero switches the downlink to the track-duration algorithm |
| min_angle_at_es / min_angle_at_sat | — | angular separation constraints (optional, default 0) |
| max_co_freq_sat | — | earth stations one satellite serves co-frequency, uplink (optional, default: no cap) |
| es_density, es_distance | — | earth-station population for the uplink run |

Useful defaults: omit `min_duration` entirely to select the classic algorithm (do not write 0); `min_angle_at_es` is ignored whenever min_duration is non-zero; `es_density`/`es_distance` are only needed for *typical* earth stations. The all-orbits marker for min_exclude is an explicit `c="0"` — an empty attribute is invalid.

## What the project database adds beyond the Recommendation

**Scenarios** (`epfd_param` + `epfd_freq`): a scenario is a named group of frequency ranges sharing one operating regime. A notice may have several (e.g. user vs gateway operations, different orbital configurations via `orbit_set_id`, modifications via `act_code`). A scenario may mix up- and downlink bands. When operating-parameter masks are used, the old per-notice parameter columns in `epfd_param` are left empty — the XML is the authority.

**The mask registry** (`mask_info` + link tables): every mask — pfd (`P`), e.i.r.p. ES (`E`), e.i.r.p. satellite (`S`), and now operating parameters (`R`) — is registered in `mask_info` with its frequency range (in **MHz**; the old GHz storage is corrected). pfd/e.i.r.p. masks link to scenarios, orbits and earth stations through `mask_lnk1`/`mask_lnk2`; operating-parameter masks are registered per notice in `mask_lnk3`.

**The key design decision — no scenario→parameter-set link exists, on purpose.** Each set carries its own frequency range, and an examination resolves its set by containment: *the set whose range contains the examined band applies*. Two validation rules make this unambiguous: no two sets' ranges may overlap, and every examined band must fall inside exactly one set. A scenario with several bands may therefore touch several sets — legal as long as their values agree (a scenario shares one operating regime by definition).

**Retired elements**: `sat_oper` (per-latitude Nco table) is superseded by the mask's `max_co_freq` array; the duplicated operating fields in `non_geo` likewise. Note the old schema split Nco by direction — `nbr_op_sat` (down) and `nbr_sat_td` (up) — while the XML has one `max_co_freq` field; the split survives naturally because up- and downlink are always different bands, hence different sets.

## Worked example: NEXT101

Old data: one scenario "Entire" covering four bands with one parameter row (exclusion zone 5°, elevation 10°, Nco 3, ES density 2.82×10⁻⁷/km², spacing 1883 km). Requirement: 19.7–20.2 GHz must use tracking duration (2400 s); everything else stays classic. Result after conversion:

| scenario | bands | operating-parameter sets |
|---|---|---|
| 1 "Classic 17.8-18.6 & 27.5-30.0 GHz" | ↓17.8–18.6, ↑27.5–28.6, ↑29.5–30 | param 8 (17.8–18.6), param 9 (27.5–30) — identical values |
| 2 "Downlink 19.7-20.2 GHz track duration" | ↓19.7–20.2 | param 7 (with min_duration 2400 s) |

The three sets carry identical values except min_duration — the old schema could not express that difference at all, which is the whole point of the conversion.

## Practical notes

- **param_id shares the mask_id key space.** `mask_info` and the masks database are keyed on (ntc_id, mask_id) across *all* mask types, so operating-parameter sets must take ids free of the pfd/e.i.r.p. masks (NEXT101 uses 7/8/9 because 1 was a pfd mask).
- **One contiguous range per set.** A set cannot span disjoint bands: 17.8–18.6 and 27.5–30 need two value-identical sets, because a single 17.8–30 span would swallow the 19.7–20.2 band that belongs to a different set. (A future EPS revision could allow an array of ranges per set, collapsing such duplicates; under the current format the duplication is the accepted cost.)
- **emi_rcp is station-centric in the data**: `E` marks bands the non-GSO *space station emits in* (downlink, the pfd-mask bands) and `R` bands it *receives in* (uplink, the e.i.r.p.-ES-mask bands). Read it from the mask types, not from memory.
- **Latitude tables use nearest-point lookup** (linear interpolation for min_exclude), so a single point at latitude 0 legitimately covers −90…+90; boundary-accurate range conversion from the old `sat_oper` needs point pairs straddling each range edge.
- **Masks database storage**: each mask, including `R` sets, is a zip-compressed XML blob in the `masks` table, entry named `mask ntc_id <ntc> mask_id <id> <low>-<high> MHz.xml`.

