"""
Scraper de previsão de ondas usando Open-Meteo Marine API.
Gratuito, sem autenticação, cobre toda a costa brasileira.
https://open-meteo.com/en/docs/marine-weather-api
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

from config import Beach, score_to_label, score_to_index

logger = logging.getLogger(__name__)

MARINE_API = "https://marine-api.open-meteo.com/v1/marine"

# ---------------------------------------------------------------------------
# Modelo de dados
# ---------------------------------------------------------------------------
@dataclass
class DayForecast:
    beach: Beach
    day: date
    score: int
    score_label: str
    score_emoji: str
    score_index: int
    wave_height: str
    wave_period: str
    wind_speed: str
    wind_direction: str

    def __str__(self) -> str:
        return (
            f"{self.score_emoji} *{self.score_label}* "
            f"({self.wave_height} / {self.wave_period} / "
            f"vento {self.wind_speed} {self.wind_direction})"
        )


# ---------------------------------------------------------------------------
# Score calculado a partir de altura + período
# ---------------------------------------------------------------------------
WIND_DIRECTIONS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                   "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]

def _degrees_to_cardinal(deg: float) -> str:
    idx = round(deg / 22.5) % 16
    return WIND_DIRECTIONS[idx]

def _compute_score(wave_height: float, wave_period: float, wind_speed_kmh: float, wind_dir_deg: float) -> int:
    """
    Calcula score 0-10 com base em:
    - Altura da onda (principal fator)
    - Período (ondas com período longo são melhores)
    - Vento (forte vento onshore piora as condições)
    """
    # Base pelo height
    if wave_height < 0.3:
        base = 0
    elif wave_height < 0.6:
        base = 2
    elif wave_height < 1.0:
        base = 4
    elif wave_height < 1.5:
        base = 5
    elif wave_height < 2.0:
        base = 6
    elif wave_height < 2.5:
        base = 7
    elif wave_height < 3.0:
        base = 8
    else:
        base = 9

    # Bônus de período
    if wave_period >= 14:
        period_bonus = 2
    elif wave_period >= 12:
        period_bonus = 1
    elif wave_period >= 10:
        period_bonus = 0
    elif wave_period >= 8:
        period_bonus = -1
    else:
        period_bonus = -2

    # Penalidade de vento forte
    if wind_speed_kmh > 40:
        wind_penalty = -2
    elif wind_speed_kmh > 25:
        wind_penalty = -1
    else:
        wind_penalty = 0

    score = max(0, min(10, base + period_bonus + wind_penalty))
    return score


# ---------------------------------------------------------------------------
# Busca via Open-Meteo Marine API
# ---------------------------------------------------------------------------
async def fetch_forecasts(beach: Beach) -> list[DayForecast]:
    params = {
        "latitude": beach.lat,
        "longitude": beach.lon,
        "daily": [
            "wave_height_max",
            "wave_period_max",
            "wind_wave_height_max",
        ],
        "hourly": [
            "wind_speed_10m",
            "wind_direction_10m",
        ],
        "wind_speed_unit": "kmh",
        "timezone": "America/Sao_Paulo",
        "forecast_days": 5,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(MARINE_API, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Open-Meteo falhou para %s: %s", beach.name, exc)
        return []

    return _parse_response(beach, data)


def _parse_response(beach: Beach, data: dict) -> list[DayForecast]:
    results = []
    daily = data.get("daily", {})
    hourly = data.get("hourly", {})

    dates       = daily.get("time", [])
    heights     = daily.get("wave_height_max", [])
    periods     = daily.get("wave_period_max", [])
    wind_speeds = hourly.get("wind_speed_10m", [])
    wind_dirs   = hourly.get("wind_direction_10m", [])

    # Média do vento por dia (24 pontos por dia)
    daily_wind_speed = []
    daily_wind_dir   = []
    for i in range(len(dates)):
        chunk_s = wind_speeds[i*24:(i+1)*24]
        chunk_d = wind_dirs[i*24:(i+1)*24]
        avg_s = sum(chunk_s) / len(chunk_s) if chunk_s else 0
        avg_d = sum(chunk_d) / len(chunk_d) if chunk_d else 0
        daily_wind_speed.append(avg_s)
        daily_wind_dir.append(avg_d)

    for i, date_str in enumerate(dates):
        try:
            h  = float(heights[i]  or 0)
            p  = float(periods[i]  or 0)
            ws = float(daily_wind_speed[i] if i < len(daily_wind_speed) else 0)
            wd = float(daily_wind_dir[i]   if i < len(daily_wind_dir)   else 0)

            score = _compute_score(h, p, ws, wd)
            label, emoji = score_to_label(score)

            results.append(DayForecast(
                beach=beach,
                day=date.fromisoformat(date_str),
                score=score,
                score_label=label,
                score_emoji=emoji,
                score_index=score_to_index(score),
                wave_height=f"{h:.1f} m",
                wave_period=f"{p:.0f} s" if p else "? s",
                wind_speed=f"{ws:.0f} km/h",
                wind_direction=_degrees_to_cardinal(wd),
            ))
        except Exception as exc:
            logger.debug("Erro ao parsear dia %d de %s: %s", i, beach.name, exc)

    logger.info("✓ %s: %d dias", beach.name, len(results))
    return results


# ---------------------------------------------------------------------------
# Busca todas as praias de forma concorrente
# ---------------------------------------------------------------------------
async def fetch_all(beaches: list[Beach]) -> dict[str, list[DayForecast]]:
    sem = asyncio.Semaphore(5)

    async def _limited(b: Beach):
        async with sem:
            return b.slug, await fetch_forecasts(b)

    pairs = await asyncio.gather(*[_limited(b) for b in beaches])
    return dict(pairs)
