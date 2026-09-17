"""National band occupancy — a licensed-station catalogue + ITU SNS filings.

Survey of frequency occupancy in one country. A national licensed catalogue is
optional and may be uploaded or, where an administration publishes one, fetched;
the filings come from a Space IFIC SNS database. Selecting systems draws the
same shared-axis occupancy strips used on Aggregate, including the
common-overlap row.
"""
from __future__ import annotations

from pathlib import Path
import shutil

from contextlib import nullcontext as _nullcontext

import pandas as pd
import streamlit as st

from lib import br_occupancy as br
from lib import theme
from lib.band_chart import st_bands_chart
from lib.manual import help_expander
from lib.state import set_persisted_state, use_persisted_state

st.set_page_config(
    page_title="National occupancy · SHARC-Orbit",
    page_icon=":material/key_visualizer:",
    layout="wide",
)
theme.inject()

st.title("National band occupancy")
help_expander("brazil_occupancy", extra=("national_catalog_format",))
st.caption(
    "Licensed occupancy from a national catalogue — load your administration's "
    "own table, or refresh a published one — plus the filings in the "
    "complete **SRS.mdb** from a BR IFIC ISO "
    f"([IFIC 3079 ISO]({br.brific_iso_url('3079')})), "
    "or the public weekly `ificXXXX.mdb` "
    "([ITU WIC 2026](https://www.itu.int/sns/wic/demowic26.html)). "
    "Pick systems to see occupied bands and their **common** overlap — the "
    "same strip chart as **Aggregate**."
)

prev = use_persisted_state("br_occupancy.form", {
    "sources": ["anatel"],
    "known_sources": [],
    "orbit": "all",
    "direction": "downlink",
    "rf_bands": [],
    "country": "",
    "country_rule": "serves",
    "letter_bands": [],
    "freq_low_ghz": "",
    "freq_high_ghz": "",
    "chart_range": [],
    "system_ids": [],
    "query": "",
})


def _fmt_fetched(meta: dict) -> str:
    ts = meta.get("fetched_at") or "never"
    return str(ts).replace("T", " ").replace("Z", " UTC")


def _operates_caption(meta: dict) -> str:
    """Provenance of an indexed catalogue, without naming one country.

    A catalogue indexed before the country became a filter still carries the
    Brazil tallies; they are shown as what they are — the rule that produced
    that index — instead of as the page's subject.
    """
    n_tot = meta.get("n_notice_total") or 0
    n_sys = meta.get("n_systems") or 0
    if meta.get("n_srv_br") or meta.get("n_adm_b"):
        return (f"{n_sys} of {n_tot} notices kept · indexed with the old "
                "single-country rule; re-index to filter by any country")
    return f"{n_sys} of {n_tot} notices indexed · filter by country below"


# ── 1. Catalogs ──────────────────────────────────────────────────────────────
# Loading a catalogue is a setup step done once and then never again, yet it
# owned the first screen on every visit and pushed the results 1 700 px down.
# Once data is present it folds into a one-line summary that still opens.
anatel = br.load_anatel_catalog()
# A broken or half-indexed catalogue must degrade to the setup card, never take
# the page down with it: the card is where the user fixes exactly this.
_sns_error = None
try:
    sns = br.load_sns_catalog()
except Exception as exc:  # noqa: BLE001 — surfaced below, not swallowed
    sns, _sns_error = [], exc
a_meta, s_meta = br.anatel_meta(), br.sns_meta()
if _sns_error is not None:
    st.warning(
        f"Could not open the indexed ITU SNS catalogue: {_sns_error}. "
        "Index an ISO or SRS below.", icon=":material/warning:",
    )
_have_data = bool(anatel or sns)
if _have_data:
    _bits = []
    if anatel:
        _bits.append(f"licensed {len(anatel)}")
    if sns:
        _bits.append(f"ITU SNS {len(sns)}")
    _cat_box = st.expander(
        f"1. Catalogs — {' · '.join(_bits)} loaded · refresh or add a source",
        expanded=False,
    )
else:
    st.subheader("1. Catalogs")
    _cat_box = _nullcontext()

with _cat_box:
    c1, c2 = st.columns(2)
    with c1:
        # One card for licensed catalogues, whoever issues them. Anatel is not a
        # section of its own any more: it is the catalogue that ships built in,
        # listed beside any other administration's.
        with st.container(border=True):
            st.markdown("**National licensed catalogues** · stations / sub-bands")
            _nat_srcs = [x for x in br.read_sources() if x.kind == "national"]
            # Providers come from the data table, so the interface never names
            # one country: adding another administration is a dict entry.
            _prov = br.national_providers()[0] if br.national_providers() else None
            if anatel and _prov:
                st.markdown(
                    theme.pill(f"{_prov['label']} · {len(anatel)} systems", "ok"),
                    unsafe_allow_html=True,
                )
                st.caption(
                    ("Published catalogue · fetched " + _fmt_fetched(a_meta))
                    if a_meta.get("fetched_at")
                    else "Published catalogue · from the bundled copy; refresh for the live file."
                )
            elif _prov:
                st.caption(f"{_prov['label']} is not downloaded yet.")
            for _ns in _nat_srcs:
                q1, q2 = st.columns([5, 1])
                with q1:
                    st.markdown(
                        theme.pill(f"{_ns.label} · {_ns.n_systems} systems", "ok"),
                        unsafe_allow_html=True,
                    )
                    st.caption(_ns.describe())
                with q2:
                    if st.button("", icon=":material/delete:",
                                 key=f"br_rm_nat_{_ns.id}",
                                 help="Forget this catalogue."):
                        br.remove_source(_ns.id)
                        st.rerun()
            if _prov and st.button(
                f"Refresh {_prov['label']}", icon=":material/download:",
                key="br_refresh_anatel",
                help=f"Downloads {_prov['url']}",
            ):
                with st.spinner("Downloading the published catalogue…"):
                    try:
                        meta = br.refresh_anatel()
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Refresh failed: {exc}")
                    else:
                        st.success(
                            f"Catalogue: **{meta['n_systems']}** systems "
                            f"({meta['bytes']:,} bytes)."
                        )
                        st.rerun()

        # ── add a national licensed catalogue ───────────────────────────────
        # Any administration can load its own licensed stations, in the shape
        # documented under "Licensed-station table format" in the page help.
        with st.container(border=True):
            st.markdown("**Add a national licensed catalogue**")
            st.caption(
                "A CSV of licensed stations, one row per station per sub-band. "
                "See **Licensed-station table format** in the help above for "
                "the fields; frequencies must be in MHz."
            )
            n1, n2 = st.columns([2, 1])
            with n1:
                _nat_label = st.text_input(
                    "Name it", key="br_nat_label",
                    placeholder="e.g. IFT licensed stations",
                )
            with n2:
                _nat_adm = st.text_input(
                    "ITU symbol", key="br_nat_adm", placeholder="MEX",
                    help="The licensing administration's ITU symbol, which is "
                         "what makes these rows findable by country. B is "
                         "Brazil, F France, D Germany.",
                )
            _nat_file = st.file_uploader(
                "CSV or zip containing it", type=["csv", "zip"],
                key="br_nat_upload",
                help="Licensed stations only. An `.mdb` of ITU filings belongs "
                     "in **Add another filing catalogue** below, not here.",
            )
            st.caption(
                ":material/info: Filings in `.mdb`, `.iso` or `srsNNNN.zip` go "
                "in **Add another filing catalogue** below."
            )
            if st.button(
                "Index this catalogue", icon=":material/table_view:",
                key="br_nat_go", width="stretch",
                disabled=not (_nat_file and _nat_adm.strip()),
            ):
                try:
                    _raw = _nat_file.getvalue()
                    if _nat_file.name.lower().endswith(".zip"):
                        _member, _raw = br.pick_catalog_csv(_raw)
                        st.caption(f"Read `{_member}` from the zip.")
                    _src = br.register_national_csv(
                        br._decode_csv_bytes(_raw),
                        label=_nat_label.strip() or _nat_file.name,
                        adm=_nat_adm.strip(),
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not read that table: {exc}")
                else:
                    st.success(
                        f"Added **{_src.label}** — {_src.n_systems} licensed "
                        f"station(s) under ITU symbol `{_nat_adm.strip().upper()}`."
                    )
                    st.rerun()

        # ── add another filing catalogue ────────────────────────────────────
        # The space under the national card. The ITU card on the right is the
        # PRIMARY source and refreshes itself; this is for bringing in a second
        # one — a filing of your own, an older IFIC, another administration's
        # SRS — to sit beside it rather than replace it.
        with st.container(border=True):
            st.markdown("**Add another filing catalogue**")
            st.caption(
                "Indexed alongside what is already loaded, never over it. "
                "An ISO, a bookshop zip, an `srsNNNN.zip`, an `.mdb`, or a "
                "folder of split parts."
            )
            _add_label = st.text_input(
                "Name it", key="br_add_label",
                placeholder="e.g. my filing, IFIC 3085, ARG SRS",
                help="Shown in the source filter and in the list below. "
                     "Left empty, the IFIC number is used.",
            )
            _add_path = st.text_input(
                "Path on this machine", key="br_add_path",
                placeholder="/path/to/srs3085.zip",
                help="Preferred for anything large: nothing is uploaded "
                     "through the browser.",
            )
            _add_files = st.file_uploader(
                "or drop a file", type=["mdb", "zip", "iso"],
                accept_multiple_files=True, key="br_add_upload",
            )
            if st.button(
                "Index and add", icon=":material/library_add:",
                key="br_add_go", width="stretch",
                disabled=not (_add_path.strip() or _add_files),
            ):
                # Each dropped file is its own catalogue unless the names say
                # they are split parts of one SRS. Handing two unrelated
                # filings to the ingest as a folder made it treat them as
                # parts, index the first and drop the rest without a word.
                _jobs: "list[tuple[str, Path]]" = []
                if _add_path.strip():
                    _p = Path(_add_path.strip()).expanduser()
                    _jobs = [(_p.stem, _p)]
                elif _add_files:
                    _stamp = f"{abs(hash(tuple(f.name for f in _add_files))):08x}"
                    _jobs = br.stage_upload_groups(
                        [(f.name, f.getvalue()) for f in _add_files],
                        br.SNS_DIR / "uploads" / _stamp,
                    )
                _done, _failed = [], []
                for _i, (_hint, _target) in enumerate(_jobs):
                    _lbl = _add_label.strip()
                    if _lbl and len(_jobs) > 1:
                        _lbl = f"{_lbl} · {_hint}"
                    try:
                        with st.spinner(
                            f"Indexing {_hint} ({_i + 1} of {len(_jobs)}) — "
                            "minutes on a full SRS…"
                        ):
                            _meta = br.ingest_local_iso(_target, label=_lbl or _hint)
                    except Exception as exc:  # noqa: BLE001
                        _failed.append(f"{_hint}: {exc}")
                    else:
                        _done.append(
                            f"**{_lbl or _hint}** — {_meta.get('n_systems', 0)} "
                            f"system(s) from {_meta.get('n_notice_total', 0)} notices"
                        )
                for _msg in _failed:
                    st.error(f"Could not index {_msg}")
                if _done:
                    st.success("Added " + "; ".join(_done) + ".")
                    st.rerun()
    with c2:
        with st.container(border=True):
            st.markdown("**ITU filings** · SRS from a BR IFIC ISO, or a weekly IFIC")
            kind = s_meta.get("kind") or ("ific" if s_meta.get("ific_no") else "")
            if s_meta.get("mdb"):
                n_tot = s_meta.get("n_notice_total")
                n_sys = len(sns)
                if kind == "srs":
                    st.markdown(theme.pill(f"SRS · {n_sys} systems", "ok"),
                                unsafe_allow_html=True)
                    st.caption(
                        f"IFIC {s_meta.get('ific_no') or '—'} · "
                        f"{n_tot or '—'} notices in the file · "
                        f"{_operates_caption(s_meta)} · "
                        f"loaded {_fmt_fetched(s_meta)}"
                    )
                else:
                    st.markdown(
                        theme.pill(f"IFIC {s_meta.get('ific_no')} · {n_sys} systems",
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
                    "every notice in the file…"
                ):
                    try:
                        meta = br.ingest_local_iso(Path(raw))
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"SRS index failed: {exc}")
                    else:
                        st.success(
                            f"SRS (IFIC {meta.get('ific_no') or '—'}): "
                            f"{meta.get('n_notice_total', 0)} notices, "
                            f"**{meta.get('n_systems', 0)}** systems indexed "
                            f"(service {meta.get('n_srv_br', 0)}, "
                            f"adm=B {meta.get('n_adm_b', 0)})."
                        )
                        st.rerun()
            if st.button("Refresh weekly IFIC", icon=":material/download:",
                         key="br_refresh_sns"):
                with st.spinner(
                    "Downloading ificXXXX.mdb from ITU and indexing "
                    "every notice in the file…"
                ):
                    try:
                        meta = br.refresh_sns()
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"IFIC refresh failed: {exc}")
                    else:
                        st.success(
                            f"IFIC **{meta.get('ific_no')}**: "
                            f"{meta.get('n_notice_total', 0)} notices this week, "
                            f"**{meta.get('n_systems', 0)}** systems indexed "
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
                            f"**{meta.get('n_systems', 0)}** systems indexed."
                        )
                        st.rerun()

    # ── indexed filing catalogues ───────────────────────────────────────────
    # The free space under the two cards. Several SRS or IFIC files can be
    # registered at once, which is what makes "compare my filing against what
    # is already there" possible; before this a second index destroyed the
    # first without a word.
    _registered = br.read_sources()
    with st.container(border=True):
        st.markdown("**Indexed filings** · one row per catalogue")
        if not _registered:
            st.caption(
                "Nothing indexed yet. Index an ISO, an `srsNNNN.zip` or a "
                "weekly IFIC above; each one is kept, so a new file never "
                "replaces the previous."
            )
        for _src in _registered:
            r1, r2, r3 = st.columns([3, 5, 1])
            with r1:
                st.markdown(
                    theme.pill(_src.label or _src.id,
                               "ok" if _src.enabled else "warn"),
                    unsafe_allow_html=True,
                )
            with r2:
                st.caption(_src.describe())
                if _src.mdb:
                    st.caption(f"`{_src.mdb}`")
            with r3:
                if st.button(
                    "", icon=":material/delete:", key=f"br_rm_{_src.id}",
                    help="Forget this catalogue. The indexed file on disk is "
                         "left alone and can be indexed again.",
                ):
                    br.remove_source(_src.id)
                    st.rerun()

if not anatel and not sns:
    st.info(
        "Load a national licensed table, or refresh a published catalogue, to "
        "see licensed occupancy. "
        "Optionally refresh **ITU SNS** to overlay the filings from "
        "the latest Space IFIC."
    )
    st.stop()

# ── 2. Filter & pick ─────────────────────────────────────────────────────────
st.subheader("2. Systems")
# One entry per catalogue, not the old anatel/sns pair: several SRS or IFIC
# files can be registered at once now, and a comparison usually means "my
# filing against what is already indexed", which needs them apart.
_srcs = br.read_sources()
_src_label = {"anatel": f"National licensed ({len(anatel)})"}
src_opts = ["anatel"] if anatel else []
for _s in _srcs:
    if not _s.enabled:
        continue
    src_opts.append(_s.id)
    _src_label[_s.id] = f"{_s.label} ({_s.n_systems})"
if not _srcs and sns:
    src_opts.append("sns")
    _src_label["sns"] = f"ITU SNS ({len(sns)})"
# Old saved state named the two kinds; map it onto whatever is registered.
_saved = [s for s in (prev.get("sources") or [])]
if "sns" in _saved and "sns" not in src_opts:
    _saved = [s for s in _saved if s != "sns"] + [x.id for x in _srcs if x.enabled]
# A catalogue just added must show up in the bars without being ticked by hand:
# the saved selection predates it, so it would otherwise be indexed, listed,
# and invisible. Only genuinely NEW ids are auto-selected, so a source the user
# deliberately unticked stays unticked.
_known = set(prev.get("known_sources") or [])
_fresh = [i for i in src_opts if i not in _known and i != "anatel"]
if _fresh and _known:
    st.caption(
        ":material/add_circle: Newly indexed and selected: "
        + ", ".join(_src_label.get(i, i) for i in _fresh)
    )
src_default = [s for s in dict.fromkeys([*_saved, *_fresh]) if s in src_opts]
src_default = src_default or src_opts[:1]

f1, f2, f3 = st.columns(3)
with f1:
    sources = st.multiselect(
        "Source",
        options=src_opts,
        default=src_default,
        format_func=lambda s: _src_label.get(s, s),
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
_picked_srcs = [s for s in sources if s not in ("anatel", "sns")]
if _picked_srcs:
    pool.extend(br.load_filings(_picked_srcs))

# ── Country / area ───────────────────────────────────────────────────────────
# The country used to be welded into the index: only what operated in Brazil
# was ever written to the catalogue. It is evidence on each row now, so it can
# be chosen here. Options are the ITU symbols that actually occur in the loaded
# data, ordered by how many systems carry them, because no symbol table ships
# with the SRS and inventing names for 237 of them would be guesswork.
_rule_opts = {
    "serves": "Serves it — notified by, service area, or an earth station there",
    "notified": "Notified by it — the filing administration only",
}
_rule = prev.get("country_rule") or "serves"
if _rule not in _rule_opts:
    _rule = "serves"

# Counted over systems that actually have something in the chosen direction.
# Counting the whole pool advertised "AUS · 5 system(s)" and then showed none,
# because the direction filter downstream dropped every one of them.
_counts: "dict[str, int]" = {}
for _s in pool:
    if not _s.intervals(direction):
        continue
    for _c in _s.country_codes(rule=_rule):
        _counts[_c] = _counts.get(_c, 0) + 1
_codes = sorted(_counts, key=lambda c: (-_counts[c], c))

k1, k2 = st.columns([1, 2])
with k1:
    _prev_c = prev.get("country") or ""
    country = st.selectbox(
        "Occupancy in",
        options=["", *_codes],
        index=(_codes.index(_prev_c) + 1) if _prev_c in _codes else 0,
        format_func=lambda c: (
            "Everywhere — no country filter" if not c
            else f"{c} · {_counts.get(c, 0)} system(s)"
        ),
        help="ITU symbols as the SRS writes them: B is Brazil, F France, "
             "D Germany, G the United Kingdom. XR1/XR2/XR3 are ITU Regions "
             "and XAA is worldwide, so a filing that declares one of those "
             "covers the countries inside it.",
    )
with k2:
    country_rule = st.radio(
        "Counts as operating there when the filing is",
        options=list(_rule_opts),
        index=list(_rule_opts).index(_rule),
        horizontal=True,
        format_func=lambda r: _rule_opts[r],
        disabled=not country,
        help="The looser rule is what the page applied to Brazil before the "
             "country was a choice, and it is what keeps a foreign filing that "
             "covers the country.",
    )

# A catalogue indexed before the country became a filter carries no service
# area and no earth-station country, so both rules collapse onto the notifying
# administration and the looser one silently stops finding foreign filings that
# cover the country. Say so instead of letting the numbers look wrong.
_has_evidence = any(s.srv_ctry or s.es_ctry for s in pool)
if country and not _has_evidence:
    st.info(
        "This catalogue was indexed before the country filter existed, so it "
        "carries only the notifying administration: both rules give the same "
        "answer and a filing that merely *serves* the country is not found. "
        "Re-index the SRS in **1. Catalogs** to get the service areas.",
        icon=":material/info:",
    )

if country:
    pool = [s for s in pool if country in s.country_codes(rule=country_rule)]

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
_BAND_ALL = "All"
st.session_state.setdefault("br_occ_freq_low", str(prev.get("freq_low_ghz") or ""))
st.session_state.setdefault("br_occ_freq_high", str(prev.get("freq_high_ghz") or ""))
st.session_state.setdefault("br_occ_freq_preset", _PRESET_NONE)


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


# A range slider over the same window the boxes hold. It is a select-slider, not
# a continuous one, because the catalogue spans 0.03 to 333 GHz: on a linear
# track the whole Ku band is under 2% and cannot be grabbed. Its stops are the
# letter-band and Article 22 edges, so a drag lands on a boundary that means
# something; the boxes stay for any frequency that is not one.
g1, g2, g3, g4 = st.columns([2, 2, 1, 1])
with g1:
    letter_bands = st.multiselect(
        "Frequency band",
        options=band_all,
        default=band_default,
        help="Derived from each system's own frequencies, so it covers licensed "
             "stations and ITU filings alike. Uses the direction selected "
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
        "RF band (national catalogue label)", options=rf_all, default=rf_default,
        help="The band label the national catalogue publishes for a licensed "
             "station. ITU filings do not carry it — use **Frequency band** "
             "for those.",
    )
    if rf_all else []
)
query = st.text_input("Name / operator / ntc_id contains", value=prev.get("query") or "")

def _orbit_class(raw: str) -> str:
    """``GEO`` / ``NGEO`` / ``""`` from whatever the catalogue wrote."""
    v = (raw or "").strip().upper()
    if not v:
        return ""
    if v.startswith("N") or "NGEO" in v or v.startswith("NON"):
        return "NGEO"
    return "GEO" if v.startswith("G") else ""


filtered = []
q = query.strip().lower()
for s in pool:
    # Exact match, not containment: "GEO" is a substring of "NGEO", so the
    # substring test let 884 non-geostationary systems through an Orbit=GEO
    # filter, Iridium and Globalstar among them.
    if orbit != "all" and _orbit_class(s.orbit) != orbit:
        continue
    # The Anatel label exists only on licensed stations. Applying it to the
    # whole pool silently deleted every ITU SNS notice while the Source control
    # still said ITU SNS was selected, so it only prunes Anatel rows.
    if rf_bands and s.source == "anatel" and not (set(rf_bands) & set(s.rf_bands)):
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

# A picked system SURVIVES a filter change. Dropping the ones that no longer
# match was silent and irreversible: tightening a band filter deleted them from
# the comparison, and loosening it back did not bring them back, because the
# pruned list had already been written to disk. Now the filter governs what the
# picker OFFERS, never what it holds; systems outside the current filter are
# reported and can be dropped deliberately.
_all_by_id = {s.id: s for s in pool}
id_to_sys = {s.id: s for s in filtered}
if "br_occ_systems" not in st.session_state:
    st.session_state["br_occ_systems"] = [
        i for i in (prev.get("system_ids") or []) if i in _all_by_id
    ]
else:
    # Only ids that vanished from the POOL (a source was switched off) go.
    st.session_state["br_occ_systems"] = [
        i for i in st.session_state["br_occ_systems"] if i in _all_by_id
    ]
_held = list(st.session_state["br_occ_systems"])
_outside = [i for i in _held if i not in id_to_sys]
# The picker's options must contain everything it holds, or Streamlit drops the
# extras on the next render — which is the very loss this fix removes.
_picker_options = [s.id for s in filtered] + _outside
# Scale guard. The catalogue holds the whole SRS now, so an unfiltered pool is
# ~15 000 options: the picker alone then ships hundreds of kB of widget payload
# on every rerun and the page stops responding to typing. Offer the first slice
# and tell the user to narrow, rather than rendering something unusable.
_PICKER_CAP = 2000
_picker_capped = len(_picker_options) > _PICKER_CAP
if _picker_capped:
    _keep = set(_held)
    _picker_options = (
        [i for i in _picker_options if i in _keep]
        + [i for i in _picker_options if i not in _keep][:_PICKER_CAP]
    )
    st.warning(
        f"{len(filtered):,} systems match. The picker is showing "
        f"{_PICKER_CAP:,} of them plus everything already selected — narrow by "
        "country, band or name to reach the rest.",
        icon=":material/filter_alt:",
    )
if _outside:
    o1, o2 = st.columns([3, 1])
    with o1:
        st.info(
            f"**{len(_outside)}** picked system(s) fall outside the current "
            "filter. They stay in the comparison and in the chart — the filter "
            "decides what the picker offers, not what it holds.",
            icon=":material/filter_alt:",
        )
    with o2:
        def _drop_outside(ids: "list[str]") -> None:
            st.session_state["br_occ_systems"] = list(ids)

        st.button(
            f"Drop the {len(_outside)} outside", icon=":material/backspace:",
            width="stretch", on_click=_drop_outside,
            args=([i for i in _held if i in id_to_sys],),
        )

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

# The picker and the marking block would otherwise list the same systems twice,
# and at a couple of hundred picks the chip wall buries everything under it.
# Collapse the picker once the selection is big: it stays reachable for adding
# a system by name, while the marking block below becomes the working surface.
_n_picked = len(st.session_state.get("br_occ_systems") or [])
_picker_box = (
    st.expander(f"Picked systems ({_n_picked}) — add or remove one by one",
                expanded=False)
    if _n_picked > 8 else _nullcontext()
)
with _picker_box:
    sel_ids = st.multiselect(
        "Pick systems to compare",
        options=_picker_options,
        key="br_occ_systems",
        format_func=lambda i: (
            (_all_by_id[i].label() + ("  ·  outside the current filter"
                                      if i not in id_to_sys else ""))
            if i in _all_by_id else i
        ),
    )
    if len(sel_ids) > 1:
        st.caption(
            ":material/info: The chips are this picker's own — clicking one "
            "does nothing, only the **×** removes it. To mark several systems "
            "and then keep or drop them in one go, use **Mark systems** below."
        )

# Pruning a long selection one chip at a time is unworkable — the list above
# runs to dozens of entries. Mark what matters here, then keep or drop it in
# one action. Marking is independent of the chips: nothing changes until a
# button is pressed.
#
# Pills rather than a table with row selection: in a Streamlit dataframe a row
# is marked through the checkbox gutter, not by clicking the row, and there is
# no ctrl-click binding to hook. A pill toggles on a plain click and renders
# filled while marked, which is the emphasis this needs — and it costs one
# click per system instead of hunting a checkbox column.
if len(sel_ids) > 1:
    with st.expander(
        f":material/ads_click: Mark systems — click to mark, then keep or drop "
        f"({len(sel_ids)} picked)",
        # One pill per picked system: at a few hundred picks an open panel is
        # several screens of chips between the filters and the chart.
        expanded=len(sel_ids) <= 40,
    ):

        def _prune_label(i: str) -> str:
            sy = id_to_sys.get(i)
            if sy is None:
                return i
            bands = "/".join(br.letter_bands_for(_pool_ivs.get(i) or []))
            tail = f" · {bands}" if bands else ""
            if sy.source == "sns" and sy.ntc_id:
                tail = f" · ntc {sy.ntc_id}{tail}"
            return f"{sy.name}{tail}"

        # Same guard: one pill per picked system is fine at forty and a wall of
        # widgets at four thousand.
        _PILL_CAP = 300
        _pill_ids = sel_ids[:_PILL_CAP]
        if len(sel_ids) > _PILL_CAP:
            st.caption(
                f"Showing the first {_PILL_CAP} of {len(sel_ids):,} picked "
                "systems; the buttons below act on what is marked here."
            )
        _marked_ids = st.pills(
            "Click a system to mark it — marked ones stay filled",
            options=_pill_ids,
            selection_mode="multi",
            format_func=_prune_label,
            key="br_occ_prune_pills",
        ) or []

        def _set_selection(ids: "list[str]") -> None:
            """Rewrite the picker's selection and forget the marks.

            Must run as a button callback: the picker widget is created above
            this block, and Streamlit refuses to let a script assign to a
            widget's own session-state key after the widget exists. A callback
            runs before the script body, so the assignment is legal there and
            the rerun that follows shows the new chips.
            """
            st.session_state["br_occ_systems"] = list(ids)
            st.session_state["br_occ_prune_pills"] = []

        m1, m2, m3 = st.columns([2, 1, 1])
        with m1:
            st.caption(
                f"**{len(_marked_ids)}** of {len(sel_ids)} marked."
                if _marked_ids else
                f"Nothing marked yet — click any of the {len(sel_ids)} systems above."
            )
        with m2:
            st.button(
                "Keep only marked", icon=":material/filter_alt:",
                width="stretch", disabled=not _marked_ids,
                on_click=_set_selection, args=(_marked_ids,),
            )
        with m3:
            st.button(
                "Remove marked", icon=":material/backspace:",
                width="stretch", disabled=not _marked_ids,
                on_click=_set_selection,
                args=([i for i in sel_ids if i not in set(_marked_ids)],),
            )

set_persisted_state("br_occupancy.form", {
    "sources": sources,
    # Remembering what has been offered is what lets a NEW catalogue be
    # selected automatically exactly once.
    "known_sources": src_opts,
    "orbit": orbit,
    "direction": direction,
    "rf_bands": rf_bands,
    "country": country,
    "country_rule": country_rule,
    "letter_bands": letter_bands,
    "freq_low_ghz": f_low.strip(),
    "freq_high_ghz": f_high.strip(),
    # Read from the widget key: this block runs before the chart section that
    # creates the slider, so the value here is the one the last run produced.
    "chart_range": [float(v) for v in
                    (st.session_state.get("br_occ_zoom") or [])],
    "system_ids": sel_ids,
    "query": query,
})

if not sel_ids:
    st.info("Select one or more systems to plot occupancy and the common overlap.")
    st.stop()

selected = [_all_by_id[i] for i in sel_ids if i in _all_by_id]
common = br.common_intervals(selected, direction) if len(selected) >= 2 else []
union = br.union_intervals(selected, direction)

# ── 3. Occupancy ─────────────────────────────────────────────────────────────
st.subheader("3. Occupied bands")

def _span_ghz(ivs: "list[tuple[float, float]]") -> float:
    return sum(hi - lo for lo, hi in ivs)


def _ranges_md(ivs: "list[tuple[float, float]]") -> str:
    return "; ".join(f"{lo:.3f}–{hi:.3f} GHz" for lo, hi in ivs) or "—"


# A metric box is one short line: the theme clips it at the column width with no
# ellipsis, so pouring a 900-character list of ranges into it hid all but the
# first two. The boxes now carry the two numbers that fit, and the ranges
# themselves are written below, where they can wrap.
m1, m2, m3 = st.columns(3)
m1.metric("Systems", len(selected))
m2.metric(
    "Union", f"{len(union)} band(s)",
    help="Bands occupied by at least one selected system, merged.",
)
m2.caption(f"{_span_ghz(union):.3f} GHz total")
m3.metric(
    "Common overlap", f"{len(common)} band(s)" if common else "none",
    help="Bands occupied by EVERY selected system at once.",
)
if common:
    m3.caption(f"{_span_ghz(common):.3f} GHz total")

if direction == "both":
    st.caption(
        ":material/warning: Direction is **Both**, so each system's uplink and "
        "downlink are merged before intersecting. A band reported as common may "
        "pair one system's uplink with another's downlink. Pick a single "
        "direction for a statement about interference."
    )

if len(selected) >= 2:
    if common:
        st.success(f"Occupied by **all** {len(selected)} selected systems: "
                   f"**{_ranges_md(common)}**.")
    else:
        # "Disjoint ranges" was wrong: the intersection being empty says nothing
        # about pairs. Count the pairs that do share a band, so the user learns
        # whether the selection has no overlap at all or merely no overlap
        # shared by every member.
        _pairs = None
        if len(selected) <= 60:
            _ivs = [s.intervals(direction) for s in selected]
            _pairs = sum(
                1
                for i in range(len(_ivs))
                for j in range(i + 1, len(_ivs))
                if br.intersect_sets(_ivs[i], _ivs[j])
            )
        msg = (f"**No band is occupied by all {len(selected)} selected systems** "
               "at once in this direction.")
        if _pairs:
            msg += (f" {_pairs} pair(s) of them do share a band — narrow the "
                    "selection to see it.")
        elif _pairs == 0:
            msg += " No two of them share a band either."
        st.warning(msg)

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
# ── Chart range ─────────────────────────────────────────────────────────────
# A view control, not a filter: it changes how much of the frequency axis the
# bars are drawn over, and nothing else. The selection, the metrics and the
# table stay exactly as they are. Without it the axis stretches over whatever
# the selection declares, and since a segment is never drawn narrower than
# 0.15% of the track, most bars end up wider than they really are: at 222
# systems the axis runs 0.03-240 GHz and 55% of the segments sit on that floor.
_chart_edges = [e for r in chart_rows for b in r["bands"] for e in b]
_chart_range = None
if _chart_edges:
    _lo_all = round(min(_chart_edges), 3)
    _hi_all = round(max(_chart_edges), 3)
    if _hi_all - _lo_all > 1e-6:
        _band_edge = {n: (lo, hi) for n, lo, hi in br.LETTER_BANDS}
        _bands_here = [
            b for b in br.LETTER_BAND_NAMES
            if any(b in br.letter_bands_for(r["bands"]) for r in chart_rows)
        ]

        def _band_bounds(name: str) -> "tuple[float, float]":
            """Slider travel for a band: its edges, clipped to the real data."""
            if not name or name == _BAND_ALL or name not in _band_edge:
                return (_lo_all, _hi_all)
            lo, hi = _band_edge[name]
            hi = _hi_all if hi == float("inf") else hi
            lo, hi = max(float(lo), _lo_all), min(float(hi), _hi_all)
            return (lo, hi) if hi > lo else (_lo_all, _hi_all)

        def _zoom_to_band() -> None:
            """Give the slider the whole of the band just picked.

            Legal only from a callback, which runs before the slider widget is
            created in that rerun; after it exists its key is Streamlit's.
            """
            st.session_state["br_occ_chart_slider"] = _band_bounds(
                st.session_state.get("br_occ_chart_band") or _BAND_ALL)

        if _bands_here:
            st.segmented_control(
                "Zoom to band",
                options=[_BAND_ALL, *_bands_here],
                key="br_occ_chart_band",
                on_change=_zoom_to_band,
                help="The slider below then travels only inside this band. "
                     "Over the whole catalogue the axis can span 0.03 to 240 "
                     "GHz, where Ku is 3% of the track and fine adjustment is "
                     "impossible; inside a band the same track covers a few "
                     "GHz and the step falls with it.",
            )

        # THE SLIDER TRAVELS INSIDE THE SELECTED BAND, not over the whole
        # catalogue. That is what buys the precision: with 0.03-240 GHz on the
        # track a pixel is ~0.5 GHz, so nothing inside Ku can be set by hand.
        _band_now = st.session_state.get("br_occ_chart_band") or _BAND_ALL
        _lo_s, _hi_s = _band_bounds(_band_now)
        # 1 MHz floor, otherwise a five-hundredth of the travel.
        _step = max(round((_hi_s - _lo_s) / 500.0, 4), 0.001)

        def _clamp(lo: float, hi: float) -> "tuple[float, float]":
            lo = min(max(float(lo), _lo_s), _hi_s)
            hi = min(max(float(hi), _lo_s), _hi_s)
            return (lo, hi) if hi > lo else (_lo_s, _hi_s)

        if "br_occ_zoom" not in st.session_state:
            _prev_rng = prev.get("chart_range") or []
            if len(_prev_rng) == 2:
                try:
                    st.session_state["br_occ_zoom"] = _clamp(*_prev_rng)
                except (TypeError, ValueError):
                    st.session_state["br_occ_zoom"] = None
            elif low_ghz is not None or high_ghz is not None:
                st.session_state["br_occ_zoom"] = _clamp(
                    low_ghz if low_ghz is not None else _lo_s,
                    high_ghz if high_ghz is not None else _hi_s)
            else:
                st.session_state["br_occ_zoom"] = None

        _zoom = st.session_state.get("br_occ_zoom")
        _start = _clamp(*_zoom) if _zoom else (_lo_s, _hi_s)
        # A stored value from another band would fall outside the new bounds,
        # which Streamlit rejects; the clamp above already fixed it, but the
        # widget's own key must be corrected too, and only before it renders.
        _held = st.session_state.get("br_occ_chart_slider")
        if _held is not None and tuple(_clamp(*_held)) != tuple(_held):
            st.session_state["br_occ_chart_slider"] = _start

        # ``value`` only on the first render: it is what makes the widget a
        # two-handle range, and afterwards the key carries that shape. Passing
        # both on every run works but makes Streamlit log a notice each time.
        _sl_kw = ({"value": (float(_start[0]), float(_start[1]))}
                  if "br_occ_chart_slider" not in st.session_state else {})
        _rng = st.slider(
            "Chart range — drag to zoom the bars; full width shows everything",
            min_value=float(_lo_s), max_value=float(_hi_s),
            step=float(_step),
            **_sl_kw,
            format="%.3f GHz",
            key="br_occ_chart_slider",
            help="View only. Bars are trimmed to this window and the axis is "
                 "redrawn over it, so a comparison is read at that band's "
                 "resolution. Nothing is removed from the selection, the "
                 "metrics or the table.",
        )
        try:
            _chart_range = (float(_rng[0]), float(_rng[1]))
        except (TypeError, IndexError, ValueError):
            _chart_range = None
        # Mirror into the plain key, which is what gets persisted to disk; the
        # widget key itself is Streamlit's and must not be written after this
        # point in the script.
        st.session_state["br_occ_zoom"] = _chart_range
        # No attempt to un-highlight the band button after a manual drag: the
        # selector's own key may not be written once the widget exists. The
        # caption below states the window in force, which is the fact that
        # matters; the button keeps showing where the zoom started from.
        if _chart_range and (_chart_range[0] <= _lo_all + 1e-9
                             and _chart_range[1] >= _hi_all - 1e-9):
            _chart_range = None          # the whole catalogue: draw everything
        if _chart_range:
            _kept = sum(1 for r in chart_rows
                        if br.intervals_touch_range(r["bands"], *_chart_range))
            st.caption(
                f"Axis {_chart_range[0]:g}–{_chart_range[1]:g} GHz · "
                f"{_kept} of {len(chart_rows)} row(s) have something in it. "
                "Tooltips still carry each assignment's real edges."
            )

st_bands_chart(
    chart_rows,
    title=f"Band occupancy — {country or 'all countries'} — {direction}",
    shared_scale=True,
    span_ghz=_chart_range,
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
            # Numbers stay numbers: as formatted strings the column sorted
            # lexicographically, so 10.7 came before 3.7, and the exported CSV
            # had to be re-parsed before use.
            "band": br.letter_bands_for([(lo, hi)])[0] if br.letter_bands_for([(lo, hi)]) else "—",
            "low (GHz)": round(lo, 4),
            "high (GHz)": round(hi, 4),
            "bandwidth (MHz)": round((hi - lo) * 1000.0, 3),
            "direction": direction,
        })
st.subheader("4. Assignments")
_df = pd.DataFrame(rows)
st.caption(
    f"{len(rows)} assignment(s) of {len(selected)} system(s), {direction}. "
    "Sortable by any column; the frequency columns are numeric."
)
st.dataframe(_df, hide_index=True, width="stretch")
st.download_button(
    "Download as CSV", data=_df.to_csv(index=False).encode("utf-8"),
    file_name=f"occupancy_{country or 'all'}_{direction}.csv", mime="text/csv",
    icon=":material/download:",
)
