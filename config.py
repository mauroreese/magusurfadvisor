"""
Configurações do bot de previsão de ondas.
Edite este arquivo para ajustar praias, horários e thresholds.
"""
import os
from dataclasses import dataclass
from typing import List

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID: str = os.environ["TELEGRAM_CHANNEL_ID"]  # ex: @meu_canal ou -1001234567890

# ---------------------------------------------------------------------------
# Agendamento (horário de Brasília, UTC-3)
# ---------------------------------------------------------------------------
DAILY_DIGEST_HOUR: int = int(os.getenv("DAILY_DIGEST_HOUR", "6"))   # 06:00 BRT padrão
DAILY_DIGEST_MINUTE: int = int(os.getenv("DAILY_DIGEST_MINUTE", "0"))

# ---------------------------------------------------------------------------
# Score mínimo para disparar alerta de ondas boas (0–5 na escala interna)
# 0=ruim 1=regular 2=bom 3=muito bom 4=ótimo 5=excelente
# ---------------------------------------------------------------------------
MIN_ALERT_SCORE_INDEX: int = int(os.getenv("MIN_ALERT_SCORE", "2"))  # padrão: bom

# ---------------------------------------------------------------------------
# Quantos dias de previsão exibir no digest diário (1–5)
# ---------------------------------------------------------------------------
FORECAST_DAYS: int = int(os.getenv("FORECAST_DAYS", "5"))

# ---------------------------------------------------------------------------
# Praias monitoradas
# path: caminho completo após /previsao/ no Surfguru
#       formato: brasil/{estado}/{cidade}/{praia}
# ---------------------------------------------------------------------------
@dataclass
class Beach:
    name: str
    path: str    # slug do Surfguru (para o link)
    lat: float   # latitude
    lon: float   # longitude

    @property
    def url(self) -> str:
        return f"https://surfguru.com.br/previsao/{self.path}"

    @property
    def slug(self) -> str:
        return self.path.replace("/", "_")


# ---------------------------------------------------------------------------
# Grupos de praias: cada grupo gera uma mensagem separada no digest
# ---------------------------------------------------------------------------
BEACH_GROUPS: List[tuple] = [
    # (nome_do_grupo, [praias])
    ("🏄 Paraná Norte", [
        Beach("Praia de Fora",         "brasil/parana/paranagua/praia-de-fora",             -25.52, -48.50),
        Beach("Praia de Ipanema",      "brasil/parana/pontal-do-parana/praia-de-ipanema",   -25.57, -48.35),
        Beach("Praia Atoleiro",        "brasil/parana/matinhos/praia-atoleiro",             -25.79, -48.52),
        Beach("Praia Matinhos",        "brasil/parana/matinhos/praia-matinhos",             -25.82, -48.54),
    ]),
    ("🏄 Paraná Sul", [
        Beach("Praia Guaratuba",       "brasil/parana/guaratuba/praia-guaratuba",           -25.88, -48.58),
        Beach("Praia dos Coroados",    "brasil/parana/guaratuba/praia-dos-coroados",        -25.90, -48.60),
        Beach("Praia da Barra do Saí", "brasil/parana/guaratuba/praia-da-barra-do-sai",    -26.00, -48.63),
    ]),
    ("🏄 Santa Catarina", [
        Beach("Itapoá",                "brasil/santa-catarina/itapoa/praia-itapoa",         -26.12, -48.61),
        Beach("São Francisco-Grande",  "brasil/santa-catarina/sao-francisco-do-sul/praia-grande", -26.24, -48.62),
        Beach("Barra do Sul",          "brasil/santa-catarina/barra-do-sul/barra-do-sul",   -26.46, -48.62),
        Beach("Penha",                 "brasil/santa-catarina/penha/penha",                 -26.77, -48.65),
        Beach("Praia Brava (Itajaí)",  "brasil/santa-catarina/itajai/praia-brava",          -26.85, -48.63),
        Beach("Praia Navegantes",      "brasil/santa-catarina/navegantes/praia-navegantes", -26.89, -48.65),
        Beach("Oceânica",              "brasil/santa-catarina/balneario-camboriu/oceanica", -26.98, -48.63),
        Beach("Praia da Ilhota (Plaza)","brasil/santa-catarina/itapema/praia-da-ilhota",    -27.09, -48.61),
    ]),
]

# Lista plana para uso no scraper
BEACHES: List[Beach] = [b for _, beaches in BEACH_GROUPS for b in beaches]

# Validação de duplicatas de slug em tempo de importação
_slugs = [b.slug for b in BEACHES]
assert len(_slugs) == len(set(_slugs)), \
    f"Slugs duplicados em BEACHES: {[s for s in _slugs if _slugs.count(s) > 1]}"

# ---------------------------------------------------------------------------
# Escala de score: (score_min, score_max, rótulo, emoji)
# Score 0-10 conforme exibido pelo Surfguru
# ---------------------------------------------------------------------------
SCORE_SCALE = [
    (0,  1,  "Ruim",      "🔴"),
    (2,  3,  "Regular",   "🟠"),
    (4,  5,  "Bom",       "🟡"),
    (6,  7,  "Muito Bom", "🟢"),
    (8,  8,  "Ótimo",     "🔵"),
    (9,  10, "Excelente", "⭐"),
]


def score_to_label(score: int) -> tuple:
    """(rótulo, emoji) para score 0–10."""
    for low, high, label, emoji in SCORE_SCALE:
        if low <= score <= high:
            return label, emoji
    return "?", "❓"


def score_to_index(score: int) -> int:
    """Índice 0–5 na escala interna."""
    for i, (low, high, *_) in enumerate(SCORE_SCALE):
        if low <= score <= high:
            return i
    return 0
