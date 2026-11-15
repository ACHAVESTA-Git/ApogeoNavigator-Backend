"""Horizons simulado para las pruebas (sin red): sirve texto con el formato real de JPL a partir de un CSV de data/."""
import csv
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parents[1] / "data"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
HEADER = "Center body name: Moon (301)\nAtmos refraction: NO (AIRLESS)\n"


def _instant(value: str) -> datetime:
    return datetime.strptime(value.strip("'"), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def load_rows(site_id: str = "shackleton") -> list[dict]:
    with open(DATA / f"efemerides_{site_id}.csv", newline="", encoding="utf-8") as f:
        return [{**r, "t": datetime.strptime(r["utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)} for r in csv.DictReader(f)]


def horizons_text(rows: list[tuple[datetime, float, float]], header: str = HEADER) -> str:
    lines = [f" {t.year}-{MONTHS[t.month - 1]}-{t.day:02d} {t:%H:%M},*,i, {az:.6f}, {el:.6f}," for t, az, el in rows]
    return f"*** cabecera ***\n{header}$$SOE\n" + "\n".join(lines) + "\n$$EOE\n*** pie ***\n"


class FakeHorizons:
    """Transporte httpx. Guarda las peticiones; `mode` fuerza fallos: http500, timeout, sin_soe, otro_centro, desfase."""

    def __init__(self, site_id: str = "shackleton", mode: str = "ok", fail_first: int = 0):
        self.rows = load_rows(site_id)
        self.mode, self.fail_first, self.requests = mode, fail_first, []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        if self.fail_first and len(self.requests) <= self.fail_first:
            return httpx.Response(503, text="Service Unavailable")
        if self.mode == "http500":
            return httpx.Response(500, text="<html>SECRETO-INTERNO-JPL</html>")
        if self.mode == "timeout":
            raise httpx.ReadTimeout("simulado", request=request)
        if self.mode == "sin_soe":
            return httpx.Response(200, text="No ephemeris for target SECRETO-INTERNO-JPL")
        start, stop = _instant(params["START_TIME"]), _instant(params["STOP_TIME"])
        step = int(re.match(r"(\d+)", params["STEP_SIZE"].strip("'")).group(1))
        key = ("sol" if params["COMMAND"] == "'10'" else "tierra")
        chosen = [r for i, r in enumerate(self.rows) if start <= r["t"] <= stop and i % step == 0]
        if self.mode == "desfase" and key == "tierra":
            chosen = chosen[:-1]
        series = [(r["t"], float(r[f"{key}_az_deg"]), float(r[f"{key}_el_deg"])) for r in chosen]
        header = "Center body name: Earth (399)\n" if self.mode == "otro_centro" else HEADER
        return httpx.Response(200, text=horizons_text(series, header))
