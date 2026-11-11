"""
Batería de validación de economy_rules.py (reglas definitivas 10/11/2026) —
ejecutar con:
    python test_economy_rules.py
Cada bloque comprueba una regla de negocio; al final se verifica que el
dinero y los cristales se conservan (nada se crea ni se pierde salvo
depósitos, retiradas, premios y cristales de bots, contabilizados aparte).
"""

from economy_rules import (
    ACCESS_BATTLES_REQUIRED, BOT, CHALLENGE_EXPIRY_SECONDS, CRYSTAL_ELO_LOSS, CRYSTAL_ELO_WIN, CRYSTAL_STAKE,
    CRYSTAL_WIN_KUR_REWARD, ELO_START, FREE_DAILY_RANKED_LIMIT, FREE_RANKED_ELO_LOSS, FREE_RANKED_ELO_WIN,
    HOUSE_FEE_PCT, MONEY_ELO_LOSS, MONEY_ELO_WIN, ChallengeKind, ChallengeStatus, Currency, EconomyError,
    EconomyServer,
)
from card_schema import NFTRarity


class Clock:
    def __init__(self, t=1_793_923_200.0):  # 2026-11-06 00:00 UTC
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def must_fail(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except EconomyError as e:
        return str(e)
    raise AssertionError(f"se esperaba EconomyError en {fn.__name__}")


def new():
    c = Clock()
    return EconomyServer(clock=c), c


FREE, CRY, MONEY = ChallengeKind.FREE, ChallengeKind.CRYSTAL, ChallengeKind.MONEY
OPEN, VS_BOT, MATCHED = ChallengeStatus.OPEN.value, ChallengeStatus.VS_BOT.value, ChallengeStatus.MATCHED.value


def nft(s, w, crystals=0, tid=7):
    s.set_nfts(w, [tid])
    if crystals:
        s.set_crystals(w, crystals)


# ============ 1. ACCESO: NFT o 300 combates (depósito, retiro, competitivo) ============
s, clk = new()
s.sync_balance("pobre", Currency.KUR, 100)          # saldo traído por el servidor (no es depósito)
assert "Depósito bloqueado" in must_fail(s.deposit, "pobre", Currency.KUR, 10)
assert "Retiro bloqueado" in must_fail(s.withdraw, "pobre", Currency.KUR, 10)
msg = must_fail(s.create_challenge, "pobre", MONEY, 5)
assert "Competitivo bloqueado" in msg and "300" in msg and "llevas 0" in msg
assert not s.access_status("pobre")["unlocked"] and s.access_status("pobre")["can_play_competitive"] is False
# ...pero SÍ puede jugar sus 10 Free sin cristales (único camino a los 300)
assert s.access_status("pobre")["can_play_free"]
# Con NFT: desbloqueado desde el primer momento
nft(s, "rica")
s.deposit("rica", Currency.KUSD, 50); s.withdraw("rica", Currency.KUSD, 20)
assert s.account("rica").balances["KUSD"] == 30.0 and s.access_status("rica")["unlocked_by"] == "nft"
# 299 combates no bastan; el 300 desbloquea (histórico total, sin reinicio mensual)
s.set_battles("pobre", ACCESS_BATTLES_REQUIRED - 1)
assert "te faltan 1" in must_fail(s.withdraw, "pobre", Currency.KUR, 1)
ch = s.create_challenge("pobre", FREE); clk.advance(60); s.process_expired(); s.report_result(ch.id, BOT)
acc = s.account("pobre")
assert acc.battles_total == ACCESS_BATTLES_REQUIRED and acc.access_unlocked
assert any(e["event"] == "access_unlocked" for e in s.journal)
s.deposit("pobre", Currency.KUR, 10); s.withdraw("pobre", Currency.KUR, 5)
assert acc.balances["KUR"] == 105.0 and s.access_status("pobre")["unlocked_by"] == "battles"
clk.advance(40 * 86400)                               # meses después sigue desbloqueado
assert acc.access_unlocked
print("acceso: NFT o 300 combates para depósito / retiro / competitivo OK")

# Camino real sin NFT: 30 días x 10 partidas free = 300 (Free y Competitivo suman)
s, clk = new()
day = 0
while not s.account("hugo").access_unlocked:
    for _ in range(FREE_DAILY_RANKED_LIMIT):
        ch = s.create_challenge("hugo", FREE); clk.advance(60); s.process_expired(); s.report_result(ch.id, BOT)
    day += 1
    clk.t = 1_793_923_200.0 + day * 86400
assert day == 30 and s.account("hugo").battles_total == 300
# los combates competitivos también suman al histórico
nft(s, "kai"); s.deposit("kai", Currency.KUR, 10); s.sync_balance("hugo", Currency.KUR, 10)
ch = s.create_challenge("kai", MONEY, 5); s.accept_challenge("hugo", ch.id); s.report_result(ch.id, None)
assert s.account("hugo").battles_total == 301 and s.account("kai").battles_total == 1
print("acceso: 300 combates acumulados (Free + Competitivo, también vs bot) OK")

# ============ 2a. FREE CON CRISTALES: solo NFT, apuesta 1 cristal ============
s, clk = new()
s.set_crystals("sinnft", 5)                           # sin NFT, aunque tenga cristales
ch = s.create_challenge("sinnft", CRY)                # se resuelve a su variante: SIN cristales
assert ch.kind == FREE.value and s.account("sinnft").crystals == 5
s.cancel_challenge("sinnft", ch.id)
nft(s, "ana")
assert "Necesitas al menos 1" in must_fail(s.create_challenge, "ana", FREE)   # NFT sin cristales
ok, why = s.can_play_free("ana"); assert not ok and "cristales" in why
s.set_crystals("ana", 3); nft(s, "bea", 3)
ch = s.create_challenge("ana", FREE)                  # NFT -> siempre con cristales
assert ch.kind == CRY.value and s.account("ana").crystals == 2   # 1 en escrow
assert s.free_ranked_remaining("ana") is None         # no usa el cupo diario
# solo otra cuenta con NFT y cristales puede aceptar
s.set_crystals("pepe", 9)                             # sin NFT
assert "exclusivo de cuentas con NFT" in must_fail(s.accept_challenge, "pepe", ch.id)
nft(s, "cero")                                        # NFT sin cristales
assert "Necesitas al menos 1" in must_fail(s.accept_challenge, "cero", ch.id)
s.accept_challenge("bea", ch.id)
assert s.account("bea").crystals == 2
s.report_result(ch.id, "ana")
a, b = s.account("ana"), s.account("bea")
assert a.crystals == 2 and b.crystals == 2            # cada uno gasta 1: nadie se lleva el cristal del rival
assert s.crystals_consumed == 2
assert a.balances["KUR"] == CRYSTAL_WIN_KUR_REWARD == 3.0 and b.balances["KUR"] == 0.0
assert a.elo == ELO_START + 20 and b.elo == ELO_START - 10
assert (CRYSTAL_ELO_WIN, CRYSTAL_ELO_LOSS, CRYSTAL_STAKE) == (20, -10, 1)
# ilimitado mientras haya cristales (más que el cupo diario)
s.set_crystals("ana", 20); s.set_crystals("bea", 20); elo_a = a.elo
for _ in range(15):
    ch = s.create_challenge("ana", FREE); s.accept_challenge("bea", ch.id); s.report_result(ch.id, None)
assert a.crystals == 5 and b.crystals == 5            # empate: el cristal también se consume (1 por partida)
assert a.elo == elo_a                                 # ...pero el Elo no cambia
# sin cristales ya no puede lanzar
s.set_crystals("ana", 0); assert "Necesitas al menos 1" in must_fail(s.create_challenge, "ana", FREE)
# cancelar ANTES de jugar devuelve el cristal
s.set_crystals("ana", 4)
ch = s.create_challenge("ana", FREE); assert a.crystals == 3
s.cancel_challenge("ana", ch.id); assert a.crystals == 4
print("free con cristales: solo NFT, gasta 1 por partida, gana +20 Elo + 3 KUR, pierde -10 OK")

# ---- 60 s sin retador real -> BOT, que aporta el otro cristal ----
s, clk = new(); nft(s, "ana", 2)
ch = s.create_challenge("ana", FREE)
clk.advance(CHALLENGE_EXPIRY_SECONDS - 1); s.process_expired(); assert ch.status == OPEN
clk.advance(1); s.process_expired()
assert ch.status == VS_BOT and ch.opponent == BOT and ch.bot_crystals == 0   # el bot no apuesta
s.report_result(ch.id, "ana")
assert s.account("ana").crystals == 1 and s.account("ana").balances["KUR"] == 3.0   # 2 - 1 gastado
assert s.account("ana").elo == ELO_START + 20 and s.crystals_consumed == 1
ch = s.create_challenge("ana", FREE); clk.advance(60); s.process_expired(); s.report_result(ch.id, BOT)
assert s.account("ana").crystals == 0 and s.account("ana").elo == ELO_START + 10 and s.crystals_consumed == 2
assert s.account("ana").balances["KUR"] == 3.0       # derrota: sin $KUR
assert "Necesitas al menos 1" in must_fail(s.create_challenge, "ana", FREE)
print("free con cristales: 60 s -> bot, mismas recompensas (+20/+3 KUR, -10 y cristal) OK")

# ============ 2b. FREE SIN CRISTALES: solo sin NFT, 10/día ============
s, clk = new()
for i in range(FREE_DAILY_RANKED_LIMIT):
    ch = s.create_challenge("dave", FREE)
    assert ch.kind == FREE.value
    clk.advance(60); s.process_expired(); assert ch.status == VS_BOT and ch.bot_crystals == 0
    s.report_result(ch.id, "dave" if i % 2 else BOT)
d = s.account("dave")
assert s.free_ranked_remaining("dave") == 0
assert "agotado tus 10" in must_fail(s.create_challenge, "dave", FREE)
assert d.elo == ELO_START + 5 * 10 - 5 * 10 and d.balances["KUR"] == 0.0 and d.crystals == 0
assert (FREE_RANKED_ELO_WIN, FREE_RANKED_ELO_LOSS) == (10, -10)
clk.advance(86400); assert s.free_ranked_remaining("dave") == 10    # nuevo día UTC
# contra un jugador real sin NFT
ch = s.create_challenge("dave", FREE)
nft(s, "conft", 5)
assert "exclusivas de cuentas sin NFT" in must_fail(s.accept_challenge, "conft", ch.id)
s.accept_challenge("eva", ch.id); assert s.free_ranked_remaining("eva") == 9
s.report_result(ch.id, "eva")
assert s.account("eva").elo == ELO_START + 10 and s.account("dave").elo == ELO_START - 10
assert s.account("eva").balances["KUR"] == 0.0
# cancelar devuelve la oportunidad
ch = s.create_challenge("eva", FREE); assert s.free_ranked_remaining("eva") == 8
s.cancel_challenge("eva", ch.id); assert s.free_ranked_remaining("eva") == 9
print("free sin cristales: solo sin NFT, 10/día, ±10 Elo, 0 KUR, 60 s -> bot OK")

# ---- Lista pública: cada uno ve la variante que puede aceptar ----
s, clk = new()
nft(s, "c1", 2); nft(s, "c2", 1); nft(s, "yo_nft", 1)
k1 = s.create_challenge("c1", FREE); clk.advance(1); k2 = s.create_challenge("c2", FREE)
f1 = s.create_challenge("f1", FREE)
assert [c.id for c in s.visible_challenges("yo_nft")] == [k1.id, k2.id]
assert [c.id for c in s.visible_challenges("yo_sin")] == [f1.id]
assert [c.id for c in s.visible_challenges("c1")] == [k2.id]
clk.advance(60)
assert s.visible_challenges("yo_nft") == [] and s.visible_challenges("yo_sin") == []
print("lista de retos: variante según NFT, sin el propio, expirados -> bot OK")

# ============ 3. COMPETITIVO: ilimitado, KUR/KUSD, CERO bots ============
s, clk = new()
nft(s, "jon"); nft(s, "kim")
for w in ("jon", "kim"):
    s.deposit(w, Currency.KUR, 1000); s.deposit(w, Currency.KUSD, 40)
assert s.account("jon").crystals == 0                 # no hacen falta cristales
for _ in range(25):                                   # sin límite diario
    ch = s.create_challenge("jon", MONEY, 1, Currency.KUR); s.accept_challenge("kim", ch.id)
    s.report_result(ch.id, None)
assert s.account("jon").balances["KUR"] == 1000.0 and s.free_ranked_remaining("jon") is None
assert "mayor que 0" in must_fail(s.create_challenge, "jon", MONEY, 0)
assert "insuficiente" in must_fail(s.create_challenge, "jon", MONEY, 5000).lower()
ch = s.create_challenge("jon", MONEY, 50, Currency.KUR)
assert s.account("jon").balances["KUR"] == 950.0     # escrow
assert "Competitivo bloqueado" in must_fail(s.accept_challenge, "nadie", ch.id)
assert "Ya tienes un reto abierto" in must_fail(s.create_challenge, "jon", MONEY, 1)
s.accept_challenge("kim", ch.id); s.report_result(ch.id, "kim")
assert s.account("kim").balances["KUR"] == 950 + 90.0 and s.house["KUR"] == 10.0
assert s.account("kim").elo == ELO_START + MONEY_ELO_WIN and s.account("jon").elo == ELO_START + MONEY_ELO_LOSS
assert s.account("kim").crystals == 0 and s.account("kim").balances["KUSD"] == 40   # sin premio de cristales
ch = s.create_challenge("jon", MONEY, 20, Currency.KUSD); s.accept_challenge("kim", ch.id)
s.report_result(ch.id, "jon")
assert s.account("jon").balances["KUSD"] == 20 + 36.0 and s.house["KUSD"] == 4.0
# ---- 60 s sin aceptar: se RELANZA, nunca bot ----
ch = s.create_challenge("jon", MONEY, 30, Currency.KUR)
bal = s.account("jon").balances["KUR"]
first_exp = ch.expires_at
clk.advance(CHALLENGE_EXPIRY_SECONDS); s.process_expired()
assert ch.status == OPEN and ch.opponent is None and ch.relaunches == 1
assert ch.expires_at == first_exp + 60 and s.account("jon").balances["KUR"] == bal   # apuesta sigue en escrow
clk.advance(10 * 60 + 5); s.process_expired()        # muchas ventanas: sigue abierto
assert ch.status == OPEN and ch.relaunches == 11 and ch.expires_at > clk.t
assert ch in s.visible_challenges("kim", MONEY)
assert "no tiene partida" in must_fail(s.report_result, ch.id, BOT)
s.accept_challenge("kim", ch.id); assert ch.status == MATCHED and ch.opponent == "kim"
s.report_result(ch.id, "jon")
ch = s.create_challenge("jon", MONEY, 30, Currency.KUR); clk.advance(500)
s.cancel_challenge("jon", ch.id)                      # el creador puede cancelar: reembolso
assert ch.status == ChallengeStatus.CANCELLED.value
assert not any(c.kind == MONEY.value and c.opponent == BOT for c in s.challenges.values())
print("competitivo: ilimitado, sin cristales, escrow 10 % casa, 60 s -> relanza, CERO bots OK")

# ============ Cristales: runner, evolución y minimarket (compraventa = acceso completo) ============
s, clk = new()
assert s.grant_crystals("alice", "runner_objective") == 5 and s.grant_crystals("alice", "runner_monster") == 6
must_fail(s.grant_crystals, "alice", "regalo", 5); must_fail(s.grant_crystals, "alice", "runner_monster", 0)
common_id = next(t for t in range(1600) if s.rarity_of(t) == NFTRarity.COMMON)
s.set_nfts("alice", [common_id]); s.grant_crystals("alice", "runner_monster", 164)
assert s.evolve_nft("alice", common_id) == NFTRarity.RARE
assert s.evolve_nft("alice", common_id) == NFTRarity.EPIC
assert s.evolve_nft("alice", common_id) == NFTRarity.LEGENDARY and s.account("alice").crystals == 0
assert "rareza máxima" in must_fail(s.evolve_nft, "alice", common_id)
s.grant_crystals("vende", "runner_monster", 30)       # sin NFT y < 300 combates: NO puede vender
assert "Venta en el market bloqueado" in must_fail(s.list_item, "vende", "crystals", 10, 20.0)
assert not s.access_status("vende")["can_trade_market"]
s.set_battles("vende", ACCESS_BATTLES_REQUIRED)       # 300 combates -> acceso completo
assert s.access_status("vende")["can_trade_market"]
s.sync_balance("pobre", Currency.KUSD, 50)
lst = s.list_item("vende", "crystals", 10, 20.0)
assert "Compra en el market bloqueado" in must_fail(s.buy_listing, "pobre", lst.id)  # comprar también exige acceso
nft(s, "compra"); s.deposit("compra", Currency.KUSD, 50)
s.buy_listing("compra", lst.id)
assert s.account("vende").balances["KUSD"] == 18.0 and s.house["KUSD"] == 20.0 * HOUSE_FEE_PCT
inv = {}
s.register_item_type("skin", take=lambda acc, n: inv.__setitem__(acc.wallet, inv.get(acc.wallet, 0) - n),
                     give=lambda acc, n: inv.__setitem__(acc.wallet, inv.get(acc.wallet, 0) + n))
assert "Venta en el market bloqueado" in must_fail(s.list_item, "sinacceso", "skin", 1, 5.0)
print("cristales: runner, evolución 20/50/100 y minimarket OK")

# ===================== Otras validaciones =====================
s, clk = new()
ch = s.create_challenge("max", FREE)
assert "propio reto" in must_fail(s.accept_challenge, "max", ch.id)
assert "Solo quien lanzó" in must_fail(s.cancel_challenge, "ned", ch.id)
s.accept_challenge("ned", ch.id)
assert "uno de los dos" in must_fail(s.report_result, ch.id, "zed")
s.report_result(ch.id, "ned")
assert "no tiene partida" in must_fail(s.report_result, ch.id, "ned")
acc = s.account("max"); acc.elo = 5
ch = s.create_challenge("max", FREE); s.accept_challenge("ned", ch.id); s.report_result(ch.id, "ned")
assert acc.elo == 0
print("validaciones (auto-aceptar, doble liquidación, Elo mínimo 0) OK")

# ===================== Persistencia JSON (y migración) =====================
s, clk = new()
nft(s, "o", 7); s.deposit("o", Currency.KUSD, 5); s.set_battles("o", 12)
ch = s.create_challenge("o", FREE); lst = s.list_item("o", "crystals", 2, 1.5)
r = EconomyServer.from_json(s.to_json(), clock=clk)
assert r.account("o").crystals == 4 and r.account("o").battles_total == 12
assert r.challenges[ch.id].status == OPEN and r.challenges[ch.id].kind == CRY.value
assert r.create_challenge("p", FREE).id > max(ch.id, lst.id)
import json as _json
old = _json.loads(s.to_json())
old["accounts"]["o"].pop("battles_total")
old["accounts"]["o"].update(battles_month="2026-11", battles_this_month=40, milestone_reached=True)
assert EconomyServer.from_json(_json.dumps(old), clock=clk).account("o").battles_total == 300
print("persistencia JSON + migración del formato mensual OK")

# ===================== Conservación =====================
import random
rng = random.Random(7)
s, clk = new()
wallets = [f"w{i}" for i in range(10)]
deposited = {"KUR": 0.0, "KUSD": 0.0}
for i, w in enumerate(wallets):
    if i % 2:
        s.set_nfts(w, [100 + i])
    else:
        s.set_battles(w, rng.choice([0, 299, 300]))
    for cur in ("KUR", "KUSD"):
        s.sync_balance(w, Currency(cur), 200); deposited[cur] += 200
    s.grant_crystals(w, "runner_monster", 40)
withdrawn = {"KUR": 0.0, "KUSD": 0.0}
for step in range(6000):
    w = rng.choice(wallets); op = rng.random()
    try:
        if op < 0.35:
            kind = rng.choice([FREE, CRY, MONEY])
            s.create_challenge(w, kind, rng.choice([1, 2.5, 10]), rng.choice(list(Currency)))
        elif op < 0.55:
            opens = [c for c in s.challenges.values() if c.status == "open"]
            if opens: s.accept_challenge(w, rng.choice(opens).id)
        elif op < 0.7:
            live = [c for c in s.challenges.values() if c.status in ("matched", "vs_bot")]
            if live:
                c = rng.choice(live); s.report_result(c.id, rng.choice([c.creator, c.opponent, None]))
        elif op < 0.78:
            s.list_item(w, "crystals", rng.randint(1, 5), rng.choice([1, 3.33, 7]))
        elif op < 0.86:
            opens = s.open_listings()
            if opens: s.buy_listing(w, rng.choice(opens).id)
        elif op < 0.93:
            cur = rng.choice(["KUR", "KUSD"]); amt = rng.choice([1, 5])
            if rng.random() < 0.5:
                s.withdraw(w, Currency(cur), amt); withdrawn[cur] += amt
            else:
                s.deposit(w, Currency(cur), amt); deposited[cur] += amt
        else:
            clk.advance(rng.choice([5, 30, 61, 86400]))
            s.process_expired()
    except EconomyError:
        pass
assert not any(c.kind == "money" and c.opponent == BOT for c in s.challenges.values())
assert not any(c.kind == "money" and c.status == "expired" for c in s.challenges.values())
for a in s.accounts.values():
    for c in s.challenges.values():
        e = c.entries.get(a.wallet)
        if e and e.get("mode") == "crystal": assert a.has_nft
        if e and e.get("mode") == "free": assert not a.has_nft
for cur in ("KUR", "KUSD"):
    in_accounts = sum(a.balances[cur] for a in s.accounts.values())
    in_escrow = sum(e["stake"] for c in s.challenges.values() if c.currency == cur for e in c.entries.values())
    expected = deposited[cur] - withdrawn[cur] + (s.rewards_paid_kur if cur == "KUR" else 0)
    total = round(in_accounts + s.house[cur] + in_escrow, 2)
    assert abs(total - expected) < 0.05, (cur, total, expected)
crystals_total = (sum(a.crystals for a in s.accounts.values())
                  + sum(l.quantity for l in s.listings.values() if l.status == "open")
                  + sum(e["crystals"] for c in s.challenges.values() for e in c.entries.values()))
assert crystals_total == 10 * 40 - s.crystals_consumed, crystals_total
for c in s.challenges.values():
    if c.status == "settled": assert all(e["crystals"] == 0 for e in c.entries.values())
assert not any(l.status == "sold" and not s.account(l.buyer).access_unlocked for l in s.listings.values())
assert not any(not s.account(l.seller).access_unlocked for l in s.listings.values())
print(f"conservación de $KUR/$KUSD y cristales en 6000 operaciones aleatorias OK "
      f"(casa: {s.house['KUR']:.2f} KUR · {s.house['KUSD']:.2f} KUSD)")

print("\nALL ECONOMY ASSERTIONS PASSED")
