"""
Envia mensagens formatadas para o canal Telegram.
"""
from __future__ import annotations

import logging
from datetime import date

import httpx

from config import TELEGRAM_TOKEN, TELEGRAM_CHANNEL_ID, FORECAST_DAYS, MIN_ALERT_SCORE_INDEX
from scraper import DayForecast

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Nomes curtos dos dias em PT-BR
WEEKDAYS_PT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
MONTHS_PT = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
             "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def _fmt_date(d: date) -> str:
    wd = WEEKDAYS_PT[d.weekday()]
    return f"{wd} {d.day}/{MONTHS_PT[d.month]}"


async def send_message(text: str) -> bool:
    """Envia mensagem Markdown ao canal."""
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)
    if resp.status_code != 200:
        logger.error("Telegram error %s: %s", resp.status_code, resp.text)
        return False
    return True


# ---------------------------------------------------------------------------
# Digest diário
# ---------------------------------------------------------------------------
def build_daily_digest(forecasts_by_beach: dict[str, list[DayForecast]]) -> str:
    """
    Monta a mensagem de resumo diário para todos os dias e praias.
    Agrupa por dia: mostra score de cada praia em cada dia.
    """
    today = date.today()

    # Coleta todos os DayForecast em uma lista plana
    all_forecasts: list[DayForecast] = []
    for slug, days in forecasts_by_beach.items():
        all_forecasts.extend(days[:FORECAST_DAYS])

    if not all_forecasts:
        return "⚠️ Não foi possível obter previsões agora. Tente mais tarde."

    # Organiza por dia
    days_map: dict[date, list[DayForecast]] = {}
    for fc in all_forecasts:
        days_map.setdefault(fc.day, []).append(fc)

    lines = ["🏄 *Previsão de Ondas — PR & SC Norte*\n"]

    for day in sorted(days_map.keys())[:FORECAST_DAYS]:
        day_label = "📅 *HOJE*" if day == today else f"📅 *{_fmt_date(day)}*"
        lines.append(day_label)
        for fc in days_map[day]:
            lines.append(
                f"  {fc.score_emoji} {fc.beach.name}: *{fc.score_label}* "
                f"— {fc.wave_height}, {fc.wave_period}, "
                f"vento {fc.wind_speed} {fc.wind_direction}"
            )
        lines.append("")  # linha em branco entre dias

    lines.append("📊 _Fonte: surfguru.com.br_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Alerta de ondas boas
# ---------------------------------------------------------------------------
def build_alert_message(good_forecasts: list[DayForecast]) -> str:
    """Monta alerta para dias/praias acima do threshold."""
    lines = ["🚨 *Alerta de Ondas Boas!*\n"]
    for fc in good_forecasts:
        lines.append(
            f"{fc.score_emoji} *{fc.beach.name}* — {_fmt_date(fc.day)}\n"
            f"   {fc.score_label} | {fc.wave_height} | {fc.wave_period} | "
            f"vento {fc.wind_speed} {fc.wind_direction}\n"
            f"   🔗 {fc.beach.url}\n"
        )
    lines.append("_Confira mais detalhes no Surfguru!_")
    return "\n".join(lines)


def filter_good_forecasts(
    forecasts_by_beach: dict[str, list[DayForecast]],
    min_index: int = MIN_ALERT_SCORE_INDEX,
) -> list[DayForecast]:
    """Retorna DayForecast com score_index >= min_index."""
    result = []
    for days in forecasts_by_beach.values():
        for fc in days:
            if fc.score_index >= min_index:
                result.append(fc)
    # Ordena por data e depois por score (melhor primeiro)
    result.sort(key=lambda f: (f.day, -f.score))
    return result
