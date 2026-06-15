"""
Previsão de ondas via Open-Meteo Marine + Weather API.
Gratuito, sem autenticação.

Fatores no score (0–10):
  Altura da onda        → base 0–8   (quanto maior melhor, ≥0.5m = regular)
  Período do swell      → ±2         (≥10s = bom, <8s penaliza)
  Energia (H×T×100)     → ±2         (≥400 = regular, escala livre acima)
  Vento                 → ±2         (offshore = melhor; onshore forte = pior)
  Direção do swell      → ±2         (E = melhor; ENE/ESE = bom)
  Terral de madrugada   → +1
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
_KNOWN_NEW   = date(2000, 1, 6)

def _moon_age(d: date) -> float:
    return (d - _KNOWN_NEW).days % _LUNAR_CYCLE

def _moon_info(d: date) -> tuple[int, str]:
    """(bônus, emoji): +1 se lua nova ou cheia (±2 dias)."""
    age = _moon_age(d)
    if age <= 2 or age >= _LUNAR_CYCLE - 2:
        return 1, "🌑"
    if abs(age - _LUNAR_CYCLE / 2) <= 2:
        return 1, "🌕"
    return 0, ""


# ---------------------------------------------------------------------------
# Terral / vento offshore de madrugada (04h–07h BRT, ≤20 km/h)
# ---------------------------------------------------------------------------
_TERRAL_HOURS   = list(range(4, 8))
_TERRAL_MAX_KMH = 20.0

def _is_offshore(deg: float) -> bool:
    return 180 <= deg <= 350

def _terral_bonus(wind_speed_h: list, wind_dir_h: list, day_idx: int) -> bool:
    """True se ≥2 de 4 horas da madrugada tiverem vento offshore leve."""
    if not wind_speed_h or not wind_dir_h:
        return False
    base = day_idx * 24
    count = 0
    for h in _TERRAL_HOURS:
        idx = base + h
        if idx >= len(wind_speed_h):
            break
        if _is_offshore(wind_dir_h[idx] or 0) and (wind_speed_h[idx] or 0) <= _TERRAL_MAX_KMH:
            count += 1
    return count >= 2


# ---------------------------------------------------------------------------
# Vento diário: velocidade máxima e direção dominante (hora de maior vento)
# ---------------------------------------------------------------------------
def _daily_wind_stats(wind_speed_h: list, wind_dir_h: list, day_idx: int) -> tuple[float, float]:
    base = day_idx * 24
    speeds, dirs = [], []
    for h in range(24):
        idx = base + h
        if idx >= len(wind_speed_h):
            break
        speeds.append(wind_speed_h[idx] or 0.0)
        dirs.append(wind_dir_h[idx] or 0.0)
    if not speeds:
        return 0.0, 0.0
    max_spd = max(speeds)
    dom_dir = float(dirs[speeds.index(max_spd)])
    return float(max_spd), dom_dir


# ---------------------------------------------------------------------------
# Score — fatores individuais
# ---------------------------------------------------------------------------
def _height_base(h: float) -> int:
    """Base da pontuação: altura da onda é o fator principal."""
    if h < 0.3:  return 0
    if h < 0.5:  return 1
    if h < 1.0:  return 3   # 0.5m = regular (base 3, neutros dão 2 = Regular)
    if h < 1.5:  return 5
    if h < 2.0:  return 6
    if h < 2.5:  return 7
    return 8


def _period_pts(p: float) -> int:
    """Período: ≥10s começa a ser bom; <8s penaliza."""
    if p < 8:   return -2
    if p < 10:  return -1
    if p < 12:  return 0
    if p < 14:  return 1
    return 2


def _energy_pts(h: float, p: float) -> int:
    """Energia H×T×100: ≥400 = regular; escala livre acima."""
    e = h * p * 100
    if e < 400:  return -2
    if e < 600:  return 0
    if e < 900:  return 1
    return 2


def _wind_pts(speed: float, direction: float) -> int:
    """
    Offshore (180–350°): melhor em qualquer velocidade → +2
    Parciais ao O (borda N/NNW e S/SSW): regular → 0
    Onshore: fraco = regular, forte = penaliza
    """
    if 180 <= direction <= 350:             # offshore
        return 2
    if direction > 350 or direction < 20:   # N/NNW — parcial
        return 0
    if 155 <= direction < 180:              # SSW — parcial
        return 0
    # Onshore (20°–155°): NNE → E → SE → S
    if speed <= 5:   return 0
    if speed <= 15:  return -1
    return -2


def _swell_pts(deg: float | None) -> int:
    """E é a melhor direção; variações próximas são boas."""
    if deg is None:         return 0
    if 80 <= deg <= 100:    return 2   # E
    if 55 <= deg <= 125:    return 1   # ENE a ESE
    if 20 <= deg <= 155:    return 0   # variações menores (NE a SE)
    return -1                           # desfavorável (N, O, S puros)


def _compute_score(
    wave_height: float,
    wave_period: float,
    wind_speed:  float,
    wind_dir:    float,
    swell_dir:   float | None,
    terral:      bool,
    moon_b:      int,
) -> int:
    total = (
        _height_base(wave_height)
        + _period_pts(wave_period)
        + _energy_pts(wave_height, wave_period)
        + _wind_pts(wind_speed, wind_dir)
        + _swell_pts(swell_dir)
        + (1 if terral else 0)
        + moon_b
    )
    return max(0, min(10, total))


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
    swell_dir:   str
    terral:      bool
    moon_label:  str

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
        "timezone":      "America/Sao_Paulo",
        "forecast_days": 6,
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
        "timezone":      "America/Sao_Paulo",
        "forecast_days": 6,
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
    results = []
    daily   = marine.get("daily", {})
    dates   = daily.get("time",                          [])
    heights = daily.get("wave_height_max",               [])
    periods = daily.get("wave_period_max",               [])
    sw_dirs = daily.get("swell_wave_direction_dominant", [])

    hourly     = (weather or {}).get("hourly", {})
    wind_spd_h = hourly.get("wind_speed_10m",    [])
    wind_dir_h = hourly.get("wind_direction_10m",[])

    def _safe(lst, i):
        return lst[i] if i < len(lst) else None

    today = date.today()
    for i, date_str in enumerate(dates):
        try:
            d = date.fromisoformat(date_str)
            if d <= today:
                continue  # previsão começa amanhã (D+1 a D+5)

            h = float(_safe(heights, i) or 0)
            p = float(_safe(periods, i) or 0)

            raw_sd = _safe(sw_dirs, i)
            sd     = float(raw_sd) if raw_sd is not None else None
            s_card = _deg_to_cardinal(sd) if sd is not None else "?"

            terral             = _terral_bonus(wind_spd_h, wind_dir_h, i)
            wind_spd, wind_dir = _daily_wind_stats(wind_spd_h, wind_dir_h, i)
            moon_b, moon_label = _moon_info(d)

            score        = _compute_score(h, p, wind_spd, wind_dir, sd, terral, moon_b)
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
