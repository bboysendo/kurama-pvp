"""
Motor de combate PvP por turnos síncronos 1v1, puro Python y desacoplado de
Streamlit/Web3/UI (sección 2 de PVP_Rules). Expone 4 funciones de alto nivel:

    start_match(deck_a, deck_b, hp_a, hp_b) -> MatchState
    play_card(match, player, card_id, attack_zones, defense_zones) -> MatchState
    end_turn(match) -> MatchState
    get_match_state(match) -> dict   # snapshot serializable para la UI

REGLAS DEFINITIVAS DE COMBATE (Sendo, 05/11/2026) — sistema de zonas múltiples
----------------------------------------------------------------------------
1. Zonas únicas por carta: ATK y DEF son NÚMERO DE ZONAS (Cabeza, Tronco,
   Piernas). Una carta de ATK N marca hasta N zonas de ataque DISTINTAS y una
   de DEF M hasta M zonas de defensa DISTINTAS — nunca 2 ataques (ni 2
   defensas) de la misma carta en la misma zona. Se pueden dejar zonas sin
   usar si sobran ataques o defensas (play_card lo valida estrictamente).
2. Daño por zona: cada ataque que entra en una zona quita ZONE_HIT_DAMAGE (5).
   Cada defensa del rival en esa zona anula UN ataque (bloqueo 1:1). Cartas
   distintas sí pueden apilar ataques/defensas en la misma zona.
   Combo Tripartito (especiales de ataque, TRIPARTITE_STRIKE): si la carta
   usa TODAS sus zonas de ataque y NINGUNO de sus ataques es bloqueado, suma
   +TRIPARTITE_COMBO_BONUS (3): 3/0 -> 5x3+3 = 18, 2/0 -> 13, 1/0 -> 8 (la
   2/2 especial sigue la misma regla: 13). Cuando en una zona hay más
   ataques que defensas, la defensa para PRIMERO los ataques de especiales
   (y por orden de juego), así un bloqueo en una zona marcada por la
   especial siempre le anula el combo.
3. Curadoras (HEALER) 0/3, 0/2, 1/1: si cubren TODAS sus zonas de defensa
   (3, 2 y 1), el jugador recibe +HEALER_HEAL_AMOUNT (3) de vida (sin pasar
   de su vida máxima). Su defensa bloquea además como cualquier otra.
4. Energía: con 3 de energía se pueden jugar 3 cartas de coste 1 en el mismo
   turno, combinando sus zonas (cada carta respeta su propia regla de zonas
   distintas). Base = coste 1, especiales = coste 2, tope de gasto 3.

Resto de reglas (sin cambios):
- Turno síncrono: las dos direcciones de daño se calculan sobre el mismo
  estado de inicio de turno y la vida se aplica a la vez (posible empate).
- Energía: empieza en STARTING_ENERGY (3); al cerrar el turno se resta lo
  gastado y se recupera +ENERGY_PER_TURN, sin superar nunca MAX_ENERGY (3).
- Mazo y mano: unicidad total — nunca dos cartas iguales ni dos con la
  misma combinación (ATK, DEF, habilidad) (build_deck_from_pool / _draw).
- Mano PERSISTENTE: al cerrar el turno solo van a descarte las cartas
  JUGADAS y se roba lo justo para volver a HAND_SIZE. Quien cierra el turno
  sin jugar ninguna carta rota UNA carta al azar (_rotate_one_card).
- Inactividad: solo cuenta el turno en que se agota el temporizador sin
  jugar, pasar ni interactuar; INACTIVITY_LIMIT seguidos = derrota.

Retirado respecto a la versión anterior (sustituido por las reglas de arriba):
Combo Tripartito acumulado en la partida, Combo Crítico (20), Golpe Limpio
(10+3) y Guardia Vital (+energía). Las habilidades heredadas (poción,
perforante, contraataque, robo de vida, combo simultáneo) no tienen cartas y
este motor no les da efecto.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional, Union

from card_schema import (
    DECK_SIZE,
    ENERGY_PER_TURN,
    HAND_SIZE,
    HEALER_HEAL_AMOUNT,
    INACTIVITY_LIMIT,
    MAX_ENERGY,
    MAX_ENERGY_SPEND_PER_TURN,
    NUM_ZONES,
    STARTING_ENERGY,
    TRIPARTITE_COMBO_BONUS,
    ZONE_HIT_DAMAGE,
    Ability,
    Card,
)


class Zone(str, Enum):
    HEAD = "head"
    TORSO = "torso"
    LEGS = "legs"


ZONE_LABEL = {Zone.HEAD: "Cabeza", Zone.TORSO: "Tronco", Zone.LEGS: "Piernas"}


class MatchStatus(str, Enum):
    ONGOING = "ongoing"
    PLAYER_A_WINS = "player_a_wins"
    PLAYER_B_WINS = "player_b_wins"
    DRAW = "draw"


class EngineError(ValueError):
    """Jugada ilegal (energía insuficiente, carta no está en mano, zona
    repetida, más zonas que ATK/DEF, partida ya terminada...) — siempre con
    un mensaje claro para mostrar en la UI."""


def max_attack_zones(card: Card) -> int:
    """Zonas de ataque que puede marcar la carta (1 por punto de ATK, máx. 3)."""
    return max(0, min(card.atk, NUM_ZONES))


def max_defense_zones(card: Card) -> int:
    """Zonas de defensa que puede marcar la carta (1 por punto de DEF, máx. 3)."""
    return max(0, min(card.defense, NUM_ZONES))


def is_tripartite_card(card: Card) -> bool:
    return card.ability == Ability.TRIPARTITE_STRIKE and max_attack_zones(card) > 0


def is_healer_card(card: Card) -> bool:
    return card.ability == Ability.HEALER and max_defense_zones(card) > 0


@dataclass
class StagedPlay:
    card: Card
    attack_zones: tuple   # zonas DISTINTAS del rival que ataca esta carta (<= ATK)
    defense_zones: tuple  # zonas DISTINTAS propias que defiende esta carta (<= DEF)


@dataclass
class TurnReport:
    """Resumen del último turno resuelto, desde el punto de vista del ATACANTE
    (lo lee la UI para el panel bajo cada luchador)."""
    hits: dict = field(default_factory=dict)       # zona.value -> ataques que entraron
    blocked: dict = field(default_factory=dict)    # zona.value -> ataques bloqueados
    damage_dealt: int = 0
    combos: list = field(default_factory=list)     # nombres de especiales que activaron el +3
    healed: int = 0                                # vida recibida por curadoras propias


@dataclass
class PlayerState:
    name: str
    hp: int
    max_hp: int
    deck: list[Card]
    hand: list[Card] = field(default_factory=list)
    discard: list[Card] = field(default_factory=list)
    energy: int = min(STARTING_ENERGY, MAX_ENERGY)
    staged_plays: list[StagedPlay] = field(default_factory=list)
    last_turn: TurnReport = field(default_factory=TurnReport)
    combos_completed: int = 0  # Combos Tripartitos conseguidos en la partida (informativo)
    battles_played: int = 0  # informativo para el dashboard (no afecta al combate)
    inactive_turns: int = 0  # turnos SEGUIDOS en que se le agotó el tiempo sin actuar (regla de inactividad)
    acted_this_turn: bool = False  # jugó, pasó turno o interactuó este turno (nunca es inactividad)

    @property
    def energy_spent_this_turn(self) -> int:
        return sum(p.card.cost_energy for p in self.staged_plays)


@dataclass
class MatchState:
    player_a: PlayerState
    player_b: PlayerState
    turn_number: int = 1
    status: MatchStatus = MatchStatus.ONGOING
    log: list[str] = field(default_factory=list)
    rng: random.Random = field(default_factory=random.Random)
    end_reason: Optional[str] = None  # "ko" | "inactivity" cuando la partida termina


def build_deck_from_pool(pool: list[Card], size: int = DECK_SIZE, rng: Optional[random.Random] = None) -> list[Card]:
    """Mazo de hasta `size` cartas SIN NINGUNA repetición: ni la misma carta
    dos veces ni dos cartas con la misma combinación ATK/DEF (si el pool
    trajera dos con las mismas stats Y la misma habilidad, solo entra una: una especial sí convive
    con la base de sus mismas stats). Con el catálogo de 16
    cartas únicas y DECK_SIZE = 16, cada mazo es exactamente el set completo
    de combinaciones, barajado."""
    rng = rng or random
    unique: dict[tuple, Card] = {}
    for card in pool:
        unique.setdefault(_uniqueness_key(card), card)
    cards = list(unique.values())
    return rng.sample(cards, min(size, len(cards)))


def _uniqueness_key(card: Card) -> tuple:
    """Identidad de juego de una carta: stats + habilidad (la base 3/0 y la especial 3/0 son distintas)."""
    return card.atk, card.defense, card.ability


def _conflicts_with_hand(card: Card, hand: list[Card]) -> bool:
    """Salvaguarda: una mano nunca lleva dos cartas iguales ni dos con la
    misma combinación ATK/DEF (con el catálogo actual ya no puede pasar,
    pero protege ante mazos de prueba o catálogos futuros)."""
    return any(
        c.id == card.id or _uniqueness_key(c) == _uniqueness_key(card)
        for c in hand
    )


def _draw(player: PlayerState, rng: random.Random, n: int = HAND_SIZE) -> None:
    """Roba hasta `n` cartas, todas distintas entre sí (ver
    _conflicts_with_hand). Si la siguiente carta del mazo choca con la mano,
    se coge la siguiente que no choque; las que choquen se quedan en el mazo
    para turnos futuros. Si en el mazo no queda ninguna válida, se rebaraja
    el descarte dentro del mazo; si aun así no hay ninguna, la mano se queda
    más corta antes que repetir carta."""
    for _ in range(n):
        if not player.deck:
            if not player.discard:
                return  # sin cartas en ningún sitio (mazo muy pequeño en pruebas)
            player.deck, player.discard = player.discard, []
            rng.shuffle(player.deck)
        idx = next(
            (i for i in range(len(player.deck) - 1, -1, -1)
             if not _conflicts_with_hand(player.deck[i], player.hand)),
            None,
        )
        if idx is None and player.discard:
            player.deck.extend(player.discard)
            player.discard = []
            rng.shuffle(player.deck)
            idx = next(
                (i for i in range(len(player.deck) - 1, -1, -1)
                 if not _conflicts_with_hand(player.deck[i], player.hand)),
                None,
            )
        if idx is None:
            return
        player.hand.append(player.deck.pop(idx))


def _refill_hand(player: PlayerState, rng: random.Random) -> None:
    """Roba solo las cartas que falten para volver a HAND_SIZE (la mano persiste entre turnos)."""
    missing = HAND_SIZE - len(player.hand)
    if missing > 0:
        _draw(player, rng, n=missing)


def _rotate_one_card(player: PlayerState, match: MatchState) -> None:
    """Regla de rotación por inactividad: el jugador cerró el turno sin jugar ninguna carta, así que
    se descarta UNA carta al azar de su mano y se roba una nueva (no se resetea la mano entera).
    La carta retirada se aparta mientras se roba la sustituta (va al descarte DESPUÉS), así nunca
    vuelve a la mano en el mismo robo aunque el mazo se rebaraje. Si no hay ninguna carta que robar
    (mazo y descarte vacíos), la carta se queda en la mano: rotar no tendría sentido."""
    if not player.hand:
        return
    removed = player.hand.pop(match.rng.randrange(len(player.hand)))
    before = len(player.hand)
    _refill_hand(player, match.rng)
    if len(player.hand) == before:
        player.hand.append(removed)  # nada que robar: no se pierde la carta
        return
    player.discard.append(removed)
    # El log es público: no revela qué carta tenía el jugador en la mano.
    match.log.append(f"🔄 {player.name} no jugó cartas: descarta 1 carta al azar de su mano y roba una nueva.")


def start_match(
    deck_a: list[Card],
    deck_b: list[Card],
    hp_a: int,
    hp_b: int,
    name_a: str = "Player A",
    name_b: str = "Player B",
    seed: Optional[int] = None,
) -> MatchState:
    rng = random.Random(seed)
    a = PlayerState(name=name_a, hp=hp_a, max_hp=hp_a, deck=list(deck_a))
    b = PlayerState(name=name_b, hp=hp_b, max_hp=hp_b, deck=list(deck_b))
    rng.shuffle(a.deck)
    rng.shuffle(b.deck)
    _draw(a, rng)
    _draw(b, rng)
    match = MatchState(player_a=a, player_b=b, rng=rng)
    match.log.append(f"Comienza la partida: {name_a} ({hp_a} HP) vs {name_b} ({hp_b} HP).")
    return match


def _get_player(match: MatchState, player: str) -> PlayerState:
    if player == "a":
        return match.player_a
    if player == "b":
        return match.player_b
    raise EngineError("player debe ser 'a' o 'b'")


ZoneArg = Union[Zone, str, Iterable, None]


def _normalize_zones(zones: ZoneArg) -> list[Zone]:
    if zones is None:
        return []
    if isinstance(zones, (Zone, str)):
        zones = [zones]
    try:
        return [Zone(z) for z in zones]
    except ValueError as e:
        raise EngineError(f"Zona no válida: {e}") from None


def validate_zones(card: Card, attack_zones: ZoneArg, defense_zones: ZoneArg) -> tuple[tuple, tuple]:
    """Regla 1 — zonas únicas por carta. Devuelve (ataques, defensas) como
    tuplas de Zone o lanza EngineError si la selección es ilegal:
    - más zonas de ataque que ATK (o de defensa que DEF);
    - la misma zona repetida dentro de la MISMA carta.
    Dejar zonas sin usar (menos que ATK/DEF, incluso ninguna) es legal."""
    atk = _normalize_zones(attack_zones)
    dfn = _normalize_zones(defense_zones)
    for kind, chosen, limit in (("ataque", atk, max_attack_zones(card)), ("defensa", dfn, max_defense_zones(card))):
        if len(chosen) > limit:
            raise EngineError(
                f"{card.name} ({card.atk}/{card.defense}) solo puede marcar {limit} zona(s) de {kind}; "
                f"se han elegido {len(chosen)}."
            )
        if len(set(chosen)) != len(chosen):
            raise EngineError(
                f"{card.name}: no se pueden poner 2 {kind}s de la misma carta en la misma zona."
            )
    return tuple(atk), tuple(dfn)


def _zones_txt(zones: tuple) -> str:
    return ", ".join(ZONE_LABEL[z] for z in zones) if zones else "—"


def play_card(
    match: MatchState,
    player: str,
    card_id: str,
    attack_zones: ZoneArg = None,
    defense_zones: ZoneArg = None,
) -> MatchState:
    """Prepara una carta para este turno con sus zonas de ataque y de defensa
    (listas de Zone/str; una sola Zone también vale). Valida la regla de
    zonas únicas por carta y la energía (regla 4: hasta 3 cartas de coste 1)."""
    if match.status != MatchStatus.ONGOING:
        raise EngineError("La partida ya ha terminado.")
    p = _get_player(match, player)
    card = next((c for c in p.hand if c.id == card_id), None)
    if card is None:
        raise EngineError(f"La carta {card_id} no está en la mano de {p.name}.")

    spent = p.energy_spent_this_turn
    if spent + card.cost_energy > MAX_ENERGY_SPEND_PER_TURN:
        raise EngineError(
            f"Tope de gasto por turno ({MAX_ENERGY_SPEND_PER_TURN}) superado: "
            f"ya gastados {spent}, esta carta cuesta {card.cost_energy}."
        )
    if spent + card.cost_energy > p.energy:
        raise EngineError(f"Energía insuficiente: tienes {p.energy}, ya comprometidos {spent}.")

    atk, dfn = validate_zones(card, attack_zones, defense_zones)
    p.hand.remove(card)
    p.staged_plays.append(StagedPlay(card=card, attack_zones=atk, defense_zones=dfn))
    p.acted_this_turn = True
    p.inactive_turns = 0
    match.log.append(
        f"{p.name} juega {card.name} ({card.atk}/{card.defense}) · ⚔️ {_zones_txt(atk)} · 🛡️ {_zones_txt(dfn)}."
    )
    return match


@dataclass
class _Direction:
    damage: int
    report: TurnReport


def _resolve_direction(attacker: PlayerState, defender: PlayerState, log: list[str]) -> _Direction:
    """Daño que `attacker` inflige a `defender` este turno (reglas 2 y 3).
    No toca la vida: el caller aplica las dos direcciones a la vez (síncrono)."""
    report = TurnReport()
    blocks = {z: 0 for z in Zone}
    for sp in defender.staged_plays:
        for z in sp.defense_zones:
            blocks[z] += 1

    # Ataques por zona: (prioridad, orden, índice de jugada). Prioridad 0 = especial
    # (la defensa la para primero), 1 = resto.
    blocked_plays: set[int] = set()
    total_hits = 0
    for z in Zone:
        hits = [
            (0 if is_tripartite_card(sp.card) else 1, i)
            for i, sp in enumerate(attacker.staged_plays) if z in sp.attack_zones
        ]
        if not hits:
            continue
        hits.sort()
        n_block = min(blocks[z], len(hits))
        for _prio, i in hits[:n_block]:
            blocked_plays.add(i)
        landed = len(hits) - n_block
        total_hits += landed
        if landed:
            report.hits[z.value] = landed
        if n_block:
            report.blocked[z.value] = n_block
            log.append(
                f"{defender.name} bloquea {n_block} ataque(s) en {ZONE_LABEL[z]}."
            )
        if landed:
            log.append(
                f"{attacker.name} inflige {landed * ZONE_HIT_DAMAGE} de daño en {ZONE_LABEL[z]} de {defender.name}"
                + (f" ({landed} ataques)." if landed > 1 else ".")
            )

    damage = total_hits * ZONE_HIT_DAMAGE
    for i, sp in enumerate(attacker.staged_plays):
        if not is_tripartite_card(sp.card):
            continue
        full = len(sp.attack_zones) == max_attack_zones(sp.card)
        if full and i not in blocked_plays:
            damage += TRIPARTITE_COMBO_BONUS
            report.combos.append(sp.card.name)
            n = len(sp.attack_zones)
            log.append(
                f"🔥 ¡Combo Tripartito de {attacker.name} con {sp.card.name}! {n} zona(s) x {ZONE_HIT_DAMAGE} "
                f"+ {TRIPARTITE_COMBO_BONUS} = {n * ZONE_HIT_DAMAGE + TRIPARTITE_COMBO_BONUS} de daño."
            )
        elif sp.attack_zones:
            reason = "le han bloqueado un ataque" if full else (
                f"solo marcó {len(sp.attack_zones)} de {max_attack_zones(sp.card)} zonas"
            )
            log.append(f"✖️ {sp.card.name} no activa el Combo Tripartito: {reason}.")
    report.damage_dealt = damage
    return _Direction(damage=damage, report=report)


def _healing(player: PlayerState, log: list[str]) -> int:
    """Regla 3: +HEALER_HEAL_AMOUNT por cada curadora que cubre TODAS sus zonas de defensa."""
    total = 0
    for sp in player.staged_plays:
        if not is_healer_card(sp.card):
            continue
        need = max_defense_zones(sp.card)
        if len(sp.defense_zones) == need:
            total += HEALER_HEAL_AMOUNT
            log.append(
                f"💚 {player.name} cubre las {need} zona(s) de {sp.card.name} y cura +{HEALER_HEAL_AMOUNT} de vida."
            )
        else:
            log.append(
                f"✖️ {sp.card.name} no cura: cubrió {len(sp.defense_zones)} de {need} zonas."
            )
    return total


def register_activity(match: MatchState, player: str) -> None:
    """El jugador ha interactuado este turno (elegir/quitar carta, marcar
    zona, jugar, pasar...): ese turno no cuenta como inactividad y el
    contador de inactividad vuelve a 0 AL MOMENTO (no al cerrar el turno)."""
    p = _get_player(match, player)
    p.acted_this_turn = True
    p.inactive_turns = 0


def pass_turn(match: MatchState, player: str) -> None:
    """PASAR TURNO voluntario: jugada táctica válida (guardar energía, no
    querer jugar, no poder pagar...). Nunca suma inactividad; recarga la
    energía como cualquier otro turno al cerrarse (end_turn)."""
    p = _get_player(match, player)
    p.acted_this_turn = True
    p.inactive_turns = 0
    if not p.staged_plays:
        match.log.append(f"⏭️ {p.name} pasa turno.")


def end_turn(match: MatchState, timed_out: bool = False) -> MatchState:
    """Resuelve el turno. `timed_out=True` cuando lo cierra el temporizador
    (TURN_TIMER_SECONDS) — solo entonces puede sumar inactividad, y solo a
    quien no haya actuado (ni jugado, ni pasado, ni interactuado)."""
    if match.status != MatchStatus.ONGOING:
        raise EngineError("La partida ya ha terminado.")

    a, b = match.player_a, match.player_b
    to_b = _resolve_direction(a, b, match.log)
    to_a = _resolve_direction(b, a, match.log)
    heal_a = _healing(a, match.log)
    heal_b = _healing(b, match.log)
    to_b.report.healed, to_a.report.healed = heal_a, heal_b
    a.last_turn, b.last_turn = to_b.report, to_a.report
    a.combos_completed += len(to_b.report.combos)
    b.combos_completed += len(to_a.report.combos)

    # Síncrono: daño y curación de los dos se aplican a la vez sobre la vida de inicio de turno.
    a.hp = max(0, min(a.max_hp, a.hp - to_a.damage + heal_a))
    b.hp = max(0, min(b.max_hp, b.hp - to_b.damage + heal_b))

    # Regla ESTRICTA de inactividad: solo cuenta el turno en que se agota el
    # temporizador sin una acción limpia. Solo jugar cartas (play_card) o
    # pulsar PASAR TURNO (pass_turn) es actividad y reinicia el contador a 0;
    # dejar una carta elegida a medias NO lo es (el panel no la juega).
    for player in (a, b):
        if player.acted_this_turn or player.staged_plays:
            player.inactive_turns = 0
        elif timed_out:
            player.inactive_turns += 1
            if player.inactive_turns < INACTIVITY_LIMIT:
                match.log.append(
                    f"⚠️ A {player.name} se le agotó el tiempo sin actuar: inactividad "
                    f"{player.inactive_turns}/{INACTIVITY_LIMIT}."
                )
        player.acted_this_turn = False

    # Fin de turno: SOLO las cartas jugadas van a descarte; las no jugadas siguen en la mano.
    # Quien no jugó ninguna carta rota 1 carta al azar (_rotate_one_card). Después se roba lo que
    # falte para volver a HAND_SIZE.
    # Energía: se DESCUENTA lo gastado este turno y luego se recupera
    # +ENERGY_PER_TURN, sin pasar nunca de MAX_ENERGY (3).
    for player in (a, b):
        spent = player.energy_spent_this_turn
        player.energy = min(MAX_ENERGY, max(0, player.energy - spent) + ENERGY_PER_TURN)
        played_any = bool(player.staged_plays)
        player.discard.extend(sp.card for sp in player.staged_plays)
        player.staged_plays = []
        if not played_any:
            _rotate_one_card(player, match)
        _refill_hand(player, match.rng)

    a_idle = a.inactive_turns >= INACTIVITY_LIMIT
    b_idle = b.inactive_turns >= INACTIVITY_LIMIT
    # El KO por vida tiene prioridad: si alguien llega a 0 HP en este mismo
    # turno, gana/empata por vida aunque además se cumpla la inactividad.
    if a.hp <= 0 and b.hp <= 0:
        match.status = MatchStatus.DRAW
        match.end_reason = "ko"
        match.log.append("¡Empate! Ambos jugadores llegan a 0 HP en el mismo turno.")
    elif a.hp <= 0:
        match.status = MatchStatus.PLAYER_B_WINS
        match.end_reason = "ko"
        match.log.append(f"{b.name} gana la partida.")
    elif b.hp <= 0:
        match.status = MatchStatus.PLAYER_A_WINS
        match.end_reason = "ko"
        match.log.append(f"{a.name} gana la partida.")
    elif a_idle and b_idle:
        match.status = MatchStatus.DRAW
        match.end_reason = "inactivity"
        match.log.append(
            f"⛔ Empate por inactividad: a ambos se les agotó el tiempo {INACTIVITY_LIMIT} turnos seguidos."
        )
    elif a_idle:
        match.status = MatchStatus.PLAYER_B_WINS
        match.end_reason = "inactivity"
        match.log.append(
            f"⛔ {a.name} pierde por inactividad ({INACTIVITY_LIMIT} turnos seguidos sin actuar). "
            f"{b.name} gana la partida."
        )
    elif b_idle:
        match.status = MatchStatus.PLAYER_A_WINS
        match.end_reason = "inactivity"
        match.log.append(
            f"⛔ {b.name} pierde por inactividad ({INACTIVITY_LIMIT} turnos seguidos sin actuar). "
            f"{a.name} gana la partida."
        )
    else:
        match.turn_number += 1
        match.log.append(f"--- Turno {match.turn_number} ---")

    return match


def auto_skip_turn(match: MatchState, player: str) -> None:
    """El temporizador de turno (TURN_TIMER_SECONDS) expiró sin acción: no se stagea nada extra para
    ese jugador. Sus cartas siguen en la mano; como no jugó ninguna, end_turn rota 1 carta al azar
    (_rotate_one_card)."""
    match.log.append(f"{_get_player(match, player).name} no actuó a tiempo: turno pasa automáticamente.")


def bot_choose_plays(match: MatchState, player: str) -> None:
    """IA mínima para el modo Vs. Bot: juega cartas aleatorias asequibles
    hasta agotar el presupuesto de energía del turno. Cada carta marca TODAS
    las zonas que permite su ATK/DEF, elegidas al azar y sin repetir."""
    p = _get_player(match, player)
    p.acted_this_turn = True  # el Bot siempre decide (jugar o pasar): nunca es inactividad
    rng = match.rng
    hand = list(p.hand)
    rng.shuffle(hand)
    for card in hand:
        spent = p.energy_spent_this_turn
        if spent + card.cost_energy > min(MAX_ENERGY_SPEND_PER_TURN, p.energy):
            continue
        atk = rng.sample(list(Zone), max_attack_zones(card))
        dfn = rng.sample(list(Zone), max_defense_zones(card))
        play_card(match, player, card.id, atk, dfn)


def get_match_state(match: MatchState) -> dict:
    def report_snapshot(r: TurnReport) -> dict:
        return {
            "hits": dict(r.hits), "blocked": dict(r.blocked), "damage_dealt": r.damage_dealt,
            "combos": list(r.combos), "healed": r.healed,
        }

    def player_snapshot(p: PlayerState) -> dict:
        return {
            "name": p.name,
            "hp": p.hp,
            "max_hp": p.max_hp,
            "energy": p.energy,
            "energy_spent_this_turn": p.energy_spent_this_turn,
            "hand": [c.to_dict() for c in p.hand],
            "staged_plays": [
                {
                    "card": sp.card.to_dict(),
                    "attack_zones": [z.value for z in sp.attack_zones],
                    "defense_zones": [z.value for z in sp.defense_zones],
                }
                for sp in p.staged_plays
            ],
            "deck_remaining": len(p.deck),
            "discard_count": len(p.discard),
            "last_turn": report_snapshot(p.last_turn),
            "combos_completed": p.combos_completed,
            "inactive_turns": p.inactive_turns,
        }

    return {
        "turn_number": match.turn_number,
        "status": match.status.value,
        "end_reason": match.end_reason,
        "inactivity_limit": INACTIVITY_LIMIT,
        "player_a": player_snapshot(match.player_a),
        "player_b": player_snapshot(match.player_b),
        "log": list(match.log),
    }
