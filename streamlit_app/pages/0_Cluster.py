"""Cluster — Ray distributed processing (optional).

Two states for the operator:

* **Standalone** — default. All simulations run sequentially on this
  host. Use this on a single machine; Ray adds no benefit here because
  the engine already saturates local cores via Numba/joblib.
* **Ray cluster** — a Ray head daemon is running. Other machines can
  attach with `ray start --address=...`. Useful only when you have
  more than one machine to spread the workload across.

The page is a state machine: it shows what's relevant to the current
state and offers the one transition that makes sense from here.
"""
from __future__ import annotations

import streamlit as st

from lib import cluster, storage, theme, tour
from lib.manual import help_expander

st.set_page_config(page_title="Cluster · SHARC-Orbit",
                    page_icon=":material/hub:", layout="wide")
theme.inject()

st.title("Cluster runtime")
help_expander("cluster")
tour.maybe_render("cluster")

ray_ok = cluster.is_ray_available()
cfg = cluster.load()
mode = cfg.get("mode", "standalone")

# Lazy status cache — querying Ray hits the network and we don't want
# every page rerun to pay that cost.
if "ray_status_cache" not in st.session_state:
    st.session_state["ray_status_cache"] = None


def _refresh_status() -> dict:
    try:
        st.session_state["ray_status_cache"] = cluster.status()
    except Exception as exc:  # noqa: BLE001
        # Stale gRPC channel or similar — drop client state and report down.
        cluster.shutdown()
        st.session_state["ray_status_cache"] = {
            "active": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return st.session_state["ray_status_cache"]


# ─── State detection ────────────────────────────────────────────────────────

# Three derived states from the persisted config + a quick health probe.
if mode == "standalone":
    state = "standalone"
elif not ray_ok:
    state = "ray_missing"
else:
    # We have a cluster config — probe to find out if it's actually alive.
    info = st.session_state["ray_status_cache"]
    if info is None:
        info = _refresh_status()
    if info.get("active") and info.get("nodes", 0) > 0:
        state = "cluster_up"
    else:
        state = "cluster_down"


# ─── State 1: Standalone ────────────────────────────────────────────────────

if state == "standalone":
    st.markdown("### Status &nbsp;·&nbsp; `STANDALONE`")
    st.markdown(
        "Single-machine mode. All simulations run sequentially on this host. "
        "The engine already uses multiple CPU cores internally via Numba and "
        "joblib, so on **one machine this is the right choice** — Ray adds "
        "overhead with no parallelism win."
    )
    st.markdown(
        "Switch to a Ray cluster only when you have **two or more machines** "
        "and want to spread the load across them."
    )
    st.divider()

    st.markdown("### Enable distributed mode")
    if not ray_ok:
        st.warning(
            "The `ray` package is not installed. Run "
            "`pip install -r streamlit_app/requirements.txt` to enable "
            "distributed mode."
        )
    else:
        import os as _os
        _host_cpus = _os.cpu_count() or 1

        # Build bind-IP options outside the form so the dependent text
        # input can react before the form is submitted.
        _ips = cluster.list_local_ipv4()
        _bind_options = ["Auto (Ray detects default route)"] + [
            f"{ip['ip']}  ·  {ip['name']}  ({ip['kind']})" for ip in _ips
        ] + ["Custom…"]
        _persisted_bind = cfg.get("node_ip_address", "")
        _default_idx = 0
        for _i, _opt in enumerate(_bind_options[1:-1], start=1):
            if _opt.startswith(f"{_persisted_bind}  ·"):
                _default_idx = _i
                break
        else:
            if _persisted_bind:
                _default_idx = len(_bind_options) - 1  # Custom…

        with st.form("start_cluster"):
            st.markdown("**Compute & ports**")
            col_cpu, col_port, col_client, col_dash = st.columns(4)
            with col_cpu:
                num_cpus_head = st.number_input(
                    f"CPUs (host has {_host_cpus})",
                    value=int(_host_cpus), min_value=1, max_value=int(_host_cpus),
                    step=1,
                    help="Caps how many CPU cores this node advertises to Ray. "
                         "Lower this to leave headroom for the OS/Streamlit.",
                )
            with col_port:
                port = st.number_input(
                    "GCS port", value=6379, min_value=1024, max_value=65535,
                    help="Workers connect here via `ray start --address=HEAD:<port>`.",
                )
            with col_client:
                client_port = st.number_input(
                    "Client port (ray://)", value=10001,
                    min_value=1024, max_value=65535,
                    help="Keep < 10002 to avoid Ray's worker port range "
                         "(10002–19999).",
                )
            with col_dash:
                dash_port = st.number_input(
                    "Dashboard port", value=8265,
                    min_value=1024, max_value=65535,
                )

            st.markdown("**Networking**")
            col_bind, col_dh = st.columns([3, 2])
            with col_bind:
                _bind_pick = st.selectbox(
                    "Bind IP (`--node-ip-address`)",
                    options=_bind_options, index=_default_idx,
                    help="Pick the interface Ray advertises to workers. "
                         "Leave on **Auto** for a plain LAN setup. Pick an "
                         "overlay address (e.g. `100.64.x.x`) when workers "
                         "reach this head through a VPN.",
                )
                if _bind_pick == "Custom…":
                    bind_ip_value = st.text_input(
                        "Custom bind IP",
                        value=_persisted_bind, placeholder="e.g. 100.64.1.10",
                    )
                elif _bind_pick == "Auto (Ray detects default route)":
                    bind_ip_value = ""
                else:
                    bind_ip_value = _bind_pick.split("  ·")[0].strip()
            with col_dh:
                dash_host = st.text_input(
                    "Dashboard bind address",
                    value="0.0.0.0",
                    help="`0.0.0.0` keeps the dashboard reachable from "
                         "other machines on the same network.",
                )

            st.markdown("")  # vertical breathing room
            submit = st.form_submit_button(
                "Start Ray head on this host", icon=":material/play_circle:",
                type="primary",
            )
        if submit:
            with st.spinner("Starting Ray head…"):
                res = cluster.start_head(
                    port=int(port),
                    dashboard_host=dash_host,
                    dashboard_port=int(dash_port),
                    ray_client_server_port=int(client_port),
                    num_cpus=int(num_cpus_head),
                    node_ip_address=bind_ip_value or None,
                )
            if res.get("ok"):
                hi = res.get("head_info") or {}
                # Prefer the explicit bind IP if the user set one; Ray's
                # parsed output uses the same IP anyway.
                ip = bind_ip_value or hi.get("ip") or "127.0.0.1"
                client_addr = f"ray://{ip}:{int(client_port)}"
                # Force the persisted head_info to reflect the bind IP so
                # the worker-attach command shown elsewhere uses it.
                hi["ip"] = ip
                hi["gcs_address"] = f"{ip}:{int(port)}"
                hi["client_address"] = client_addr
                cluster.shutdown()
                cluster.save({"mode": "cluster_client", "address": client_addr,
                               "num_cpus": int(num_cpus_head),
                               "node_ip_address": bind_ip_value,
                               "head_info": hi})
                st.session_state["ray_status_cache"] = None
                st.success(f"Ray head started · {int(num_cpus_head)} CPU(s) "
                            f"on this node.")
                st.rerun()
            else:
                st.error(res.get("error") or res.get("stderr") or "Failed to start head.")
                with st.expander("CLI output"):
                    st.code((res.get("stdout") or "") + "\n" + (res.get("stderr") or ""),
                             language="text")


# ─── State 2: Ray not installed ─────────────────────────────────────────────

elif state == "ray_missing":
    st.markdown("### Status &nbsp;·&nbsp; `RAY NOT INSTALLED`")
    st.warning(
        "The persisted config requests a Ray cluster but the `ray` Python "
        "package is missing. Install with "
        "`pip install -r streamlit_app/requirements.txt`, then refresh."
    )
    if st.button("Reset to standalone", icon=":material/restart_alt:", type="primary"):
        cluster.shutdown()
        cluster.save({"mode": "standalone", "address": "", "num_cpus": 0,
                       "head_info": {}})
        st.session_state["ray_status_cache"] = None
        st.rerun()


# ─── State 3: Cluster down (config says cluster, but unreachable) ──────────

elif state == "cluster_down":
    st.markdown("### Status &nbsp;·&nbsp; `RAY CLUSTER UNREACHABLE`")
    info = st.session_state["ray_status_cache"] or {}
    st.error(
        "Cannot reach the Ray head. The daemon may have been killed, the "
        "machine rebooted, or the network changed."
    )
    if info.get("error"):
        st.caption(f"Last error: `{info['error']}`")

    last_head = cfg.get("head_info") or {}
    if last_head.get("ip"):
        st.markdown(
            f"**Last known head:** `{last_head.get('gcs_address', '—')}` "
            f"(client: `{last_head.get('client_address', '—')}`)"
        )

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if st.button("Re-check", icon=":material/refresh:", key="recheck_down"):
            cluster.shutdown()
            _refresh_status()
            st.rerun()
    with col_b:
        if st.button("Restart head on this host",
                       icon=":material/restart_alt:", type="primary",
                       key="restart_head_down"):
            cluster.stop_node()
            # Reuse last persisted num_cpus + bind IP (else auto-detect).
            res = cluster.start_head(
                num_cpus=int(cfg.get("num_cpus") or 0) or None,
                node_ip_address=cfg.get("node_ip_address") or None,
            )
            if res.get("ok"):
                hi = res.get("head_info") or {}
                # Re-apply the persisted bind IP to the advertised endpoints
                # (same patch as the start flow): Ray's parsed output may
                # report a different interface than the pinned one.
                _bind = (cfg.get("node_ip_address") or "").strip()
                if _bind:
                    _gcs_port = (hi.get("gcs_address") or ":6379"
                                 ).rsplit(":", 1)[-1] or "6379"
                    _cli_port = (hi.get("client_address") or ":10001"
                                 ).rsplit(":", 1)[-1] or "10001"
                    hi["ip"] = _bind
                    hi["gcs_address"] = f"{_bind}:{_gcs_port}"
                    hi["client_address"] = f"ray://{_bind}:{_cli_port}"
                cluster.shutdown()
                # Carry over the persisted bind IP + CPU cap — `save()`
                # merges with DEFAULT_CFG only, so omitting them here would
                # silently reset both.
                cluster.save({
                    "mode": "cluster_client",
                    "address": hi.get("client_address", ""),
                    "num_cpus": int(cfg.get("num_cpus") or 0),
                    "node_ip_address": cfg.get("node_ip_address") or "",
                    "head_info": hi,
                })
                st.session_state["ray_status_cache"] = None
                st.success("Head restarted.")
                st.rerun()
            else:
                st.error(res.get("error") or res.get("stderr") or "Restart failed.")
    with col_c:
        if st.button("Return to standalone", icon=":material/home:",
                       key="back_standalone_down"):
            cluster.shutdown()
            cluster.save({"mode": "standalone", "address": "", "num_cpus": 0,
                           "head_info": {}})
            st.session_state["ray_status_cache"] = None
            st.rerun()


# ─── State 4: Cluster up ────────────────────────────────────────────────────

else:  # state == "cluster_up"
    info = st.session_state["ray_status_cache"] or {}
    st.markdown(
        f"### Status &nbsp;·&nbsp; `RAY CLUSTER ACTIVE` "
        f"&nbsp;·&nbsp; {info.get('nodes', '?')} node(s)"
    )

    resources = info.get("resources", {})
    head = cfg.get("head_info") or {}

    # Top-line metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Nodes", info.get("nodes", "?"))
    m2.metric("Total CPUs", int(resources.get("CPU", 0)))
    if "GPU" in resources:
        m3.metric("Total GPUs", int(resources.get("GPU", 0)))
    else:
        m3.metric("Total GPUs", 0)
    if "memory" in resources:
        m4.metric("Memory (GiB)", f"{resources['memory'] / (1024**3):.1f}")

    # Head endpoint + dashboard link
    if head.get("ip"):
        st.markdown(
            f"**Head:** `{head.get('gcs_address', '—')}` "
            f"&nbsp;·&nbsp; **Client:** `{head.get('client_address', '—')}` "
            + (f"&nbsp;·&nbsp; **[Dashboard]({head['dashboard']})**"
                 if head.get("dashboard") else "")
        )

    # Node table
    if info.get("node_details"):
        rows = []
        for n in info["node_details"]:
            r = n.get("resources", {})
            alive = bool(n.get("alive"))
            rows.append({
                "address": n.get("node_manager_address") or "?",
                "status": "🟩 alive" if alive else "🟥 down",
                "CPU": int(r.get("CPU", 0) or 0),
                "GPU": int(r.get("GPU", 0) or 0),
                "memory_GiB": (
                    f"{r.get('memory', 0) / (1024**3):.1f}"
                    if r.get("memory") else "—"
                ),
            })
        try:
            import pandas as pd
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        except Exception:  # noqa: BLE001
            for row in rows:
                st.write(row)

    st.divider()

    # Attach worker hint
    st.markdown("### Attach another machine")
    st.markdown(
        "Run this on each worker machine. The worker only needs:\n\n"
        "- the **same Python major.minor** as the head (3.10 / 3.11 / 3.12);\n"
        "- the **same Ray version**, including the `[default]` extra "
        "(`pip install \"ray[default]==<version>\"`);\n"
        "- network reachability to the head's GCS port "
        f"(`--address={head.get('gcs_address') or '<HEAD_IP>:6379'}`).\n\n"
        "Engine + UI code (`src/` + `streamlit_app/`) and the uploads "
        "directory are shipped automatically by Ray as `py_modules` / "
        "`working_dir` on the first task — **no manual rsync**, no need "
        "for matching paths or usernames between head and workers."
    )
    worker_addr = head.get("gcs_address") or "<HEAD_IP>:6379"
    worker_cpus = st.number_input(
        "CPUs to use on the worker (0 = all cores)",
        value=0, min_value=0, step=1,
        help="Caps how many cores this worker advertises to Ray. "
             "Leave 0 to use every core; set lower to leave headroom for "
             "the OS / other work on that machine.",
    )
    _cpu_flag = f" --num-cpus={int(worker_cpus)}" if int(worker_cpus) > 0 else ""
    st.code(f"ray start --address={worker_addr}{_cpu_flag}", language="bash")

    # ── MDB files shipped to workers via Ray working_dir ───────────────
    from pathlib import Path as _Path
    uploads = storage.list_uploads()
    from lib import UPLOADS_DIR  # local import (cycle-safe)
    rel_entries: list[str] = []
    for u in uploads:
        for k in ("srs_path", "mask_path"):
            p = u.get(k)
            if not p:
                continue
            try:
                rel = _Path(p).resolve().relative_to(UPLOADS_DIR.resolve())
                rel_entries.append(str(rel))
            except ValueError:
                continue
    rel_entries = sorted(set(rel_entries))
    uploads_size = cluster.uploads_dir_size_bytes()
    size_mb = uploads_size / (1024 * 1024)

    with st.expander(
        f"Files shipped to workers ({len(rel_entries)} item(s) · "
        f"{size_mb:.1f} MB total)",
        expanded=False,
    ):
        if not rel_entries:
            st.caption("No filings uploaded yet. Once you upload SRS/mask "
                          "files, Ray will ship them automatically.")
        else:
            st.markdown(
                "Ray packages `streamlit_app/data/uploads/` as the job's "
                "`working_dir` and replicates it to every node before tasks "
                "run. Workers receive **relative paths**, no manual sync "
                "required — they just need the same Python + Ray version, "
                "and this repo's `streamlit_app/` checkout."
            )
            st.code("\n".join(rel_entries), language="text")
            if size_mb > 1500:
                st.warning(
                    f"`uploads/` is {size_mb:.0f} MB. Ray's default "
                    "working_dir limit is raised to 2 GB by SHARC-Orbit "
                    "(see `cluster.uploads_runtime_env`). If you exceed "
                    "this, lift the limit further or prune unused uploads."
                )
            st.caption(
                "Alternative (no Ray shipping): `rsync` each worker's "
                "`streamlit_app/data/uploads/` from the head's matching "
                "path. The path resolver tries Ray's working_dir first, "
                "then the local uploads dir."
            )

    st.divider()

    # Controls — refresh / stop
    ctrl_a, ctrl_b = st.columns(2)
    with ctrl_a:
        if st.button("Refresh status", icon=":material/refresh:",
                       key="refresh_up"):
            _refresh_status()
            st.rerun()
    with ctrl_b:
        if st.button("Stop cluster & return to standalone",
                       icon=":material/stop_circle:", key="stop_up"):
            cluster.stop_node()
            cluster.save({"mode": "standalone", "address": "", "num_cpus": 0,
                           "head_info": {}})
            st.session_state["ray_status_cache"] = None
            st.success("Cluster stopped.")
            st.rerun()


# ─── Footer: terminal alternatives (collapsed) ──────────────────────────────

with st.expander("Terminal commands (alternative to the UI)"):
    hints = cluster.cli_hints()
    st.markdown("Start head:")
    st.code(hints["head"], language="bash")
    st.markdown("Attach worker:")
    st.code(hints["worker"], language="bash")
    st.markdown("Stop a node:")
    st.code(hints["stop"], language="bash")
    st.markdown(
        "Useful when you need to drive Ray from SSH on a remote worker, "
        "or recover from an inconsistent state. The UI above wraps these "
        "commands but operates only on this host."
    )
