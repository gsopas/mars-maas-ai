import os
import time
from textwrap import dedent
from typing import Dict, Any, Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Allow override via env var; default to the community MAAS2 endpoint
MAAS_BASE: str = os.getenv("MAAS_BASE", "https://api.maas2.apollorion.com")  # "/" latest, "/{sol}" specific

app = FastAPI(title="Curiosity MAAS Weather API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ── Tiny in-memory cache to avoid hammering MAAS ──────────────────────────────
CACHE: Dict[str, Dict[str, Any]] = {}
TTL = 15 * 60  # 15 minutes

def _get_cached(key: str) -> Optional[Any]:
    row = CACHE.get(key)
    if not row:
        return None
    if time.time() - row["t"] > TTL:
        CACHE.pop(key, None)
        return None
    return row["v"]

def _set_cached(key: str, value: Any) -> None:
    CACHE[key] = {"t": time.time(), "v": value}

def _fetch_maas(path: str) -> Any:
    url = f"{MAAS_BASE}{path}"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "mars-weather-demo/1.0"})
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Upstream MAAS error: {e}")

def _normalize_maas(d: dict) -> dict:
    # MAAS fields: sol, terrestrial_date, min_temp, max_temp, pressure, season, sunrise, sunset, etc.
    def to_float(x):
        try:
            return float(x)
        except Exception:
            return None

    return {
        "source": "curiosity_rems_maas",
        "sol": d.get("sol"),
        "earth_date": d.get("terrestrial_date"),  # YYYY-MM-DD
        "season": d.get("season"),
        "temperature_c": {
            "min": to_float(d.get("min_temp")),
            "max": to_float(d.get("max_temp")),
            "min_gts": to_float(d.get("min_gts_temp")),
            "max_gts": to_float(d.get("max_gts_temp")),
        },
        "pressure_pa": to_float(d.get("pressure")),
        "pressure_qual": d.get("pressure_string"),
        "sunrise_local": d.get("sunrise"),
        "sunset_local": d.get("sunset"),
        "uv_index": d.get("local_uv_irradiance_index"),
        "atmo_opacity": d.get("atmo_opacity"),
    }

# ── Health endpoints ──────────────────────────────────────────────────────────
@app.get("/ping")
def ping():
    return {"ok": True}

@app.get("/healthz")
def healthz():
    return {"ok": True}

# ── Normalised, cached API ────────────────────────────────────────────────────
@app.get("/weather/latest")
def weather_latest():
    key = "latest"
    cached = _get_cached(key)
    if cached:
        return cached
    data = _fetch_maas("/")  # MAAS2: "/" == latest
    out = _normalize_maas(data)
    _set_cached(key, out)
    return out

@app.get("/weather/{sol}")
def weather_by_sol(sol: int):
    key = f"sol:{sol}"
    cached = _get_cached(key)
    if cached:
        return cached
    data = _fetch_maas(f"/{sol}")
    if not data or (isinstance(data, dict) and data.get("error")):
        raise HTTPException(status_code=404, detail=f"No data for sol {sol}")
    out = _normalize_maas(data)
    _set_cached(key, out)
    return out

# ── Raw passthrough (no normalisation) ────────────────────────────────────────
@app.get("/maas")
def maas(sol: int = Query(0, ge=0)):
    """
    Raw passthrough:
      - sol=0 → latest ("/")
      - sol>0 → "/{sol}"
    Returns the MAAS2 JSON as-is.
    """
    path = "/" if sol == 0 else f"/{sol}"
    data = _fetch_maas(path)
    if not data:
        raise HTTPException(status_code=502, detail="Empty MAAS response")
    return data

# ── AI summary helpers & endpoints ────────────────────────────────────────────
def _format_brief(d: dict) -> str:
    """Deterministic, no-LLM fallback summary from normalized payload."""
    if not d:
        return "No data."
    sol = d.get("sol")
    day = d.get("earth_date")
    t = d.get("temperature_c") or {}
    tmin, tmax = t.get("min"), t.get("max")
    press = d.get("pressure_pa")
    season = d.get("season")
    uv = d.get("uv_index")
    sky = d.get("atmo_opacity")

    parts = [f"Mars (Curiosity/REMS) — Sol {sol} ({day})."]
    if tmin is not None and tmax is not None:
        parts.append(f"Temp: {tmin:.1f}°C to {tmax:.1f}°C.")
    elif tmin is not None or tmax is not None:
        parts.append(f"Temp: min {tmin if tmin is not None else '?'}°C, max {tmax if tmax is not None else '?'}°C.")
    if press is not None:
        parts.append(f"Pressure: {press:.0f} Pa.")
    if season:
        parts.append(f"Season: {season}.")
    if sky:
        parts.append(f"Atmospheric opacity: {sky}.")
    if uv:
        parts.append(f"UV Index: {uv}.")
    return " ".join(parts)

def _prompt_from_weather(d: dict) -> str:
    return dedent(f"""
    You are a concise science reporter. Summarise the Curiosity/REMS weather for a general audience in 2–3 sentences.
    Include Sol number and Earth date; mention temperatures in °C (range), pressure in Pa (if present), season and sky/opacity if present.
    Avoid speculation and do not invent numbers.
    Data (JSON):
    {d}
    """).strip()

def _call_llm(prompt: str) -> str:
    """
    OpenAI-compatible call. Configure via env vars:
      LLM_API_URL, LLM_API_KEY, LLM_MODEL
    """
    import json  # stdlib
    api_url = os.getenv("LLM_API_URL")
    api_key = os.getenv("LLM_API_KEY")
    model   = os.getenv("LLM_MODEL")
    if not (api_url and api_key and model):
        raise RuntimeError("LLM not configured (set LLM_API_URL, LLM_API_KEY, LLM_MODEL).")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a careful, concise science reporter."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }
    r = requests.post(api_url, headers=headers, data=json.dumps(payload), timeout=20)
    r.raise_for_status()
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return str(data)

@app.get("/ai/brief/latest")
def ai_brief_latest():
    d = weather_latest()  # reuse normalized data
    try:
        return {"mode": "llm", "text": _call_llm(_prompt_from_weather(d))}
    except Exception:
        return {"mode": "fallback", "text": _format_brief(d)}

@app.get("/ai/brief/{sol}")
def ai_brief_by_sol(sol: int):
    d = weather_by_sol(sol)
    try:
        return {"mode": "llm", "text": _call_llm(_prompt_from_weather(d))}
    except Exception:
        return {"mode": "fallback", "text": _format_brief(d)}
