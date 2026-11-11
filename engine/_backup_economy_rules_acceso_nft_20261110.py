"""
Reglas de negocio y economía de Kurama Run — lógica interna de servidor.

SIN contratos inteligentes: todo saldo ($KUR, $KUSD, cristales), Elo,
límite diario, escrow de retos y comisiones de la casa vive en este libro
mayor interno (`EconomyServer`). Es Python puro (sin Streamlit ni web3) para
poder ejecutarlo en el backend real, en tests o embebido en el dashboard.

Reglas implementadas (revisión 06/11/2026, estilo Monster Clash):

CRISTALES (energía del juego, pertenecen al JUGADOR, no al NFT)
- Se obtienen en el modo plataformas (runner): cumpliendo objetivos o
  derrotando monstruos  -> `grant_crystals(wallet, cantidad, fuente)`.
- Con cristales (> 0) los retos del modo free son ILIMITADOS (revisión
  06/11/2026: tenerlos basta, CRYSTAL_COST_PER_CHALLENGE = 0; si algún día
  deben gastarse por reto, basta con subir esa constante).
- Con 0 cristales se aplica estricta y únicamente el límite de
  FREE_DAILY_RANKED_LIMIT (10) retos al día — tenga o no NFT.
- La lista pública de retos solo muestra a los jugadores que retan CON
  cristales (`visible_challenges`). Un reto free que expira
  (CHALLENGE_EXPIRY_SECONDS = 60 s) sin que nadie lo acepte -> BOT.
- Ganar una partida con cristales -> +CRYSTAL_WIN_KUR_REWARD (3 $KUR) y más
  Elo (CRYSTAL_ELO_WIN en vez de FREE_RANKED_ELO_WIN).
- Evolucionan la rareza de un NFT (Común->Raro 20, Raro->Épico 50,
  Épico->Legendario 100 cristales) -> `evolve_nft`.
- Se venden en el minimarket a cambio de $KUSD; la casa se queda
  HOUSE_FEE_PCT (10 %) -> `list_item(..., "crystals", ...)` / `buy_listing`.

USUARIOS SIN CRISTALES
- Retos free de ranking: máximo FREE_DAILY_RANKED_LIMIT (10) al día (UTC).
- Usuarios free (sin NFT): al completar MONTHLY_BATTLES_MILESTONE (300) batallas de ranking en un
  mismo mes se habilitan para siempre: retiro de saldo, venta en el mercado y
  retos por dinero.

RETOS POR DINERO ($KUR / $KUSD)
- Solo para cuentas con NFT o con el hito de 300 batallas.
- Ambos jugadores dejan la apuesta en escrow; el ganador se lleva el bote
  menos HOUSE_FEE_PCT (10 %) para la casa. Empate -> se devuelve todo.
- Si expira (60 s) sin ser aceptado -> se CANCELA y se devuelve la apuesta,
  nunca se juega contra un bot.

Todas las cifras son constantes de este módulo: ajustarlas aquí no requiere
tocar nada más.
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional

from blockchain_connector import derive_placeholder_rarity
from card_schema import NFTRarity

# --------------------------------------------------------------------------- #
# Parámetros de negocio
# --------------------------------------------------------------------------- #
CHALLENGE_EXPIRY_SECONDS = 60

# Cristales
CRYSTAL_COST_PER_CHALLENGE = 0          # tener > 0 cristales = retos ilimitados (no se gastan)
CRYSTAL_WIN_KUR_REWARD = 3.0            # $KUR al ganar una partida con cristales
CRYSTAL_SOURCES = ("runner_objective", "runner_monster")
# Cantidad por defecto si el runner no indica una (puede pasar la suya propia).
CRYSTAL_DEFAULT_REWARD = {"runner_objective": 5, "runner_monster": 1}
# Evolución de rareza: rareza actual -> (rareza siguiente, coste en cristales)
EVOLUTION_PATH: dict[NFTRarity, tuple[NFTRarity, int]] = {
    NFTRarity.COMMON: (NFTRarity.RARE, 20),
    NFTRarity.RARE: (NFTRarity.EPIC, 50),
    NFTRarity.EPIC: (NFTRarity.LEGENDARY, 100),
}

# Elo
ELO_START = 1000
ELO_FLOOR = 0
FREE_RANKED_ELO_WIN, FREE_RANKED_ELO_LOSS = 10, -10
CRYSTAL_ELO_WIN, CRYSTAL_ELO_LOSS = 20, -10      # "más Elo" que un reto free
MONEY_ELO_WIN, MONEY_ELO_LOSS = 25, -25

# Usuarios free
FREE_DAILY_RANKED_LIMIT = 10
MONTHLY_BATTLES_MILESTONE = 300

# Comisión de la casa: minimarket y bote de los retos por dinero
HOUSE_FEE_PCT = 0.10

BOT = "BOT"


class Currency(str, Enum):
    KUR = "KUR"
    KUSD = "KUSD"


class ChallengeKind(str, Enum):
    FREE = "free"          # modo free sin cristales (límite diario)
    CRYSTAL = "crystal"    # modo free con cristales (ilimitado, visible en la lista)
    MONEY = "money"        # apuesta en $KUR o $KUSD


class ChallengeStatus(str, Enum):
    OPEN = "open"                  # esperando rival (máx. 60 s)
    MATCHED = "matched"            # aceptado por otro jugador
    VS_BOT = "vs_bot"              # expiró y se emparejó con un bot (free/cristal)
    CANCELLED = "cancelled"        # cancelado por su creador
    EXPIRED = "expired"            # reto por dinero expirado: cancelado y reembolsado
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
    battles_month: str = ""            # mes UTC (YYYY-MM) del contador mensual
    battles_this_month: int = 0
    milestone_reached: bool = False    # 300 batallas en un mes: desbloqueo permanente

    @property
    def has_nft(self) -> bool:
        return bool(self.nft_token_ids)

    @property
    def money_unlocked(self) -> bool:
        """Retos por dinero, retiro y venta en el mercado."""
        return self.has_nft or self.milestone_reached


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
        self.accounts: dict[str, Account] = {}
        self.challenges: dict[int, Challenge] = {}
        self.listings: dict[int, Listing] = {}
        self.house: dict[str, float] = {Currency.KUR.value: 0.0, Currency.KUSD.value: 0.0}
        self.rewards_paid_kur = 0.0          # $KUR emitidos como premio de cristales
        self.nft_rarity: dict[int, str] = {}  # rarezas evolucionadas (override del placeholder)
        self.journal: list[dict] = []        # auditoría de cada movimiento
        self._ids = itertools.count(1)
        self._item_types: dict[str, _ItemType] = {}
        self.register_item_type(
            "crystals",
            take=self._take_crystals,
            give=lambda acc, n: setattr(acc, "crystals", acc.crystals + n),
            requires_unlock=False,  # vender cristales es su uso previsto
        )

    # ------------------------------------------------------------------ util
    def _now(self) -> float:
        return float(self.clock())

    def _day(self) -> str:
        return datetime.fromtimestamp(self._now(), timezone.utc).strftime("%Y-%m-%d")

    def _month(self) -> str:
        return datetime.fromtimestamp(self._now(), timezone.utc).strftime("%Y-%m")

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

    # ------------------------------------------------- saldo y NFTs (servidor)
    def deposit(self, wallet: str, currency: Currency, amount: float) -> None:
        """Abono interno (recarga, puente desde exchange, ajustes de soporte)."""
        if amount <= 0:
            raise EconomyError("El importe debe ser mayor que 0.")
        self._balance_move(self.account(wallet), currency, amount, "deposit")

    def set_nfts(self, wallet: str, token_ids: list[int]) -> None:
        """Sincroniza los NFT que posee la cuenta (indexador/servidor)."""
        acc = self.account(wallet)
        acc.nft_token_ids = sorted(set(int(t) for t in token_ids))

    def withdraw(self, wallet: str, currency: Currency, amount: float) -> None:
        acc = self.account(wallet)
        if not acc.money_unlocked:
            raise EconomyError(
                f"Retiro bloqueado: necesitas un NFT o completar {MONTHLY_BATTLES_MILESTONE} batallas en un mes "
                f"(llevas {self._battles_this_month(acc)} este mes)."
            )
        if amount <= 0:
            raise EconomyError("El importe debe ser mayor que 0.")
        self._balance_move(acc, currency, -amount, "withdraw")

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

    # ------------------------------------------------------- contadores free
    def _free_used_today(self, acc: Account) -> int:
        return acc.free_ranked_used if acc.free_ranked_day == self._day() else 0

    def free_ranked_remaining(self, wallet: str) -> Optional[int]:
        """Retos free que le quedan hoy; None = ilimitado (tiene cristales)."""
        acc = self.account(wallet)
        if acc.crystals > 0:
            return None
        return max(0, FREE_DAILY_RANKED_LIMIT - self._free_used_today(acc))

    def use_daily_slot(self, wallet: str) -> bool:
        """Regla GLOBAL de cristales para cualquier partida (free o
        competitiva): con cristales (> 0) es ilimitada y devuelve False (no
        gasta nada); con 0 consume 1 de las FREE_DAILY_RANKED_LIMIT del día y
        devuelve True, o lanza EconomyError si ya no quedan."""
        acc = self.account(wallet)
        if acc.crystals > 0:
            return False
        if self._free_used_today(acc) >= FREE_DAILY_RANKED_LIMIT:
            raise EconomyError(
                f"Has agotado tus {FREE_DAILY_RANKED_LIMIT} partidas de hoy. "
                "Consigue cristales en el runner para seguir jugando sin límite."
            )
        if acc.free_ranked_day != self._day():
            acc.free_ranked_day, acc.free_ranked_used = self._day(), 0
        acc.free_ranked_used += 1
        self._log("daily_slot", wallet=acc.wallet, used=acc.free_ranked_used)
        return True

    def refund_daily_slot(self, wallet: str) -> None:
        """Devuelve una oportunidad del día (reto cancelado o expirado sin jugarse)."""
        acc = self.account(wallet)
        if acc.free_ranked_day == self._day() and acc.free_ranked_used > 0:
            acc.free_ranked_used -= 1
            self._log("daily_slot", wallet=acc.wallet, used=acc.free_ranked_used, reason="refund")

    def set_crystals(self, wallet: str, amount: int) -> None:
        """Fija el saldo de cristales (panel de pruebas / soporte)."""
        if amount < 0:
            raise EconomyError("Los cristales no pueden ser negativos.")
        acc = self.account(wallet)
        self._log("crystals", wallet=acc.wallet, delta=int(amount) - acc.crystals, reason="set")
        acc.crystals = int(amount)

    def _battles_this_month(self, acc: Account) -> int:
        return acc.battles_this_month if acc.battles_month == self._month() else 0

    def _count_battle(self, acc: Account) -> None:
        month = self._month()
        if acc.battles_month != month:
            acc.battles_month, acc.battles_this_month = month, 0
        acc.battles_this_month += 1
        if not acc.milestone_reached and acc.battles_this_month >= MONTHLY_BATTLES_MILESTONE:
            acc.milestone_reached = True
            self._log("milestone", wallet=acc.wallet, battles=acc.battles_this_month, month=month)

    # ---------------------------------------------------------------- retos
    def _pay_entry(self, acc: Account, ch: Challenge) -> None:
        """Cobra la entrada de un participante humano (creador o quien acepta)."""
        # mode: "crystal" (jugó con cristales: ilimitado, premio de cristales)
        #       "free"    (sin cristales: gasta 1 de sus 10 oportunidades del día)
        entry = {"crystals": 0, "free_slot": False, "stake": 0.0, "mode": ch.kind}
        kind = ChallengeKind(ch.kind)
        if kind is not ChallengeKind.MONEY:
            # Modo free: decide la situación de CADA jugador (creador o quien
            # acepta), no el tipo del reto.
            if acc.crystals > 0:
                self._take_crystals(acc, CRYSTAL_COST_PER_CHALLENGE)
                entry["crystals"] = CRYSTAL_COST_PER_CHALLENGE
                entry["mode"] = ChallengeKind.CRYSTAL.value
            else:
                if self._free_used_today(acc) >= FREE_DAILY_RANKED_LIMIT:
                    raise EconomyError(
                        f"Has agotado tus {FREE_DAILY_RANKED_LIMIT} retos free de hoy. "
                        "Consigue cristales en el runner para seguir jugando sin límite."
                    )
                if acc.free_ranked_day != self._day():
                    acc.free_ranked_day, acc.free_ranked_used = self._day(), 0
                acc.free_ranked_used += 1
                entry["free_slot"] = True
                entry["mode"] = ChallengeKind.FREE.value
        else:  # MONEY
            if not acc.money_unlocked:
                raise EconomyError(
                    f"Los retos por dinero son solo para cuentas con NFT o con {MONTHLY_BATTLES_MILESTONE} "
                    f"batallas en un mes (llevas {self._battles_this_month(acc)} este mes)."
                )
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

    def visible_challenges(self, viewer: Optional[str] = None) -> list[Challenge]:
        """Lista pública del modo free: SOLO jugadores reales retando con
        cristales (los retos sin cristales no se listan: expiran contra un
        bot). Excluye los retos del propio `viewer`."""
        self.process_expired()
        v = viewer.lower() if viewer else None
        return sorted(
            (c for c in self.challenges.values()
             if c.status == ChallengeStatus.OPEN.value and c.kind == ChallengeKind.CRYSTAL.value
             and c.creator != v),
            key=lambda c: c.expires_at,
        )

    def open_challenge_of(self, wallet: str) -> Optional[Challenge]:
        w = wallet.lower()
        return next((c for c in self.challenges.values()
                     if c.creator == w and c.status == ChallengeStatus.OPEN.value), None)

    def create_challenge(self, wallet: str, kind: ChallengeKind, stake: float = 0.0,
                         currency: Currency = Currency.KUR) -> Challenge:
        self.process_expired()
        kind = ChallengeKind(kind)
        acc = self.account(wallet)
        if self.open_challenge_of(acc.wallet):
            raise EconomyError("Ya tienes un reto abierto: espera a que se acepte, expire o cancélalo.")
        if kind is ChallengeKind.MONEY:
            if stake <= 0:
                raise EconomyError("La apuesta de un reto por dinero debe ser mayor que 0.")
            currency = Currency(currency)
        else:
            if kind is ChallengeKind.CRYSTAL and acc.crystals <= 0:
                raise EconomyError("No tienes cristales: tu reto será free (límite diario).")
            # Modo free: el reto es "con cristales" si quien lo lanza los tiene.
            kind = ChallengeKind.CRYSTAL if acc.crystals > 0 else ChallengeKind.FREE
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
        self.process_expired()
        ch = self._challenge(challenge_id)
        acc = self.account(wallet)
        if ch.status != ChallengeStatus.OPEN.value:
            raise EconomyError(f"El reto #{ch.id} ya no está disponible ({ch.status}).")
        if ch.creator == acc.wallet:
            raise EconomyError("No puedes aceptar tu propio reto.")
        self._pay_entry(acc, ch)
        ch.status, ch.opponent = ChallengeStatus.MATCHED.value, acc.wallet
        self._log("challenge_matched", id=ch.id, opponent=acc.wallet)
        return ch

    def process_expired(self) -> list[Challenge]:
        """Llamar periódicamente (o antes de cada operación, como ya hace este
        servidor): free/cristal -> bot; dinero -> cancelado con reembolso."""
        changed = []
        now = self._now()
        for ch in self.challenges.values():
            if ch.status != ChallengeStatus.OPEN.value or now < ch.expires_at:
                continue
            if ch.kind == ChallengeKind.MONEY.value:
                self._refund_entry(ch, ch.creator)
                ch.status = ChallengeStatus.EXPIRED.value
                self._log("challenge_expired_refund", id=ch.id)
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
            elo = {w: (MONEY_ELO_WIN, MONEY_ELO_LOSS) for w in humans}
        else:
            # Modo free: premio y Elo según cómo jugó CADA uno (con o sin cristales).
            elo = {}
            for w in humans:
                crystal_mode = ch.entries.get(w, {}).get("mode") == ChallengeKind.CRYSTAL.value
                elo[w] = (CRYSTAL_ELO_WIN, CRYSTAL_ELO_LOSS) if crystal_mode else (FREE_RANKED_ELO_WIN, FREE_RANKED_ELO_LOSS)
                if crystal_mode and w == win:
                    self.rewards_paid_kur = _money(self.rewards_paid_kur + CRYSTAL_WIN_KUR_REWARD)
                    self._balance_move(self.account(w), Currency.KUR, CRYSTAL_WIN_KUR_REWARD,
                                       f"premio cristales reto #{ch.id}")

        if win is not None:
            for w in humans:
                acc = self.account(w)
                elo_win, elo_loss = elo[w]
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
        retiran del vendedor (escrow) y cómo se entregan. `requires_unlock`:
        si el vendedor necesita NFT o el hito de 300 batallas."""
        self._item_types[name] = _ItemType(take=take, give=give, requires_unlock=requires_unlock)

    def list_item(self, wallet: str, item_type: str, quantity: int, price_kusd: float) -> Listing:
        if item_type not in self._item_types:
            raise EconomyError(f"Tipo de ítem desconocido: {item_type!r}.")
        if quantity <= 0 or price_kusd <= 0:
            raise EconomyError("Cantidad y precio deben ser mayores que 0.")
        acc = self.account(wallet)
        it = self._item_types[item_type]
        if it.requires_unlock and not acc.money_unlocked:
            raise EconomyError(
                f"Venta bloqueada: necesitas un NFT o completar {MONTHLY_BATTLES_MILESTONE} batallas en un mes."
            )
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
            "nft_rarity": self.nft_rarity, "journal": self.journal,
        })

    @classmethod
    def from_json(cls, raw: str, clock: Callable[[], float] = time.time) -> "EconomyServer":
        d = json.loads(raw)
        s = cls(clock=clock)
        s.accounts = {k: Account(**v) for k, v in d["accounts"].items()}
        s.challenges = {int(k): Challenge(**v) for k, v in d["challenges"].items()}
        s.listings = {int(k): Listing(**v) for k, v in d["listings"].items()}
        s.house, s.rewards_paid_kur = d["house"], d["rewards_paid_kur"]
        s.nft_rarity = {int(k): v for k, v in d["nft_rarity"].items()}
        s.journal = d["journal"]
        s._ids = itertools.count(max([0, *s.challenges, *s.listings]) + 1)
        return s
