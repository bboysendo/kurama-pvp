"""Pruebas manuales del motor PvP (python test_pvp_engine_manual.py).
Reglas definitivas de zonas múltiples (Sendo, 05/11/2026) — ver pvp_engine.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from card_schema import (
    HEALER_HEAL_AMOUNT, MAX_ENERGY, SPECIAL_COMBO_BONUS, TRIPARTITE_COMBO_BONUS, ZONE_HIT_DAMAGE, Ability, Card, CardCategory, Faction,
)
from pvp_engine import (
    EngineError, MatchStatus, Zone, build_deck_from_pool, end_turn, get_match_state, max_attack_zones,
    max_defense_zones, pass_turn, play_card, start_match,
)

H, T, L = Zone.HEAD, Zone.TORSO, Zone.LEGS
ALL = [H, T, L]
TRI = Ability.TRIPARTITE_STRIKE
HEAL = Ability.HEALER


def mk(id, atk, defense, ability=Ability.NONE):
    return Card(id=id, name=id, faction=Faction.NEUTRAL, category=CardCategory.STANDARD,
                atk=atk, defense=defense, ability=ability)


def play_full(m, p, cid):
    """Juega la carta marcando todas sus zonas (las primeras N)."""
    card = next(c for c in getattr(m, f"player_{p}").hand if c.id == cid)
    play_card(m, p, cid, ALL[:max_attack_zones(card)], ALL[:max_defense_zones(card)])


def duel(a_hand, b_hand, a_plays, b_plays, a_energy=3, b_energy=3, hp=50):
    """Partida controlada: fija manos/energía, juega [(id, [zonas_atk], [zonas_def])] y resuelve el turno."""
    m = start_match([mk("pa", 0, 0)] * 30, [mk("pb", 0, 0)] * 30, hp_a=hp, hp_b=hp, seed=99)
    m.player_a.hand, m.player_b.hand = list(a_hand), list(b_hand)
    m.player_a.energy, m.player_b.energy = a_energy, b_energy
    for cid, z, dz in a_plays:
        play_card(m, "a", cid, z, dz)
    for cid, z, dz in b_plays:
        play_card(m, "b", cid, z, dz)
    end_turn(m)
    return m


def must_fail(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except EngineError as e:
        return str(e)
    raise AssertionError("se esperaba EngineError")


assert ZONE_HIT_DAMAGE == 5 and TRIPARTITE_COMBO_BONUS == 3 and SPECIAL_COMBO_BONUS == 3 and HEALER_HEAL_AMOUNT == 3

# ================= Regla 1: zonas únicas por carta =================
m = start_match([mk("x", 0, 0)] * 30, [mk("y", 0, 0)] * 30, hp_a=50, hp_b=50, seed=1)
m.player_a.hand = [mk("a3", 3, 0), mk("d3", 0, 3), mk("h21", 2, 1)]
print("repetir zona:", must_fail(play_card, m, "a", "a3", [H, H, T], []))
print("más zonas que ATK:", must_fail(play_card, m, "a", "h21", [H, T, L], []))
print("más zonas que DEF:", must_fail(play_card, m, "a", "h21", [H], [H, T]))
must_fail(play_card, m, "a", "d3", [], [T, T])
must_fail(play_card, m, "a", "d3", [H], [])          # 0 ATK: no puede atacar
must_fail(play_card, m, "a", "a3", [], [H])          # 0 DEF: no puede defender
must_fail(play_card, m, "a", "a3", ["brazo"], [])    # zona inexistente
assert len(m.player_a.hand) == 3 and not m.player_a.staged_plays  # una jugada ilegal no consume nada
play_card(m, "a", "a3", [H, T], [])                  # obviar zonas sobrantes es legal
play_card(m, "a", "h21", ["legs", "head"], "torso")  # str y una sola zona también valen
assert m.player_a.staged_plays[1].attack_zones == (L, H) and m.player_a.staged_plays[1].defense_zones == (T,)
print("regla 1 (zonas únicas por carta, obviar zonas) OK")

# ================= Regla 2: 5 por zona, bloqueo 1:1 =================
m = duel([mk("a3", 3, 0)], [], [("a3", ALL, [])], [])
assert m.player_b.hp == 50 - 15 - 3, m.player_b.hp              # 3 zonas tocadas a la vez: +3 Tripartito
m = duel([mk("a3", 3, 0)], [], [("a3", [H], [])], [])
assert m.player_b.hp == 45, m.player_b.hp                       # 1 de 3 zonas usada -> 5
m = duel([mk("a3", 3, 0)], [mk("d2", 0, 2)], [("a3", ALL, [])], [("d2", [], [H, L])])
assert m.player_b.hp == 45, m.player_b.hp                       # 2 bloqueados, entra Tronco
m = duel([mk("a3", 3, 0)], [mk("d3", 0, 3)], [("a3", ALL, [])], [("d3", [], ALL)])
assert m.player_b.hp == 50, m.player_b.hp                       # todo bloqueado
# Cartas distintas SÍ apilan en la misma zona; cada defensa para 1 ataque.
m = duel([mk("a1", 1, 0), mk("b1", 1, 0), mk("c1", 1, 0)], [mk("d1", 0, 1)],
         [("a1", [H], []), ("b1", [H], []), ("c1", [H], [])], [("d1", [], [H])])
assert m.player_b.hp == 50 - 10, m.player_b.hp                  # solo Cabeza afectada: sin Tripartito
# Simultáneo: los dos reciben a la vez.
m = duel([mk("h21", 2, 1)], [mk("h12", 1, 2)], [("h21", [H, T], [L])], [("h12", [L], [H, L])])
assert m.player_b.hp == 45 and m.player_a.hp == 50, (m.player_a.hp, m.player_b.hp)
# 0/0: se juega pero no hace nada.
m = duel([mk("z", 0, 0)], [], [("z", [], [])], [])
assert m.player_b.hp == 50 and m.player_a.hp == 50
print("regla 2 (5 por zona, bloqueo 1:1, apilar, simultáneo) OK")

# ================= Regla 2: Combo Tripartito (zonas afectadas, acumulado, 1 vez) =================
m = start_match([mk("pa", 0, 0)] * 30, [mk("pb", 0, 0)] * 30, hp_a=100, hp_b=100, seed=7)
def turn(m, a_cards, b_cards=()):
    m.player_a.hand = [c for c, _, _ in a_cards]
    m.player_b.hand = [c for c, _, _ in b_cards]
    m.player_a.energy = m.player_b.energy = 3
    for c, z, dz in a_cards:
        play_card(m, "a", c.id, z, dz)
    for c, z, dz in b_cards:
        play_card(m, "b", c.id, z, dz)
    end_turn(m)
turn(m, [(mk("j1", 1, 0), [H], [])])                                     # afecta Cabeza
assert m.player_b.hp == 95 and m.player_a.zones_affected == {H} and not m.player_a.tripartite_claimed
turn(m, [(mk("j2", 1, 0), [T], [])], [(mk("w", 0, 1), [], [T])])        # bloqueado: Tronco NO queda afectado
assert m.player_b.hp == 95 and m.player_a.zones_affected == {H}
turn(m, [(mk("j3", 2, 0), [T, H], [])])                                  # afecta Tronco (Cabeza repetida no cuenta)
assert m.player_b.hp == 85 and m.player_a.zones_affected == {H, T}
turn(m, [(mk("j4", 1, 0), [L], [])])                                     # 3.ª zona -> +3 una vez
assert m.player_b.hp == 85 - 5 - 3, m.player_b.hp
assert m.player_a.tripartite_claimed and m.player_a.last_turn.tripartite
assert any("Combo Tripartito" in l for l in m.log)
turn(m, [(mk("j5", 3, 0), ALL, [])])                                     # ya cobrado: nunca más
assert m.player_b.hp == 77 - 15 and not m.player_a.last_turn.tripartite, m.player_b.hp
assert sum("¡Combo Tripartito" in l for l in m.log) == 1
# Cada jugador lleva su propio combo; el del rival no afecta al tuyo.
assert m.player_b.zones_affected == set() and not m.player_b.tripartite_claimed
print("combo tripartito (zonas afectadas acumuladas, +3 una sola vez) OK")

# ================= Regla 2: Combo Especial (+3 de las especiales, repetible) =================
def fresh_with_tripartite_used():
    m = start_match([mk("pa", 0, 0)] * 30, [mk("pb", 0, 0)] * 30, hp_a=100, hp_b=100, seed=8)
    m.player_a.zones_affected, m.player_a.tripartite_claimed = set(Zone), True
    return m
for atk, expected in ((3, 18), (2, 13), (1, 8)):   # 5 x zonas + 3
    m = fresh_with_tripartite_used()
    turn(m, [(mk("sp", atk, 0, TRI), ALL[:atk], [])])
    assert m.player_b.hp == 100 - expected, (atk, m.player_b.hp)
    assert m.player_a.last_turn.combos == ["sp"]
    assert any("Combo Especial" in l for l in m.log)
# Repetible: segunda especial en otro turno vuelve a sumar +3.
turn(m, [(mk("sp2", 2, 0, TRI), [T, L], [])])
assert m.player_b.hp == 100 - 8 - 13 and m.player_a.combos_completed == 2, m.player_b.hp
# Los dos bonus se suman: 3/0 especial en las 3 zonas desde cero = 15 + 3 (especial) + 3 (tripartito) = 21.
m = duel([mk("sp", 3, 0, TRI)], [], [("sp", ALL, [])], [])
assert m.player_b.hp == 50 - 21, m.player_b.hp
assert m.player_a.last_turn.combos == ["sp"] and m.player_a.last_turn.tripartite
# 2/0 especial que completa la 3.ª zona afectada: 10 + 3 + 3 = 16.
m = start_match([mk("pa", 0, 0)] * 30, [mk("pb", 0, 0)] * 30, hp_a=100, hp_b=100, seed=9)
m.player_a.zones_affected = {H}
turn(m, [(mk("sp", 2, 0, TRI), [T, L], [])])
assert m.player_b.hp == 100 - 16, m.player_b.hp
m = fresh_with_tripartite_used()
turn(m, [(mk("sp", 2, 2, TRI), [H, L], [T])])
assert m.player_b.hp == 100 - 13, m.player_b.hp                  # la 2/2 especial sigue la misma regla
# No usa todas sus zonas -> solo base, sin +3 especial.
m = fresh_with_tripartite_used()
turn(m, [(mk("sp", 3, 0, TRI), [H, T], [])])
assert m.player_b.hp == 90 and not m.player_a.last_turn.combos, m.player_b.hp
# Un ataque bloqueado -> especial anulado: 2 x 5 = 10.
m = fresh_with_tripartite_used()
turn(m, [(mk("sp", 3, 0, TRI), ALL, [])], [(mk("d1", 0, 1), [], [T])])
assert m.player_b.hp == 90 and not m.player_a.last_turn.combos, m.player_b.hp
m = fresh_with_tripartite_used()
turn(m, [(mk("sp", 1, 0, TRI), [L], [])], [(mk("d1", 0, 1), [], [L])])
assert m.player_b.hp == 100, m.player_b.hp
# Defensa en otra zona no bloquea: especial completo.
m = fresh_with_tripartite_used()
turn(m, [(mk("sp", 2, 0, TRI), [H, T], [])], [(mk("d1", 0, 1), [], [L])])
assert m.player_b.hp == 87, m.player_b.hp
# Una carta normal con las mismas stats no tiene el +3 especial.
m = fresh_with_tripartite_used()
turn(m, [(mk("n", 3, 0), ALL, [])])
assert m.player_b.hp == 85, m.player_b.hp
# Zona compartida con una carta normal y 1 sola defensa: la defensa para primero a la especial.
m = fresh_with_tripartite_used()
turn(m, [(mk("sp", 2, 0, TRI), [H, T], []), (mk("n1", 1, 0), [H], [])], [(mk("d1", 0, 1), [], [H])])
assert m.player_b.hp == 90 and not m.player_a.last_turn.combos, m.player_b.hp  # 2 de 3 entran, sin +3
print("combo especial (18 / 13 / 8, repetible, se suma al tripartito, anulado si bloquean) OK")

# ================= Regla 3: curadoras =================
for dfn, zones in ((3, ALL), (2, [H, T])):
    m = duel([], [mk("hl", 0, dfn, HEAL)], [], [("hl", [], zones)], hp=50)
    m2 = start_match([mk("pa", 0, 0)] * 30, [mk("pb", 0, 0)] * 30, hp_a=50, hp_b=50, seed=3)
    m2.player_b.hp = 40
    m2.player_b.hand = [mk("hl", 0, dfn, HEAL)]
    play_card(m2, "b", "hl", [], zones)
    end_turn(m2)
    assert m2.player_b.hp == 43, (dfn, m2.player_b.hp)
    assert m.player_b.hp == 50                                  # nunca pasa de la vida máxima
m = start_match([mk("pa", 0, 0)] * 30, [mk("pb", 0, 0)] * 30, hp_a=50, hp_b=50, seed=4)
m.player_b.hp = 40
m.player_b.hand = [mk("hl", 0, 3, HEAL)]
play_card(m, "b", "hl", [], [H, T])                              # 2 de 3: no cura
end_turn(m)
assert m.player_b.hp == 40, m.player_b.hp
# 1/1: defiende su zona -> +3, y además ataca y bloquea como cualquier otra.
m = duel([mk("a2", 2, 0)], [mk("hl", 1, 1, HEAL)], [("a2", [H, T], [])], [("hl", [L], [H])], hp=50)
assert m.player_b.hp == 50 - 5 + 3 and m.player_a.hp == 45, (m.player_a.hp, m.player_b.hp)
m = duel([], [mk("hl", 1, 1, HEAL)], [], [("hl", [L], [])])
assert m.player_b.hp == 50 and m.player_a.hp == 45               # sin defensa marcada: no cura
assert any("no cura" in l for l in m.log)
print("regla 3 (curadoras 0/3, 0/2, 1/1 +3 si cubren todas sus zonas) OK")

# ================= Regla 4: energía, 3 cartas de coste 1 =================
m = duel([mk("c1", 1, 0), mk("c2", 2, 1), mk("c3", 1, 2)], [mk("d3", 0, 3)],
         [("c1", [H], []), ("c2", [H, T], [L]), ("c3", [L], [H, T])], [("d3", [], [H])], a_energy=3)
assert m.player_a.energy == 1, m.player_a.energy                 # 3 - 3 + 1
assert m.player_b.hp == 50 - 15 - 3, m.player_b.hp               # 4 ataques, 1 bloqueado; 3 zonas -> +3
m = start_match([mk("x", 0, 0)] * 30, [mk("y", 0, 0)] * 30, hp_a=50, hp_b=50, seed=5)
m.player_a.hand = [mk("sp", 3, 0, TRI), mk("c1", 1, 0), mk("c2", 1, 1)]
play_card(m, "a", "sp", ALL, [])                                 # coste 2
play_card(m, "a", "c1", [H], [])                                 # coste 1 -> 3 de 3
must_fail(play_card, m, "a", "c2", [T], [L])                     # ya no queda energía
m.player_a.energy = 2
m.player_a.staged_plays = []
m.player_a.hand = [mk("c1", 1, 0), mk("c2", 1, 1), mk("c3", 0, 1)]
play_card(m, "a", "c1", [H], [])
play_card(m, "a", "c2", [T], [L])
must_fail(play_card, m, "a", "c3", [], [H])                       # 2 de energía -> solo 2 cartas
print("regla 4 (3 cartas de coste 1 con 3 de energía, tope respetado) OK")

# --- Victoria / empate ---
m = duel([mk("a3", 3, 0)], [], [("a3", ALL, [])], [], hp=18)
assert m.status == MatchStatus.PLAYER_A_WINS, m.status
m = duel([mk("a1", 1, 0)], [mk("b1", 1, 0)], [("a1", [H], [])], [("b1", [T], [])], hp=5)
assert m.status == MatchStatus.DRAW, m.status
print("victoria / empate OK")

# --- Snapshot para la UI ---
m = start_match([mk("x", 0, 0)] * 30, [mk("y", 0, 0)] * 30, hp_a=50, hp_b=50, seed=6)
m.player_a.hand = [mk("sp", 2, 0, TRI)]
play_card(m, "a", "sp", [H, L], [])
s = get_match_state(m)
assert s["player_a"]["staged_plays"][0]["attack_zones"] == ["head", "legs"]
end_turn(m)
s = get_match_state(m)
assert s["player_a"]["last_turn"] == {"hits": {"head": 1, "legs": 1}, "blocked": {}, "damage_dealt": 13,
                                      "combos": ["sp"], "tripartite": False, "healed": 0}, s["player_a"]["last_turn"]
assert s["player_a"]["zones_affected"] == ["head", "legs"] and s["player_a"]["tripartite_claimed"] is False
assert s["player_a"]["combos_completed"] == 1
print("snapshot OK")

# --- Energía: el acumulador nunca supera MAX_ENERGY y se recupera +1 por turno ---
m = start_match([mk("e", 1, 0)] * 30, [mk("f", 0, 1)] * 30, hp_a=999, hp_b=999, seed=20)
assert m.player_a.energy == 3
m.player_a.hand = [mk("s2", 2, 0, TRI), mk("c1", 1, 0), mk("c0", 0, 1)]
play_card(m, "a", "s2", [H, T], [])
play_card(m, "a", "c1", [L], [])
end_turn(m)
assert m.player_a.energy == 1, m.player_a.energy
end_turn(m); assert m.player_a.energy == 2
end_turn(m); end_turn(m); assert m.player_a.energy == 3
print("energy spend/regen OK")

# --- Inactividad: SOLO cuando se agota el tiempo sin actuar 3 turnos seguidos ---
deckA = [mk("a_act", 0, 1)] * 15
deckB = [mk("b_idle", 0, 1)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=12)
for _ in range(3):
    play_full(m, "a", "a_act")  # A juega; a B se le agota el tiempo
    end_turn(m, timed_out=True)
s = get_match_state(m)
assert s["status"] == "player_a_wins" and s["end_reason"] == "inactivity", (s["status"], s["end_reason"])
print("inactivity loss (timeouts) OK, status =", s["status"])

# Jugar cualquier carta reinicia el contador de inactividad.
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=13)
for _ in range(2):
    play_full(m, "a", "a_act")
    end_turn(m, timed_out=True)
assert m.player_b.inactive_turns == 2
play_full(m, "a", "a_act")
play_full(m, "b", "b_idle")
end_turn(m, timed_out=True)
assert m.player_b.inactive_turns == 0 and m.status == MatchStatus.ONGOING
print("inactivity counter reset by playing OK")

# Ambos agotan el tiempo 3 veces => empate por inactividad.
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=14)
for _ in range(3):
    end_turn(m, timed_out=True)
assert m.status == MatchStatus.DRAW and m.end_reason == "inactivity"
print("double inactivity draw OK")

# PASAR TURNO voluntario NUNCA suma inactividad (aunque pueda pagar cartas),
# y además reinicia el contador acumulado por tiempos agotados.
from pvp_engine import pass_turn, register_activity
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=15)
end_turn(m, timed_out=True); end_turn(m, timed_out=True)
assert m.player_a.inactive_turns == 2 and m.player_b.inactive_turns == 2
for _ in range(10):
    pass_turn(m, "a"); pass_turn(m, "b")
    end_turn(m, timed_out=True)
    assert m.status == MatchStatus.ONGOING
assert m.player_a.inactive_turns == 0 and m.player_b.inactive_turns == 0
print("voluntary pass never counts as inactivity OK (10 pases seguidos)")

# Interactuar (cartas elegidas) cuando se agota el tiempo tampoco es inactividad.
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=16)
register_activity(m, "a")
end_turn(m, timed_out=True)
assert m.player_a.inactive_turns == 0 and m.player_b.inactive_turns == 1
print("interaction before timeout is not inactivity OK")

# Cualquier acción pone el contador a 0 AL MOMENTO (no al cerrar el turno).
for action in ("register", "pass", "play"):
    m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=18)
    end_turn(m, timed_out=True); end_turn(m, timed_out=True)
    assert m.player_a.inactive_turns == 2
    if action == "register":
        register_activity(m, "a")
    elif action == "pass":
        pass_turn(m, "a")
    else:
        play_full(m, "a", m.player_a.hand[0].id)
    assert m.player_a.inactive_turns == 0, (action, m.player_a.inactive_turns)
    assert get_match_state(m)["player_a"]["inactive_turns"] == 0
    pass_turn(m, "b")
    end_turn(m, timed_out=True)
    assert m.player_a.inactive_turns == 0 and m.status == MatchStatus.ONGOING, action
print("immediate reset on any action OK (elegir/pasar/jugar)")

# Cerrar el turno sin timeout (p.ej. 'Resolver turno' en Hotseat) no penaliza.
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=17)
for _ in range(5):
    end_turn(m)
assert m.player_a.inactive_turns == 0 and m.status == MatchStatus.ONGOING
print("end_turn without timeout never penalizes OK")

# --- Unicidad total: catálogo, mazo y mano sin ninguna repetición ---
import random as _random
from card_schema import DECK_SIZE
from cards_catalog import REQUIRED_BASE_COMBOS, SPECIAL_CARDS, build_catalog, validate_catalog
from pvp_engine import bot_choose_plays as _bot

catalog = build_catalog()
validate_catalog(catalog, check_files=True)
base = [c for c in catalog if c.category == CardCategory.STANDARD]
specials = [c for c in catalog if c.category == CardCategory.SPECIAL]
assert len(base) == len(REQUIRED_BASE_COMBOS) == 16 and len(specials) == 7 and len(catalog) == 23 == DECK_SIZE
assert sorted((c.atk, c.defense) for c in base) == sorted(REQUIRED_BASE_COMBOS)
assert all(c.cost_energy == 1 for c in base), "las 16 base cuestan 1"
assert all(c.cost_energy == 2 for c in specials), "las 7 especiales cuestan 2"
assert sorted((c.atk, c.defense) for c in specials if c.ability == Ability.TRIPARTITE_STRIKE) == [(1, 0), (2, 0), (2, 2), (3, 0)]
assert sorted((c.atk, c.defense) for c in specials if c.ability == Ability.HEALER) == [(0, 2), (0, 3), (1, 1)]
for seed in range(200):
    deck = build_deck_from_pool(catalog, rng=_random.Random(seed))
    assert len(deck) == 23
    assert len({c.id for c in deck}) == 23, "carta repetida en el mazo"
    assert sorted((c.atk, c.defense) for c in deck if c.category == CardCategory.STANDARD) == sorted(
        REQUIRED_BASE_COMBOS), "combinación base repetida/ausente"
print("deck OK: 16 base (coste 1) + 7 especiales (coste 2), cada una exactamente una vez")

# Un pool con dos cartas de la misma combinación nunca mete las dos.
dup_pool = [mk("x1", 2, 0), mk("x2", 2, 0), mk("y", 1, 1)]
deck = build_deck_from_pool(dup_pool, size=16, rng=_random.Random(0))
assert len(deck) == 2 and len({(c.atk, c.defense) for c in deck}) == 2
print("duplicate stats filtered OK")

# Mano: nunca dos cartas iguales ni con la misma combinación, toda la partida.
for seed in range(300):
    rng = _random.Random(seed)
    m = start_match(build_deck_from_pool(catalog, rng=rng), build_deck_from_pool(catalog, rng=rng),
                    hp_a=999, hp_b=999, seed=seed)
    for _turn in range(12):
        for pl in (m.player_a, m.player_b):
            assert len(pl.hand) == 3, (seed, _turn, [c.id for c in pl.hand])
            assert len({(c.atk, c.defense, c.ability) for c in pl.hand}) == 3 and len({c.id for c in pl.hand}) == 3
        _bot(m, "a"); _bot(m, "b")
        end_turn(m)
        if m.status != MatchStatus.ONGOING:
            break
print("hand uniqueness OK (300 partidas x 12 turnos)")

# --- Mano persistente: las cartas no jugadas se quedan; solo se repone lo jugado ---
from pvp_engine import pass_turn
from card_schema import HAND_SIZE
for seed in range(200):
    rng = _random.Random(seed)
    m = start_match(build_deck_from_pool(catalog, rng=rng), build_deck_from_pool(catalog, rng=rng),
                    hp_a=999, hp_b=999, seed=seed)
    a = m.player_a
    before = [c.id for c in a.hand]
    cheapest = min(a.hand, key=lambda c: c.cost_energy)
    play_full(m, "a", cheapest.id)
    kept = [cid for cid in before if cid != cheapest.id]
    end_turn(m)
    after = [c.id for c in a.hand]
    assert after[:len(kept)] == kept, (seed, before, after)        # las no jugadas siguen, en su orden
    assert len(after) == HAND_SIZE and cheapest.id not in after     # solo se repone la jugada
    assert len({(c.atk, c.defense, c.ability) for c in a.hand}) == HAND_SIZE   # unicidad intacta
print("persistent hand OK (200 partidas)")

# --- Rotación: pasar sin jugar descarta 1 carta al azar y roba 1 nueva ---
rotated_positions = set()
for seed in range(200):
    rng = _random.Random(seed)
    m = start_match(build_deck_from_pool(catalog, rng=rng), build_deck_from_pool(catalog, rng=rng),
                    hp_a=999, hp_b=999, seed=seed)
    a = m.player_a
    before = [c.id for c in a.hand]
    discard_before = len(a.discard)
    pass_turn(m, "a")
    end_turn(m)
    after = [c.id for c in a.hand]
    gone = [cid for cid in before if cid not in after]
    assert len(gone) == 1 and len(after) == HAND_SIZE, (seed, before, after)   # cambia EXACTAMENTE 1
    assert a.discard[-1].id == gone[0] and len(a.discard) == discard_before + 1
    assert after[-1] not in before                                           # la nueva es distinta
    rotated_positions.add(before.index(gone[0]))
    assert any("descarta 1 carta al azar" in line for line in m.log)
assert rotated_positions == set(range(HAND_SIZE)), rotated_positions          # al azar, no siempre la misma
print("rotation on pass OK (1 carta al azar)")

# Mismo efecto si se le agota el tiempo sin actuar (timed_out) — B tampoco juega aquí.
m = start_match(build_deck_from_pool(catalog, rng=_random.Random(7)),
                build_deck_from_pool(catalog, rng=_random.Random(8)), hp_a=999, hp_b=999, seed=7)
ha, hb = [c.id for c in m.player_a.hand], [c.id for c in m.player_b.hand]
end_turn(m, timed_out=True)
assert len(set(ha) - {c.id for c in m.player_a.hand}) == 1
assert len(set(hb) - {c.id for c in m.player_b.hand}) == 1
print("rotation on timeout OK")

# Sin nada que robar (mazo y descarte vacíos) la carta no se pierde.
m = start_match([mk("s1", 1, 0), mk("s2", 0, 1), mk("s3", 2, 0)], [mk("t1", 1, 0)], hp_a=99, hp_b=99, seed=1)
assert len(m.player_a.hand) == 3 and not m.player_a.deck
end_turn(m)
assert sorted(c.id for c in m.player_a.hand) == ["s1", "s2", "s3"] and not m.player_a.discard
print("rotation with empty deck keeps the hand OK")

print("\nALL ENGINE ASSERTIONS PASSED")
