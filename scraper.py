"""
Previsão de ondas via Open-Meteo Marine + Weather API.
Gratuito, sem autenticação.

Fatores no score (0–10):
  Altura da onda        → base 0–9
  Período do swell      → ±2
  Proporção wind wave   → ±0 a −2  (mar bagunçado)
  Direção do swell      → +1 (S/SE/E) +1 extra (virando S→SE)
  Terral de madrugada   → +1 (vento offshore leve 04–07h)
  Lua cheia / nova      → +1
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date

import httpx

from config import Beach, score_to_label, score_to_index

logger = logging.getLogger(__name__)

MARINE_API  = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_API = "https://api.open-meteo.com/v1/forecast"

_DIRS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
         "S","SSO","SO","OSO","O","ONO","NO","NNO"]

def _deg_to_cardinal(deg: float) -> str:
    return _DIRS[round(deg / 22.5) % 16]


# ---------------------------------------------------------------------------
# Fase da Lua
# ---------------------------------------------------------------------------
_LUNAR_CYCLE = 29.53058867
_KNOWN_NEW   = date(2000, 1, 6)   # lua nova confirmada

def _moon_age(d: date) -> float:
    return (d - _KNOWN_NEW).days % _LUNAR_CYCLE

def _moon_info(d: date) -> tuple[int, str]:
    """(bônus, emoji): +1 se lua nova ou cheia (±2 dias)."""
    age = _moon_age(d)
    if age <= 2 or age >= _LUNAR_CYCLE - 2:
        return 1, "🌑"   # lua nova
    if abs(age - _LUNAR_CYCLE / 2) <= 2:
        return 1, "🌕"   # lua cheia
    return 0, ""


# ---------------------------------------------------------------------------
# Direção do swell
# Costa PR/SC voltada para E/ESE (~80°–100°)
# Swell favorável: de S a E (80°–200°)
# Zona de "virando": hoje SE/SSE (110°–165°), ontem mais S (155°–220°)
# ---------------------------------------------------------------------------
_SWELL_GOOD     = (80,  200)
_VIRADA_HOJE    = (110, 165)
_VIRADA_ONTEM   = (155, 220)

def _swell_bonus(deg: float | None, prev_deg: float | None) -> int:
    if deg is None:
        return 0
    bonus = 0
    if _SWELL_GOOD[0] <= deg <= _SWELL_GOOD[1]:
        bonus += 1
    # Swell virando de S para SE
    if (prev_deg is not None
            and _VIRADA_ONTEM[0] <= prev_deg <= _VIRADA_ONTEM[1]
            and _VIRADA_HOJE[0]  <= deg       <= _VIRADA_HOJE[1]):
        bonus += 1
    return bonus


# ---------------------------------------------------------------------------
# Terral / vento offshore de madrugada
# Para costa voltada a E/ESE, offshore vem de O/NO/SO (180°–350°)
# ---------------------------------------------------------------------------
_OFFSHORE_RANGE = (180, 350)
_TERRAL_HOURS   = list(range(4, 8))    # 04h–07h BRT
_TERRAL_MAX_KMH = 20.0                  # terral leve

def _is_offshore(deg: float) -> bool:
    return _OFFSHORE_RANGE[0] <= deg <= _OFFSHORE_RANGE[1]

def _terral_bonus(wind_speed: list, wind_dir: list, day_idx: int) -> bool:
    """True se ≥2 de 4 horas da madrugada tiverem terral leve."""
    if not wind_speed or not wind_dir:
        return False
    base = day_idx * 24
    offshore_count = 0
    for h in _TERRAL_HOURS:
        idx = base + h
        if idx >= len(wind_speed):
            break
        spd = wind_speed[idx] or 0
        drn = wind_dir[idx]   or 0
        if _is_offshore(drn) and spd <= _TERRAL_MAX_KMH:
            offshore_count += 1
    return offshore_count >= 2


# ---------------------------------------------------------------------------
# Score 0–10
# ---------------------------------------------------------------------------
def _compute_score(
    wave_height: float,
    wave_period: float,
    wind_wave:   float,
    swell_b:     int,
    terral:      bool,
    moon_b:      int,
) -> int:
    # Base: altura total
    if   wave_height < 0.3: base = 0
    elif wave_height < 0.6: base = 2
    elif wave_height < 1.0: base = 4
    elif wave_height < 1.5: base = 5
    elif wave_height < 2.0: base = 6
    elif wave_height < 2.5: base = 7
    elif wave_height < 3.0: base = 8
    else:                   base = 9

    # Período
    if   wave_period >= 14: period_b = 2
    elif wave_period >= 12: period_b = 1
    elif wave_period >= 10: period_b = 0
    elif wave_period >= 8:  period_b = -1
    else:                   period_b = -2

    # Mar picado (proporção onda de vento / total)
    ratio = wind_wave / wave_height if wave_height > 0 else 0
    if   ratio > 0.7: chop = -2
    elif ratio > 0.5: chop = -1
    else:             chop = 0

    terral_b = 1 if terral else 0

    return max(0, min(10, base + period_b + chop + swell_b + terral_b + moon_b))


# ---------------------------------------------------------------------------
# Modelo de dados
# ---------------------------------------------------------------------------
@dataclass
class DayForecast:
    beach:       Beach
    day:         date
    score:       int
    score_label: str
    score_emoji: str
    score_index: int
    wave_height: str
    wave_period: str
    wind_wave:   str
    swell_dir:   str    # cardinal (SE, S, E…)
    terral:      bool   # terral detectado na madrugada
    moon_label:  str    # 🌕 🌑 ou ""

    def extras_str(self) -> str:
        parts = []
        if self.terral:     parts.append("💨")
        if self.moon_label: parts.append(self.moon_label)
        return " " + "".join(parts) if parts else ""

    def __str__(self) -> str:
        return (
            f"{self.score_emoji} *{self.score_label}* "
            f"({self.wave_height} / {self.wave_period} / swell {self.swell_dir})"
            f"{self.extras_str()}"
        )


# ---------------------------------------------------------------------------
# Chamadas às APIs (paralelas por praia)
# ---------------------------------------------------------------------------
async def _fetch_marine(beach: Beach, client: httpx.AsyncClient) -> dict | None:
    params = {
        "latitude":  beach.lat,
        "longitude": beach.lon,
        "daily": [
            "wave_height_max",
            "wave_period_max",
            "wind_wave_height_max",
            "swell_wave_direction_dominant",
        ],
        "timezone":     "America/Sao_Paulo",
        "forecast_days": 5,
    }
    try:
        r = await client.get(MARINE_API, params=params)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.error("Marine API falhou para %s: %s", beach.name, exc)
        return None


async def _fetch_weather(beach: Beach, client: httpx.AsyncClient) -> dict | None:
    params = {
        "latitude":  beach.lat,
        "longitude": beach.lon,
        "hourly": ["wind_speed_10m", "wind_direction_10m"],
        "wind_speed_unit": "kmh",
        "timezone":     "America/Sao_Paulo",
        "forecast_days": 5,
    }
    try:
        r = await client.get(WEATHER_API, params=params)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.warning("Weather API falhou para %s (vento ignorado): %s", beach.name, exc)
        return None


# ---------------------------------------------------------------------------
# Parse da resposta
# ---------------------------------------------------------------------------
def _parse_response(
    beach:   Beach,
    marine:  dict,
    weather: dict | None,
) -> list[DayForecast]:
    results  = []
    daily    = marine.get("daily", {})
    dates    = daily.get("time",                         [])
    heights  = daily.get("wave_height_max",              [])
    periods  = daily.get("wave_period_max",              [])
    ww_list  = daily.get("wind_wave_height_max",         [])
    sw_dirs  = daily.get("swell_wave_direction_dominant",[])

    hourly     = (weather or {}).get("hourly", {})
    wind_speed = hourly.get("wind_speed_10m",    [])
    wind_dir   = hourly.get("wind_direction_10m",[])

    def _safe(lst, i):
        return lst[i] if i < len(lst) else None

    for i, date_str in enumerate(dates):
        try:
            h  = float(_safe(heights, i) or 0)
            p  = float(_safe(periods, i) or 0)
            ww = float(_safe(ww_list, i) or 0)

            raw_sd  = _safe(sw_dirs, i)
            raw_psd = _safe(sw_dirs, i - 1) if i > 0 else None

            sd  = float(raw_sd)  if raw_sd  is not None else None
            psd = float(raw_psd) if raw_psd is not None else None

            sb     = _swell_bonus(sd, psd)
            s_card = _deg_to_cardinal(sd) if sd is not None else "?"

            terral = _terral_bonus(wind_speed, wind_dir, i)

            d = date.fromisoformat(date_str)
            moon_b, moon_label = _moon_info(d)

            score = _compute_score(h, p, ww, sb, terral, moon_b)
            label, emoji = score_to_label(score)

            results.append(DayForecast(
                beach       = beach,
                day         = d,
                score       = score,
                score_label = label,
                score_emoji = emoji,
                score_index = score_to_index(score),
                wave_height = f"{h:.1f}m",
                wave_period = f"{p:.0f}s" if p else "?s",
                wind_wave   = f"{ww:.1f}m",
                swell_dir   = s_card,
                terral      = terral,
                moon_label  = moon_label,
            ))
        except Exception as exc:
            logger.debug("Erro dia %d de %s: %s", i, beach.name, exc)

    logger.info("✓ %s: %d dias", beach.name, len(results))
    return results


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------
async def fetch_forecasts(beach: Beach) -> list[DayForecast]:
    async with httpx.AsyncClient(timeout=15) as client:
        marine_data, weather_data = await asyncio.gather(
            _fetch_marine(beach, client),
            _fetch_weather(beach, client),
        )
    if marine_data is None:
        return []
    return _parse_response(beach, marine_data, weather_data)


async def fetch_all(beaches: list[Beach]) -> dict[str, list[DayForecast]]:
    sem = asyncio.Semaphore(5)

    async def _limited(b: Beach):
        async with sem:
            return b.slug, await fetch_forecasts(b)

    pairs = await asyncio.gather(*[_limited(b) for b in beaches])
    return dict(pairs)
