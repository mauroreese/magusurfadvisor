"""
Ponto de entrada do bot.

Modos de uso:
    python main.py --digest   # envia o digest diário e sai (usado pelo GitHub Actions)
    python main.py --alerts   # verifica alertas e envia se houver ondas boas, e sai
    python main.py --test     # igual ao --digest, mas também imprime no terminal
    python main.py            # modo contínuo com scheduler (para servidores próprios)
"""
import argparse
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


async def _run_digest(verbose: bool = False) -> None:
    from config import BEACHES
    from scraper import fetch_all
    from notifier import build_daily_digest, send_message

    logger.info("Buscando previsões para digest diário...")
    forecasts = await fetch_all(BEACHES)
    messages = build_daily_digest(forecasts)  # lista: uma mensagem por grupo

    if verbose:
        for msg in messages:
            print("\n" + "=" * 60)
            print(msg)
        print("=" * 60 + "\n")

    for i, msg in enumerate(messages, 1):
        ok = await send_message(msg)
        if ok:
            logger.info("✓ Mensagem %d/%d enviada.", i, len(messages))
        else:
            logger.error("✗ Falha ao enviar mensagem %d.", i)
            sys.exit(1)
    logger.info("✓ Digest completo (%d mensagens).", len(messages))


async def _run_alerts() -> None:
    from config import BEACHES, MIN_ALERT_SCORE_INDEX
    from scraper import fetch_all
    from notifier import filter_good_forecasts, build_alert_message, send_message

    logger.info("Verificando alertas de ondas boas...")
    forecasts = await fetch_all(BEACHES)
    good = filter_good_forecasts(forecasts, min_index=MIN_ALERT_SCORE_INDEX)

    if good:
        msg = build_alert_message(good)
        ok = await send_message(msg)
        logger.info("✓ Alerta enviado (%d praias/dias).", len(good)) if ok else logger.error("✗ Falha ao enviar alerta.")
    else:
        logger.info("Nenhuma praia acima do threshold no momento.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bot de previsão de ondas — PR & SC Norte")
    parser.add_argument("--digest", action="store_true", help="Envia digest diário e sai")
    parser.add_argument("--alerts", action="store_true", help="Verifica e envia alertas, e sai")
    parser.add_argument("--test",   action="store_true", help="Digest com saída no terminal")
    args = parser.parse_args()

    if args.digest:
        asyncio.run(_run_digest())
    elif args.alerts:
        asyncio.run(_run_alerts())
    elif args.test:
        asyncio.run(_run_digest(verbose=True))
    else:
        from bot import run
        asyncio.run(run())


if __name__ == "__main__":
    main()
