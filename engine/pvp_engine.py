"""
Motor de combate PvP por turnos síncronos 1v1, puro Python y desacoplado de
Streamlit/Web3/UI (sección 2 de PVP_Rules). Expone 4 funciones de alto nivel:

    start_match(deck_a, deck_b, hp_a, hp_b) -> MatchState
    play_card(match, player, card_id, zone) -> MatchState
    end_turn(match) -> MatchState
    get_match_state(match) -> dict   # snapshot serializable para la UI

Reglas modeladas (ver PVP_Rules/PVP KURAMARUN....md para el texto original):
- Zonas de impacto Cabeza/Tronco/Piernas; cada carta jugada se dirige a UNA
  zona y aporta ahí su ATK (ataque a esa zona del rival) y/o su DEF (escudo
  en esa misma zona propia) — las cartas híbridas aportan ambos a la vez.
- Daño real = max(0, ATK atacante en zona Z - DEF rival en zona Z), salvo:
    * Rompedefensas/Perforante (PIERCING): ignora el escudo de esa zona.
    * Poción (HEAL_POTION): bloquea el daño de esa zona del todo y cura al
      defensor por max(heal_amount de la poción, daño que iba a recibir).
    * Contraataque (COUNTER): si el rival NO atacó esa zona este turno,
      devuelve `counter_damage` directo (ignorando defensa) — si sí la
      atacó, la carta solo actúa como escudo normal (su propio DEF).
- Combo Tripartito: la primera vez que un jugador ha dañado (>0) las 3 zonas
  del rival a lo largo de TODA la partida, ese golpe suma +3 de daño extra.
- Combo Crítico: una vez completado el combo tripartito, cualquier carta de
  Ataque Puro (3 ATK/0 DEF) jugada después inflige 20 de daño directo fijo en
  vez del cálculo normal (ignora escudo/perforante/etc. de esa zona).
- Energía: empieza en STARTING_ENERGY, +1 por turno, pero el gasto de un
  mismo turno no puede superar MAX_ENERGY_SPEND_PER_TURN (tal cual la
  sección 2.2 — la energía no gastada sí se acumula en el pool para turnos
  futuros, solo el GASTO por turno está topado).
- Mano: se renueva por completo cada turno (se descartan las cartas no
  jugadas y se roban HAND_SIZE nuevas) — el mazo de descarte se reباraja
  automáticamente en cuanto el mazo de robo se queda sin cartas.

Ambigüedades del documento resueltas con un criterio explícito (y señaladas
en el propio código): si el equipo de diseño quiso decir otra cosa, son
constantes/ifs puntuales fáciles de cambiar, no una reescritura.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from card_schema import (
    DECK_SIZE,
    ENERGY_PER_TURN,
    HAND_SIZE,
    MAX_ENERGY_SPEND_PER_TURN,
    STARTING_ENERGY,
    TRIPARTITE_COMBO_BONUS,
    Ability,
    Card,
)


class Zone(str, Enum):
    HEAD = "head"
    TORSO = "torso"
    LEGS = "legs"


class MatchStatus(str, Enum):
    ONGOING = "ongoing"
    PLAYER_A_WINS = "player_a_wins"
    PLAYER_B_WINS = "player_b_wins"
    DRAW = "draw"


class EngineError(ValueError):
    """Jugada ilegal (energía insuficiente, carta no está en mano, partida ya
    terminada, etc.) — siempre con un mensaje claro para mostrar en la UI."""


@dataclass
class StagedPlay:
    card: Card
    zone: Zone            # zona de ATAQUE (dónde pega el ATK de la carta)
    defense_zone: Zone    # zona de DEFENSA (dónde protege el DEF de la carta) — Waves Ducks: puede diferir de `zone`


@dataclass
class PlayerState:
    name: str
    hp: int
    max_hp: int
    deck: list[Card]
    hand: list[Card] = field(default_factory=list)
    discard: list[Card] = field(default_factory=list)
    energy: int = STARTING_ENERGY
    staged_plays: list[StagedPlay] = field(default_factory=list)
    zones_damaged: set = field(default_factory=set)   # Zone completados contra el rival, toda la partida
    tripartite_claimed: bool = False
    battles_played: int = 0  # informativo para el dashboard (no afecta al combate)

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


def build_deck_from_pool(pool: list[Card], size: int = DECK_SIZE, rng: Optional[random.Random] = None) -> list[Card]:
    """Construye un mazo de `size` cartas a partir de un pool (p.ej. las 27
    del catálogo competitivo). Sin repetidas si el pool alcanza; si no,
    completa con repetición para no romper el tamaño fijo del mazo."""
    rng = rng or random
    if size <= len(pool):
        return rng.sample(pool, size)
    return [rng.choice(pool) for _ in range(size)]


def _draw(player: PlayerState, rng: random.Random, n: int = HAND_SIZE) -> None:
    for _ in range(n):
        if not player.deck:
            if not player.discard:
                return  # sin cartas en ningún sitio (mazo muy pequeño en pruebas)
            player.deck, player.discard = player.discard, []
            rng.shuffle(player.deck)
        player.hand.append(player.deck.pop())


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


def play_card(
    match: MatchState, player: str, card_id: str, zone: Zone, defense_zone: Optional[Zone] = None
) -> MatchState:
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

    zone = Zone(zone)
    defense_zone = Zone(defense_zone) if defense_zone is not None else zone
    p.hand.remove(card)
    p.staged_plays.append(StagedPlay(card=card, zone=zone, defense_zone=defense_zone))
    if defense_zone == zone:
        match.log.append(f"{p.name} juega {card.name} ({card.atk}/{card.defense}) en {zone.value}.")
    else:
        match.log.append(
            f"{p.name} juega {card.name} atacando {zone.value} y defendiendo {defense_zone.value}."
        )
    return match


@dataclass
class _ZoneMaps:
    attack: dict
    shield: dict
    piercing_zones: set
    counter_damage: dict
    potion_zone: Optional[Zone]
    potion_card: Optional[Card]
    heavy_attack_zones: set  # zonas donde se jugó una carta 3 ATK/0 DEF este turno


def _build_zone_maps(player: PlayerState) -> _ZoneMaps:
    attack = {z: 0 for z in Zone}
    shield = {z: 0 for z in Zone}
    counter_damage = {z: 0 for z in Zone}
    piercing_zones = set()
    heavy_attack_zones = set()
    potion_zone, potion_card = None, None

    for staged in player.staged_plays:
        card, zone, def_zone = staged.card, staged.zone, staged.defense_zone
        if card.ability == Ability.HEAL_POTION:
            potion_zone, potion_card = def_zone, card
            continue
        attack[zone] += card.atk
        shield[def_zone] += card.defense
        if card.ability == Ability.PIERCING:
            piercing_zones.add(zone)
        if card.ability == Ability.COUNTER:
            counter_damage[def_zone] += card.counter_damage
        if card.atk == 3 and card.defense == 0:
            heavy_attack_zones.add(zone)

    return _ZoneMaps(attack, shield, piercing_zones, counter_damage, potion_zone, potion_card, heavy_attack_zones)


def _resolve_direction(attacker: PlayerState, defender: PlayerState, maps_atk: _ZoneMaps, maps_def: _ZoneMaps, log: list[str]) -> tuple[int, int]:
    """Daño total que `attacker` inflige a `defender` y curación que
    `defender` recibe, resolviendo las 3 zonas. Devuelve (daño_total,
    curacion_total); no aplica los cambios de HP (eso lo hace el caller, para
    que las dos direcciones se calculen sobre el MISMO estado "congelado" de
    inicio de turno, de verdad síncrono)."""
    total_damage = 0
    total_heal = 0
    newly_completed = False

    for z in Zone:
        raw_atk = maps_atk.attack[z]

        if maps_def.potion_zone == z:
            if raw_atk > 0:
                heal = max(maps_def.potion_card.heal_amount, raw_atk)
                total_heal += heal
                log.append(f"{defender.name} bloquea {z.value} con {maps_def.potion_card.name} y cura {heal} HP.")
            continue

        if raw_atk == 0:
            continue  # nada que resolver en esta zona para esta dirección

        if attacker.tripartite_claimed and z in maps_atk.heavy_attack_zones:
            damage = 20
            log.append(f"¡Combo Crítico! {attacker.name} inflige 20 de daño directo en {z.value}.")
        else:
            shield = 0 if z in maps_atk.piercing_zones else maps_def.shield[z]
            damage = max(0, raw_atk - shield)

        if damage <= 0:
            continue

        if z not in attacker.zones_damaged:
            attacker.zones_damaged.add(z)
            if not attacker.tripartite_claimed and len(attacker.zones_damaged) == 3:
                attacker.tripartite_claimed = True
                damage += TRIPARTITE_COMBO_BONUS
                newly_completed = True
                log.append(f"¡Combo Tripartito completado por {attacker.name}! +{TRIPARTITE_COMBO_BONUS} de daño extra.")

        total_damage += damage
        log.append(f"{attacker.name} inflige {damage} de daño en {z.value} de {defender.name}.")

    return total_damage, total_heal


def _collect_counter_hits(attacker: PlayerState, defender: PlayerState, maps_atk: _ZoneMaps, maps_def: _ZoneMaps, log: list[str]) -> int:
    """Daño de contraataque que `defender` devuelve a `attacker`: zonas donde
    `defender` tiene un COUNTER activo y `attacker` NO atacó esa zona este
    turno. Se resuelve aparte de `_resolve_direction` porque golpea en la
    dirección CONTRARIA (defensor -> atacante)."""
    total = 0
    for z in Zone:
        counter = maps_def.counter_damage[z]
        if counter > 0 and maps_atk.attack[z] == 0:
            total += counter
            log.append(f"{defender.name} contraataca en {z.value}: {counter} de daño directo a {attacker.name}.")
    return total


def end_turn(match: MatchState) -> MatchState:
    if match.status != MatchStatus.ONGOING:
        raise EngineError("La partida ya ha terminado.")

    a, b = match.player_a, match.player_b
    maps_a = _build_zone_maps(a)
    maps_b = _build_zone_maps(b)

    damage_to_b, heal_to_b = _resolve_direction(a, b, maps_a, maps_b, match.log)
    damage_to_a, heal_to_a = _resolve_direction(b, a, maps_b, maps_a, match.log)
    counter_to_a = _collect_counter_hits(a, b, maps_a, maps_b, match.log)  # contraataques de B que golpean a A
    counter_to_b = _collect_counter_hits(b, a, maps_b, maps_a, match.log)  # contraataques de A que golpean a B

    b.hp = max(0, min(b.max_hp, b.hp - damage_to_b - counter_to_b + heal_to_b))
    a.hp = max(0, min(a.max_hp, a.hp - damage_to_a - counter_to_a + heal_to_a))

    # Fin de turno: las cartas jugadas y las que quedaron en mano van a descarte.
    for player in (a, b):
        player.discard.extend(sp.card for sp in player.staged_plays)
        player.discard.extend(player.hand)
        player.hand = []
        player.staged_plays = []
        player.energy += ENERGY_PER_TURN
        _draw(player, match.rng)

    if a.hp <= 0 and b.hp <= 0:
        match.status = MatchStatus.DRAW
        match.log.append("¡Empate! Ambos jugadores llegan a 0 HP en el mismo turno.")
    elif a.hp <= 0:
        match.status = MatchStatus.PLAYER_B_WINS
        match.log.append(f"{b.name} gana la partida.")
    elif b.hp <= 0:
        match.status = MatchStatus.PLAYER_A_WINS
        match.log.append(f"{a.name} gana la partida.")
    else:
        match.turn_number += 1
        match.log.append(f"--- Turno {match.turn_number} ---")

    return match


def auto_skip_turn(match: MatchState, player: str) -> None:
    """El temporizador de 20s expiró sin acción: no se stagea nada extra para
    ese jugador (sus cartas no jugadas se descartan igualmente en
    end_turn)."""
    match.log.append(f"{_get_player(match, player).name} no actuó a tiempo: turno pasa automáticamente.")


def bot_choose_plays(match: MatchState, player: str) -> None:
    """IA mínima para el modo Vs. Bot: juega cartas aleatorias asequibles en
    zonas aleatorias hasta agotar el presupuesto de energía del turno."""
    p = _get_player(match, player)
    rng = match.rng
    hand = list(p.hand)
    rng.shuffle(hand)
    for card in hand:
        spent = p.energy_spent_this_turn
        if spent + card.cost_energy > min(MAX_ENERGY_SPEND_PER_TURN, p.energy):
            continue
        zone = rng.choice(list(Zone))
        defense_zone = rng.choice(list(Zone))
        play_card(match, player, card.id, zone, defense_zone=defense_zone)


def get_match_state(match: MatchState) -> dict:
    def player_snapshot(p: PlayerState) -> dict:
        return {
            "name": p.name,
            "hp": p.hp,
            "max_hp": p.max_hp,
            "energy": p.energy,
            "energy_spent_this_turn": p.energy_spent_this_turn,
            "hand": [c.to_dict() for c in p.hand],
            "staged_plays": [
                {"card": sp.card.to_dict(), "zone": sp.zone.value, "defense_zone": sp.defense_zone.value}
                for sp in p.staged_plays
            ],
            "deck_remaining": len(p.deck),
            "discard_count": len(p.discard),
            "zones_damaged": sorted(z.value for z in p.zones_damaged),
            "tripartite_claimed": p.tripartite_claimed,
        }

    return {
        "turn_number": match.turn_number,
        "status": match.status.value,
        "player_a": player_snapshot(match.player_a),
        "player_b": player_snapshot(match.player_b),
        "log": list(match.log),
    }
