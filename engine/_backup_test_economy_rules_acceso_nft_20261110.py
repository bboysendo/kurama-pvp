"""
Batería de validación de economy_rules.py — ejecutar con:
    python test_economy_rules.py
Cada bloque comprueba una regla de negocio; al final se verifica que el
dinero se conserva (nada se crea ni se pierde salvo depósitos, retiros y
premios de cristales, que quedan contabilizados aparte).
"""

from economy_rules import (
    BOT, CHALLENGE_EXPIRY_SECONDS, CRYSTAL_ELO_WIN, CRYSTAL_ELO_LOSS, CRYSTAL_WIN_KUR_REWARD, ELO_START,
    FREE_DAILY_RANKED_LIMIT, FREE_RANKED_ELO_LOSS, FREE_RANKED_ELO_WIN, HOUSE_FEE_PCT, MONEY_ELO_WIN,
    MONTHLY_BATTLES_MILESTONE, ChallengeKind, ChallengeStatus, Currency, EconomyError, EconomyServer,
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

# ===================== Cristales: obtención en el runner =====================
s, clk = new()
assert s.grant_crystals("alice", "runner_objective") == 5
assert s.grant_crystals("alice", "runner_monster") == 6
assert s.grant_crystals("alice", "runner_monster", 4) == 10
must_fail(s.grant_crystals, "alice", "regalo", 5)          # fuente no válida
must_fail(s.grant_crystals, "alice", "runner_monster", 0)
print("cristales: obtención por objetivos / monstruos OK")

# ===== Cristales (> 0): retos free ilimitados sin gastarlos; expira -> BOT =====
s, clk = new()
s.set_crystals("alice", 1)
for i in range(25):  # más que el límite diario: con cristales no hay límite
    ch = s.create_challenge("alice", FREE)            # el modo free detecta los cristales solo
    assert ch.kind == ChallengeKind.CRYSTAL.value
    clk.advance(CHALLENGE_EXPIRY_SECONDS - 1)
    assert s.challenges[ch.id].status == ChallengeStatus.OPEN.value
    clk.advance(1)
    s.process_expired()
    assert ch.status == ChallengeStatus.VS_BOT.value and ch.opponent == BOT
    s.report_result(ch.id, BOT)
assert s.account("alice").crystals == 1 and s.free_ranked_remaining("alice") is None
# se queda sin cristales -> estricta y únicamente 10 retos al día (los 25 anteriores no cuentan)
s.set_crystals("alice", 0)
assert s.free_ranked_remaining("alice") == FREE_DAILY_RANKED_LIMIT
for _ in range(FREE_DAILY_RANKED_LIMIT):
    ch = s.create_challenge("alice", FREE); assert ch.kind == ChallengeKind.FREE.value
    clk.advance(60); s.process_expired(); assert ch.status == ChallengeStatus.VS_BOT.value
    s.report_result(ch.id, BOT)
assert "Has agotado" in must_fail(s.create_challenge, "alice", FREE)
assert "No tienes cristales" in must_fail(s.create_challenge, "alice", CRY)
s.set_crystals("alice", 3)                            # recupera cristales: vuelve a ser ilimitado
s.create_challenge("alice", FREE)
# Regla global (también Competitivo): use_daily_slot comparte el mismo cupo de 10
s2, c2 = new()
assert s2.use_daily_slot("comp") is True and s2.free_ranked_remaining("comp") == 9
s2.refund_daily_slot("comp"); assert s2.free_ranked_remaining("comp") == 10
for _ in range(7): s2.use_daily_slot("comp")                             # 7 competitivas
ch = s2.create_challenge("comp", FREE); s2.accept_challenge("x", ch.id)  # 8.ª
s2.use_daily_slot("comp"); s2.use_daily_slot("comp")                    # 9.ª y 10.ª
assert s2.free_ranked_remaining("comp") == 0
assert "agotado" in must_fail(s2.use_daily_slot, "comp")
assert "agotado" in must_fail(s2.create_challenge, "comp", FREE)
s2.set_crystals("comp", 1)
assert s2.use_daily_slot("comp") is False                               # con cristales: ilimitado
c2.advance(86400); s2.set_crystals("comp", 0); assert s2.free_ranked_remaining("comp") == 10
print("cristales: >0 = ilimitado (no se gastan), 0 = 10/día, expiración 60 s -> bot OK")

# ===== Lista de retos: solo jugadores reales retando CON cristales =====
s, clk = new()
s.set_crystals("rico", 2); s.set_crystals("rosa", 1)
c_rico = s.create_challenge("rico", FREE); clk.advance(1); c_rosa = s.create_challenge("rosa", FREE)
c_pobre = s.create_challenge("pobre", FREE)           # sin cristales: no aparece
assert [c.id for c in s.visible_challenges("yo")] == [c_rico.id, c_rosa.id]
assert [c.id for c in s.visible_challenges("rico")] == [c_rosa.id]   # no ves tu propio reto
s.accept_challenge("yo", c_rico.id)                    # sin cristales: le cuesta 1 de sus 10
assert s.free_ranked_remaining("yo") == 9
assert [c.id for c in s.visible_challenges("yo")] == [c_rosa.id]
clk.advance(60)
assert s.visible_challenges("yo") == []                # expiran -> bot, desaparecen de la lista
assert c_rosa.status == c_pobre.status == ChallengeStatus.VS_BOT.value
print("lista de retos: solo retos con cristales, sin el propio, expirados -> bot OK")

# ===== Ganar con cristales: +3 $KUR y más Elo; perder: Elo de free =====
s, clk = new()
s.grant_crystals("alice", "runner_monster", 3)
s.grant_crystals("bob", "runner_monster", 3)
ch = s.create_challenge("alice", CRY)
s.accept_challenge("bob", ch.id)
assert s.account("alice").crystals == 3 and s.account("bob").crystals == 3   # no se gastan
s.report_result(ch.id, "alice")
a, b = s.account("alice"), s.account("bob")
assert a.balances["KUR"] == CRYSTAL_WIN_KUR_REWARD == 3.0
assert a.elo == ELO_START + CRYSTAL_ELO_WIN and b.elo == ELO_START + CRYSTAL_ELO_LOSS
assert CRYSTAL_ELO_WIN > FREE_RANKED_ELO_WIN
# contra el bot también cobra si gana
ch = s.create_challenge("alice", CRY); clk.advance(60); s.process_expired()
s.report_result(ch.id, "alice")
assert a.balances["KUR"] == 6.0 and s.rewards_paid_kur == 6.0
# perder contra el bot: nada de $KUR
ch = s.create_challenge("alice", CRY); clk.advance(60); s.process_expired()
s.report_result(ch.id, BOT)
assert a.balances["KUR"] == 6.0
# rival SIN cristales que acepta y gana: Elo de free y sin premio de cristales
ch = s.create_challenge("alice", CRY); s.accept_challenge("pepe", ch.id); s.report_result(ch.id, "pepe")
assert s.account("pepe").balances["KUR"] == 0.0 and s.account("pepe").elo == ELO_START + FREE_RANKED_ELO_WIN
print("cristales: victoria = +3 $KUR y +Elo (vs humano y vs bot) OK")

# =================== Cristales: evolución de rareza del NFT ===================
s, clk = new()
common_id = next(t for t in range(1600) if s.rarity_of(t) == NFTRarity.COMMON)
s.set_nfts("alice", [common_id])
s.grant_crystals("alice", "runner_monster", 170)
assert s.evolve_nft("alice", common_id) == NFTRarity.RARE and s.account("alice").crystals == 150
assert s.evolve_nft("alice", common_id) == NFTRarity.EPIC and s.account("alice").crystals == 100
assert s.evolve_nft("alice", common_id) == NFTRarity.LEGENDARY and s.account("alice").crystals == 0
assert "rareza máxima" in must_fail(s.evolve_nft, "alice", common_id)
assert "no es tuyo" in must_fail(s.evolve_nft, "alice", common_id + 1)
s.set_nfts("bob", [common_id + 1 if s.rarity_of(common_id + 1) == NFTRarity.COMMON else common_id])
s2, _ = new(); s2.set_nfts("x", [common_id]); s2.grant_crystals("x", "runner_monster", 19)
assert "insuficientes" in must_fail(s2.evolve_nft, "x", common_id) and s2.rarity_of(common_id) == NFTRarity.COMMON
print("cristales: evolución 20 / 50 / 100 OK")

# ============== Minimarket: cristales por $KUSD, 10 % para la casa ==============
s, clk = new()
s.grant_crystals("alice", "runner_monster", 30)     # cuenta free sin NFT: puede vender cristales
s.deposit("bob", Currency.KUSD, 50)
lst = s.list_item("alice", "crystals", 10, 20.0)
assert s.account("alice").crystals == 20             # quedan en escrow mientras está anunciado
assert "propio" in must_fail(s.buy_listing, "alice", lst.id)
s.buy_listing("bob", lst.id)
assert s.account("bob").crystals == 10 and s.account("bob").balances["KUSD"] == 30.0
assert s.account("alice").balances["KUSD"] == 18.0  # 20 - 10 %
assert s.house["KUSD"] == 2.0 == 20.0 * HOUSE_FEE_PCT
assert "ya no está activo" in must_fail(s.buy_listing, "bob", lst.id)
lst2 = s.list_item("alice", "crystals", 5, 100.0)
assert "insuficiente" in must_fail(s.buy_listing, "bob", lst2.id).lower()
assert s.account("alice").crystals == 15
s.cancel_listing("alice", lst2.id)
assert s.account("alice").crystals == 20
assert "Cristales insuficientes" in must_fail(s.list_item, "alice", "crystals", 99, 1.0)
# ítem genérico (gancho para Milton): exige NFT o hito 300 para vender
inv = {}
s.register_item_type("skin",
                     take=lambda acc, n: inv.__setitem__(acc.wallet, inv.get(acc.wallet, 0) - n),
                     give=lambda acc, n: inv.__setitem__(acc.wallet, inv.get(acc.wallet, 0) + n))
assert "Venta bloqueada" in must_fail(s.list_item, "alice", "skin", 1, 5.0)
s.set_nfts("carol", [1]); inv["carol"] = 1; s.deposit("bob", Currency.KUSD, 10)
l3 = s.list_item("carol", "skin", 1, 10.0); s.buy_listing("bob", l3.id)
assert inv == {"carol": 0, "bob": 1} and s.account("carol").balances["KUSD"] == 9.0 and s.house["KUSD"] == 3.0
print("minimarket: cristales por $KUSD con 10 % casa + ítems genéricos OK")

# ================== Free sin NFT: 10 retos de ranking al día ==================
s, clk = new()
for i in range(FREE_DAILY_RANKED_LIMIT):
    ch = s.create_challenge("dave", FREE)
    clk.advance(60); s.process_expired()           # reto free que expira -> bot
    assert ch.status == ChallengeStatus.VS_BOT.value
    s.report_result(ch.id, "dave" if i % 2 else BOT)
assert s.free_ranked_remaining("dave") == 0
assert "Has agotado" in must_fail(s.create_challenge, "dave", FREE)
assert s.account("dave").elo == ELO_START + 5 * FREE_RANKED_ELO_WIN + 5 * FREE_RANKED_ELO_LOSS
# cancelar devuelve la oportunidad del día
s2, c2 = new()
ch = s2.create_challenge("erin", FREE); assert s2.free_ranked_remaining("erin") == 9
s2.cancel_challenge("erin", ch.id); assert s2.free_ranked_remaining("erin") == 10
# aceptar un reto free también consume oportunidad
ch = s2.create_challenge("erin", FREE); s2.accept_challenge("fred", ch.id)
assert s2.free_ranked_remaining("fred") == 9
# nuevo día UTC: se reinicia
clk.advance(24 * 3600)
assert s.free_ranked_remaining("dave") == 10
s.create_challenge("dave", FREE)
# con NFT pero sin cristales: TAMBIÉN 10 al día (el límite solo depende de los cristales)
s.set_nfts("gina", [5]); assert s.free_ranked_remaining("gina") == 10
for _ in range(10):
    ch = s.create_challenge("gina", FREE); clk.advance(60); s.process_expired(); s.report_result(ch.id, BOT)
assert "Has agotado" in must_fail(s.create_challenge, "gina", FREE)
print("free sin cristales: límite 10/día (con o sin NFT), reinicio diario, cancelar devuelve OK")

# ============ Hito de 300 batallas al mes: retiro, mercado y dinero ============
s, clk = new()
clk.t = 1_793_491_200.0  # 2026-11-01 00:00 UTC
s.deposit("hugo", Currency.KUR, 100)
assert "Retiro bloqueado" in must_fail(s.withdraw, "hugo", Currency.KUR, 10)
assert "solo para cuentas con NFT" in must_fail(s.create_challenge, "hugo", MONEY, 5)
day = 0
while not s.account("hugo").milestone_reached:
    for _ in range(FREE_DAILY_RANKED_LIMIT):
        ch = s.create_challenge("hugo", FREE); clk.advance(60); s.process_expired(); s.report_result(ch.id, BOT)
    day += 1
    clk.t = 1_793_491_200.0 + day * 86400
assert day == 30 and s.account("hugo").battles_this_month == MONTHLY_BATTLES_MILESTONE
s.withdraw("hugo", Currency.KUR, 10)
assert s.account("hugo").balances["KUR"] == 90.0
ch = s.create_challenge("hugo", MONEY, 5); s.cancel_challenge("hugo", ch.id)
# el contador mensual se reinicia al cambiar de mes, pero el desbloqueo se mantiene
clk.t = 1_796_083_200.0  # 2026-12-01
ch = s.create_challenge("hugo", FREE); clk.advance(60); s.process_expired(); s.report_result(ch.id, BOT)
assert s.account("hugo").battles_this_month == 1 and s.account("hugo").money_unlocked
# 299 en un mes + 1 el mes siguiente NO desbloquea
s3, c3 = new(); c3.t = 1_793_491_200.0
s3.set_nfts("ivy", [9])  # con NFT (para jugar sin límite) se fuerza el conteo directo
acc = s3.account("ivy"); acc.battles_month, acc.battles_this_month = "2026-11", 299
c3.t = 1_796_083_200.0; s3._count_battle(acc)
assert not acc.milestone_reached and acc.battles_this_month == 1
print("hito 300 batallas/mes: desbloquea retiro, mercado y retos por dinero OK")

# ================== Retos por dinero ($KUR y $KUSD) ==================
s, clk = new()
s.set_nfts("jon", [11]); s.set_nfts("kim", [12])
s.deposit("jon", Currency.KUR, 100); s.deposit("kim", Currency.KUR, 100)
s.deposit("jon", Currency.KUSD, 40); s.deposit("kim", Currency.KUSD, 40)
assert "mayor que 0" in must_fail(s.create_challenge, "jon", MONEY, 0)
assert "insuficiente" in must_fail(s.create_challenge, "jon", MONEY, 500).lower()
# sin NFT ni hito no puede aceptar
ch = s.create_challenge("jon", MONEY, 50, Currency.KUR)
assert s.account("jon").balances["KUR"] == 50.0        # en escrow
assert "solo para cuentas con NFT" in must_fail(s.accept_challenge, "nofree", ch.id)
assert "Ya tienes un reto abierto" in must_fail(s.create_challenge, "jon", MONEY, 1)
s.accept_challenge("kim", ch.id)
s.report_result(ch.id, "kim")
assert s.account("kim").balances["KUR"] == 50 + 90.0     # bote 100 - 10 %
assert s.account("jon").balances["KUR"] == 50.0
assert s.house["KUR"] == 10.0
assert s.account("kim").elo == ELO_START + MONEY_ELO_WIN
# en $KUSD
ch = s.create_challenge("jon", MONEY, 20, Currency.KUSD); s.accept_challenge("kim", ch.id)
s.report_result(ch.id, "jon")
assert s.account("jon").balances["KUSD"] == 20 + 36.0 and s.house["KUSD"] == 4.0
# empate: se devuelve todo, sin comisión
ch = s.create_challenge("jon", MONEY, 10, Currency.KUR); s.accept_challenge("kim", ch.id)
s.report_result(ch.id, None)
assert s.account("jon").balances["KUR"] == 50.0 and s.account("kim").balances["KUR"] == 140.0 and s.house["KUR"] == 10.0
# expira sin aceptar: cancelado y reembolsado, NUNCA contra bot
ch = s.create_challenge("jon", MONEY, 30, Currency.KUR)
assert s.account("jon").balances["KUR"] == 20.0
clk.advance(CHALLENGE_EXPIRY_SECONDS)
s.process_expired()
assert ch.status == ChallengeStatus.EXPIRED.value and ch.opponent is None
assert s.account("jon").balances["KUR"] == 50.0
assert "no tiene partida" in must_fail(s.report_result, ch.id, "jon")
assert "ya no está disponible" in must_fail(s.accept_challenge, "kim", ch.id)
# aceptar justo al expirar tampoco vale (el servidor procesa la expiración antes)
ch = s.create_challenge("jon", MONEY, 5, Currency.KUR); clk.advance(60)
assert "ya no está disponible" in must_fail(s.accept_challenge, "kim", ch.id)
assert s.account("jon").balances["KUR"] == 50.0
# usuario free con el hito también puede
s.account("lia").milestone_reached = True; s.deposit("lia", Currency.KUR, 10)
ch = s.create_challenge("lia", MONEY, 10); s.cancel_challenge("lia", ch.id)
assert s.account("lia").balances["KUR"] == 10.0
print("retos por dinero: solo NFT/hito, escrow, 10 % casa, empate, expira -> cancelado sin bot OK")

# ===================== Otras validaciones =====================
s, clk = new()
ch = s.create_challenge("max", FREE)
assert "propio reto" in must_fail(s.accept_challenge, "max", ch.id)
assert "Solo quien lanzó" in must_fail(s.cancel_challenge, "ned", ch.id)
s.accept_challenge("ned", ch.id)
assert "uno de los dos" in must_fail(s.report_result, ch.id, "zed")
s.report_result(ch.id, "ned")
assert "no tiene partida" in must_fail(s.report_result, ch.id, "ned")   # no se liquida dos veces
acc = s.account("max"); acc.elo = 5
ch = s.create_challenge("max", FREE); s.accept_challenge("ned", ch.id); s.report_result(ch.id, "ned")
assert acc.elo == 0                                                     # nunca baja de 0
print("validaciones (auto-aceptar, doble liquidación, Elo mínimo 0) OK")

# ===================== Persistencia JSON =====================
s, clk = new()
s.grant_crystals("o", "runner_monster", 7); s.set_nfts("o", [3]); s.deposit("o", Currency.KUSD, 5)
ch = s.create_challenge("o", CRY); lst = s.list_item("o", "crystals", 2, 1.5)
r = EconomyServer.from_json(s.to_json(), clock=clk)
assert r.account("o").crystals == 5 and r.challenges[ch.id].status == "open" and r.listings[lst.id].quantity == 2
assert r.create_challenge("p", FREE).id > max(ch.id, lst.id)
print("persistencia JSON OK")

# ===================== Conservación del dinero =====================
# Simulación aleatoria: lo que hay en cuentas + casa + escrow == depósitos - retiros + premios de cristales.
import random
rng = random.Random(7)
s, clk = new()
wallets = [f"w{i}" for i in range(8)]
deposited = {"KUR": 0.0, "KUSD": 0.0}
for i, w in enumerate(wallets):
    if i % 2:
        s.set_nfts(w, [100 + i])
    for cur in ("KUR", "KUSD"):
        s.deposit(w, Currency(cur), 200); deposited[cur] += 200
    s.grant_crystals(w, "runner_monster", 40)
withdrawn = {"KUR": 0.0, "KUSD": 0.0}
for step in range(4000):
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
        elif op < 0.8:
            s.list_item(w, "crystals", rng.randint(1, 5), rng.choice([1, 3.33, 7]))
        elif op < 0.9:
            opens = s.open_listings()
            if opens: s.buy_listing(w, rng.choice(opens).id)
        elif op < 0.95:
            cur = rng.choice(["KUR", "KUSD"]); amt = rng.choice([1, 5])
            s.withdraw(w, Currency(cur), amt); withdrawn[cur] += amt
        else:
            clk.advance(rng.choice([5, 30, 61]))
            s.process_expired()
    except EconomyError:
        pass
for cur in ("KUR", "KUSD"):
    in_accounts = sum(a.balances[cur] for a in s.accounts.values())
    in_escrow = sum(e["stake"] for c in s.challenges.values() if c.currency == cur for e in c.entries.values())
    expected = deposited[cur] - withdrawn[cur] + (s.rewards_paid_kur if cur == "KUR" else 0)
    total = round(in_accounts + s.house[cur] + in_escrow, 2)
    assert abs(total - expected) < 0.05, (cur, total, expected)
    assert all(a.balances[cur] >= 0 for a in s.accounts.values())
crystals_total = sum(a.crystals for a in s.accounts.values()) + sum(
    l.quantity for l in s.listings.values() if l.status == "open") + sum(
    e["crystals"] for c in s.challenges.values() if c.status == "open" for e in c.entries.values())
spent = sum(e["crystals"] for c in s.challenges.values() if c.status not in ("open", "cancelled")
            for e in c.entries.values())
assert crystals_total + spent == 8 * 40, (crystals_total, spent)
print(f"conservación de $KUR/$KUSD y cristales en 4000 operaciones aleatorias OK "
      f"(casa: {s.house['KUR']:.2f} KUR · {s.house['KUSD']:.2f} KUSD)")

print("\nALL ECONOMY ASSERTIONS PASSED")
