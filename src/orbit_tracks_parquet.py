"""Compact columnar storage of orbits (ECEF FIXED, meters) for CZML windows.

Parquet schema: entity_id (string), t_s (float64), x_m, y_m, z_m (float64).

Segmented mode (recommended): multiple Parquet files per ``t_s`` range + JSON manifest,
so the backend fetches only the needed objects from object storage (without downloading
the entire series).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

ORBIT_MANIFEST_NAME = "orbit_tracks_manifest.json"
ORBIT_SEG_SUBDIR = "orbit_seg"
MANIFEST_FORMAT_V1 = "orbit_parquet_segments_v1"

# Aligned with the viewer's CZML_WINDOW_SPAN_S (visualization/index.html)
DEFAULT_ORBIT_SEGMENT_S = 1800.0

# Above this the orbit Parquet is omitted (avoids impractical RAM/time).
DEFAULT_ORBIT_PARQUET_MAX_ROWS = 120_000_000


def downsample_cartesian_epoch_samples(cart: list[float], max_points: int) -> list[float]:
    """``cart`` is a flat list [t0,x,y,z, t1,...] (CZML epoch). Reduces to ~max_points samples."""
    n = len(cart) // 4
    if n <= 0 or max_points <= 0 or n <= max_points:
        return list(cart)
    step = max(1, (n + max_points - 1) // max_points)
    out: list[float] = []
    for i in range(0, n, step):
        j = i * 4
        out.extend(cart[j : j + 4])
    # ensure last point
    last = (n - 1) * 4
    if out[-4] != cart[last]:
        out.extend(cart[last : last + 4])
    return out


def append_sat_cart_as_rows(
    entity_id: str,
    cart: list[float],
    *,
    entity_ids: list[str],
    ts: list[float],
    xs: list[float],
    ys: list[float],
    zs: list[float],
) -> None:
    for i in range(0, len(cart), 4):
        if i + 3 >= len(cart):
            break
        entity_ids.append(entity_id)
        ts.append(float(cart[i]))
        xs.append(float(cart[i + 1]))
        ys.append(float(cart[i + 2]))
        zs.append(float(cart[i + 3]))


def write_orbit_tracks_parquet_columns(
    path: str,
    *,
    entity_id: list[str],
    t_s: list[float],
    x_m: list[float],
    y_m: list[float],
    z_m: list[float],
) -> None:
    """Write a single Parquet file (useful in tests or legacy)."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for orbit_tracks Parquet") from exc

    n = len(entity_id)
    if n == 0:
        logger.warning("orbit_tracks: no rows — not writing %s", path)
        return
    if not (len(t_s) == len(x_m) == len(y_m) == len(z_m) == n):
        raise ValueError("orbit_tracks: columns with mismatched sizes")

    table = pa.table(
        {
            "entity_id": entity_id,
            "t_s": t_s,
            "x_m": x_m,
            "y_m": y_m,
            "z_m": z_m,
        }
    )
    order = sorted(range(n), key=lambda i: (t_s[i], entity_id[i]))
    table = table.take(order)
    pq.write_table(
        table,
        path,
        compression="zstd",
        write_statistics=True,
        version="2.6",
    )
    sz_mb = os.path.getsize(path) / 1024 / 1024
    logger.info("orbit_tracks Parquet: %s (%d rows, %.2f MB)", path, n, sz_mb)


def write_orbit_tracks_segmented(
    base_dir: str,
    *,
    entity_id: list[str],
    t_s: list[float],
    x_m: list[float],
    y_m: list[float],
    z_m: list[float],
    segment_s: float = DEFAULT_ORBIT_SEGMENT_S,
    max_rows: int | None = None,
) -> str | None:
    """Write ``ORBIT_SEG_SUBDIR/{index:06d}.parquet`` and a manifest in ``base_dir``.

    Groups by time segment with **a single global sort** O(n log n) and
    contiguous slices — avoids ``seg_idx == ui`` per segment, which is O(n × nsegments) and
    stalls with tens of millions of rows.

    If ``len(entity_id) > max_rows`` (env ``WCG_ORBIT_PARQUET_MAX_ROWS`` or
    ``DEFAULT_ORBIT_PARQUET_MAX_ROWS``), writes nothing and returns ``None``.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for orbit_tracks Parquet") from exc

    n = len(entity_id)
    if n == 0:
        logger.warning("orbit_tracks segmented: no rows — not writing to %s", base_dir)
        return None

    if not (len(t_s) == len(x_m) == len(y_m) == len(z_m) == n):
        raise ValueError("orbit_tracks: columns with mismatched sizes")

    row_cap = max_rows
    if row_cap is None:
        try:
            row_cap = int(os.environ.get("WCG_ORBIT_PARQUET_MAX_ROWS", DEFAULT_ORBIT_PARQUET_MAX_ROWS))
        except ValueError:
            row_cap = DEFAULT_ORBIT_PARQUET_MAX_ROWS
    if n > row_cap:
        logger.warning(
            "orbit_tracks segmented: %d rows exceeds the limit %d "
            "(WCG_ORBIT_PARQUET_MAX_ROWS / config) — omitting the Parquet; "
            "CZML keeps only the LOD preview. Increase sample_interval or disable orbit_tracks_parquet.",
            n,
            row_cap,
        )
        return None

    seg = max(1e-6, float(segment_s))
    t_arr = np.asarray(t_s, dtype=np.float64)
    seg_idx = np.floor(t_arr / seg).astype(np.int64)
    eid_arr = np.asarray(entity_id, dtype=object)
    x_arr = np.asarray(x_m, dtype=np.float64)
    y_arr = np.asarray(y_m, dtype=np.float64)
    z_arr = np.asarray(z_m, dtype=np.float64)

    logger.info(
        "orbit_tracks segmented: sorting %d rows (segment_s=%.1f s)...",
        n,
        seg,
    )
    # Primary sort by segment index, secondary by t_s (stable across satellites).
    order = np.lexsort((t_arr, seg_idx))
    seg_s = seg_idx[order]
    t_o = t_arr[order]
    x_o = x_arr[order]
    y_o = y_arr[order]
    z_o = z_arr[order]
    e_o = eid_arr[order]

    breaks = np.flatnonzero(seg_s[1:] != seg_s[:-1]) + 1
    boundaries = np.concatenate(([0], breaks, [n]))
    nseg = int(len(boundaries) - 1)

    seg_dir = os.path.join(base_dir, ORBIT_SEG_SUBDIR)
    os.makedirs(seg_dir, exist_ok=True)

    items: list[dict[str, Any]] = []
    log_every = max(1, nseg // 25) if nseg > 25 else 1
    for j in range(nseg):
        a = int(boundaries[j])
        b = int(boundaries[j + 1])
        ui = int(seg_s[a])
        sub_t = t_o[a:b]
        sub_x = x_o[a:b]
        sub_y = y_o[a:b]
        sub_z = z_o[a:b]
        sub_eid = e_o[a:b].tolist()
        t_min = float(sub_t[0])
        t_max = float(sub_t[-1])
        fname = f"{ui:06d}.parquet"
        rel_key = f"{ORBIT_SEG_SUBDIR}/{fname}"
        out_path = os.path.join(seg_dir, fname)
        table = pa.table(
            {
                "entity_id": sub_eid,
                "t_s": sub_t,
                "x_m": sub_x,
                "y_m": sub_y,
                "z_m": sub_z,
            }
        )
        pq.write_table(
            table,
            out_path,
            compression="zstd",
            write_statistics=True,
            version="2.6",
        )
        items.append(
            {
                "index": ui,
                "t_min": t_min,
                "t_max": t_max,
                "rel_key": rel_key,
            }
        )
        if (j + 1) % log_every == 0 or j + 1 == nseg:
            logger.info(
                "  orbit_seg: wrote %d/%d files (last index=%d, %d rows)",
                j + 1,
                nseg,
                ui,
                b - a,
            )

    items.sort(key=lambda x: x["index"])
    manifest: dict[str, Any] = {
        "format": MANIFEST_FORMAT_V1,
        "segment_s": seg,
        "row_count": n,
        "items": items,
    }
    man_path = os.path.join(base_dir, ORBIT_MANIFEST_NAME)
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, allow_nan=False, indent=0)
    logger.info(
        "orbit_tracks segmented: %d rows → %d files in %s (segment_s=%.1f s)",
        n,
        len(items),
        seg_dir,
        seg,
    )
    return man_path
