"""
Formatação e envio de mensagens para o canal Telegram.
"""
from __future__ import annotations

import logging
from datetime import date

import httpx

from config import TELEGRAM_TOKEN, TELEGRAM_CHANNEL_ID, FORECAST_DAYS, MIN_ALERT_SCORE_INDEX
from scraper import DayForecast

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

WEEKDAYS_PT = ["Seg","Ter","Qua","Qui","Sex","Sáb","Dom"]
MONTHS_PT   = ["","Jan","Fev","Mar","Abr","Mai","Jun",
               "Jul","Ago","Set","Out","Nov","Dez"]


def _fmt_date(d: date) -> str:
    return f"{WEEKDAYS_PT[d.weekday()]} {d.day}/{MONTHS_PT[d.month]}"


async def send_message(text: str) -> bool:
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


# ---------------------------------------------------------------------------
# Digest diário
# ---------------------------------------------------------------------------
def build_daily_digest(forecasts_by_beach: dict[str, list[DayForecast]]) -> list[str]:
    """
    Retorna uma lista de mensagens — uma por dia de previsão.
    Telegram limita mensagens a 4096 caracteres; 19 praias × 5 dias
    não cabe em uma única mensagem.
    """
    today = date.today()

    all_forecasts: list[DayForecast] = []
    for days in forecasts_by_beach.values():
        all_forecasts.extend(days[:FORECAST_DAYS])

    if not all_forecasts:
        return ["⚠️ Não foi possível obter previsões agora. Tente mais tarde."]

    days_map: dict[date, list[DayForecast]] = {}
    for fc in all_forecasts:
        days_map.setdefault(fc.day, []).append(fc)

    messages = []
    sorted_days = sorted(days_map.keys())[:FORECAST_DAYS]

    for idx, day in enumerate(sorted_days):
        day_label = "HOJE" if day == today else _fmt_date(day)
        # Cabeçalho: só na 1ª mensagem inclui o título do bot
        if idx == 0:
            header = f"🏄 *Previsão PR & SC Norte*\n📅 *{day_label}*\n"
        else:
            header = f"📅 *{day_label}*\n"

        lines = [header]
        for fc in days_map[day]:
            extras = fc.extras_str()
            lines.append(
                f"  {fc.score_emoji} {fc.beach.name}: *{fc.score_label}*"
                f" — {fc.wave_height} / {fc.wave_period} / {fc.swell_dir}"
                f"{extras}"
            )

        # Rodapé só na última mensagem
        if idx == len(sorted_days) - 1:
            lines.append("\n📊 _open-meteo.com_")

        messages.append("\n".join(lines))

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
