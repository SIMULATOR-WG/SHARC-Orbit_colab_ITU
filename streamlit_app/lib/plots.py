"""plots.py — Plotly chart factories (CCDF, EPFD timeline, percentiles, 3D).

Returns plotly Figures; pages render them with st.plotly_chart(fig).
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import plotly.graph_objects as go


# ─── CCDF ───────────────────────────────────────────────────────────────────

CCDF_MIN_PERCENT = 1e-7
CCDF_MAX_PERCENT = 150.0
CCDF_Y_TICKS = [100, 10, 1, 0.1, 0.01, 0.001, 0.0001, 0.00001, 0.000001, 0.0000001]


def _format_pct_tick(pct: float) -> str:
    """Format a percentage axis tick (e.g. 0.001 → '0.001%')."""
    if pct >= 1:
        # 100 -> "100%", 10 -> "10%", 1 -> "1%"
        return f"{int(pct) if pct == int(pct) else pct}%"
    if pct >= 1e-4:
        return f"{pct:.4f}%"
    # 1e-5 -> "1e-5%", 1e-6 -> "1e-6%", ...
    return f"{pct:.0e}".replace("e+0", "e").replace("e-0", "e-").replace("+", "") + "%"


def ccdf_chart(
    series: Sequence[dict[str, Any]],
    *,
    title: str = "CCDF — EPFD↓",
    x_title: str = "EPFD↓ (dBW/m²/40 kHz)",
    y_title: str = "% of time exceeded",
    limit_curves: Sequence[dict[str, Any]] | None = None,
    height: int = 520,
    x_min: float | None = None,
    x_max: float | None = None,
) -> go.Figure:
    """Render CCDFs on a log-y axis."""
    fig = go.Figure()
    for s in series:
        fig.add_trace(
            go.Scatter(
                x=list(s.get("epfd") or []),
                y=list(s.get("percent") or []),
                mode="lines",
                name=s.get("name") or "series",
                line=dict(
                    color=s.get("color"),
                    dash=s.get("dash") or "solid",
                    width=s.get("width", 2),
                ),
                hovertemplate="EPFD=%{x:.2f} dB · %{y:.4f}%<extra>" + (s.get("name") or "") + "</extra>",
            )
        )
    for lc in limit_curves or []:
        fig.add_trace(
            go.Scatter(
                x=list(lc.get("epfd") or []),
                y=list(lc.get("percent") or []),
                mode="lines",
                name=lc.get("name") or "limit",
                line=dict(
                    color=lc.get("color", "#ef4444"),
                    dash=lc.get("dash") or "dash",
                    width=lc.get("width", 1.5),
                ),
            )
        )
    tickvals = list(CCDF_Y_TICKS)
    ticktext = [_format_pct_tick(v) for v in tickvals]
    xaxis_kwargs: dict[str, Any] = dict(
        title=x_title,
        showgrid=True,
        gridcolor="rgba(255,255,255,0.18)",
        gridwidth=1,
        zeroline=False,
        dtick=5,
        tick0=-220,
        tickangle=-45,
        minor=dict(
            ticks="",
            dtick=1,
            showgrid=True,
            gridcolor="rgba(255,255,255,0.07)",
            gridwidth=1,
        ),
    )
    if x_min is not None and x_max is not None:
        xaxis_kwargs["range"] = [float(x_min), float(x_max)]

    fig.update_layout(
        title=title,
        xaxis=xaxis_kwargs,
        yaxis=dict(
            title=y_title,
            type="log",
            tickmode="array",
            tickvals=tickvals,
            ticktext=ticktext,
            range=[math.log10(CCDF_MIN_PERCENT), math.log10(CCDF_MAX_PERCENT)],
            showgrid=True,
            gridcolor="rgba(255,255,255,0.22)",
            gridwidth=1,
            zeroline=False,
            minor=dict(
                ticks="",
                showgrid=True,
                gridcolor="rgba(255,255,255,0.08)",
                gridwidth=1,
            ),
        ),
        legend=dict(orientation="h", y=-0.25),
        margin=dict(l=60, r=20, t=50, b=80),
        height=int(height),
        template="plotly_dark",
        plot_bgcolor="#060912",
    )
    return fig


# ─── EPFD timeline ──────────────────────────────────────────────────────────


def epfd_timeline_chart(
    t_s: Sequence[float],
    epfd_db: Sequence[float],
    *,
    title: str = "EPFD↓ time series",
) -> go.Figure:
    fig = go.Figure(
        go.Scatter(x=list(t_s), y=list(epfd_db), mode="lines", name="EPFD↓", line=dict(width=1.2))
    )
    fig.update_layout(
        title=title,
        xaxis_title="time (s)",
        yaxis_title="EPFD↓ (dBW/m²/40 kHz)",
        height=380,
        template="plotly_dark",
        margin=dict(l=60, r=20, t=50, b=50),
    )
    return fig


# ─── Percentiles bar ────────────────────────────────────────────────────────


def percentiles_chart(percentiles: dict[str, float], *, title: str = "Normative percentiles") -> go.Figure:
    keys = list(percentiles.keys())
    vals = [percentiles[k] for k in keys]
    fig = go.Figure(go.Bar(x=keys, y=vals, marker_color="#4fd1c5"))
    fig.update_layout(
        title=title,
        xaxis_title="% of time exceeded",
        yaxis_title="EPFD↓ (dBW/m²/40 kHz)",
        height=340,
        template="plotly_dark",
        margin=dict(l=60, r=20, t=50, b=50),
    )
    return fig


# ─── Globe with WCG / grid points (Scattergeo orthographic) ────────────────


def globe_chart(
    *,
    es_points: Sequence[dict[str, Any]] | None = None,
    gso_points: Sequence[dict[str, Any]] | None = None,
    grid_points: Sequence[dict[str, Any]] | None = None,
    grid_gso_lons: Sequence[float] | None = None,
    title: str = "Geometry on the globe",
    height: int = 520,
    center_lat: float | None = None,
    center_lon: float | None = None,
) -> go.Figure:
    """Render ES + GSO + grid points on an orthographic globe.

    Args:
        es_points: list of {lat, lon, label?, color?, max_epfd?}
        gso_points: list of {lon, label?, color?}  — plotted on equator
        grid_points: list of {lat, lon} — light grid markers
        grid_gso_lons: GSO longitude sweep (for method_2/5)
    """
    fig = go.Figure()
    if grid_points:
        fig.add_trace(go.Scattergeo(
            lat=[p["lat"] for p in grid_points],
            lon=[p["lon"] for p in grid_points],
            mode="markers",
            marker=dict(size=4, color="rgba(96,165,250,0.45)",
                          line=dict(width=0)),
            name="ES grid",
            hovertemplate="ES grid · lat=%{lat:.1f}° · lon=%{lon:.1f}°<extra></extra>",
            # Selection styling: don't dim others, emphasize the click.
            unselected=dict(marker=dict(opacity=1.0)),
            selected=dict(marker=dict(
                opacity=1.0, size=14,
                color="#fbbf24",
            )),
        ))
    if es_points:
        colors = [p.get("color", "#4fd1c5") for p in es_points]
        labels = [p.get("label", "") for p in es_points]
        hovers = []
        for p in es_points:
            ml = f"<br>max EPFD↓ {p['max_epfd']:.2f} dB" if p.get("max_epfd") is not None else ""
            hovers.append(
                f"{p.get('label','ES')}<br>lat=%{{lat:.2f}}°<br>lon=%{{lon:.2f}}°{ml}"
            )
        fig.add_trace(go.Scattergeo(
            lat=[p["lat"] for p in es_points],
            lon=[p["lon"] for p in es_points],
            text=labels,
            mode="markers+text",
            marker=dict(size=12, color=colors, symbol="star",
                          line=dict(color="#ffffff", width=1)),
            name="ES",
            textfont=dict(size=11, color="#e6eaf2"),
            textposition="top right",
            hovertemplate=[h + "<extra></extra>" for h in hovers],
            unselected=dict(marker=dict(opacity=1.0)),
            selected=dict(marker=dict(
                opacity=1.0, size=18, color="#fbbf24",
            )),
        ))
    if gso_points:
        gso_colors = [p.get("color", "#fde047") for p in gso_points]
        gso_labels = [p.get("label", "") for p in gso_points]
        fig.add_trace(go.Scattergeo(
            lat=[0.0] * len(gso_points),
            lon=[p["lon"] for p in gso_points],
            text=gso_labels,
            mode="markers+text",
            marker=dict(size=10, color=gso_colors, symbol="diamond",
                          line=dict(color="#000000", width=1)),
            name="GSO",
            textfont=dict(size=10, color="#fde047"),
            textposition="bottom right",
            hovertemplate="GSO · lon=%{lon:.1f}°<extra></extra>",
            unselected=dict(marker=dict(opacity=1.0)),
            selected=dict(marker=dict(
                opacity=1.0, size=16, color="#fbbf24",
            )),
        ))
    if grid_gso_lons:
        fig.add_trace(go.Scattergeo(
            lat=[0.0] * len(grid_gso_lons),
            lon=list(grid_gso_lons),
            mode="markers",
            marker=dict(size=6, color="rgba(253,224,71,0.40)", symbol="diamond"),
            name="GSO sweep",
            hovertemplate="GSO sweep · lon=%{lon:.1f}°<extra></extra>",
            unselected=dict(marker=dict(opacity=1.0)),
            selected=dict(marker=dict(
                opacity=1.0, size=14, color="#fbbf24",
            )),
        ))

    # Auto-center
    if center_lat is None or center_lon is None:
        all_lats = ([p["lat"] for p in (es_points or [])]
                    + [p["lat"] for p in (grid_points or [])])
        all_lons = ([p["lon"] for p in (es_points or [])]
                    + [p["lon"] for p in (gso_points or [])]
                    + [p["lon"] for p in (grid_points or [])])
        center_lat = sum(all_lats) / len(all_lats) if all_lats else 0.0
        center_lon = sum(all_lons) / len(all_lons) if all_lons else 0.0

    fig.update_geos(
        projection_type="orthographic",
        projection_rotation=dict(lon=center_lon, lat=center_lat, roll=0),
        showcoastlines=True, coastlinecolor="rgba(255,255,255,0.45)",
        showland=True, landcolor="#0e1a2c",
        showocean=True, oceancolor="#060912",
        showcountries=True, countrycolor="rgba(255,255,255,0.15)",
        showlakes=True, lakecolor="#060912",
        bgcolor="rgba(0,0,0,0)",
        lataxis_showgrid=True, lataxis_gridcolor="rgba(255,255,255,0.08)",
        lonaxis_showgrid=True, lonaxis_gridcolor="rgba(255,255,255,0.08)",
    )
    fig.update_layout(
        title=title, template="plotly_dark",
        height=int(height),
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(orientation="h", y=-0.05),
        # Single-point click: each click replaces the previous selection
        # instead of accumulating; previously-marked points return to
        # their unselected style automatically.
        clickmode="event+select",
        dragmode=False,
    )
    return fig


# ─── 3D Earth + constellation (substitutes Cesium) ──────────────────────────


def earth_3d_chart(
    *,
    satellites_xyz: Sequence[tuple[float, float, float]] | None = None,
    earth_resolution: int = 90,
    title: str = "Constellation — 3D preview",
    height: int = 760,
    uirevision: str | None = None,
) -> go.Figure:
    import numpy as np

    u, v = np.mgrid[0 : 2 * math.pi : earth_resolution * 1j, 0 : math.pi : earth_resolution * 1j]
    xs = np.cos(u) * np.sin(v)
    ys = np.sin(u) * np.sin(v)
    zs = np.cos(v)
    fig = go.Figure()
    fig.add_trace(
        go.Surface(
            x=xs,
            y=ys,
            z=zs,
            showscale=False,
            opacity=1.0,
            colorscale=[[0, "#0e3463"], [1, "#1d6fd3"]],
            # No tooltip label, but keep the trace clickable/hoverable ("none",
            # not "skip") so a click on empty globe still returns xyz — the
            # scenario view uses it to select the nearest satellite.
            hoverinfo="none",
            # No wireframe / facet grid: hide the surface hover-contour lines and
            # use a dense mesh so the sphere reads smooth (not a lat/lon grid).
            hidesurface=False,
            contours=dict(
                x=dict(highlight=False),
                y=dict(highlight=False),
                z=dict(highlight=False),
            ),
            lighting=dict(ambient=0.85, diffuse=0.4, specular=0.05),
        )
    )
    if satellites_xyz:
        sx, sy, sz = zip(*satellites_xyz)
        fig.add_trace(
            go.Scatter3d(
                x=list(sx),
                y=list(sy),
                z=list(sz),
                mode="markers",
                marker=dict(size=2.5, color="#fde047"),
                name="satellites",
            )
        )
    fig.update_layout(
        title=title,
        scene=dict(
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            zaxis=dict(visible=False),
            aspectmode="data",
            # Persist the user's camera (zoom/rotation) across reruns: with a
            # stable chart key Streamlit does Plotly.react, and a constant
            # uirevision tells Plotly to keep the camera instead of resetting.
            uirevision=uirevision if uirevision is not None else True,
        ),
        # Top-level uirevision keeps other UI-driven state stable too.
        uirevision=uirevision if uirevision is not None else True,
        height=int(height),
        template="plotly_dark",
        margin=dict(l=0, r=0, t=40, b=0),
    )
    return fig


def earth_3d_heatmap_surface(
    pfd_grid,
    lat_deg,
    lon_deg,
    *,
    refbw_khz: float = 40.0,
    radius: float = 1.002,
    colorscale: str = "Inferno",
    zmin: float | None = None,
    zmax: float | None = None,
):
    """A ``go.Surface`` heat-map projected onto the unit globe.

    ``pfd_grid`` is ``(n_lat, n_lon)`` (dBW/m²/refBW) with ``NaN`` where the
    satellite is below the horizon. The geometry is punched with the same
    ``NaN`` mask so only the visible cap is drawn (transparent elsewhere);
    ``surfacecolor`` carries the PFD value. ``lat_deg``/``lon_deg`` are the 1D
    axis grids used to build the mesh (radius slightly > 1 so the sheet sits
    just above the base Earth surface added by :func:`earth_3d_chart`).
    """
    import numpy as np

    pfd = np.asarray(pfd_grid, dtype=float)
    lat = np.radians(np.asarray(lat_deg, dtype=float))
    lon = np.radians(np.asarray(lon_deg, dtype=float))
    LAT, LON = np.meshgrid(lat, lon, indexing="ij")
    cl = np.cos(LAT)
    xs = radius * cl * np.cos(LON)
    ys = radius * cl * np.sin(LON)
    zs = radius * np.sin(LAT)

    # Punch holes in the geometry where there is no visibility so the base
    # globe shows through instead of a flat-coloured shell.
    gap = ~np.isfinite(pfd)
    xs = np.where(gap, np.nan, xs)
    ys = np.where(gap, np.nan, ys)
    zs = np.where(gap, np.nan, zs)

    finite = pfd[np.isfinite(pfd)]
    if zmin is None:
        zmin = float(finite.min()) if finite.size else -180.0
    if zmax is None:
        zmax = float(finite.max()) if finite.size else -120.0

    return go.Surface(
        x=xs, y=ys, z=zs,
        surfacecolor=pfd,
        colorscale=colorscale,
        cmin=zmin, cmax=zmax,
        showscale=True,
        connectgaps=False,
        opacity=1.0,
        name="footprint",
        # No hover label (the pointer covers it): the hovered ground point is
        # read from a fixed corner panel instead (populated on click). Hover
        # events still fire, so the crosshair highlight below still tracks.
        hoverinfo="none",
        colorbar=dict(
            title=f"PFD<br>(dBW/m²/{refbw_khz:.0f} kHz)", thickness=14, len=0.7,
        ),
        hidesurface=False,
        # Pointing curves: the crosshair contour lines that track the mouse over
        # the footprint (Plotly Surface highlight lines) — shown on the mask so
        # the hovered ground point is pinned along all three axes.
        contours=dict(
            x=dict(highlight=True, highlightcolor="#22d3ee", highlightwidth=2),
            y=dict(highlight=True, highlightcolor="#22d3ee", highlightwidth=2),
            z=dict(highlight=True, highlightcolor="#22d3ee", highlightwidth=2),
        ),
    )


def graticule_3d(
    *,
    step_deg: float = 30.0,
    radius: float = 1.001,
    color: str = "rgba(255,255,255,0.35)",
    equator_color: str = "#22d3ee",
    equator_width: float = 3.0,
    npts: int = 181,
):
    """Lat/lon graticule for the 3D globe — returns a LIST of two traces:
    the grid (parallels every ``step_deg`` except the equator + meridians)
    and the EQUATOR as its own emphasized line. Segments separated by
    ``None`` breaks. Add with ``fig.add_traces(graticule_3d())``."""
    import numpy as np

    xs: list = []
    ys: list = []
    zs: list = []

    def _line(lat_arr, lon_arr):
        la = np.radians(np.asarray(lat_arr, dtype=float))
        lo = np.radians(np.asarray(lon_arr, dtype=float))
        cl = np.cos(la)
        return ((radius * cl * np.cos(lo)).tolist(),
                (radius * cl * np.sin(lo)).tolist(),
                (radius * np.sin(la)).tolist())

    def _add(lat_arr, lon_arr):
        x, y, z = _line(lat_arr, lon_arr)
        xs.extend(x + [None])
        ys.extend(y + [None])
        zs.extend(z + [None])

    lons = np.linspace(-180.0, 180.0, npts)
    for lat in np.arange(-60.0, 60.0 + 1e-6, step_deg):
        if abs(lat) < 1e-9:
            continue                       # equator drawn separately below
        _add(np.full(npts, lat), lons)
    lats = np.linspace(-90.0, 90.0, npts)
    for lon in np.arange(-180.0, 180.0, step_deg):
        _add(lats, np.full(npts, lon))

    grid = go.Scatter3d(
        x=xs, y=ys, z=zs, mode="lines",
        line=dict(color=color, width=1),
        name="lat/lon grid", hoverinfo="skip", showlegend=False,
    )
    ex, ey, ez = _line(np.zeros(npts), lons)
    equator = go.Scatter3d(
        x=ex, y=ey, z=ez, mode="lines",
        line=dict(color=equator_color, width=equator_width),
        name="equator", hoverinfo="name", showlegend=False,
    )
    return [grid, equator]


def equator_3d(
    *,
    radius: float = 1.003,
    color: str = "rgba(45,212,191,0.95)",  # cyan — the equatorial GSO-arc plane
    width: int = 3,
    npts: int = 361,
    name: str = "equator (GSO plane)",
):
    """Bright equator line (lat=0) on the globe — the GSO-arc plane reference.

    Distinct from :func:`graticule_3d` (which draws a faint lat=0 parallel among
    the others): thicker, brighter, and drawn just above the sphere so it reads
    as the equator against the PFD footprint heat-map."""
    import numpy as np

    lon = np.radians(np.linspace(-180.0, 180.0, npts))
    return go.Scatter3d(
        x=(radius * np.cos(lon)).tolist(),
        y=(radius * np.sin(lon)).tolist(),
        z=[0.0] * npts,
        mode="lines",
        line=dict(color=color, width=width),
        name=name, hoverinfo="skip", showlegend=False,
    )
