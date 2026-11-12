"""
Reglas de negocio y economía de Kurama Run — lógica interna de servidor.

SIN contratos inteligentes: todo saldo ($KUR, $KUSD, cristales), Elo,
límite diario, escrow de retos y comisiones de la casa vive en este libro
mayor interno (`EconomyServer`). Es Python puro (sin Streamlit ni web3) para
poder ejecutarlo en el backend real, en tests o embebido en el dashboard.

Reglas DEFINITIVAS (revisión 11/11/2026):

1. REQUISITO GENERAL DE ACCESO — NFT o experiencia
   - Una cuenta está DESBLOQUEADA si tiene al menos un NFT o si acumula
     ACCESS_BATTLES_REQUIRED (300) combates en total (histórico, sumando
     Free —con o sin cristales, también contra bots— y Competitivo).
   - ACCESO COMPLETO = Competitivo + depósitos + retiradas + COMPRAVENTA en
     el market de ítems (publicar y comprar; ver minimarket más abajo).
   - Sin desbloqueo: bloqueados los DEPÓSITOS, las RETIRADAS, los combates
     COMPETITIVOS y la compraventa en el market. Una cuenta sin NFT y con
     menos de 300 combates SÍ puede jugar Free, tanto con cristales (si los
     tiene, ganados en misiones/runner) como sin cristales (10/día): es el
     camino para llegar a los 300.

2. MODO FREE
   ESTATUS NFT: una cuenta tiene "estatus NFT" si posee un NFT o si
   acumula ACCESS_BATTLES_REQUIRED (300) batallas totales (veterana). Con
   300+ batallas se la trata EXACTAMENTE igual que a una cuenta con NFT
   (Free con cristales, Competitivo, depósitos, market) aunque no tenga
   ningún NFT equipado. Ver Account.nft_status.
   VARIANTE ELEGIDA POR EL JUGADOR (revisión 12/11/2026): cualquier cuenta,
   con o sin NFT / 300 batallas, elige jugar Free CON o SIN cristales
   (EconomyServer.set_free_variant -> Account.free_variant). Si no ha
   elegido, por defecto: con estatus NFT -> con cristales; sin él -> sin.
   La lista pública solo muestra humanos de la MISMA variante elegida y
   nunca bots.
   a) CON CRISTALES (ChallengeKind.CRYSTAL) — abierto a TODAS las cuentas
      que tengan cristales. Apuesta FIJA: no existe apostar varios cristales.
      Cada partida GASTA exactamente CRYSTAL_STAKE (1) cristal propio: se
      retiene al lanzar/aceptar el reto y se CONSUME al jugarse, pase lo que
      pase. Los cristales NO se acumulan en un bote ni se roban al rival.
      Victoria: +CRYSTAL_ELO_WIN (20) Elo y +CRYSTAL_WIN_KUR_REWARD (3) $KUR
      (el cristal gastado no se recupera). Derrota: CRYSTAL_ELO_LOSS (-10) Elo
      y pierde el cristal apostado. Empate: Elo sin cambios y el cristal
      también se consume (se jugó la partida). Solo si el reto se CANCELA
      antes de jugarse se devuelve el cristal.
      Sin retador real en CHALLENGE_EXPIRY_SECONDS (60 s) -> BOT, con las
      mismas recompensas/penalizaciones (el bot no apuesta cristales).
   b) SIN CRISTALES (ChallengeKind.FREE) — abierto a TODAS las cuentas.
      FREE_DAILY_RANKED_LIMIT (10) oportunidades al día (UTC). No se apuesta
      nada ni se gana $KUR. Gana +10 Elo, pierde -10 Elo.
      Sin retador real en 60 s -> BOT.
   Si el jugador cambia de variante con un reto Free abierto de la otra,
   ese reto se retira y se reembolsa (cristal u oportunidad del día).

3. MODO COMPETITIVO (ChallengeKind.MONEY) — apuestas reales en $KUSD o $KUR
   - Requiere cuenta desbloqueada (punto 1). Sin cristales y sin límite
     diario: tantas partidas como se quiera.
   - Ambos jugadores dejan la apuesta en escrow; el ganador se lleva el bote
     menos HOUSE_FEE_PCT (10 %). Empate -> se devuelve todo.
   - CERO BOTS: si en 60 s nadie acepta, la búsqueda se RELANZA (otros 60 s,
     el reto sigue abierto y la apuesta sigue en escrow) hasta que un jugador
     real acepte o el creador lo cancele. Nunca juega la máquina.

Cristales: se obtienen en el runner (`grant_crystals`), evolucionan la rareza
de un NFT (`evolve_nft`) y se venden en el minimarket por $KUSD.

Todas las cifras son constantes de este módulo: ajustarlas aquí no requiere
tocar nada más.
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional

from blockchain_connector import derive_placeholder_rarity
from card_schema import NFTRarity

# --------------------------------------------------------------------------- #
# Parámetros de negocio
# --------------------------------------------------------------------------- #
CHALLENGE_EXPIRY_SECONDS = 60           # búsqueda de retador real (Free -> bot; Competitivo -> relanza)

# Acceso: NFT o experiencia
ACCESS_BATTLES_REQUIRED = 300           # combates totales (Free + Competitivo) para desbloquear sin NFT

# Free con cristales (solo NFT)
CRYSTAL_STAKE = 1                       # cristales que gasta cada jugador por partida (se consumen)
CRYSTAL_WIN_KUR_REWARD = 3.0            # $KUR al ganar
CRYSTAL_ELO_WIN, CRYSTAL_ELO_LOSS = 20, -10

# Free sin cristales (solo sin NFT)
FREE_DAILY_RANKED_LIMIT = 10
FREE_RANKED_ELO_WIN, FREE_RANKED_ELO_LOSS = 10, -10

# Competitivo (no fijado por las reglas de negocio: se mantiene el valor previo)
MONEY_ELO_WIN, MONEY_ELO_LOSS = 25, -25

# Comisión de la casa: minimarket y bote de los retos por dinero
HOUSE_FEE_PCT = 0.10

ELO_START = 1000
ELO_FLOOR = 0

# Versión del modelo de datos del libro mayor (Account / Challenge / EconomyServer).
# Súbela cada vez que añadas o renombres atributos/propiedades: el dashboard
# compara este número con el del EconomyServer guardado en st.session_state y,
# si no coincide, lo reconstruye (ver pvp_dashboard.ensure_economy_server).
# Sin esto, una sesión abierta en Streamlit Cloud sigue usando objetos creados
# con la clase ANTIGUA tras un git push -> AttributeError (p. ej. nft_status).
ECONOMY_SCHEMA_VERSION = 5

# Cristales: fuentes del runner y evolución de rareza
CRYSTAL_SOURCES = ("runner_objective", "runner_monster")
CRYSTAL_DEFAULT_REWARD = {"runner_objective": 5, "runner_monster": 1}
EVOLUTION_PATH: dict[NFTRarity, tuple[NFTRarity, int]] = {
    NFTRarity.COMMON: (NFTRarity.RARE, 20),
    NFTRarity.RARE: (NFTRarity.EPIC, 50),
    NFTRarity.EPIC: (NFTRarity.LEGENDARY, 100),
}

BOT = "BOT"
# Cuentas de bot (respaldo automático de Free). Nunca publican retos ni
# aparecen en la lista pública: solo entran en segundo plano vía
# process_expired() cuando un reto Free se queda sin rival real.
BOT_WALLET_PREFIX = "bot:"


def is_bot_wallet(wallet: Optional[str]) -> bool:
    w = (wallet or "").lower()
    return w == BOT.lower() or w.startswith(BOT_WALLET_PREFIX)


# Cuentas simuladas (sim:*) que el prototipo usaba para rellenar la lista
# pública. Las controla la IA del bot, así que a efectos de la lista pública
# SON bots y nunca deben mostrarse como contrincantes elegibles.
SIMULATED_WALLET_PREFIX = "sim:"


def is_simulated_wallet(wallet: Optional[str]) -> bool:
    return (wallet or "").lower().startswith(SIMULATED_WALLET_PREFIX)


def is_listable_opponent(wallet: Optional[str]) -> bool:
    """True solo para jugadores REALES: ni bots (BOT / bot:*) ni simulados (sim:*).
    Es el único filtro que decide quién puede aparecer en la lista pública,
    tanto en Competitivo como en Free (con o sin cristales)."""
    return bool(wallet) and not is_bot_wallet(wallet) and not is_simulated_wallet(wallet)


class Currency(str, Enum):
    KUR = "KUR"
    KUSD = "KUSD"


class ChallengeKind(str, Enum):
    FREE = "free"          # Free sin cristales: solo cuentas sin NFT, 10/día
    CRYSTAL = "crystal"    # Free con cristales: solo cuentas con NFT, apuesta 1 cristal
    MONEY = "money"        # Competitivo: apuesta en $KUR o $KUSD, sin bots


FREE_KINDS = (ChallengeKind.FREE.value, ChallengeKind.CRYSTAL.value)


class ChallengeStatus(str, Enum):
    OPEN = "open"                  # esperando rival
    MATCHED = "matched"            # aceptado por otro jugador real
    VS_BOT = "vs_bot"              # Free: expiró y se emparejó con un bot
    CANCELLED = "cancelled"        # cancelado por su creador (reembolsado)
    EXPIRED = "expired"            # (histórico) ya no se usa: el competitivo se relanza
    SETTLED = "settled"            # partida terminada y liquidada


class EconomyError(ValueError):
    """Operación no permitida por las reglas de negocio."""


def _money(x: float) -> float:
    return round(float(x) + 1e-12, 2)


# --------------------------------------------------------------------------- #
# Modelos
# --------------------------------------------------------------------------- #
@dataclass
class Account:
    wallet: str
    crystals: int = 0
    balances: dict = field(default_factory=lambda: {Currency.KUR.value: 0.0, Currency.KUSD.value: 0.0})
    elo: int = ELO_START
    nft_token_ids: list = field(default_factory=list)
    free_ranked_day: str = ""          # día UTC (YYYY-MM-DD) del contador diario
    free_ranked_used: int = 0
    battles_total: int = 0             # combates jugados en total (Free + Competitivo)
    free_variant: str = ""             # "crystal" | "free" elegido por el jugador; "" = por defecto

    @property
    def has_nft(self) -> bool:
        return bool(self.nft_token_ids)

    @property
    def veteran(self) -> bool:
        """Ha acumulado ACCESS_BATTLES_REQUIRED (300) batallas totales."""
        return self.battles_total >= ACCESS_BATTLES_REQUIRED

    @property
    def nft_status(self) -> bool:
        """Estatus equivalente a tener NFT: NFT real o 300+ batallas. Decide
        la variante Free (con cristales) y el acceso completo."""
        return self.has_nft or self.veteran

    @property
    def access_unlocked(self) -> bool:
        """Depósitos, retiradas, Competitivo y venta en el mercado."""
        return self.nft_status

    # Alias de compatibilidad con el nombre anterior.
    money_unlocked = access_unlocked

    @property
    def free_kind(self) -> ChallengeKind:
        """Variante de Free que juega la cuenta: la que ELIGIÓ el jugador
        (con o sin cristales, tenga o no NFT). Sin elección: con estatus NFT
        -> con cristales; sin estatus NFT -> sin cristales."""
        if self.free_variant in (ChallengeKind.CRYSTAL.value, ChallengeKind.FREE.value):
            return ChallengeKind(self.free_variant)
        return ChallengeKind.CRYSTAL if self.nft_status else ChallengeKind.FREE


@dataclass
class Challenge:
    id: int
    kind: str
    creator: str
    created_at: float
    expires_at: float
    status: str = ChallengeStatus.OPEN.value
    currency: Optional[str] = None
    stake: float = 0.0
    opponent: Optional[str] = None      # wallet del rival o BOT
    winner: Optional[str] = None        # wallet, BOT o None (empate)
    # Lo que cada participante humano aportó al entrar, para poder reembolsar
    # exactamente: {"wallet": {"crystals": n, "free_slot": bool, "stake": x}}
    entries: dict = field(default_factory=dict)
    bot_crystals: int = 0               # OBSOLETO (siempre 0): el bot ya no apuesta cristales
    relaunches: int = 0                 # Competitivo: veces que se relanzó la búsqueda


@dataclass
class Listing:
    id: int
    seller: str
    item_type: str
    quantity: int
    price_kusd: float
    status: str = "open"                # open | sold | cancelled
    buyer: Optional[str] = None
    fee_kusd: float = 0.0


@dataclass
class _ItemType:
    """Cómo se custodia un tipo de ítem del minimarket. `take` lo retira del
    vendedor al publicar (escrow) y `give` se lo entrega a quien corresponda
    (comprador al vender, vendedor al cancelar)."""
    take: Callable[[Account, int], None]
    give: Callable[[Account, int], None]
    requires_unlock: bool


# --------------------------------------------------------------------------- #
# Servidor de economía
# --------------------------------------------------------------------------- #
class EconomyServer:
    """Libro mayor interno. `clock` es inyectable (tests / reloj del servidor)."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.schema_version = ECONOMY_SCHEMA_VERSION
        self.accounts: dict[str, Account] = {}
        self.challenges: dict[int, Challenge] = {}
        self.listings: dict[int, Listing] = {}
        self.house: dict[str, float] = {Currency.KUR.value: 0.0, Currency.KUSD.value: 0.0}
        self.rewards_paid_kur = 0.0          # $KUR emitidos como premio de Free con cristales
        self.crystals_consumed = 0           # cristales gastados en partidas Free con cristales
        self.nft_rarity: dict[int, str] = {}  # rarezas evolucionadas (override del placeholder)
        self.journal: list[dict] = []        # auditoría de cada movimiento
        self._ids = itertools.count(1)
        self._item_types: dict[str, _ItemType] = {}
        self.register_item_type(
            "crystals",
            take=self._take_crystals,
            give=lambda acc, n: setattr(acc, "crystals", acc.crystals + n),
            requires_unlock=True,  # toda compraventa del market exige acceso completo
        )

    # ------------------------------------------------------------------ util
    def _now(self) -> float:
        return float(self.clock())

    def _day(self) -> str:
        return datetime.fromtimestamp(self._now(), timezone.utc).strftime("%Y-%m-%d")

    def _log(self, event: str, **data) -> None:
        self.journal.append({"t": self._now(), "event": event, **data})

    def account(self, wallet: str) -> Account:
        key = wallet.lower()
        if key not in self.accounts:
            self.accounts[key] = Account(wallet=key)
        return self.accounts[key]

    def _balance_move(self, acc: Account, currency: Currency, delta: float, reason: str) -> None:
        cur = Currency(currency).value
        new = _money(acc.balances.get(cur, 0.0) + delta)
        if new < 0:
            raise EconomyError(f"Saldo insuficiente de ${cur}: tienes {acc.balances.get(cur, 0.0):.2f}.")
        acc.balances[cur] = new
        self._log("balance", wallet=acc.wallet, currency=cur, delta=_money(delta), reason=reason)

    # ------------------------------------------------------------ acceso
    def _locked_msg(self, acc: Account, what: str) -> str:
        return (
            f"{what} bloqueado: necesitas un NFT o haber jugado {ACCESS_BATTLES_REQUIRED} combates "
            f"(llevas {acc.battles_total}; te faltan {max(0, ACCESS_BATTLES_REQUIRED - acc.battles_total)})."
        )

    def _require_access(self, acc: Account, what: str) -> None:
        if not acc.access_unlocked:
            raise EconomyError(self._locked_msg(acc, what))

    def access_status(self, wallet: str) -> dict:
        """Resumen para la interfaz: qué puede hacer la cuenta ahora mismo."""
        acc = self.account(wallet)
        ok_free, why_free = self.can_play_free(wallet)
        return {
            "has_nft": acc.has_nft,
            "nft_status": acc.nft_status,
            "veteran": acc.veteran,
            "elo": acc.elo,
            "battles_total": acc.battles_total,
            "battles_required": ACCESS_BATTLES_REQUIRED,
            "unlocked": acc.access_unlocked,
            "unlocked_by": "nft" if acc.has_nft else ("battles" if acc.access_unlocked else None),
            "free_kind": acc.free_kind.value,
            "free_remaining": self.free_ranked_remaining(wallet),
            "can_play_free": ok_free,
            "free_block_reason": why_free,
            "can_play_competitive": acc.access_unlocked,
            "can_trade_market": acc.access_unlocked,
            "crystals": acc.crystals,
        }

    def can_play_free(self, wallet: str) -> tuple[bool, str]:
        """¿Puede lanzar/aceptar un reto Free de la variante que ha elegido?"""
        acc = self.account(wallet)
        if acc.free_kind is ChallengeKind.CRYSTAL:
            if acc.crystals < CRYSTAL_STAKE:
                return False, (f"Free con cristales: necesitas al menos {CRYSTAL_STAKE} 💎 "
                               "(consíguelos en misiones o en el runner) o cambia a «Sin cristales».")
            return True, ""
        if self._free_used_today(acc) >= FREE_DAILY_RANKED_LIMIT:
            return False, f"Has agotado tus {FREE_DAILY_RANKED_LIMIT} partidas Free gratuitas de hoy."
        return True, ""

    # ------------------------------------------------- saldo y NFTs (servidor)
    def deposit(self, wallet: str, currency: Currency, amount: float) -> None:
        """Depósito del USUARIO: requiere NFT o 300 combates."""
        acc = self.account(wallet)
        self._require_access(acc, "Depósito")
        if amount <= 0:
            raise EconomyError("El importe debe ser mayor que 0.")
        self._balance_move(acc, currency, amount, "deposit")

    def withdraw(self, wallet: str, currency: Currency, amount: float) -> None:
        """Retirada del USUARIO: requiere NFT o 300 combates."""
        acc = self.account(wallet)
        self._require_access(acc, "Retiro")
        if amount <= 0:
            raise EconomyError("El importe debe ser mayor que 0.")
        self._balance_move(acc, currency, -amount, "withdraw")

    def sync_balance(self, wallet: str, currency: Currency, amount: float) -> None:
        """Ajuste interno de servidor (NO es un depósito del usuario): vuelca
        el saldo real de la wallet/custodia en el libro mayor — indexador,
        soporte, cuentas de prueba. No pasa por el control de acceso."""
        if amount < 0:
            raise EconomyError("El saldo no puede ser negativo.")
        acc = self.account(wallet)
        cur = Currency(currency).value
        delta = _money(amount - acc.balances.get(cur, 0.0))
        if delta:
            acc.balances[cur] = _money(amount)
            self._log("balance", wallet=acc.wallet, currency=cur, delta=delta, reason="sync")

    def set_nfts(self, wallet: str, token_ids: list[int]) -> Optional[Challenge]:
        """Sincroniza los NFT que posee (o tiene equipados) la cuenta.

        Si el cambio altera su variante Free (con NFT -> con cristales; sin
        NFT -> sin cristales) y tenía abierto un reto Free de la variante
        anterior, ese reto ya no es válido: se retira y se reembolsa (cristal
        u oportunidad del día). Devuelve el reto retirado, o None."""
        acc = self.account(wallet)
        acc.nft_token_ids = sorted(set(int(t) for t in token_ids))
        return self._retire_mismatched_free_challenge(acc)

    def _retire_mismatched_free_challenge(self, acc: Account) -> Optional[Challenge]:
        ch = self.open_challenge_of(acc.wallet)
        if ch is None or ch.kind == ChallengeKind.MONEY.value or ch.kind == acc.free_kind.value:
            return None
        self._refund_entry(ch, ch.creator)
        ch.status = ChallengeStatus.CANCELLED.value
        self._log("challenge_cancel", id=ch.id, reason="free_variant_changed")
        return ch

    def set_free_variant(self, wallet: str, kind: ChallengeKind) -> Optional[Challenge]:
        """El jugador elige jugar Free CON (CRYSTAL) o SIN (FREE) cristales,
        tenga o no NFT. Si tenía un reto Free abierto de la otra variante, se
        retira y se reembolsa. Devuelve el reto retirado, o None."""
        kind = ChallengeKind(kind)
        if kind is ChallengeKind.MONEY:
            raise EconomyError("La variante Free solo puede ser con o sin cristales.")
        acc = self.account(wallet)
        if acc.free_variant != kind.value:
            acc.free_variant = kind.value
            self._log("free_variant", wallet=acc.wallet, variant=kind.value)
        return self._retire_mismatched_free_challenge(acc)

    @staticmethod
    def _creator_fits_variant(ch: Challenge, creator: Account) -> bool:
        """El creador sigue jugando la variante de su reto Free (si cambió de
        variante, su reto ya se retiró; esto es solo una barrera extra)."""
        if ch.kind in (ChallengeKind.CRYSTAL.value, ChallengeKind.FREE.value):
            return creator.free_kind.value == ch.kind
        return True

    def set_battles(self, wallet: str, n: int) -> Optional[Challenge]:
        """Fija el histórico de combates (migración / soporte / pruebas)."""
        if n < 0:
            raise EconomyError("Los combates no pueden ser negativos.")
        acc = self.account(wallet)
        acc.battles_total = int(n)
        self._log("battles", wallet=acc.wallet, total=acc.battles_total, reason="set")
        return self._retire_mismatched_free_challenge(acc)

    # ------------------------------------------------------------- cristales
    def grant_crystals(self, wallet: str, source: str, amount: Optional[int] = None) -> int:
        """Cristales ganados en el runner (objetivo cumplido / monstruo derrotado)."""
        if source not in CRYSTAL_SOURCES:
            raise EconomyError(f"Fuente de cristales no válida: {source!r} (válidas: {', '.join(CRYSTAL_SOURCES)}).")
        n = CRYSTAL_DEFAULT_REWARD[source] if amount is None else int(amount)
        if n <= 0:
            raise EconomyError("La cantidad de cristales debe ser mayor que 0.")
        acc = self.account(wallet)
        acc.crystals += n
        self._log("crystals", wallet=acc.wallet, delta=n, reason=source)
        return acc.crystals

    def _take_crystals(self, acc: Account, n: int) -> None:
        if acc.crystals < n:
            raise EconomyError(f"Cristales insuficientes: tienes {acc.crystals}, necesitas {n}.")
        acc.crystals -= n
        self._log("crystals", wallet=acc.wallet, delta=-n, reason="spend")

    def set_crystals(self, wallet: str, amount: int) -> None:
        """Fija el saldo de cristales (panel de pruebas / soporte)."""
        if amount < 0:
            raise EconomyError("Los cristales no pueden ser negativos.")
        acc = self.account(wallet)
        self._log("crystals", wallet=acc.wallet, delta=int(amount) - acc.crystals, reason="set")
        acc.crystals = int(amount)

    def rarity_of(self, token_id: int) -> NFTRarity:
        if token_id in self.nft_rarity:
            return NFTRarity(self.nft_rarity[token_id])
        return derive_placeholder_rarity(token_id)

    def evolve_nft(self, wallet: str, token_id: int) -> NFTRarity:
        acc = self.account(wallet)
        if token_id not in acc.nft_token_ids:
            raise EconomyError(f"El NFT #{token_id} no es tuyo.")
        current = self.rarity_of(token_id)
        if current not in EVOLUTION_PATH:
            raise EconomyError(f"El NFT #{token_id} ya tiene la rareza máxima ({current.value}).")
        nxt, cost = EVOLUTION_PATH[current]
        self._take_crystals(acc, cost)
        self.nft_rarity[token_id] = nxt.value
        self._log("evolve", wallet=acc.wallet, token_id=token_id, frm=current.value, to=nxt.value, cost=cost)
        return nxt

    # ------------------------------------------------------- contadores
    def _free_used_today(self, acc: Account) -> int:
        return acc.free_ranked_used if acc.free_ranked_day == self._day() else 0

    def free_ranked_remaining(self, wallet: str) -> Optional[int]:
        """Partidas Free sin cristales que le quedan hoy. None si la cuenta
        juega ahora Free CON cristales (esa variante no usa el cupo diario)."""
        acc = self.account(wallet)
        if acc.free_kind is ChallengeKind.CRYSTAL:
            return None
        return max(0, FREE_DAILY_RANKED_LIMIT - self._free_used_today(acc))

    def _count_battle(self, acc: Account) -> None:
        before = acc.access_unlocked
        acc.battles_total += 1
        if not before and acc.access_unlocked:
            self._log("access_unlocked", wallet=acc.wallet, battles=acc.battles_total)
            self._retire_mismatched_free_challenge(acc)  # pasa a Free con cristales

    # ---------------------------------------------------------------- retos
    def _pay_entry(self, acc: Account, ch: Challenge) -> None:
        """Cobra la entrada de un participante humano (creador o quien acepta)
        y comprueba que la variante del reto es la que le corresponde."""
        entry = {"crystals": 0, "free_slot": False, "stake": 0.0, "mode": ch.kind}
        kind = ChallengeKind(ch.kind)
        if kind is ChallengeKind.CRYSTAL:
            # Abierto a TODAS las cuentas (con o sin NFT / 300 batallas):
            # solo hace falta tener el cristal.
            if acc.crystals < CRYSTAL_STAKE:
                raise EconomyError(
                    f"Necesitas al menos {CRYSTAL_STAKE} 💎 para jugar Free con cristales "
                    f"(tienes {acc.crystals}). Consíguelos en el runner."
                )
            self._take_crystals(acc, CRYSTAL_STAKE)
            entry["crystals"] = CRYSTAL_STAKE
        elif kind is ChallengeKind.FREE:
            if self._free_used_today(acc) >= FREE_DAILY_RANKED_LIMIT:
                raise EconomyError(
                    f"Has agotado tus {FREE_DAILY_RANKED_LIMIT} partidas Free gratuitas de hoy. "
                    "Vuelve mañana (se reinician a las 00:00 UTC)."
                )
            if acc.free_ranked_day != self._day():
                acc.free_ranked_day, acc.free_ranked_used = self._day(), 0
            acc.free_ranked_used += 1
            entry["free_slot"] = True
        else:  # MONEY
            self._require_access(acc, "Modo Competitivo")
            self._balance_move(acc, Currency(ch.currency), -ch.stake, f"escrow reto #{ch.id}")
            entry["stake"] = ch.stake
        ch.entries[acc.wallet] = entry

    def _refund_entry(self, ch: Challenge, wallet: str) -> None:
        entry = ch.entries.get(wallet)
        if not entry:
            return
        acc = self.account(wallet)
        if entry["crystals"]:
            acc.crystals += entry["crystals"]
            self._log("crystals", wallet=wallet, delta=entry["crystals"], reason=f"reembolso reto #{ch.id}")
        if entry["free_slot"] and acc.free_ranked_day == datetime.fromtimestamp(
            ch.created_at, timezone.utc
        ).strftime("%Y-%m-%d"):
            acc.free_ranked_used = max(0, acc.free_ranked_used - 1)
        if entry["stake"]:
            self._balance_move(acc, Currency(ch.currency), entry["stake"], f"reembolso reto #{ch.id}")
        entry.update(crystals=0, free_slot=False, stake=0.0)

    def visible_challenges(self, viewer: Optional[str] = None,
                           kind: Optional[ChallengeKind] = None) -> list[Challenge]:
        """Lista PÚBLICA de retos abiertos que `viewer` puede aceptar.

        - Competitivo: kind=ChallengeKind.MONEY.
        - Free: el viewer SOLO ve la variante que ELIGIÓ (Account.free_kind),
          se pida el `kind` que se pida, para que la UI no pueda mezclarlas:
            * con cristales -> solo humanos que apuestan cristales (CRYSTAL);
            * sin cristales -> solo humanos sin cristales (FREE).
          Da igual que el viewer o el rival tengan NFT o no.
        - Nunca incluye bots (BOT / bot:*) ni cuentas simuladas (sim:*) — ver
          is_listable_opponent —; el bot solo actúa en segundo plano como
          respaldo de Free (process_expired) ni retos cuyo creador ya no
          cumple su variante.
        - Excluye los retos del propio `viewer`."""
        self.process_expired()
        v = viewer.lower() if viewer else None
        requested = ChallengeKind(kind) if kind is not None else None
        if requested is ChallengeKind.MONEY:
            kind = ChallengeKind.MONEY
        elif v:
            kind = self.account(v).free_kind
        else:
            kind = requested or ChallengeKind.CRYSTAL
        return sorted(
            (c for c in self.challenges.values()
             if c.status == ChallengeStatus.OPEN.value and c.kind == kind.value and c.creator != v
             and is_listable_opponent(c.creator)
             and self._creator_fits_variant(c, self.account(c.creator))),
            key=lambda c: c.expires_at,
        )

    def open_challenge_of(self, wallet: str) -> Optional[Challenge]:
        w = wallet.lower()
        return next((c for c in self.challenges.values()
                     if c.creator == w and c.status == ChallengeStatus.OPEN.value), None)

    def create_challenge(self, wallet: str, kind: ChallengeKind, stake: float = 0.0,
                         currency: Currency = Currency.KUR) -> Challenge:
        """kind=FREE o CRYSTAL -> modo Free (la variante es la que eligió el
        jugador, ver set_free_variant); kind=MONEY -> Competitivo con `stake` en `currency`."""
        self.process_expired()
        kind = ChallengeKind(kind)
        acc = self.account(wallet)
        if is_bot_wallet(acc.wallet):
            raise EconomyError("Los bots no publican retos: solo actúan como respaldo automático.")
        if self.open_challenge_of(acc.wallet):
            raise EconomyError("Ya tienes un reto abierto: espera a que se acepte, expire o cancélalo.")
        if kind is ChallengeKind.MONEY:
            self._require_access(acc, "Modo Competitivo")
            if stake <= 0:
                raise EconomyError("La apuesta de un reto competitivo debe ser mayor que 0.")
            currency = Currency(currency)
        else:
            kind = acc.free_kind
            stake, currency = 0.0, None
        now = self._now()
        ch = Challenge(
            id=next(self._ids), kind=kind.value, creator=acc.wallet, created_at=now,
            expires_at=now + CHALLENGE_EXPIRY_SECONDS, currency=currency.value if currency else None,
            stake=_money(stake),
        )
        self._pay_entry(acc, ch)  # si falla, el reto no llega a existir
        self.challenges[ch.id] = ch
        self._log("challenge_open", id=ch.id, wallet=acc.wallet, kind=ch.kind, stake=ch.stake, currency=ch.currency)
        return ch

    def cancel_challenge(self, wallet: str, challenge_id: int) -> Challenge:
        self.process_expired()
        ch = self._challenge(challenge_id)
        if ch.creator != wallet.lower():
            raise EconomyError("Solo quien lanzó el reto puede cancelarlo.")
        if ch.status != ChallengeStatus.OPEN.value:
            raise EconomyError(f"El reto #{ch.id} ya no está abierto ({ch.status}).")
        self._refund_entry(ch, ch.creator)
        ch.status = ChallengeStatus.CANCELLED.value
        self._log("challenge_cancel", id=ch.id)
        return ch

    def accept_challenge(self, wallet: str, challenge_id: int) -> Challenge:
        """Un jugador REAL acepta. Free: debe haber elegido esa misma variante
        (con cristales y tener 1 💎, o sin cristales y tener cupo del día);
        Competitivo: cuenta desbloqueada."""
        self.process_expired()
        ch = self._challenge(challenge_id)
        acc = self.account(wallet)
        if ch.status != ChallengeStatus.OPEN.value:
            raise EconomyError(f"El reto #{ch.id} ya no está disponible ({ch.status}).")
        if ch.creator == acc.wallet:
            raise EconomyError("No puedes aceptar tu propio reto.")
        if is_bot_wallet(acc.wallet):
            raise EconomyError("Los bots no aceptan retos de la lista pública.")
        if ch.kind != ChallengeKind.MONEY.value and ch.kind != acc.free_kind.value:
            raise EconomyError("Ese reto es de la otra variante Free (con/sin cristales): "
                               "cambia tu selección para aceptarlo.")
        self._pay_entry(acc, ch)
        ch.status, ch.opponent = ChallengeStatus.MATCHED.value, acc.wallet
        self._log("challenge_matched", id=ch.id, opponent=acc.wallet)
        return ch

    def process_expired(self) -> list[Challenge]:
        """Temporizador de 60 s. Free (con o sin cristales) -> BOT.
        Competitivo -> se RELANZA la búsqueda otros 60 s (nunca bot)."""
        changed = []
        now = self._now()
        for ch in self.challenges.values():
            if ch.status != ChallengeStatus.OPEN.value or now < ch.expires_at:
                continue
            if ch.kind == ChallengeKind.MONEY.value:
                # Relanza tantas ventanas como hayan pasado, alineadas al reloj.
                missed = int((now - ch.expires_at) // CHALLENGE_EXPIRY_SECONDS) + 1
                ch.expires_at += missed * CHALLENGE_EXPIRY_SECONDS
                ch.relaunches += missed
                self._log("challenge_relaunch", id=ch.id, relaunches=ch.relaunches)
            else:
                ch.status, ch.opponent = ChallengeStatus.VS_BOT.value, BOT
                self._log("challenge_vs_bot", id=ch.id)
            changed.append(ch)
        return changed

    def report_result(self, challenge_id: int, winner: Optional[str]) -> Challenge:
        """Liquida la partida. `winner`: wallet ganadora, BOT o None (empate)."""
        ch = self._challenge(challenge_id)
        if ch.status not in (ChallengeStatus.MATCHED.value, ChallengeStatus.VS_BOT.value):
            raise EconomyError(f"El reto #{ch.id} no tiene partida en curso ({ch.status}).")
        players = [ch.creator, ch.opponent]
        win = winner.lower() if winner and winner != BOT else winner
        if win is not None and win not in players:
            raise EconomyError("El ganador debe ser uno de los dos participantes.")
        humans = [p for p in players if p != BOT]
        kind = ChallengeKind(ch.kind)

        for w in humans:
            self._count_battle(self.account(w))

        if kind is ChallengeKind.MONEY:
            cur = Currency(ch.currency)
            if win is None:  # empate: cada uno recupera su apuesta, sin comisión
                for w in humans:
                    self._refund_entry(ch, w)
            else:
                pot = _money(sum(e["stake"] for e in ch.entries.values()))
                fee = _money(pot * HOUSE_FEE_PCT)
                self.house[cur.value] = _money(self.house[cur.value] + fee)
                self._balance_move(self.account(win), cur, pot - fee, f"premio reto #{ch.id}")
                self._log("house_fee", currency=cur.value, amount=fee, reason=f"reto #{ch.id}")
                for e in ch.entries.values():
                    e["stake"] = 0.0
            elo_win, elo_loss = MONEY_ELO_WIN, MONEY_ELO_LOSS
        elif kind is ChallengeKind.CRYSTAL:
            # Se juega la partida -> el cristal de cada humano se CONSUME
            # (gane, pierda o empate). No hay bote ni se transfieren cristales.
            for w, e in ch.entries.items():
                if e["crystals"]:
                    self.crystals_consumed += e["crystals"]
                    self._log("crystals_consumed", wallet=w, amount=e["crystals"], reason=f"reto #{ch.id}")
                    e["crystals"] = 0
            if win is not None and win != BOT:
                acc = self.account(win)
                self.rewards_paid_kur = _money(self.rewards_paid_kur + CRYSTAL_WIN_KUR_REWARD)
                self._balance_move(acc, Currency.KUR, CRYSTAL_WIN_KUR_REWARD, f"premio Free con cristales reto #{ch.id}")
            elo_win, elo_loss = CRYSTAL_ELO_WIN, CRYSTAL_ELO_LOSS
        else:  # FREE sin cristales: solo Elo
            elo_win, elo_loss = FREE_RANKED_ELO_WIN, FREE_RANKED_ELO_LOSS

        if win is not None:
            for w in humans:
                acc = self.account(w)
                acc.elo = max(ELO_FLOOR, acc.elo + (elo_win if w == win else elo_loss))

        ch.winner = win
        ch.status = ChallengeStatus.SETTLED.value
        self._log("challenge_settled", id=ch.id, winner=win)
        return ch

    def _challenge(self, challenge_id: int) -> Challenge:
        if challenge_id not in self.challenges:
            raise EconomyError(f"Reto #{challenge_id} no encontrado.")
        return self.challenges[challenge_id]

    # ------------------------------------------------------------ minimarket
    def register_item_type(self, name: str, take: Callable[[Account, int], None],
                           give: Callable[[Account, int], None], requires_unlock: bool = True) -> None:
        """Punto de enganche para nuevos ítems del minimarket (Milton): cómo se
        retiran del vendedor (escrow) y cómo se entregan. La compraventa
        (publicar y comprar) exige SIEMPRE acceso completo (NFT o 300
        combates); `requires_unlock` se mantiene por compatibilidad."""
        self._item_types[name] = _ItemType(take=take, give=give, requires_unlock=requires_unlock)

    def list_item(self, wallet: str, item_type: str, quantity: int, price_kusd: float) -> Listing:
        if item_type not in self._item_types:
            raise EconomyError(f"Tipo de ítem desconocido: {item_type!r}.")
        if quantity <= 0 or price_kusd <= 0:
            raise EconomyError("Cantidad y precio deben ser mayores que 0.")
        acc = self.account(wallet)
        it = self._item_types[item_type]
        self._require_access(acc, "Venta en el market")
        it.take(acc, quantity)
        lst = Listing(id=next(self._ids), seller=acc.wallet, item_type=item_type,
                      quantity=int(quantity), price_kusd=_money(price_kusd))
        self.listings[lst.id] = lst
        self._log("market_list", id=lst.id, wallet=acc.wallet, item=item_type, qty=quantity, price=lst.price_kusd)
        return lst

    def cancel_listing(self, wallet: str, listing_id: int) -> Listing:
        lst = self._listing(listing_id)
        if lst.seller != wallet.lower():
            raise EconomyError("Solo el vendedor puede retirar su anuncio.")
        if lst.status != "open":
            raise EconomyError(f"El anuncio #{lst.id} ya no está activo ({lst.status}).")
        self._item_types[lst.item_type].give(self.account(lst.seller), lst.quantity)
        lst.status = "cancelled"
        self._log("market_cancel", id=lst.id)
        return lst

    def buy_listing(self, wallet: str, listing_id: int) -> Listing:
        lst = self._listing(listing_id)
        buyer = self.account(wallet)
        if lst.status != "open":
            raise EconomyError(f"El anuncio #{lst.id} ya no está activo ({lst.status}).")
        if lst.seller == buyer.wallet:
            raise EconomyError("No puedes comprar tu propio anuncio.")
        self._require_access(buyer, "Compra en el market")
        fee = _money(lst.price_kusd * HOUSE_FEE_PCT)
        self._balance_move(buyer, Currency.KUSD, -lst.price_kusd, f"compra anuncio #{lst.id}")
        self._balance_move(self.account(lst.seller), Currency.KUSD, lst.price_kusd - fee, f"venta anuncio #{lst.id}")
        self.house[Currency.KUSD.value] = _money(self.house[Currency.KUSD.value] + fee)
        self._item_types[lst.item_type].give(buyer, lst.quantity)
        lst.status, lst.buyer, lst.fee_kusd = "sold", buyer.wallet, fee
        self._log("market_sale", id=lst.id, buyer=buyer.wallet, fee=fee)
        return lst

    def open_listings(self, item_type: Optional[str] = None) -> list[Listing]:
        return [l for l in self.listings.values()
                if l.status == "open" and (item_type is None or l.item_type == item_type)]

    def _listing(self, listing_id: int) -> Listing:
        if listing_id not in self.listings:
            raise EconomyError(f"Anuncio #{listing_id} no encontrado.")
        return self.listings[listing_id]

    # ------------------------------------------------------------ persistencia
    def to_json(self) -> str:
        return json.dumps({
            "accounts": {k: asdict(v) for k, v in self.accounts.items()},
            "challenges": {k: asdict(v) for k, v in self.challenges.items()},
            "listings": {k: asdict(v) for k, v in self.listings.items()},
            "house": self.house, "rewards_paid_kur": self.rewards_paid_kur,
            "crystals_consumed": self.crystals_consumed,
            "nft_rarity": self.nft_rarity, "journal": self.journal,
        })

    @classmethod
    def from_json(cls, raw: str, clock: Callable[[], float] = time.time) -> "EconomyServer":
        d = json.loads(raw)
        s = cls(clock=clock)
        s.accounts = {k: _account_from_dict(v) for k, v in d["accounts"].items()}
        ch_fields = {f.name for f in fields(Challenge)}
        s.challenges = {int(k): Challenge(**{a: b for a, b in v.items() if a in ch_fields})
                        for k, v in d["challenges"].items()}
        s.listings = {int(k): Listing(**v) for k, v in d["listings"].items()}
        s.house, s.rewards_paid_kur = d["house"], d["rewards_paid_kur"]
        s.crystals_consumed = d.get("crystals_consumed", 0)
        s.nft_rarity = {int(k): v for k, v in d["nft_rarity"].items()}
        s.journal = d["journal"]
        s._ids = itertools.count(max([0, *s.challenges, *s.listings]) + 1)
        return s


def _account_from_dict(v: dict) -> Account:
    """Carga una cuenta tolerando el formato anterior (contador mensual +
    `milestone_reached`): el histórico pasa a `battles_total` y quien ya
    tenía el hito conserva el desbloqueo."""
    known = {f.name for f in fields(Account)}
    acc = Account(**{k: val for k, val in v.items() if k in known})
    if "battles_total" not in v:
        acc.battles_total = int(v.get("battles_this_month", 0))
        if v.get("milestone_reached"):
            acc.battles_total = max(acc.battles_total, ACCESS_BATTLES_REQUIRED)
    return acc
