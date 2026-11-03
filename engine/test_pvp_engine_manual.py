import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from card_schema import MAX_ENERGY, SIMULTANEOUS_COMBO_BONUS, Ability, Card, CardCategory, Faction
from pvp_engine import (
    EngineError, MatchStatus, Zone, build_deck_from_pool, end_turn, get_match_state, play_card, start_match,
)

def mk(id, atk, defense, ability=Ability.NONE, heal=0, counter=0, bonus=0):
    return Card(id=id, name=id, faction=Faction.NEUTRAL, category=CardCategory.STANDARD,
                atk=atk, defense=defense, ability=ability, heal_amount=heal, counter_damage=counter,
                energy_bonus=bonus)

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
deckA = [mk("a_2a", 3, 0, ability=Ability.CLEAN_STRIKE), mk("a_2b", 2, 1, ability=Ability.CLEAN_STRIKE)] + [mk("a_pad", 1, 0)] * 13
deckB = [mk("b_pad", 0, 0)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=9)
m.player_a.hand = [mk("a_2a", 3, 0, ability=Ability.CLEAN_STRIKE), mk("a_2b", 2, 1, ability=Ability.CLEAN_STRIKE), mk("a_pad", 1, 0)]  # especiales: coste 2
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

# --- Inactividad: SOLO cuando se agota el tiempo sin actuar 3 turnos seguidos ---
deckA = [mk("a_act", 0, 1)] * 15
deckB = [mk("b_idle", 0, 1)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=12)
for _ in range(3):
    play_card(m, "a", "a_act", Zone.HEAD)  # A juega; a B se le agota el tiempo
    end_turn(m, timed_out=True)
s = get_match_state(m)
assert s["status"] == "player_a_wins" and s["end_reason"] == "inactivity", (s["status"], s["end_reason"])
print("inactivity loss (timeouts) OK, status =", s["status"])

# Jugar cualquier carta reinicia el contador de inactividad.
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=13)
for _ in range(2):
    play_card(m, "a", "a_act", Zone.HEAD)
    end_turn(m, timed_out=True)
assert m.player_b.inactive_turns == 2
play_card(m, "a", "a_act", Zone.HEAD)
play_card(m, "b", "b_idle", Zone.HEAD)
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
        play_card(m, "a", m.player_a.hand[0].id, Zone.HEAD)
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

m.player_a.hand = [mk("c2", 2, 1, ability=Ability.CLEAN_STRIKE), mk("c1", 1, 0), mk("c1b", 0, 1)]
play_card(m, "a", "c2", Zone.HEAD)      # coste 2
play_card(m, "a", "c1", Zone.TORSO)     # coste 1 -> gastados 3 de 3
end_turn_no_idle(m)
assert m.player_a.energy == 1, m.player_a.energy   # 3 - 3 + 1
assert m.player_b.energy == 3, m.player_b.energy   # no gastó: sigue en el tope
m.player_a.hand = [mk("d2", 2, 1, ability=Ability.CLEAN_STRIKE), mk("d1", 1, 0), mk("d0", 0, 0)]
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

# --- Pasar turno sin energía: recarga +1 y sin inactividad ---
m = start_match([mk("p1", 1, 0)] * 16, [mk("q1", 1, 0)] * 16, hp_a=50, hp_b=50, seed=40)
m.player_a.energy = 1
m.player_a.hand = [mk("big1", 3, 0, ability=Ability.CLEAN_STRIKE), mk("big2", 2, 1, ability=Ability.CLEAN_STRIKE), mk("big3", 0, 3, ability=Ability.GUARD_ENERGY, bonus=2)]  # todas coste 2
pass_turn(m, "a")
end_turn(m, timed_out=True)
assert m.player_a.energy == 2, m.player_a.energy
assert m.player_a.inactive_turns == 0, m.player_a.inactive_turns
print("pass without energy OK: energía", m.player_a.energy)

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

# =================== Especiales (revisión 03/11/2026) ===================
def _duel(a_hand, b_hand, a_plays, b_plays, a_energy=3, b_energy=3):
    """Partida controlada: fija manos/energía, juega las cartas indicadas [(id, zona_atk, zona_def)] y resuelve."""
    m = start_match([mk("pa", 0, 0)] * 30, [mk("pb", 0, 0)] * 30, hp_a=50, hp_b=50, seed=99)
    m.player_a.hand, m.player_b.hand = list(a_hand), list(b_hand)
    m.player_a.energy, m.player_b.energy = a_energy, b_energy
    for cid, z, dz in a_plays:
        play_card(m, "a", cid, z, defense_zone=dz)
    for cid, z, dz in b_plays:
        play_card(m, "b", cid, z, defense_zone=dz)
    end_turn(m)
    return m

# Golpe Limpio sin defensa: 10 + 3 = 13 (cualquiera de las 4, sea cual sea su ATK).
for atk, dfn in [(3, 0), (2, 0), (2, 2), (1, 0)]:
    m = _duel([mk("cs", atk, dfn, ability=Ability.CLEAN_STRIKE)], [], [("cs", Zone.HEAD, Zone.HEAD)], [])
    assert m.player_b.hp == 50 - 13, (atk, dfn, m.player_b.hp)
    assert any("Golpe Limpio" in l for l in m.log)
print("clean strike unblocked = 13 OK (las 4 especiales)")

# Bloqueada (cualquier DEF en esa zona): combo anulado, daño normal ATK − DEF.
m = _duel([mk("cs", 3, 0, ability=Ability.CLEAN_STRIKE)], [mk("sh", 0, 1)],
          [("cs", Zone.HEAD, Zone.HEAD)], [("sh", Zone.HEAD, Zone.HEAD)])
assert m.player_b.hp == 50 - 2, m.player_b.hp
assert any("se anula" in l for l in m.log)
m = _duel([mk("cs", 2, 0, ability=Ability.CLEAN_STRIKE)], [mk("sh", 0, 3)],
          [("cs", Zone.TORSO, Zone.TORSO)], [("sh", Zone.TORSO, Zone.TORSO)])
assert m.player_b.hp == 50, m.player_b.hp
# Defensa en OTRA zona no bloquea: entra limpio.
m = _duel([mk("cs", 2, 0, ability=Ability.CLEAN_STRIKE)], [mk("sh", 0, 3)],
          [("cs", Zone.LEGS, Zone.LEGS)], [("sh", Zone.HEAD, Zone.HEAD)])
assert m.player_b.hp == 50 - 13, m.player_b.hp
# Una carta normal con las mismas stats no tiene el plus.
m = _duel([mk("n", 3, 0)], [], [("n", Zone.HEAD, Zone.HEAD)], [])
assert m.player_b.hp == 47, m.player_b.hp
print("clean strike blocked -> combo anulado OK")

# Guardia Vital: bloqueo total del ataque rival => +energy_bonus (tope 3).
g = lambda b: mk("g", 0, 3, ability=Ability.GUARD_ENERGY, bonus=b)
m = _duel([mk("x", 2, 0)], [g(2)], [("x", Zone.HEAD, Zone.HEAD)], [("g", Zone.HEAD, Zone.HEAD)], b_energy=3)
# B: 3 - 2 (coste) + 1 (regen) = 2, +2 por bloquear => 3 (tope)
assert m.player_b.energy == 3 and m.player_b.hp == 50, (m.player_b.energy, m.player_b.hp)
assert any("Guardia Vital" in l for l in m.log)
m = _duel([mk("x", 2, 0)], [mk("g", 0, 2, ability=Ability.GUARD_ENERGY, bonus=1), mk("y", 0, 0)],
          [("x", Zone.HEAD, Zone.HEAD)], [("g", Zone.HEAD, Zone.HEAD)], b_energy=2)
assert m.player_b.energy == 2, m.player_b.energy           # 2 - 2 + 1 = 1, +1 => 2
# Sin ataque en su zona: no hay bloqueo, no hay energía extra.
m = _duel([mk("x", 2, 0)], [g(2)], [("x", Zone.LEGS, Zone.LEGS)], [("g", Zone.HEAD, Zone.HEAD)], b_energy=2)
assert m.player_b.energy == 1, m.player_b.energy           # 2 - 2 + 1
# Bloqueo PARCIAL (entra daño): no cuenta como bloqueo con éxito.
m = _duel([mk("x", 3, 0)], [mk("g", 0, 2, ability=Ability.GUARD_ENERGY, bonus=1)],
          [("x", Zone.TORSO, Zone.TORSO)], [("g", Zone.TORSO, Zone.TORSO)], b_energy=2)
assert m.player_b.energy == 1 and m.player_b.hp == 49, (m.player_b.energy, m.player_b.hp)
# La femenina también para un Golpe Limpio (lo anula) y gana su energía.
m = _duel([mk("cs", 2, 0, ability=Ability.CLEAN_STRIKE)], [g(2)],
          [("cs", Zone.HEAD, Zone.HEAD)], [("g", Zone.HEAD, Zone.HEAD)], b_energy=2)
assert m.player_b.hp == 50 and m.player_b.energy == 3, (m.player_b.hp, m.player_b.energy)
print("guard energy OK (bloqueo total +energía, parcial/sin ataque no)")

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
assert sorted((c.atk, c.defense) for c in specials if c.ability == Ability.CLEAN_STRIKE) == [(1, 0), (2, 0), (2, 2), (3, 0)]
assert sorted((c.atk, c.defense, c.energy_bonus) for c in specials if c.ability == Ability.GUARD_ENERGY) == [
    (0, 2, 1), (0, 3, 2), (1, 1, 1)]
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
    play_card(m, "a", cheapest.id, Zone.HEAD)
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
