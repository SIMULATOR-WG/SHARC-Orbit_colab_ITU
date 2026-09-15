"""Brazil band occupancy — Anatel licensed stations + ITU SNS filings.

Survey of frequency occupancy in Brazil. Refresh pulls the latest Anatel
open-data zip and the latest Space IFIC SNS database from ITU. Selecting
systems draws the same shared-axis occupancy strips used on Aggregate,
including the common-overlap row.
"""
from __future__ import annotations

from pathlib import Path
import shutil

import pandas as pd
import streamlit as st

from lib import br_occupancy as br
from lib import theme
from lib.band_chart import st_bands_chart
from lib.manual import help_expander
from lib.state import set_persisted_state, use_persisted_state

st.set_page_config(
    page_title="Brazil occupancy · SHARC-Orbit",
    page_icon=":material/cell_tower:",
    layout="wide",
)
theme.inject()

st.title("Brazil band occupancy")
help_expander("brazil_occupancy")
st.caption(
    "Licensed occupancy in Brazil from "
    "[Anatel satélites](https://www.anatel.gov.br/dadosabertos/paineis_de_dados/"
    "espectro_e_orbita/satelites.zip) plus Brazilian / matching filings in the "
    "complete **SRS.mdb** from a BR IFIC ISO "
    f"([IFIC 3079 ISO]({br.brific_iso_url('3079')})), "
    "or the public weekly `ificXXXX.mdb` "
    "([ITU WIC 2026](https://www.itu.int/sns/wic/demowic26.html)). "
    "Pick systems to see occupied bands and their **common** overlap — the "
    "same strip chart as **Aggregate**."
)

prev = use_persisted_state("br_occupancy.form", {
    "sources": ["anatel"],
    "orbit": "all",
    "direction": "downlink",
    "rf_bands": [],
    "letter_bands": [],
    "freq_low_ghz": "",
    "freq_high_ghz": "",
    "chart_span": "filter",
    "system_ids": [],
    "query": "",
})


def _fmt_fetched(meta: dict) -> str:
    ts = meta.get("fetched_at") or "never"
    return str(ts).replace("T", " ").replace("Z", " UTC")


def _operates_caption(meta: dict) -> str:
    return (
        "operates in Brazil (any administration) · "
        f"service B/XR2/XAA {meta.get('n_srv_br', meta.get('n_srv_or_es_b', 0))} · "
        f"ES in B {meta.get('n_es_br', 0)} · "
        f"adm=B {meta.get('n_adm_b', 0)}"
    )


# ── 1. Catalogs ──────────────────────────────────────────────────────────────
st.subheader("1. Catalogs")
anatel = br.load_anatel_catalog()
sns = br.load_sns_catalog()
a_meta, s_meta = br.anatel_meta(), br.sns_meta()
c1, c2 = st.columns(2)
with c1:
    with st.container(border=True):
        st.markdown("**Anatel** · licensed stations / sub-bands")
        if anatel:
            st.markdown(theme.pill(f"{len(anatel)} systems", "ok"), unsafe_allow_html=True)
            if a_meta.get("fetched_at"):
                st.caption(f"Fetched {_fmt_fetched(a_meta)}")
            else:
                st.caption("Loaded from local `docs/satelites/` (click Refresh for the live zip).")
        else:
            st.caption("Not downloaded yet.")
        if st.button("Refresh Anatel zip", icon=":material/download:",
                     key="br_refresh_anatel"):
            with st.spinner("Downloading Anatel satelites.zip…"):
                try:
                    meta = br.refresh_anatel()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Anatel refresh failed: {exc}")
                else:
                    st.success(
                        f"Anatel catalog: **{meta['n_systems']}** systems "
                        f"({meta['bytes']:,} bytes)."
                    )
                    st.rerun()
with c2:
    with st.container(border=True):
        st.markdown("**ITU** · BR IFIC SRS (ISO) or weekly IFIC")
        kind = s_meta.get("kind") or ("ific" if s_meta.get("ific_no") else "")
        if s_meta.get("mdb"):
            n_tot = s_meta.get("n_notice_total")
            n_sys = len(sns)
            if kind == "srs":
                st.markdown(theme.pill(f"SRS · {n_sys} operate in Brazil", "ok"),
                            unsafe_allow_html=True)
                st.caption(
                    f"IFIC {s_meta.get('ific_no') or '—'} · "
                    f"{n_tot or '—'} notices in the file · "
                    f"{_operates_caption(s_meta)} · "
                    f"loaded {_fmt_fetched(s_meta)}"
                )
            else:
                st.markdown(
                    theme.pill(f"IFIC {s_meta.get('ific_no')} · {n_sys} operate in Brazil",
                               "ok" if n_sys else "warn"),
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"Weekly circular {s_meta.get('ific_date') or '—'} · "
                    f"{n_tot or '—'} notices in this IFIC "
                    f"(not the full SRS) · {_operates_caption(s_meta)} · "
                    f"fetched {_fmt_fetched(s_meta)}"
                )
        else:
            st.caption(
                "The complete **SRS** is `databases/SRS_Data/srsNNNN.zip` "
                "inside the BR IFIC ISO (split Access parts), not the "
                "public weekly `ificXXXX.mdb`."
            )
        iso_raw = st.text_input(
            "Path to ISO / bookshop zip / srsNNNN.zip / SRS folder",
            key="br_iso_path",
            placeholder="streamlit_app/data/br_occupancy/sns/srs3079",
            help="Paste a path on this machine: BR IFIC ISO, ITU bookshop zip, "
                 "srsNNNN.zip, a folder of split SRS .mdb files, or one .mdb.",
        )
        if st.button("Index this ISO / SRS", icon=":material/database:",
                     key="br_iso_index", disabled=not str(iso_raw or "").strip()):
            raw = str(iso_raw).strip().strip('"').strip("'")
            with st.spinner(
                "Extracting SRS.mdb from the ISO (if needed) and indexing "
                "systems that operate in Brazil…"
            ):
                try:
                    meta = br.ingest_local_iso(Path(raw))
                except Exception as exc:  # noqa: BLE001
                    st.error(f"SRS index failed: {exc}")
                else:
                    st.success(
                        f"SRS (IFIC {meta.get('ific_no') or '—'}): "
                        f"{meta.get('n_notice_total', 0)} notices, "
                        f"**{meta.get('n_systems', 0)}** operate in Brazil "
                        f"(service {meta.get('n_srv_br', 0)}, "
                        f"adm=B {meta.get('n_adm_b', 0)})."
                    )
                    st.rerun()
        if st.button("Refresh weekly IFIC", icon=":material/download:",
                     key="br_refresh_sns"):
            with st.spinner(
                "Downloading ificXXXX.mdb from ITU and indexing "
                "systems that operate in Brazil…"
            ):
                try:
                    meta = br.refresh_sns()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"IFIC refresh failed: {exc}")
                else:
                    st.success(
                        f"IFIC **{meta.get('ific_no')}**: "
                        f"{meta.get('n_notice_total', 0)} notices this week, "
                        f"**{meta.get('n_systems', 0)}** operate in Brazil "
                        f"(service {meta.get('n_srv_br', 0)}, "
                        f"adm=B {meta.get('n_adm_b', 0)})."
                    )
                    st.rerun()
        srs_files = st.file_uploader(
            "Or drop srsNNNN.zip — not ificXXXX.mdb, and not the 1.7 GB parts",
            type=["mdb", "zip"],
            accept_multiple_files=True,
            key="br_srs_upload",
            help="Prefer the path field: the split SRS is already at "
                 "streamlit_app/data/br_occupancy/sns/srs3079/ "
                 "(part1 = notices, part3 = frequencies). "
                 "Browser upload rejects files above the size limit.",
        )
        if srs_files and st.button(
            "Index uploaded SRS", icon=":material/database:", key="br_srs_index"
        ):
            dest = br.SNS_DIR / "_upload_srs"
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True, exist_ok=True)
            for item in srs_files:
                (dest / Path(item.name).name).write_bytes(item.getvalue())
            zips = list(dest.glob("*.zip"))
            mdbs = list(dest.glob("*.mdb")) + list(dest.glob("*.MDB"))
            target = zips[0] if len(zips) == 1 and not mdbs else dest
            with st.spinner("Indexing SRS (this can take several minutes)…"):
                try:
                    meta = br.ingest_local_iso(target)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"SRS index failed: {exc}")
                else:
                    st.success(
                        f"SRS: {meta.get('n_notice_total', 0)} notices, "
                        f"**{meta.get('n_systems', 0)}** operate in Brazil."
                    )
                    st.rerun()

if not anatel and not sns:
    st.info(
        "Refresh **Anatel** to load licensed occupancy in Brazil. "
        "Optionally refresh **ITU SNS** to overlay Brazilian filings from "
        "the latest Space IFIC."
    )
    st.stop()

# ── 2. Filter & pick ─────────────────────────────────────────────────────────
st.subheader("2. Systems")
src_opts = []
if anatel:
    src_opts.append("anatel")
if sns:
    src_opts.append("sns")
src_default = [s for s in (prev.get("sources") or []) if s in src_opts] or src_opts[:1]

f1, f2, f3 = st.columns(3)
with f1:
    sources = st.multiselect(
        "Source",
        options=src_opts,
        default=src_default,
        format_func=lambda s: {
            "anatel": f"Anatel licensed ({len(anatel)})",
            "sns": f"ITU SNS ({len(sns)})",
        }.get(s, s),
    )
with f2:
    orbit_opts = ["all", "GEO", "NGEO"]
    orbit = st.selectbox(
        "Orbit",
        orbit_opts,
        index=orbit_opts.index(prev.get("orbit") or "all")
        if (prev.get("orbit") or "all") in orbit_opts else 0,
    )
with f3:
    direction = st.radio(
        "Direction",
        options=["downlink", "uplink", "both"],
        index=["downlink", "uplink", "both"].index(prev.get("direction") or "downlink"),
        horizontal=True,
        format_func=lambda d: {
            "downlink": "Downlink (space→Earth)",
            "uplink": "Uplink (Earth→space)",
            "both": "Both",
        }[d],
    )

pool = []
if "anatel" in sources:
    pool.extend(anatel)
if "sns" in sources:
    pool.extend(sns)

# Letter band and explicit range work off the frequencies themselves, so they
# apply to ITU SNS notices too. The Anatel label exists only on licensed
# stations, so its control is hidden when no Anatel row is in the pool —
# otherwise it renders as an empty, unusable select.
_pool_ivs = {s.id: s.intervals(direction) for s in pool}
band_all = [
    b for b in br.LETTER_BAND_NAMES
    if any(b in br.letter_bands_for(_pool_ivs[s.id]) for s in pool)
]
band_default = [b for b in (prev.get("letter_bands") or []) if b in band_all]

# The From/To boxes take any range, but typing band edges from memory is how
# a wrong examination band gets in. The picker fills them from the Article 22
# tables the engine itself uses, plus the letter bands.
_PRESETS = br.frequency_presets()
_PRESET_NONE = "Custom range"
st.session_state.setdefault("br_occ_freq_low", str(prev.get("freq_low_ghz") or ""))
st.session_state.setdefault("br_occ_freq_high", str(prev.get("freq_high_ghz") or ""))
st.session_state.setdefault("br_occ_freq_preset", _PRESET_NONE)
# Read back before the chart draws, and written to disk by the block that
# persists the form, which runs earlier in the script than the chart itself.
if st.session_state.get("br_occ_chart_span") not in ("filter", "all"):
    st.session_state["br_occ_chart_span"] = (
        "filter" if (prev.get("chart_span") or "filter") == "filter" else "all"
    )


def _apply_freq_preset() -> None:
    """Fill the two boxes from the picked band.

    Runs as an ``on_change`` callback, which is the only point where a widget's
    own session-state key may be written: Streamlit reruns afterwards and the
    text inputs are created with the new values. Writing them after the widgets
    exist would raise instead.
    """
    rng = _PRESETS.get(st.session_state.get("br_occ_freq_preset", ""))
    if rng is None:                      # "Custom range" clears nothing
        return
    st.session_state["br_occ_freq_low"] = f"{rng[0]:g}"
    st.session_state["br_occ_freq_high"] = f"{rng[1]:g}"


g1, g2, g3, g4 = st.columns([2, 2, 1, 1])
with g1:
    letter_bands = st.multiselect(
        "Frequency band",
        options=band_all,
        default=band_default,
        help="Derived from each system's own frequencies, so it covers Anatel "
             "stations and ITU SNS notices alike. Uses the direction selected "
             "above. Edges follow satellite practice: C from 3.4 GHz, Ku/Ka "
             "split at 17.7 GHz.",
    )
with g2:
    st.selectbox(
        "Fill range from band",
        options=[_PRESET_NONE, *_PRESETS],
        key="br_occ_freq_preset",
        on_change=_apply_freq_preset,
        help="Writes the two boxes on the right. The named bands come first "
             "(C, Ku, Ka …) and use the same edges as the Frequency band "
             "filter; after them come the Article 22 bands the engine runs "
             "examinations at. Edit either box afterwards to depart from the "
             "preset.",
    )
with g3:
    f_low = st.text_input(
        "From (GHz)", key="br_occ_freq_low",
        placeholder="e.g. 19.7",
        help="Optional. Keeps systems with any assignment above this frequency.",
    )
with g4:
    f_high = st.text_input(
        "To (GHz)", key="br_occ_freq_high",
        placeholder="e.g. 20.2",
        help="Optional. Keeps systems with any assignment below this frequency.",
    )


def _ghz(text: str) -> float | None:
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        v = float(text)
    except ValueError:
        st.warning(f"Ignoring frequency `{text}`: not a number.", icon=":material/warning:")
        return None
    if v <= 0:
        st.warning("Ignoring a frequency that is not positive.", icon=":material/warning:")
        return None
    return v


low_ghz, high_ghz = _ghz(f_low), _ghz(f_high)
if low_ghz is not None and high_ghz is not None and low_ghz > high_ghz:
    low_ghz, high_ghz = high_ghz, low_ghz
    st.caption("Frequency bounds were swapped so the range reads low to high.")

rf_all = sorted({b for s in pool for b in s.rf_bands if b})
rf_default = [b for b in (prev.get("rf_bands") or []) if b in rf_all]
rf_bands = (
    st.multiselect(
        "RF band (Anatel label)", options=rf_all, default=rf_default,
        help="The label Anatel publishes for a licensed station. ITU SNS "
             "notices do not carry it — use **Frequency band** for those.",
    )
    if rf_all else []
)
query = st.text_input("Name / operator / ntc_id contains", value=prev.get("query") or "")

filtered = []
q = query.strip().lower()
for s in pool:
    if orbit != "all" and orbit not in (s.orbit or "").upper():
        continue
    if rf_bands and not (set(rf_bands) & set(s.rf_bands)):
        continue
    if letter_bands and not (
        set(letter_bands) & set(br.letter_bands_for(_pool_ivs[s.id]))
    ):
        continue
    if (low_ghz is not None or high_ghz is not None) and not br.intervals_touch_range(
        _pool_ivs[s.id], low_ghz, high_ghz
    ):
        continue
    if q:
        blob = " ".join(
            [s.name, s.operator, s.ntc_id, s.position, s.adm]
        ).lower()
        if q not in blob:
            continue
    if not s.intervals(direction):
        continue
    filtered.append(s)

st.caption(f"{len(filtered)} system(s) after filters.")
id_to_sys = {s.id: s for s in filtered}
if "br_occ_systems" not in st.session_state:
    st.session_state["br_occ_systems"] = [
        i for i in (prev.get("system_ids") or []) if i in id_to_sys
    ]
else:
    st.session_state["br_occ_systems"] = [
        i for i in st.session_state["br_occ_systems"] if i in id_to_sys
    ]
b_sel, b_all, b_clear = st.columns([3, 1, 1])
with b_all:
    if st.button("Select filtered", icon=":material/done_all:",
                 disabled=not filtered, width="stretch"):
        st.session_state["br_occ_systems"] = [s.id for s in filtered]
        st.rerun()
with b_clear:
    if st.button("Clear", icon=":material/filter_alt_off:", width="stretch"):
        st.session_state["br_occ_systems"] = []
        st.rerun()

sel_ids = st.multiselect(
    "Pick systems to compare",
    options=[s.id for s in filtered],
    key="br_occ_systems",
    format_func=lambda i: id_to_sys[i].label() if i in id_to_sys else i,
)

set_persisted_state("br_occupancy.form", {
    "sources": sources,
    "orbit": orbit,
    "direction": direction,
    "rf_bands": rf_bands,
    "letter_bands": letter_bands,
    "freq_low_ghz": f_low.strip(),
    "freq_high_ghz": f_high.strip(),
    "chart_span": st.session_state.get("br_occ_chart_span", "filter"),
    "system_ids": sel_ids,
    "query": query,
})

if not sel_ids:
    st.info("Select one or more systems to plot occupancy and the common overlap.")
    st.stop()

selected = [id_to_sys[i] for i in sel_ids if i in id_to_sys]
common = br.common_intervals(selected, direction) if len(selected) >= 2 else []
union = br.union_intervals(selected, direction)

# ── 3. Occupancy ─────────────────────────────────────────────────────────────
st.subheader("3. Occupied bands")

m1, m2, m3 = st.columns(3)
m1.metric("Systems", len(selected))
m2.metric(
    "Union",
    "; ".join(f"{lo:.3f}–{hi:.3f} GHz" for lo, hi in union) or "—",
)
m3.metric(
    "Common overlap",
    "; ".join(f"{lo:.3f}–{hi:.3f} GHz" for lo, hi in common) or "—",
)

if len(selected) >= 2:
    if common:
        st.success(
            "Common occupied band(s) across all selected systems: **"
            + "; ".join(f"{lo:.3f}–{hi:.3f} GHz" for lo, hi in common)
            + "**."
        )
    else:
        st.warning(
            "Selected systems have **no common occupied band** "
            "(disjoint ranges) in this direction."
        )

chart_rows = []
for s in selected:
    iv = s.intervals(direction)
    chart_rows.append({
        "label": s.label(),
        "bands": iv,
        "kind": "tx",
        "sublabel": (
            (s.operator or s.position or "")
            + (f" · {', '.join(s.rf_bands)}" if s.rf_bands else "")
        ) or None,
    })
if len(selected) >= 2:
    chart_rows.append({
        "label": "Common occupancy (all selected)",
        "bands": common,
        "kind": "common",
        "sublabel": direction,
    })
# The axis normally spans the data, so one C-band assignment squeezes a Ka
# comparison into a sliver. Offer the filtered range as the axis instead.
_filter_span = None
if low_ghz is not None or high_ghz is not None:
    _edges = [e for r in chart_rows for b in r["bands"] for e in b]
    _lo = low_ghz if low_ghz is not None else (min(_edges) if _edges else 0.0)
    _hi = high_ghz if high_ghz is not None else (max(_edges) if _edges else 0.0)
    _filter_span = (float(_lo), float(_hi))
    _span_name = f"{_filter_span[0]:g}–{_filter_span[1]:g} GHz"
elif letter_bands:
    _picked = [(lo, hi) for name, lo, hi in br.LETTER_BANDS if name in letter_bands]
    _edges = [e for r in chart_rows for b in r["bands"] for e in b]
    _lo = min(lo for lo, _hi in _picked)
    _hi = max(hi for _lo2, hi in _picked)
    if _hi == float("inf"):
        _hi = max(_edges) if _edges else _lo + 1.0
    _filter_span = (float(_lo), float(_hi))
    _span_name = "/".join(n for n in br.LETTER_BAND_NAMES if n in letter_bands)

if _filter_span is not None:
    zoom = st.radio(
        "Axis span",
        options=["filter", "all"],
        key="br_occ_chart_span",
        horizontal=True,
        format_func=lambda v: (
            f"Selected band ({_span_name})" if v == "filter"
            else "Everything the systems declare"
        ),
        help="The strips are drawn on one shared axis. Limiting it to the "
             "band you filtered on trims the assignments outside it, so the "
             "comparison is read at that band's resolution.",
    )
else:
    zoom = "all"

st_bands_chart(
    chart_rows,
    title=f"Band occupancy — Brazil — {direction}",
    shared_scale=True,
    span_ghz=_filter_span if zoom == "filter" else None,
)

rows = []
for s in selected:
    for lo, hi in s.intervals(direction):
        rows.append({
            "system": s.name,
            "source": s.source,
            "ntc_id": s.ntc_id or "—",
            "orbit": s.orbit or "—",
            "position": s.position or "—",
            "operator": s.operator or "—",
            "RF": ", ".join(s.rf_bands) or "—",
            "band (GHz)": f"{lo:.4f} – {hi:.4f}",
            "bandwidth (MHz)": f"{(hi - lo) * 1000:.1f}",
            "direction": direction,
        })
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
