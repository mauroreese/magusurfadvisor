"""
Formatação e envio de mensagens para o canal Telegram.
"""
from __future__ import annotations

import logging
from datetime import date

import httpx

from config import TELEGRAM_TOKEN, TELEGRAM_CHANNEL_ID, FORECAST_DAYS, MIN_ALERT_SCORE_INDEX, BEACH_GROUPS
from scraper import DayForecast

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

WEEKDAYS_PT = ["Seg","Ter","Qua","Qui","Sex","Sáb","Dom"]
MONTHS_PT   = ["","Jan","Fev","Mar","Abr","Mai","Jun",
               "Jul","Ago","Set","Out","Nov","Dez"]


def _fmt_date(d: date) -> str:
    return f"{WEEKDAYS_PT[d.weekday()]} {d.day}/{MONTHS_PT[d.month]}"


async def _send_single(text: str) -> bool:
    """Envia uma mensagem simples (≤4096 chars)."""
    payload = {
        "chat_id":                  TELEGRAM_CHANNEL_ID,
        "text":                     text,
        "parse_mode":               "Markdown",
        "disable_web_page_preview": True,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)
    if resp.status_code != 200:
        logger.error("Telegram error %s: %s", resp.status_code, resp.text)
        return False
    return True


async def send_message(text: str) -> bool:
    """Envia texto, dividindo automaticamente se passar de 4000 chars."""
    MAX = 4000
    if len(text) <= MAX:
        return await _send_single(text)
    # Divide no último \n antes do limite
    parts, remaining = [], text
    while len(remaining) > MAX:
        cut = remaining.rfind("\n", 0, MAX)
        if cut == -1:
            cut = MAX
        parts.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    parts.append(remaining)
    for part in parts:
        if not await _send_single(part):
            return False
    return True


# ---------------------------------------------------------------------------
# Digest diário
# ---------------------------------------------------------------------------
def _build_group_message(
    group_name: str,
    beaches: list,
    forecasts_by_beach: dict[str, list[DayForecast]],
) -> str:
    """Monta uma mensagem para um grupo de praias (um estado/região)."""
    # Coleta e organiza por dia
    all_fc: list[DayForecast] = []
    for beach in beaches:
        all_fc.extend(forecasts_by_beach.get(beach.slug, [])[:FORECAST_DAYS])

    if not all_fc:
        return f"*{group_name}*\n⚠️ Sem dados disponíveis.\n"

    days_map: dict[date, list[DayForecast]] = {}
    for fc in all_fc:
        days_map.setdefault(fc.day, []).append(fc)

    lines = [f"*{group_name} — próximos 5 dias*\n"]
    for day in sorted(days_map.keys())[:FORECAST_DAYS]:
        lines.append(f"📅 *{_fmt_date(day)}*")
        for fc in days_map[day]:
            lines.append(
                f"  {fc.score_emoji} {fc.beach.name}: *{fc.score_label}*"
                f" — {fc.wave_height} / {fc.wave_period} / {fc.swell_dir}"
                f"{fc.extras_str()}"
            )
        lines.append("")
    lines.append("📊 _open-meteo.com_")
    return "\n".join(lines)


def build_daily_digest(forecasts_by_beach: dict[str, list[DayForecast]]) -> list[str]:
    """
    Retorna uma lista de strings — uma mensagem por grupo de praias.
    Cada mensagem é enviada separadamente pelo Telegram.
    """
    messages = []
    for group_name, beaches in BEACH_GROUPS:
        messages.append(_build_group_message(group_name, beaches, forecasts_by_beach))
    return messages


# ---------------------------------------------------------------------------
# Alerta de ondas boas
# ---------------------------------------------------------------------------
def build_alert_message(good_forecasts: list[DayForecast]) -> str:
    lines = ["🚨 *Alerta de Ondas Boas!*\n"]
    for fc in good_forecasts:
        extras = []
        if fc.terral:      extras.append("💨 terral")
        if fc.moon_label:  extras.append(fc.moon_label)
        extra_str = " | " + " ".join(extras) if extras else ""
        lines.append(
            f"{fc.score_emoji} *{fc.beach.name}* — {_fmt_date(fc.day)}\n"
            f"   {fc.score_label} | {fc.wave_height} | {fc.wave_period}"
            f" | swell {fc.swell_dir}{extra_str}\n"
            f"   🔗 {fc.beach.url}\n"
        )
    lines.append("_Dados: open-meteo.com_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Filtro de alertas
# ---------------------------------------------------------------------------
def filter_good_forecasts(
    forecasts_by_beach: dict[str, list[DayForecast]],
    min_index: int = MIN_ALERT_SCORE_INDEX,
) -> list[DayForecast]:
    result = []
    for days in forecasts_by_beach.values():
        for fc in days:
            if fc.score_index >= min_index:
                result.append(fc)
    result.sort(key=lambda f: (f.day, -f.score))
    return result
