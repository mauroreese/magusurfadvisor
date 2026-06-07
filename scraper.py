"""
Scraper do Surfguru usando Playwright (headless Chromium).

Estratégia:
  1. Tenta o endpoint JSON do surfguru.space (leve, sem browser).
  2. Se falhar ou retornar vazio, usa Playwright para renderizar a página.

A função principal é `fetch_forecasts(beach) -> list[DayForecast]`.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Page

from config import Beach, score_to_label, score_to_index

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Modelo de dados
# ---------------------------------------------------------------------------
@dataclass
class DayForecast:
    beach: Beach
    day: date
    score: int            # 0–10 (escala Surfguru)
    score_label: str      # ruim / regular / bom / muito bom / ótimo / excelente
    score_emoji: str
    score_index: int      # 0–5
    wave_height: str      # ex: "1.2 m"
    wave_period: str      # ex: "10 s"
    wind_speed: str       # ex: "15 km/h"
    wind_direction: str   # ex: "SW"

    def __str__(self) -> str:
        return (
            f"{self.score_emoji} *{self.score_label}* "
            f"({self.wave_height} / {self.wave_period} / "
            f"vento {self.wind_speed} {self.wind_direction})"
        )

# ---------------------------------------------------------------------------
# 1. Tentativa via JSON (rápida, sem browser)
# ---------------------------------------------------------------------------
async def _try_json_api(beach: Beach) -> list[DayForecast] | None:
    url = beach.json_url  # surfguru.space/previsao/brasil/{estado}/{cidade}/{praia}.json
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "SurfBot/1.0", "Accept": "application/json"},
                follow_redirects=True,
            )
        if resp.status_code != 200 or not resp.content:
            return None
        data = resp.json()
        return _parse_json(beach, data)
    except Exception as exc:
        logger.debug("JSON API falhou para %s: %s", beach.slug, exc)
        return None


def _parse_json(beach: Beach, data: dict | list) -> list[DayForecast]:
    """
    Tenta extrair previsão do JSON do surfguru.space.
    A estrutura exata pode variar; ajuste os campos conforme necessário.
    """
    results: list[DayForecast] = []
    # Tenta estrutura comum: lista de dias ou dicionário com chave "days"/"forecast"
    days_raw = data if isinstance(data, list) else (
        data.get("days") or data.get("forecast") or data.get("data") or []
    )
    today = date.today()
    for i, day_data in enumerate(days_raw[:5]):
        try:
            score = int(day_data.get("score", day_data.get("rating", 0)))
            label, emoji = score_to_label(score)
            results.append(DayForecast(
                beach=beach,
                day=today + timedelta(days=i),
                score=score,
                score_label=label,
                score_emoji=emoji,
                score_index=score_to_index(score),
                wave_height=_fmt_height(day_data.get("wave_height", day_data.get("waveHeight", "?"))),
                wave_period=_fmt_period(day_data.get("wave_period", day_data.get("wavePeriod", "?"))),
                wind_speed=_fmt_wind_speed(day_data.get("wind_speed", day_data.get("windSpeed", "?"))),
                wind_direction=str(day_data.get("wind_direction", day_data.get("windDir", "?"))),
            ))
        except Exception as exc:
            logger.debug("Erro ao parsear dia %d: %s", i, exc)
    return results if results else None


# ---------------------------------------------------------------------------
# 2. Fallback via Playwright
# ---------------------------------------------------------------------------
async def _scrape_playwright(beach: Beach) -> list[DayForecast]:
    """Renderiza a página do Surfguru e extrai a tabela de previsão."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = await browser.new_page()
        page.set_default_timeout(30_000)

        # Interceptar resposta JSON se o site buscar da API durante o carregamento
        json_payload: list[dict] = []

        async def handle_response(response):
            if "surfguru.space" in response.url and ".json" in response.url:
                try:
                    body = await response.json()
                    json_payload.append(body)
                except Exception:
                    pass

        page.on("response", handle_response)

        await page.goto(beach.url, wait_until="networkidle", timeout=45_000)

        # Se interceptamos o JSON durante o carregamento, usar isso
        if json_payload:
            parsed = _parse_json(beach, json_payload[0])
            if parsed:
                await browser.close()
                return parsed

        # Caso contrário, raspar o HTML renderizado
        result = await _extract_from_page(page, beach)
        await browser.close()
        return result


async def _extract_from_page(page: Page, beach: Beach) -> list[DayForecast]:
    """
    Extrai dados da tabela de previsão renderizada.

    O Surfguru exibe scores como números (0-10) em células coloridas.
    Ajuste os seletores se o layout mudar.
    """
    today = date.today()
    results: list[DayForecast] = []

    try:
        # Aguarda a tabela de previsão aparecer
        await page.wait_for_selector("table.forecast, .forecast-table, [class*='forecast']", timeout=15_000)
    except Exception:
        logger.warning("Tabela de previsão não encontrada para %s", beach.name)
        return results

    # Tenta extrair linhas da tabela
    rows = await page.query_selector_all("table tr, .forecast-row, [class*='day-row']")

    # Extrai texto de cada linha e tenta parsear score
    day_offset = 0
    for row in rows:
        text = await row.inner_text()
        score = _extract_score_from_text(text)
        if score is None:
            continue

        label, emoji = score_to_label(score)
        wave_h, wave_p, wind_s, wind_d = _extract_conditions_from_text(text)

        results.append(DayForecast(
            beach=beach,
            day=today + timedelta(days=day_offset),
            score=score,
            score_label=label,
            score_emoji=emoji,
            score_index=score_to_index(score),
            wave_height=wave_h,
            wave_period=wave_p,
            wind_speed=wind_s,
            wind_direction=wind_d,
        ))
        day_offset += 1
        if day_offset >= 5:
            break

    return results


# ---------------------------------------------------------------------------
# Helpers de parsing de texto
# ---------------------------------------------------------------------------
def _extract_score_from_text(text: str) -> Optional[int]:
    """Tenta encontrar um score 0-10 no texto de uma linha."""
    # Padrão: número isolado entre 0 e 10
    matches = re.findall(r'\b([0-9]|10)\b', text)
    for m in matches:
        v = int(m)
        if 0 <= v <= 10:
            return v
    return None


def _extract_conditions_from_text(text: str) -> tuple[str, str, str, str]:
    """Tenta extrair altura, período, vento e direção do texto."""
    # Altura: número com vírgula/ponto + 'm'
    h_match = re.search(r'([\d.,]+)\s*m\b', text)
    wave_h = f"{h_match.group(1)} m" if h_match else "? m"

    # Período: número + 's'
    p_match = re.search(r'(\d+)\s*s\b', text)
    wave_p = f"{p_match.group(1)} s" if p_match else "? s"

    # Velocidade do vento: número + km/h ou nós
    ws_match = re.search(r'(\d+)\s*(km/h|kn|kt|mph)', text, re.IGNORECASE)
    wind_s = f"{ws_match.group(1)} {ws_match.group(2)}" if ws_match else "? km/h"

    # Direção do vento: N/S/E/W/NE/SW etc.
    wd_match = re.search(r'\b(N|S|E|W|NE|NW|SE|SW|NNE|NNW|SSE|SSW|ENE|ESE|WNW|WSW)\b', text)
    wind_d = wd_match.group(1) if wd_match else "?"

    return wave_h, wave_p, wind_s, wind_d


def _fmt_height(v) -> str:
    try:
        return f"{float(v):.1f} m"
    except Exception:
        return str(v) + " m" if v != "?" else "? m"


def _fmt_period(v) -> str:
    try:
        return f"{int(float(v))} s"
    except Exception:
        return str(v) + " s" if v != "?" else "? s"


def _fmt_wind_speed(v) -> str:
    try:
        return f"{int(float(v))} km/h"
    except Exception:
        return str(v) + " km/h" if v != "?" else "? km/h"


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------
async def fetch_forecasts(beach: Beach) -> list[DayForecast]:
    """
    Retorna lista de DayForecast para os próximos dias (até 5).
    Tenta JSON API primeiro; cai no Playwright se necessário.
    """
    logger.info("Buscando previsão: %s", beach.name)

    result = await _try_json_api(beach)
    if result:
        logger.info("  ✓ JSON API (%d dias)", len(result))
        return result

    logger.info("  → JSON vazio, usando Playwright")
    result = await _scrape_playwright(beach)
    logger.info("  ✓ Playwright (%d dias)", len(result))
    return result


async def fetch_all(beaches: list[Beach]) -> dict[str, list[DayForecast]]:
    """Busca todas as praias de forma concorrente (máx 3 simultâneas)."""
    sem = asyncio.Semaphore(3)

    async def _limited(b: Beach):
        async with sem:
            try:
                return b.slug, await fetch_forecasts(b)
            except Exception as exc:
                logger.error("Erro em %s: %s", b.name, exc)
                return b.slug, []

    tasks = [_limited(b) for b in beaches]
    pairs = await asyncio.gather(*tasks)
    return dict(pairs)
