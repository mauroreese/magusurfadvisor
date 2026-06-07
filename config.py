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
    path: str   # ex: "brasil/santa-catarina/itapema/praia-da-ilhota"

    @property
    def url(self) -> str:
        return f"https://surfguru.com.br/previsao/{self.path}"

    @property
    def json_url(self) -> str:
        return f"https://surfguru.space/previsao/{self.path}.json"

    @property
    def slug(self) -> str:
        """Path completo normalizado — chave única interna (evita colisão entre praias com mesmo nome)."""
        return self.path.replace("/", "_")


BEACHES: List[Beach] = [
    # ── Paraná — URLs confirmadas ────────────────────────────────────────────
    Beach("Praia de Fora",        "brasil/parana/paranagua/praia-de-fora"),
    Beach("Praia de Ipanema",     "brasil/parana/pontal-do-parana/praia-de-ipanema"),
    Beach("Praia Atoleiro",       "brasil/parana/matinhos/praia-atoleiro"),
    Beach("Praia Matinhos",       "brasil/parana/matinhos/praia-matinhos"),
    Beach("Praia Guaratuba",      "brasil/parana/guaratuba/praia-guaratuba"),
    Beach("Praia dos Coroados",   "brasil/parana/guaratuba/praia-dos-coroados"),
    Beach("Praia da Barra do Saí","brasil/parana/guaratuba/praia-da-barra-do-sai"),
    # Beach("Ilha do Mel",        "brasil/parana/paranagua/praia-de-fora"),
    Beach("Caiobá",               "brasil/parana/matinhos/praia-brava"),
    # ── Santa Catarina — URLs confirmadas ────────────────────────────────────
    Beach("Praia Navegantes",     "brasil/santa-catarina/navegantes/praia-navegantes"),
    Beach("Praia Brava (Itajaí)", "brasil/santa-catarina/itajai/praia-brava"),
    Beach("Oceânica",             "brasil/santa-catarina/balneario-camboriu/oceanica"),
    Beach("Praia da Ilhota (Plaza)", "brasil/santa-catarina/itapema/praia-da-ilhota"),
    # ── Santa Catarina — paths a confirmar no Surfguru ───────────────────
    Beach("Itapoá",               "brasil/santa-catarina/itapoa/praia-itapoa"),
    Beach("Barra do Sul",         "brasil/santa-catarina/barra-do-sul/barra-do-sul"),
    Beach("São Francisco do Sul-itaguaçu", "brasil/santa-catarina/sao-francisco-do-sul/praia-itaguacu"),
    Beach("São Francisco do Sul-grande", "brasil/santa-catarina/sao-francisco-do-sul/praia-grande"),


    Beach("Barra Velha",          "brasil/santa-catarina/barra-velha/praia-itajuba"),
    Beach("Piçarras",             "brasil/santa-catarina/balneario-picarras/praia-de-ponta-do-jacques"),
    Beach("Penha",                "brasil/santa-catarina/penha/penha"),
]

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
