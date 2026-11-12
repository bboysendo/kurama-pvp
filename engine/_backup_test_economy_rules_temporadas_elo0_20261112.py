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

# ============ 2a. FREE CON CRISTALES: abierto a todos, apuesta 1 cristal ============
s, clk = new()
s.set_crystals("sinnft", 5)                           # sin NFT, aunque tenga cristales
ch = s.create_challenge("sinnft", CRY)                # sin elegir variante -> por defecto SIN cristales
assert ch.kind == FREE.value and s.account("sinnft").crystals == 5
s.cancel_challenge("sinnft", ch.id)
# ...pero SÍ puede usar sus cristales si elige jugar con cristales (12/11/2026)
s.set_free_variant("sinnft", CRY)
ch = s.create_challenge("sinnft", FREE)
assert ch.kind == CRY.value and s.account("sinnft").crystals == 4 and not s.account("sinnft").nft_status
s.cancel_challenge("sinnft", ch.id); assert s.account("sinnft").crystals == 5
nft(s, "ana")
assert "Necesitas al menos 1" in must_fail(s.create_challenge, "ana", FREE)   # NFT sin cristales
ok, why = s.can_play_free("ana"); assert not ok and "cristales" in why
s.set_crystals("ana", 3); nft(s, "bea", 3)
ch = s.create_challenge("ana", FREE)                  # NFT -> siempre con cristales
assert ch.kind == CRY.value and s.account("ana").crystals == 2   # 1 en escrow
assert s.free_ranked_remaining("ana") is None         # no usa el cupo diario
# solo otra cuenta con NFT y cristales puede aceptar
s.set_crystals("pepe", 9)                             # sin NFT
assert "otra variante" in must_fail(s.accept_challenge, "pepe", ch.id)   # eligió (por defecto) sin cristales
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
assert "otra variante" in must_fail(s.accept_challenge, "conft", ch.id)   # con NFT eligió (por defecto) con cristales
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

# ============ Lista pública de retos según selector de NFT (filtro 11/11/2026) ============
from economy_rules import is_bot_wallet
s, clk = new()
nft(s, "h_cri", crystals=3)                      # humano con NFT que apuesta cristales
r_cri = s.create_challenge("h_cri", FREE)
r_sin = s.create_challenge("h_sin", FREE)        # humano sin NFT (sin cristales)
assert r_cri.kind == CRY.value and r_sin.kind == FREE.value
# Bots: nunca publican retos ni aparecen en la lista
assert is_bot_wallet(BOT) and is_bot_wallet("bot:fox") and not is_bot_wallet("h_sin")
assert "bots no publican" in must_fail(s.create_challenge, "bot:fox", FREE)
assert "bots no publican" in must_fail(s.create_challenge, BOT, FREE)
# 1) Viewer CON NFT en Free: solo humanos con cristales, aunque la UI pida FREE
nft(s, "yo", crystals=2)
for k in (None, FREE, CRY):
    assert [c.id for c in s.visible_challenges("yo", k)] == [r_cri.id], k
# 2) Selector "Ninguno / Sin NFT": solo humanos sin cristales, aunque pida CRYSTAL
s.set_nfts("yo", [])
for k in (None, FREE, CRY):
    assert [c.id for c in s.visible_challenges("yo", k)] == [r_sin.id], k
# 3) Cambiar el selector con un reto Free propio abierto lo retira y reembolsa
s.set_nfts("yo", [7])
mio = s.create_challenge("yo", FREE)
assert mio.kind == CRY.value and s.account("yo").crystals == 2 - CRYSTAL_STAKE
gone = s.set_nfts("yo", [])                      # -> "Ninguno / Sin NFT"
assert gone is mio and mio.status == "cancelled" and s.account("yo").crystals == 2
assert mio not in s.visible_challenges("h_cri")
mio2 = s.create_challenge("yo", FREE)            # ahora sin cristales (gasta 1 de 10)
assert mio2.kind == FREE.value and s.free_ranked_remaining("yo") == FREE_DAILY_RANKED_LIMIT - 1
assert s.set_nfts("yo", [7]) is mio2 and s.account("yo").free_ranked_used == 0
assert s.set_nfts("yo", [7]) is None             # sin cambio de variante: nada que retirar
# 4) Un reto cuyo creador cambió de variante no se lista (defensa en profundidad)
s.accounts["h_sin"].nft_token_ids = [9]          # mutación directa, sin pasar por set_nfts
s.set_nfts("v_sin", [])
assert r_sin not in s.visible_challenges("v_sin")
s.accounts["h_sin"].nft_token_ids = []
# 5) El bot solo entra en segundo plano: el reto expira -> VS_BOT y sale de la lista
assert r_sin in s.visible_challenges("v_sin")
clk.advance(CHALLENGE_EXPIRY_SECONDS + 1)
assert s.visible_challenges("v_sin") == [] and r_sin.status == VS_BOT and r_sin.opponent == BOT
print("lista pública de retos: filtro NFT/sin NFT, sin bots, retirada al cambiar de selector OK")


# ============ Estatus NFT por 300 batallas + Free estricto (revisión 11/11/2026 b) ============
s, clk = new()
# Free estricto: sin NFT y < 300 batallas -> 10/día sin cristales, Elo fijo +10 / -10
s.set_crystals("novato", 3)                          # tener cristales no cambia nada sin estatus NFT
s.set_battles("novato", 50)
acc = s.account("novato")
assert not acc.nft_status and acc.free_kind == FREE and s.free_ranked_remaining("novato") == 10
e0 = acc.elo
ch = s.create_challenge("novato", CRY); assert ch.kind == FREE.value and acc.crystals == 3
clk.advance(61); s.process_expired(); s.report_result(ch.id, "novato")
assert acc.elo == e0 + 10 and acc.battles_total == 51
ch = s.create_challenge("novato", FREE); clk.advance(61); s.process_expired(); s.report_result(ch.id, BOT)
assert acc.elo == e0 and acc.crystals == 3 and acc.balances["KUR"] == 0.0
for _ in range(8):
    ch = s.create_challenge("novato", FREE); clk.advance(61); s.process_expired(); s.report_result(ch.id, BOT)
assert s.free_ranked_remaining("novato") == 0 and acc.elo == e0 - 80
assert "agotado tus 10" in must_fail(s.create_challenge, "novato", FREE)
# 300 batallas sin NFT -> estatus NFT: Free con cristales (1 fijo), Competitivo, sin cupo diario
assert s.set_battles("novato", ACCESS_BATTLES_REQUIRED) is None
assert acc.nft_status and acc.veteran and not acc.has_nft and acc.free_kind == CRY
assert s.free_ranked_remaining("novato") is None and s.access_status("novato")["nft_status"]
ch = s.create_challenge("novato", FREE)              # cupo agotado da igual: ya juega con cristales
assert ch.kind == CRY.value and acc.crystals == 3 - CRYSTAL_STAKE == 2
nft(s, "rival", 1); s.accept_challenge("rival", ch.id); s.report_result(ch.id, "novato")
assert acc.crystals == 2 and acc.elo == e0 - 80 + CRYSTAL_ELO_WIN   # 1 cristal consumido, nunca más
assert ch.status == "settled"
s.sync_balance("novato", Currency.KUR, 20); s.create_challenge("novato", MONEY, 5)   # Competitivo abierto
# Veterano visible en la lista Free con cristales; un sin-NFT < 300 no lo ve
s, clk = new()
s.set_battles("vet", 300); s.set_crystals("vet", 1)
rv = s.create_challenge("vet", FREE); assert rv.kind == CRY.value
nft(s, "conft", 2); assert rv in s.visible_challenges("conft")
assert rv not in s.visible_challenges("nuevo")
# Cruzar las 300 jugando retira el reto Free sin cristales que quedase abierto
s, clk = new()
s.set_battles("cruza", 299)
r1 = s.create_challenge("cruza", FREE)                # partida 300 en curso
clk.advance(61); s.process_expired()                  # -> vs BOT
r2 = s.create_challenge("cruza", FREE)                # otro reto sin cristales abierto mientras tanto
assert r2.kind == FREE.value and s.free_ranked_remaining("cruza") == 8
s.report_result(r1.id, "cruza")                       # llega a 300 -> estatus NFT
assert s.account("cruza").nft_status and r2.status == "cancelled"
assert s.account("cruza").free_ranked_used == 1        # la oportunidad de r2 se devolvió
# set_battles por debajo de 300 lo devuelve a Free sin cristales (y retira el reto con cristales)
s.set_crystals("cruza", 1); r3 = s.create_challenge("cruza", FREE); assert r3.kind == CRY.value
assert s.set_battles("cruza", 10) is r3 and s.account("cruza").crystals == 1
assert s.account("cruza").free_kind == FREE
print("estatus NFT por 300 batallas, Free estricto 10/día ±10 Elo, apuesta fija de 1 cristal OK")


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
        if e and e.get("mode") == "crystal": assert a.nft_status   # NFT o 300+ batallas
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


# --------------------------------------------------------------------------- #
# Lista pública SIN bots: ni BOT / bot:* ni simulados sim:* en ningún modo.
# --------------------------------------------------------------------------- #
from economy_rules import is_listable_opponent
assert not is_listable_opponent("BOT") and not is_listable_opponent("bot:alfa")
assert not is_listable_opponent("sim:frostfox_7") and not is_listable_opponent("")
assert is_listable_opponent("0xreal")
_s = EconomyServer()
for _w, _nft in (("0xreal_nft", [7]), ("sim:frostfox_7", [8]), ("0xreal_sin", []), ("sim:void", [])):
    _s.set_nfts(_w, _nft)
    _s.sync_balance(_w, Currency.KUR, 100.0)
    if _nft:
        _s.set_crystals(_w, 3)
_s.create_challenge("sim:frostfox_7", ChallengeKind.FREE)       # Free con cristales (simulado)
_s.create_challenge("0xreal_nft", ChallengeKind.FREE)           # Free con cristales (real)
_s.create_challenge("sim:void", ChallengeKind.FREE)             # Free sin cristales (simulado)
_s.create_challenge("0xreal_sin", ChallengeKind.FREE)           # Free sin cristales (real)
for _viewer, _nft in (("0xviewer_nft", [9]), ("0xviewer_sin", [])):
    _s.set_nfts(_viewer, _nft)
_s.set_nfts("sim:moneybot", [10]); _s.sync_balance("sim:moneybot", Currency.KUR, 500.0)
_s.create_challenge("sim:moneybot", ChallengeKind.MONEY, 10.0, Currency.KUR)
for _viewer in ("0xviewer_nft", "0xviewer_sin"):
    for _k in (ChallengeKind.FREE, ChallengeKind.CRYSTAL, ChallengeKind.MONEY):
        _vis = _s.visible_challenges(_viewer, _k)
        assert all(is_listable_opponent(c.creator) for c in _vis), (_viewer, _k, [c.creator for c in _vis])
assert [c.creator for c in _s.visible_challenges("0xviewer_nft", ChallengeKind.CRYSTAL)] == ["0xreal_nft"]
assert [c.creator for c in _s.visible_challenges("0xviewer_sin", ChallengeKind.FREE)] == ["0xreal_sin"]
assert _s.visible_challenges("0xviewer_nft", ChallengeKind.MONEY) == []
print("lista pública sin bots ni simulados en Competitivo / Free con y sin cristales OK")
