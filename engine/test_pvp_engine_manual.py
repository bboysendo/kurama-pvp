import sys
sys.path.insert(0, r"C:\Users\rosen\Documents\PVP\engine")

from card_schema import Ability, Card, CardCategory, Faction
from pvp_engine import Zone, MatchStatus, start_match, play_card, end_turn, get_match_state, EngineError

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
play_card(m, "a", "a_hit", Zone.HEAD)
end_turn(m)
play_card(m, "a", "a_hit", Zone.TORSO)
end_turn(m)
play_card(m, "a", "a_hit", Zone.LEGS)
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
deckA = [mk("a_2a", 3, 0), mk("a_2b", 2, 0)] + [mk("a_pad", 1, 0)] * 13
deckB = [mk("b_pad", 0, 0)] * 15
m = start_match(deckA, deckB, hp_a=50, hp_b=50, seed=9)
m.player_a.hand = [mk("a_2a", 3, 0), mk("a_2b", 2, 0), mk("a_pad", 1, 0)]
play_card(m, "a", "a_2a", Zone.HEAD)  # costs 2, uses the whole per-turn budget
try:
    play_card(m, "a", "a_2b", Zone.TORSO)  # would need 2 more -> exceeds MAX_ENERGY_SPEND_PER_TURN
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

print("\nALL ENGINE ASSERTIONS PASSED")
