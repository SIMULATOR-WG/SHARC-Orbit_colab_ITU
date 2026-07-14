from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .srs_reader import (
    list_non_geo_systems,
    list_pfd_masks_from_mask_mdb,
    read_group_for_mask,
    read_mask_info,
)
from .article22_tables import apply_article22_limits_to_config, suggest_services_for_frequency_range

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
RUN_LOCK = threading.Lock()
RUN_STATE_LOCK = threading.Lock()
RUN_PROCESS_LOCK = threading.Lock()
RUN_PROCESS: subprocess.Popen | None = None
RUN_STATE = {
    "running": False,
    "aborting": False,
    "finished": False,
    "ok": None,
    "returncode": None,
    "command": None,
    "logs": "",
    "viewer_url": "/visualization/index.html",
    "started_at": None,
    "updated_at": None,
}


def _pick_directory_dialog(initial_dir: str | None = None) -> str | None:
    start_dir = _resolve_scan_dir(initial_dir)

    # Try zenity first, which usually integrates better on Linux desktops.
    zenity = shutil.which("zenity")
    if zenity:
        try:
            proc = subprocess.run(
                [zenity, "--file-selection", "--directory", "--filename", str(start_dir) + "/"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0:
                selected = (proc.stdout or "").strip()
                if selected:
                    return selected
            elif proc.returncode == 1:
                return None
        except Exception:
            pass

    # Fallback to the native dialog via tkinter.
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(initialdir=str(start_dir))
        root.destroy()
        return selected or None
    except Exception:
        return None


def _resolve_scan_dir(base_dir: str | None) -> Path:
    raw = str(base_dir or "").strip()
    if not raw:
        return ROOT_DIR / "data"
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (ROOT_DIR / candidate).resolve()
    return candidate


def _display_path(path: Path) -> str:
    try:
        return os.path.relpath(path, ROOT_DIR)
    except Exception:
        return str(path)


def _scan_mdb_files(base_dir: str | None = None) -> tuple[list[str], str]:
    scan_dir = _resolve_scan_dir(base_dir)
    if not scan_dir.exists():
        raise FileNotFoundError(f"Folder not found: {scan_dir}")
    if not scan_dir.is_dir():
        raise NotADirectoryError(f"Path is not a folder: {scan_dir}")

    files: set[str] = set()
    for path in scan_dir.rglob("*.mdb"):
        if path.is_file():
            files.add(_display_path(path))
    for path in scan_dir.rglob("*.MDB"):
        if path.is_file():
            files.add(_display_path(path))
    return sorted(files), _display_path(scan_dir)


def _json_response(handler: SimpleHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _snapshot_run_state() -> dict:
    with RUN_STATE_LOCK:
        return dict(RUN_STATE)


def _append_run_log(text: str) -> None:
    if not text:
        return
    with RUN_STATE_LOCK:
        RUN_STATE["logs"] += text
        lines = RUN_STATE["logs"].splitlines()
        if len(lines) > 500:
            RUN_STATE["logs"] = "\n".join(lines[-500:]) + ("\n" if RUN_STATE["logs"].endswith("\n") else "")
        RUN_STATE["updated_at"] = time.time()


def _set_run_state(**kwargs) -> None:
    with RUN_STATE_LOCK:
        RUN_STATE.update(kwargs)
        RUN_STATE["updated_at"] = time.time()


def _run_export_in_background(command: list[str]) -> None:
    global RUN_PROCESS
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
            start_new_session=True,
        )
        with RUN_PROCESS_LOCK:
            RUN_PROCESS = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            _append_run_log(line)
        proc.wait()
        _set_run_state(
            running=False,
            aborting=False,
            finished=True,
            ok=(proc.returncode == 0),
            returncode=proc.returncode,
        )
    except Exception as exc:
        _append_run_log(f"\n[launcher] Error while running simulation: {exc}\n")
        _set_run_state(
            running=False,
            aborting=False,
            finished=True,
            ok=False,
            returncode=-1,
        )
    finally:
        with RUN_PROCESS_LOCK:
            RUN_PROCESS = None
        if RUN_LOCK.locked():
            RUN_LOCK.release()


def _abort_running_process() -> tuple[bool, str]:
    with RUN_PROCESS_LOCK:
        proc = RUN_PROCESS
    if proc is None:
        return False, "No simulation running."
    if proc.poll() is not None:
        return False, "The simulation has already finished."

    _append_run_log("\n[launcher] Abort request sent by the user.\n")
    _set_run_state(aborting=True)
    try:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _append_run_log("[launcher] Process did not terminate with SIGTERM; sending SIGKILL.\n")
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                proc.kill()
            proc.wait(timeout=5)
        return True, "Simulation aborted."
    except Exception as exc:
        return False, f"Failed to abort simulation: {exc}"


class LauncherHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/mdb-files":
            params = parse_qs(parsed.query)
            base_dir = (params.get("base_dir") or [""])[0]
            try:
                files, resolved_base_dir = _scan_mdb_files(base_dir)
                _json_response(
                    self,
                    {"files": files, "base_dir": resolved_base_dir},
                )
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/pick-folder":
            params = parse_qs(parsed.query)
            initial_dir = (params.get("initial_dir") or [""])[0]
            selected = _pick_directory_dialog(initial_dir)
            _json_response(
                self,
                {
                    "selected": selected,
                    "cancelled": selected is None,
                },
            )
            return
        if parsed.path == "/api/run-status":
            _json_response(self, _snapshot_run_state())
            return
        if parsed.path == "/api/notices":
            params = parse_qs(parsed.query)
            rel_path = (params.get("srs_mdb") or [""])[0]
            if not rel_path:
                _json_response(self, {"error": "Parameter srs_mdb is required."}, status=400)
                return
            try:
                systems = list_non_geo_systems(str(ROOT_DIR / rel_path))
                _json_response(self, {"systems": systems})
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/masks":
            params = parse_qs(parsed.query)
            rel_path = (params.get("mask_mdb") or [""])[0]
            ntc_id = (params.get("ntc_id") or [None])[0]
            srs_mdb = (params.get("srs_mdb") or [None])[0]
            if not rel_path:
                _json_response(self, {"error": "Parameter mask_mdb is required."}, status=400)
                return
            try:
                payload = list_pfd_masks_from_mask_mdb(str(ROOT_DIR / rel_path), ntc_id=ntc_id)
                srs_masks = {}
                if srs_mdb:
                    for item in read_mask_info(str(ROOT_DIR / srs_mdb), ntc_id=ntc_id):
                        srs_masks[item.mask_id] = item
                for item in payload:
                    meta = srs_masks.get(int(item["mask_id"]))
                    if meta is not None:
                        freq_min_ghz = float(meta.freq_min_ghz)
                        freq_max_ghz = float(meta.freq_max_ghz)
                        grp = None
                        if srs_mdb:
                            grp = read_group_for_mask(
                                str(ROOT_DIR / srs_mdb),
                                ntc_id=str(ntc_id or ""),
                                mask_id=int(item["mask_id"]),
                                preferred_emi_rcp="E",
                            )
                        if grp is not None:
                            # Refine the band by the operating group, but never
                            # invert it: if the mask band and the grp band do
                            # not overlap, the grp band is the operating-
                            # frequency authority (the mask is bound by the
                            # mask_lnk1 link, not by band containment).
                            if grp.freq_min_ghz is not None and grp.freq_max_ghz is not None:
                                g_lo = float(grp.freq_min_ghz)
                                g_hi = float(grp.freq_max_ghz)
                                lo = max(freq_min_ghz, g_lo)
                                hi = min(freq_max_ghz, g_hi)
                                if hi > lo:
                                    freq_min_ghz, freq_max_ghz = lo, hi
                                else:
                                    freq_min_ghz, freq_max_ghz = g_lo, g_hi
                            item["grp_id"] = grp.grp_id
                            item["grp_emi_rcp"] = grp.emi_rcp
                            item["grp_elev_min_deg"] = grp.elev_min_deg
                        item["freq_min_ghz"] = freq_min_ghz
                        item["freq_max_ghz"] = freq_max_ghz
                        item["mask_freq_min_ghz"] = meta.freq_min_ghz
                        item["mask_freq_max_ghz"] = meta.freq_max_ghz
                        item["label"] = (
                            f"mask_id {item['mask_id']} · {item['ntc_id'] or 'n/a'} · "
                            f"{freq_min_ghz:.3f}-{freq_max_ghz:.3f} GHz"
                        )
                        services = suggest_services_for_frequency_range(
                            meta.freq_min_ghz,
                            meta.freq_max_ghz,
                        )
                        item["service_candidates"] = services
                        if len(services) == 1:
                            item["suggested_service"] = services[0]
                _json_response(self, {"masks": payload})
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/article22-preview":
            params = parse_qs(parsed.query)
            try:
                freq_min = float((params.get("freq_min_ghz") or [""])[0])
                freq_max = float((params.get("freq_max_ghz") or [""])[0])
                service = str((params.get("service") or ["FSS"])[0] or "FSS").upper()
                diameter_m = float((params.get("diameter_m") or ["1.2"])[0] or 1.2)
                sim_freq_ghz = (params.get("simulation_frequency_ghz") or [None])[0]
            except ValueError:
                _json_response(self, {"error": "Invalid parameters for Art. 22 preview."}, status=400)
                return
            try:
                cfg = {
                    "non_gso": {"frequency_ghz": float(sim_freq_ghz) if sim_freq_ghz not in (None, "") else freq_min},
                    "gso_es": {
                        "service": service,
                        "antenna_diameter_m": diameter_m,
                    },
                    "pfd_mask": {
                        "freq_min_ghz": freq_min,
                        "freq_max_ghz": freq_max,
                        "simulation_frequency_ghz": (
                            float(sim_freq_ghz) if sim_freq_ghz not in (None, "") else None
                        ),
                    },
                    "article22_limits": {"reference_bandwidth_khz": 40.0},
                }
                apply_article22_limits_to_config(cfg)
                art22 = cfg.get("article22_limits", {})
                _json_response(
                    self,
                    {
                        "ok": bool(art22.get("limits")),
                        "rr_reference": art22.get("rr_reference"),
                        "reference_bandwidth_khz": art22.get("reference_bandwidth_khz"),
                        "rf_diam_cm": art22.get("_epfd_rf_diam_cm"),
                        "rf_pattern_rr": art22.get("_epfd_rf_pattern_rr"),
                        "frequency_run_ghz": cfg.get("non_gso", {}).get("frequency_ghz"),
                        "limits": art22.get("limits", []),
                    },
                )
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, status=400)
            return
        if parsed.path in {"/", "/launcher", "/launcher/"}:
            self.path = "/visualization/launcher.html"
        elif parsed.path in {"/viewer", "/viewer/"}:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/visualization/index.html")
            self.end_headers()
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/abort":
            ok, message = _abort_running_process()
            _json_response(
                self,
                {"ok": ok, "message": message},
                status=200 if ok else 409,
            )
            return
        if parsed.path != "/api/run":
            self.send_error(HTTPStatus.NOT_FOUND, "Route not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            _json_response(self, {"error": "Invalid JSON."}, status=400)
            return

        srs_mdb = str(payload.get("srs_mdb") or "").strip()
        mask_mdb = str(payload.get("mask_mdb") or "").strip()
        ntc_id = str(payload.get("ntc_id") or "").strip()
        mask_id = payload.get("mask_id")
        if not srs_mdb or not mask_mdb or not ntc_id or mask_id in (None, ""):
            _json_response(
                self,
                {"error": "Fill in SRS MDB, Masks MDB, notice and mask_id."},
                status=400,
            )
            return

        command = [
            sys.executable,
            "-m",
            "src.export_visualization",
            "--mdb",
            srs_mdb,
            "--pfd-mask-mdb",
            mask_mdb,
            "--mask-id",
            str(mask_id),
            "--ntc-id",
            ntc_id,
            "--service",
            str(payload.get("service") or "FSS").upper(),
            "--es-antenna-diameter",
            str(float(payload.get("es_antenna_diameter") or 1.2)),
            "--dual-time-step-mode",
            ({"on": "s1503"}.get(
                str(payload.get("dual_time_step_mode") or "off").strip().lower(),
                str(payload.get("dual_time_step_mode") or "off"),
            )),
        ]

        if payload.get("nsteps") not in (None, ""):
            command.extend([
                "--nsteps",
                str(int(payload["nsteps"])),
            ])
        if payload.get("coarse_time_step_s") not in (None, ""):
            command.extend([
                "--coarse-time-step-s",
                str(float(payload["coarse_time_step_s"])),
            ])
        if payload.get("fine_time_step_s") not in (None, ""):
            command.extend([
                "--fine-time-step-s",
                str(float(payload["fine_time_step_s"])),
            ])

        if payload.get("simulation_frequency_ghz") not in (None, ""):
            command.extend([
                "--simulation-frequency-ghz",
                str(float(payload["simulation_frequency_ghz"])),
            ])

        s1503_step = payload.get("s1503_step")
        if payload.get("wcga_s1503"):
            command.append("--wcga-s1503")
            if s1503_step not in (None, ""):
                command.extend(["--s1503-step", str(float(s1503_step))])
        if payload.get("no_static_es"):
            command.append("--no-static-es")
        if not bool(payload.get("apply_gso_min_elevation", True)):
            command.append("--no-apply-gso-min-elevation")
        if payload.get("strict_max_co_freq_total"):
            command.append("--strict-max-co-freq-total")
        if payload.get("artificial_precession"):
            command.append("--artificial-precession")
        if payload.get("no_s1503_literal_time_step"):
            command.append("--no-s1503-literal-time-step")
        if payload.get("sample_interval") not in (None, ""):
            command.extend(["--sample-interval", str(float(payload["sample_interval"]))])

        acquired = RUN_LOCK.acquire(blocking=False)
        if not acquired:
            _json_response(
                self,
                {"error": "A simulation is already running in this interface."},
                status=409,
            )
            return

        _set_run_state(
            running=True,
            aborting=False,
            finished=False,
            ok=None,
            returncode=None,
            command=command,
            logs="",
            viewer_url="/visualization/index.html",
            started_at=time.time(),
        )
        worker = threading.Thread(
            target=_run_export_in_background,
            args=(command,),
            daemon=True,
        )
        worker.start()
        _json_response(
            self,
            {
                "ok": True,
                "started": True,
                "command": command,
                "viewer_url": "/visualization/index.html",
            },
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Web interface to configure and run EPFD simulations.")
    parser.add_argument("--port", type=int, default=8091, help="Port of the web launcher.")
    args = parser.parse_args()

    httpd = ThreadingHTTPServer(("", args.port), LauncherHandler)
    logger.info("EPFD launcher at http://localhost:%d", args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down launcher...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
