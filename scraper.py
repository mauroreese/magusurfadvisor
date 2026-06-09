"""
Previsão de ondas via Open-Meteo Marine API.
Gratuito, sem autenticação, cobre toda a costa brasileira.
https://open-meteo.com/en/docs/marine-weather-api

Variáveis usadas (todas disponíveis na marine API):
  daily: wave_height_max, wave_period_max, wind_wave_height_max
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date

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
    wind_wave: str  # altura das ondas de vento (proxy de vento local)

    def __str__(self) -> str:
        return (
            f"{self.score_emoji} *{self.score_label}* "
            f"({self.wave_height} / {self.wave_period} / "
            f"vento-local {self.wind_wave})"
        )


# ---------------------------------------------------------------------------
# Score calculado a partir de altura + período + wind wave
# ---------------------------------------------------------------------------
def _compute_score(wave_height: float, wave_period: float, wind_wave_height: float) -> int:
    """
    Score 0-10:
    - Base: altura total da onda
    - Bônus: período longo = swell limpo
    - Penalidade: alta proporção de ondas de vento = condições ruins
    """
    # Base pela altura total
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

    # Penalidade: se a onda de vento é grande em relação à total → mar bagunçado
    wind_ratio = wind_wave_height / wave_height if wave_height > 0 else 0
    if wind_ratio > 0.7:
        wind_penalty = -2
    elif wind_ratio > 0.5:
        wind_penalty = -1
    else:
        wind_penalty = 0

    return max(0, min(10, base + period_bonus + wind_penalty))


# ---------------------------------------------------------------------------
# Busca via Open-Meteo Marine API
# ---------------------------------------------------------------------------
async def fetch_forecasts(beach: Beach) -> list[DayForecast]:
    params = {
        "latitude": beach.lat,
        "longitude": beach.lon,
        "daily": ["wave_height_max", "wave_period_max", "wind_wave_height_max"],
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

    dates      = daily.get("time", [])
    heights    = daily.get("wave_height_max", [])
    periods    = daily.get("wave_period_max", [])
    wind_waves = daily.get("wind_wave_height_max", [])

    for i, date_str in enumerate(dates):
        try:
            h  = float(heights[i]    or 0)
            p  = float(periods[i]    or 0)
            ww = float(wind_waves[i] or 0)

            score = _compute_score(h, p, ww)
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
                wind_wave=f"{ww:.1f} m",
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
