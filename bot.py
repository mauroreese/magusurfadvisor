"""
Agendador do bot. Dois jobs:
  1. digest_job  — roda todo dia no horário configurado → envia resumo 5 dias
  2. alert_job   — roda a cada 6 horas → envia alerta se há ondas acima do threshold
"""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import BEACHES, DAILY_DIGEST_HOUR, DAILY_DIGEST_MINUTE, MIN_ALERT_SCORE_INDEX
from scraper import fetch_all
from notifier import (
    build_daily_digest,
    build_alert_message,
    filter_good_forecasts,
    send_message,
)

logger = logging.getLogger(__name__)

# Evita enviar o mesmo alerta duas vezes pelo mesmo (praia+dia)
_sent_alerts: set[str] = set()


async def digest_job() -> None:
    """Busca todas as praias e envia o digest diário."""
    logger.info("▶ digest_job iniciado")
    forecasts = await fetch_all(BEACHES)
    msg = build_daily_digest(forecasts)
    ok = await send_message(msg)
    logger.info("digest_job %s", "enviado ✓" if ok else "FALHOU ✗")


async def alert_job() -> None:
    """
    Verifica se há ondas boas (acima do threshold) e envia alerta.
    Não reenvia o mesmo alerta no mesmo dia para a mesma praia.
    """
    logger.info("▶ alert_job iniciado")
    forecasts = await fetch_all(BEACHES)
    good = filter_good_forecasts(forecasts, min_index=MIN_ALERT_SCORE_INDEX)

    # Filtra já enviados
    new_good = []
    for fc in good:
        key = f"{fc.beach.slug}_{fc.day.isoformat()}"
        if key not in _sent_alerts:
            new_good.append(fc)
            _sent_alerts.add(key)

    if new_good:
        msg = build_alert_message(new_good)
        ok = await send_message(msg)
        logger.info("alert_job: %d novos alertas %s", len(new_good), "✓" if ok else "✗")
    else:
        logger.info("alert_job: nenhuma novidade")

    # Limpa alertas de dias passados para não encher memória
    from datetime import date
    today_str = date.today().isoformat()
    old = {k for k in _sent_alerts if k.split("_")[-1] < today_str}
    _sent_alerts.difference_update(old)


def create_scheduler() -> AsyncIOScheduler:
    tz = "America/Sao_Paulo"
    scheduler = AsyncIOScheduler(timezone=tz)

    # Digest diário no horário configurado (padrão 06:00 BRT)
    scheduler.add_job(
        digest_job,
        CronTrigger(hour=DAILY_DIGEST_HOUR, minute=DAILY_DIGEST_MINUTE, timezone=tz),
        id="digest",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Verificação de alertas a cada 6 horas
    scheduler.add_job(
        alert_job,
        IntervalTrigger(hours=6, timezone=tz),
        id="alerts",
        replace_existing=True,
        misfire_grace_time=300,
    )

    return scheduler


async def run() -> None:
    scheduler = create_scheduler()
    scheduler.start()
    logger.info(
        "Scheduler iniciado. Digest às %02d:%02d BRT. Alertas a cada 6h.",
        DAILY_DIGEST_HOUR,
        DAILY_DIGEST_MINUTE,
    )
    # Mantém o loop vivo
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("Bot encerrado.")
