import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from card_schema import MAX_ENERGY, SIMULTANEOUS_COMBO_BONUS, Ability, Card, CardCategory, Faction
from pvp_engine import (
    EngineError, MatchStatus, Zone, build_deck_from_pool, end_turn, get_match_state, play_card, start_match,
)

def mk(id, atk, defense, ability=Ability.NONE, heal=0, counter=0):
    return Card(id=id, name=id, faction=Faction.NEUTRAL, category=CardCategory.STANDARD,
                atk=atk, defense=defense, ability=ability, heal_amount=heal, counter_damage=counter)

# --- Basic damage: A attacks HEAD with 3/0, B has no shield -> 3 dmg ---
deckA = [mk("a_atk3", 3, 0)] * 15
deckB = [mk("b_def0", 0, 0)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=1)
play_card(m, "a", "a_atk3", Zone.HEAD)
end_turn(m)
s = get_match_state(m)
assert s["player_b"]["hp"] == 47, s["player_b"]["hp"]
print("basic damage OK, B hp =", s["player_b"]["hp"])

# --- Shield mitigates: B shields HEAD with 0/3, A attacks HEAD with 2/0 -> 0 dmg ---
deckA = [mk("a_atk2", 2, 0)] * 15
deckB = [mk("b_def3", 0, 3)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=2)
play_card(m, "a", "a_atk2", Zone.TORSO)
play_card(m, "b", "b_def3", Zone.TORSO)
end_turn(m)
s = get_match_state(m)
assert s["player_b"]["hp"] == 50, s["player_b"]["hp"]
print("shield mitigation OK, B hp =", s["player_b"]["hp"])

# --- Piercing ignores shield ---
deckA = [mk("a_pierce", 2, 0, ability=Ability.PIERCING)] * 15
deckB = [mk("b_def3", 0, 3)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=3)
play_card(m, "a", "a_pierce", Zone.LEGS)
play_card(m, "b", "b_def3", Zone.LEGS)
end_turn(m)
s = get_match_state(m)
assert s["player_b"]["hp"] == 48, s["player_b"]["hp"]  # 2 dmg despite 3 DEF shield
print("piercing OK, B hp =", s["player_b"]["hp"])

# --- Potion blocks and heals ---
deckA = [mk("a_atk3b", 3, 0)] * 15
deckB = [mk("b_potion", 0, 0, ability=Ability.HEAL_POTION, heal=2)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=40, seed=4)
m.player_b.hp = 35  # simula daño previo, para poder ver la curación sin tope de max_hp
play_card(m, "a", "a_atk3b", Zone.HEAD)
play_card(m, "b", "b_potion", Zone.HEAD)
end_turn(m)
s = get_match_state(m)
assert s["player_b"]["hp"] == 38, s["player_b"]["hp"]  # 35 + max(2, 3) = 38, damage blocked to 0
print("potion heal OK, B hp =", s["player_b"]["hp"])

# --- Counter triggers only if attacker did NOT attack that zone ---
# (counter card itself has atk=1/def=1, so it ALSO lands its own 1 normal
# attack dmg via the regular b->a resolution, on top of the 4-dmg reflect —
# that's intentional: counter cards are hybrids, not pure-utility cards)
deckA = [mk("a_idle", 0, 0)] * 15
deckB = [mk("b_counter", 1, 1, ability=Ability.COUNTER, counter=4)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=5)
play_card(m, "b", "b_counter", Zone.TORSO)  # A plays nothing on TORSO
end_turn(m)
s = get_match_state(m)
assert s["player_a"]["hp"] == 45, s["player_a"]["hp"]  # 1 (normal atk, unshielded) + 4 (counter reflect)
print("counter trigger OK, A hp =", s["player_a"]["hp"])

# Pure counter (atk=0) only deals the reflect damage, nothing else
deckA = [mk("a_idle2", 0, 0)] * 15
deckB = [mk("b_counter_pure", 0, 1, ability=Ability.COUNTER, counter=4)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=5)
play_card(m, "b", "b_counter_pure", Zone.TORSO)
end_turn(m)
s = get_match_state(m)
assert s["player_a"]["hp"] == 46, s["player_a"]["hp"]  # only the 4-dmg reflect
print("pure counter (atk=0) OK, A hp =", s["player_a"]["hp"])

# Counter reflect should NOT add on top if attacker DID attack that zone —
# the counter card's own 1 ATK still lands normally (A has no shield there).
deckA = [mk("a_atk1", 1, 0)] * 15
deckB = [mk("b_counter2", 1, 1, ability=Ability.COUNTER, counter=4)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=6)
play_card(m, "a", "a_atk1", Zone.TORSO)
play_card(m, "b", "b_counter2", Zone.TORSO)
end_turn(m)
s = get_match_state(m)
assert s["player_a"]["hp"] == 49, s["player_a"]["hp"]  # only the card's own 1 atk, no +4 reflect
assert s["player_b"]["hp"] == 50, s["player_b"]["hp"]  # 1 atk - 1 def shield = 0 dmg
print("counter reflect NOT triggered when zone was attacked OK")

# --- Tripartite combo: damage all 3 zones across the match -> +3 bonus on 3rd ---
deckA = [mk("a_hit", 1, 0)] * 15
deckB = [mk("b_pass", 0, 0)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=7)
# B juega su carta 0/0 cada turno (no altera el daño) para no caer en la
# regla de inactividad (INACTIVITY_LIMIT turnos seguidos sin jugar = derrota).
play_card(m, "a", "a_hit", Zone.HEAD)
play_card(m, "b", "b_pass", Zone.LEGS)
end_turn(m)
play_card(m, "a", "a_hit", Zone.TORSO)
play_card(m, "b", "b_pass", Zone.LEGS)
end_turn(m)
play_card(m, "a", "a_hit", Zone.LEGS)
play_card(m, "b", "b_pass", Zone.LEGS)
end_turn(m)
s = get_match_state(m)
# 1 + 1 + (1+3 tripartite bonus) = 6 total damage
assert s["player_b"]["hp"] == 44, s["player_b"]["hp"]
assert s["player_a"]["tripartite_claimed"] is True
print("tripartite combo OK, B hp =", s["player_b"]["hp"])

# --- Critical combo: after tripartite claimed, a 3/0 card deals flat 20 ---
play_card(m, "a", "a_hit", Zone.HEAD)  # a_hit is 1/0, not heavy enough -> normal path
end_turn(m)
s = get_match_state(m)
print("post-tripartite normal hit, B hp =", s["player_b"]["hp"])

deckA2 = [mk("a_heavy", 3, 0)] * 15
deckB2 = [mk("b_pass2", 0, 0)] * 15
m2 = start_match(deckA2, deckB2, hp_a=50, hp_b=100, seed=8)
m2.player_a.tripartite_claimed = True  # simulate combo already completed
play_card(m2, "a", "a_heavy", Zone.HEAD)
end_turn(m2)
s2 = get_match_state(m2)
assert s2["player_b"]["hp"] == 80, s2["player_b"]["hp"]  # flat 20 damage
print("critical combo OK, B hp =", s2["player_b"]["hp"])

# --- Energy cap enforcement (force the hand so the test is deterministic) ---
deckA = [mk("a_2a", 3, 0), mk("a_2b", 2, 1)] + [mk("a_pad", 1, 0)] * 13
deckB = [mk("b_pad", 0, 0)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=9)
m.player_a.hand = [mk("a_2a", 3, 0), mk("a_2b", 2, 1), mk("a_pad", 1, 0)]
play_card(m, "a", "a_2a", Zone.HEAD)  # costs 2 of the 3 per-turn budget
try:
    play_card(m, "a", "a_2b", Zone.TORSO)  # would need 2 more -> 4 > MAX_ENERGY_SPEND_PER_TURN (3)
    raise AssertionError("expected EngineError for exceeding the per-turn spend cap")
except EngineError as e:
    print("energy cap correctly rejected second card:", e)

# --- Victory condition ---
deckA = [mk("a_finisher", 3, 0)] * 15
deckB = [mk("b_weak", 0, 0)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=2, seed=10)
play_card(m, "a", "a_finisher", Zone.HEAD)
end_turn(m)
s = get_match_state(m)
assert s["status"] == "player_a_wins", s["status"]
print("victory condition OK, status =", s["status"])

# --- Decoupled attack/defense zone (Waves Ducks style): a card can shield a
# DIFFERENT zone than the one it attacks. ---
deckA = [mk("a_split", 2, 3)] * 15
deckB = [mk("b_split", 2, 0)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=11)
# A attacks HEAD but defends LEGS; B attacks LEGS -> A's LEGS shield (3) blocks it.
play_card(m, "a", "a_split", Zone.HEAD, defense_zone=Zone.LEGS)
play_card(m, "b", "b_split", Zone.LEGS)
end_turn(m)
s = get_match_state(m)
assert s["player_b"]["hp"] == 48, s["player_b"]["hp"]  # A's 2 atk on HEAD, B unshielded there
assert s["player_a"]["hp"] == 50, s["player_a"]["hp"]  # B's 2 atk on LEGS fully blocked by A's 3 def there
print("decoupled attack/defense zone OK, A hp =", s["player_a"]["hp"], "B hp =", s["player_b"]["hp"])

# --- Inactividad: 3 turnos SEGUIDOS sin jugar ninguna carta => derrota ---
deckA = [mk("a_act", 0, 1)] * 15
deckB = [mk("b_idle", 0, 1)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=12)
for _ in range(3):
    play_card(m, "a", "a_act", Zone.HEAD)  # A juega; B no hace nada
    end_turn(m)
s = get_match_state(m)
assert s["status"] == "player_a_wins" and s["end_reason"] == "inactivity", (s["status"], s["end_reason"])
print("inactivity loss OK, status =", s["status"])

# Jugar cualquier carta reinicia el contador de inactividad.
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=13)
for _ in range(2):
    play_card(m, "a", "a_act", Zone.HEAD)
    end_turn(m)
play_card(m, "a", "a_act", Zone.HEAD)
play_card(m, "b", "b_idle", Zone.HEAD)
end_turn(m)
assert m.player_b.inactive_turns == 0 and m.status == MatchStatus.ONGOING
print("inactivity counter reset OK")

# Ambos inactivos => empate por inactividad.
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=14)
for _ in range(3):
    end_turn(m)
assert m.status == MatchStatus.DRAW and m.end_reason == "inactivity"
print("double inactivity draw OK")

# --- Energía: el acumulador nunca supera MAX_ENERGY (3) ---
assert MAX_ENERGY == 3
deckA = [mk("a_e", 1, 0)] * 15
deckB = [mk("b_e", 0, 1)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=20)
assert m.player_a.energy == 3
for _ in range(6):
    play_card(m, "a", "a_e", Zone.HEAD)
    play_card(m, "b", "b_e", Zone.TORSO)
    end_turn(m)
    assert m.player_a.energy <= MAX_ENERGY and m.player_b.energy <= MAX_ENERGY, (m.player_a.energy, m.player_b.energy)
print("energy cap 3 OK, energy =", m.player_a.energy)

# --- Energía: lo gastado se descuenta y se recupera +1 por turno (máx. 3) ---
deckA = [mk("e1", 1, 0), mk("e2", 0, 1), mk("e3", 2, 1), mk("e4", 1, 1)] * 4
deckB = [mk("f1", 0, 0)] * 16
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=30)

def end_turn_no_idle(m):
    # este test solo mira la energía: se neutraliza la regla de inactividad
    m.player_a.inactive_turns = m.player_b.inactive_turns = 0
    end_turn(m)

m.player_a.hand = [mk("c2", 2, 1), mk("c1", 1, 0), mk("c1b", 0, 1)]
play_card(m, "a", "c2", Zone.HEAD)      # coste 2
play_card(m, "a", "c1", Zone.TORSO)     # coste 1 -> gastados 3 de 3
end_turn_no_idle(m)
assert m.player_a.energy == 1, m.player_a.energy   # 3 - 3 + 1
assert m.player_b.energy == 3, m.player_b.energy   # no gastó: sigue en el tope
m.player_a.hand = [mk("d2", 2, 1), mk("d1", 1, 0), mk("d0", 0, 0)]
try:
    play_card(m, "a", "d2", Zone.HEAD)  # coste 2 con solo 1 de energía
    raise AssertionError("debía faltar energía")
except EngineError:
    pass
play_card(m, "a", "d1", Zone.HEAD)      # coste 1 -> 0
end_turn_no_idle(m)
assert m.player_a.energy == 1, m.player_a.energy   # 1 - 1 + 1
end_turn_no_idle(m)                                        # no juega nada
assert m.player_a.energy == 2, m.player_a.energy
end_turn_no_idle(m); end_turn_no_idle(m)
assert m.player_a.energy == 3, m.player_a.energy   # nunca pasa de 3
print("energy spend/regen OK")

# --- Pasar turno sin energía: recarga +1 y NO cuenta como inactividad ---
m = start_match([mk("p1", 1, 0)] * 16, [mk("q1", 1, 0)] * 16, hp_a=50, hp_b=50, seed=40)
m.player_a.energy = 1
m.player_a.hand = [mk("big1", 3, 0), mk("big2", 2, 1), mk("big3", 0, 3)]  # todas coste 2
end_turn(m)  # A pasa forzado; B también pasa pero SÍ podía pagar
assert m.player_a.energy == 2, m.player_a.energy
assert m.player_a.inactive_turns == 0, m.player_a.inactive_turns
assert m.player_b.inactive_turns == 1, m.player_b.inactive_turns
print("forced pass OK: energía", m.player_a.energy, "inactividad", m.player_a.inactive_turns)

# Pasar voluntariamente (pudiendo pagar) sí suma inactividad.
m = start_match([mk("p1", 1, 0)] * 16, [mk("q1", 1, 0)] * 16, hp_a=50, hp_b=50, seed=41)
play_card(m, "b", "q1", Zone.HEAD)
end_turn(m)
assert m.player_a.inactive_turns == 1 and m.player_a.energy == 3
print("voluntary pass counts as inactivity OK")

# --- Robo de Vida: cura al atacante el daño hecho en su zona (hasta heal_amount) ---
deckA = [mk("a_vamp", 2, 0, ability=Ability.LIFESTEAL, heal=3)] * 15
deckB = [mk("b_hit", 3, 0)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=21)
play_card(m, "a", "a_vamp", Zone.HEAD)
play_card(m, "b", "b_hit", Zone.TORSO)
end_turn(m)
assert m.player_b.hp == 48, m.player_b.hp
assert m.player_a.hp == 49, m.player_a.hp  # 50 - 3 + 2 robados
print("lifesteal OK, A hp =", m.player_a.hp)

# Robo de Vida sin daño (zona bloqueada) no cura nada.
deckB = [mk("b_wall", 0, 3)] * 15
m = start_match(deckA, deckB, hp_a=40, hp_b=50, seed=22)
m.player_a.hp = 30
play_card(m, "a", "a_vamp", Zone.HEAD)
play_card(m, "b", "b_wall", Zone.HEAD)
end_turn(m)
assert m.player_a.hp == 30 and m.player_b.hp == 50, (m.player_a.hp, m.player_b.hp)
print("lifesteal blocked OK")

# --- Combo Simultáneo: +2 si dañas 2 zonas el mismo turno, +4 si dañas 3 ---
deckA = [mk("a_combo", 1, 0, ability=Ability.COMBO_STRIKE)] + [mk("a_jab", 1, 0)] * 14
deckB = [mk("b_idle_c", 0, 0)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=23)
m.player_a.hand = [mk("a_combo", 1, 0, ability=Ability.COMBO_STRIKE), mk("a_jab", 1, 0)]
play_card(m, "a", "a_combo", Zone.HEAD)
play_card(m, "a", "a_jab", Zone.LEGS)
play_card(m, "b", "b_idle_c", Zone.TORSO)
end_turn(m)
assert m.player_b.hp == 50 - 2 - SIMULTANEOUS_COMBO_BONUS[2], m.player_b.hp
print("simultaneous combo (2 zones) OK, B hp =", m.player_b.hp)

# Sin carta de combo, dañar 2 zonas no da bonus.
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=24)
m.player_a.hand = [mk("a_jab", 1, 0), mk("a_jab2", 1, 0)]
play_card(m, "a", "a_jab", Zone.HEAD)
play_card(m, "a", "a_jab2", Zone.LEGS)
play_card(m, "b", "b_idle_c", Zone.TORSO)
end_turn(m)
assert m.player_b.hp == 48, m.player_b.hp
print("no combo card -> no bonus OK")

# Combo de 3 zonas: 3 cartas de coste 1 en el mismo turno (gasto 3 = tope).
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=26)
m.player_a.hand = [mk("a_combo", 1, 0, ability=Ability.COMBO_STRIKE), mk("a_j1", 1, 0), mk("a_j2", 1, 0)]
play_card(m, "a", "a_combo", Zone.HEAD)
play_card(m, "a", "a_j1", Zone.TORSO)
play_card(m, "a", "a_j2", Zone.LEGS)
play_card(m, "b", "b_idle_c", Zone.TORSO)
end_turn(m)
# 3 zonas dañadas: 3 de daño + 3 del Combo Tripartito (primera vez) + 4 del Combo Simultáneo
assert m.player_b.hp == 50 - 3 - 3 - SIMULTANEOUS_COMBO_BONUS[3], m.player_b.hp
print("simultaneous combo (3 zones) OK, B hp =", m.player_b.hp)

# --- Unicidad total: catálogo, mazo y mano sin ninguna repetición ---
import random as _random
from card_schema import DECK_SIZE
from cards_catalog import REQUIRED_BASE_COMBOS, build_catalog, validate_catalog
from pvp_engine import bot_choose_plays as _bot

catalog = build_catalog()
validate_catalog(catalog, check_files=True)
assert len(catalog) == len(REQUIRED_BASE_COMBOS) == 16 == DECK_SIZE
assert sorted((c.atk, c.defense) for c in catalog) == sorted(REQUIRED_BASE_COMBOS)
for seed in range(200):
    deck = build_deck_from_pool(catalog, rng=_random.Random(seed))
    assert len(deck) == 16
    assert len({c.id for c in deck}) == 16, "carta repetida en el mazo"
    assert sorted((c.atk, c.defense) for c in deck) == sorted(REQUIRED_BASE_COMBOS), "combinación repetida/ausente"
print("deck OK: las 16 combinaciones, cada una exactamente una vez")

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
            assert len({(c.atk, c.defense) for c in pl.hand}) == 3 and len({c.id for c in pl.hand}) == 3
        _bot(m, "a"); _bot(m, "b")
        end_turn(m)
        if m.status != MatchStatus.ONGOING:
            break
print("hand uniqueness OK (300 partidas x 12 turnos)")

print("\nALL ENGINE ASSERTIONS PASSED")
