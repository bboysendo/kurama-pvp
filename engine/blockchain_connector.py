"""
Integración Web3 / BSC para el PVP de Kurama Run.

Lee el NFT ERC-721 (0x8331e28e7818d72ee58dfcabd7f8b71afe143f06, colección
"Genesis Founders", 1600 fox, BSC chain id 56) y el token ERC-20 $KUR
(0x0Eb0FF4dBFc7D44b9Df21cE65707A042bFbd42DD) para: verificar propiedad de
NFTs, derivar el HP base / buffs de combate según rareza, y calcular las
comisiones/recompensas en KUR de cada partida (sección 2.1 y 4 de
PVP_Rules).

Diseño desacoplado a propósito:
- Si `web3` no está instalado, o el RPC no responde, o se pide `simulate=True`
  explícitamente, `BlockchainConnector` cae automáticamente a un backend de
  SIMULACIÓN en memoria con la misma interfaz pública — así el dashboard y el
  motor de combate funcionan 100% en local sin wallet real ni fondos reales,
  tal y como pidió el usuario para probar antes de integrar esto en el
  proyecto principal.
- No se ejecuta NINGUNA transacción real (ni apuestas ni recompensas) desde
  este prototipo: `compute_match_fee` / `compute_match_reward` solo
  CALCULAN los importes; moverlos on-chain es responsabilidad del backend
  con custodia de claves que ya tenga el equipo (igual que hace
  kasman/worker/index.ts con el pool de KAS — nunca claves en el propio
  conector de lectura).
- La rareza real de cada NFT depende del tokenURI/metadata on-chain, que no
  estaba disponible en esta carpeta (ver Kuramarun_Whitepaper.htm: solo trae
  el cascarón HTML de una app Next.js, sin contenido — no hay tabla de
  rareza). `derive_placeholder_rarity()` genera una distribución determinista
  y balanceada a partir del token_id mientras no se conecte la fuente real
  (contrato o API de metadata); sustitúyela por una llamada real en cuanto
  el equipo exponga los traits.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Optional

from card_schema import BASE_HP_BY_RARITY, Card, NFTRarity

BSC_CHAIN_ID = 56
BSC_RPC_URL = "https://bsc-dataseed.binance.org/"

NFT_CONTRACT_ADDRESS = "0x8331e28e7818d72ee58dfcabd7f8b71afe143f06"
KUR_TOKEN_ADDRESS = "0x0Eb0FF4dBFc7D44b9Df21cE65707A042bFbd42DD"
NFT_COLLECTION_SUPPLY = 1600  # "Genesis Founders" — confirmado en mint.kuramarun.com

# ABIs mínimas (solo las funciones que este conector necesita leer).
ERC721_ABI = [
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "tokenId", "type": "uint256"}], "name": "ownerOf",
     "outputs": [{"name": "", "type": "address"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "tokenId", "type": "uint256"}], "name": "tokenURI",
     "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "index", "type": "uint256"}],
     "name": "tokenOfOwnerByIndex", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]
ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol",
     "outputs": [{"name": "", "type": "string"}], "type": "function"},
]

# Buffs de combate extra por rareza — EXTRA a lo que pide el usuario: el
# documento de reglas solo fija el HP base por rareza (BASE_HP_BY_RARITY en
# card_schema.py); no define un multiplicador de ATK/DEF de mazo. Este
# porcentaje es un placeholder razonable para que "aplicar los
# modificadores/buffs de los NFTs en las estadísticas del mazo" tenga un
# efecto visible ya en el prototipo — el equipo de diseño debe confirmarlo o
# sustituirlo por los valores oficiales.
DECK_STAT_BUFF_PCT: dict[NFTRarity, float] = {
    NFTRarity.NONE: 0.0,
    NFTRarity.COMMON: 0.0,
    NFTRarity.RARE: 0.05,
    NFTRarity.EPIC: 0.10,
    NFTRarity.LEGENDARY: 0.15,
}

# Recompensas de la sección 4 (SISTEMA DE RANKING) de PVP_Rules, tal cual.
FREE_MODE_ELO_WIN = 10
FREE_MODE_ELO_LOSS = -10
FREE_MODE_KUR_REWARD = 0.0
COMPETITIVE_MODE_ELO_WIN = 25
COMPETITIVE_MODE_ELO_LOSS = -25
# Recompensa de victoria en modo Competitivo/Cristal: rango pedido por el
# equipo de pruebas (antes era un fijo de 10 KUR).
COMPETITIVE_MODE_KUR_REWARD_MIN = 3.0
COMPETITIVE_MODE_KUR_REWARD_MAX = 5.0
# Comisión aplicada a la apuesta/entrada en KUR antes de la partida (no fijada
# explícitamente por las reglas; placeholder razonable tipo "rake" de sala).
MATCH_FEE_PCT = 0.05
# Comisión de la sala sobre el pozo total de un Reto 1v1 directo (sección
# "Retos Directos" pedida por el equipo de pruebas).
CHALLENGE_FEE_PCT = 0.10


def roll_competitive_kur_reward() -> float:
    """Recompensa real (aleatoria) que se acredita al ganador de una partida
    en modo Competitivo/Cristal, dentro del rango 3-5 $KUR pedido."""
    return round(random.uniform(COMPETITIVE_MODE_KUR_REWARD_MIN, COMPETITIVE_MODE_KUR_REWARD_MAX), 2)


@dataclass
class ChallengePayout:
    stake_kur: float   # lo que aporta cada jugador
    pool_kur: float     # pozo total (ambas aportaciones)
    fee_kur: float       # comisión de la sala (10% del pozo)
    payout_kur: float   # lo que se lleva el ganador


def compute_challenge_payout(stake_kur: float) -> ChallengePayout:
    """Economía de un Reto 1v1: cada jugador aporta `stake_kur`, la sala se
    queda CHALLENGE_FEE_PCT del pozo total y el ganador se lleva el resto."""
    pool = stake_kur * 2
    fee = round(pool * CHALLENGE_FEE_PCT, 6)
    return ChallengePayout(stake_kur=stake_kur, pool_kur=pool, fee_kur=fee, payout_kur=round(pool - fee, 6))


def derive_placeholder_rarity(token_id: int, supply: int = NFT_COLLECTION_SUPPLY) -> NFTRarity:
    """Distribución determinista ~60% Común / 25% Raro / 12% Épico / 3%
    Legendario, estable para un mismo token_id (hash, no random) para que no
    cambie entre ejecuciones. SOLO placeholder — sustituir por la rareza real
    en cuanto el contrato/API de metadata esté disponible."""
    digest = hashlib.sha256(f"kuramarun-fox-{token_id}".encode()).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF  # float determinista en [0, 1)
    if bucket < 0.60:
        return NFTRarity.COMMON
    if bucket < 0.85:
        return NFTRarity.RARE
    if bucket < 0.97:
        return NFTRarity.EPIC
    return NFTRarity.LEGENDARY


def apply_nft_buffs_to_deck(deck: list[Card], rarity: NFTRarity) -> list[Card]:
    """Devuelve una copia del mazo con ATK/DEF escalados por el buff de
    rareza (ver DECK_STAT_BUFF_PCT). No muta las cartas originales del
    catálogo — el motor de combate siempre trabaja sobre estas copias
    "equipadas", nunca sobre el catálogo base."""
    import copy

    pct = DECK_STAT_BUFF_PCT.get(rarity, 0.0)
    buffed = []
    for card in deck:
        c = copy.deepcopy(card)
        if pct:
            c.atk = round(c.atk * (1 + pct))
            c.defense = round(c.defense * (1 + pct))
        buffed.append(c)
    return buffed


def compute_base_hp(rarity: NFTRarity) -> int:
    return BASE_HP_BY_RARITY[rarity]


@dataclass
class MatchEconomy:
    mode: str              # "free" | "competitive"
    stake_kur: float
    fee_kur: float
    pool_kur: float         # lo que realmente queda en juego tras la comisión
    win_elo: int
    loss_elo: int
    win_kur_reward_min: float
    win_kur_reward_max: float


def compute_match_economy(mode: str, stake_kur: float = 0.0) -> MatchEconomy:
    """Calcula comisión/apuesta antes de la partida y el rango de recompensa
    de victoria, según el modo (sección 4 de PVP_Rules). No mueve fondos — el
    backend con custodia de claves es quien ejecuta la transferencia real
    usando estos importes (la recompensa real y aleatoria se decide al
    terminar la partida con roll_competitive_kur_reward())."""
    if mode not in ("free", "competitive"):
        raise ValueError("mode debe ser 'free' o 'competitive'")

    if mode == "free":
        return MatchEconomy(
            mode=mode, stake_kur=0.0, fee_kur=0.0, pool_kur=0.0,
            win_elo=FREE_MODE_ELO_WIN, loss_elo=FREE_MODE_ELO_LOSS,
            win_kur_reward_min=FREE_MODE_KUR_REWARD, win_kur_reward_max=FREE_MODE_KUR_REWARD,
        )

    fee = round(stake_kur * MATCH_FEE_PCT, 6)
    return MatchEconomy(
        mode=mode, stake_kur=stake_kur, fee_kur=fee, pool_kur=round(stake_kur - fee, 6),
        win_elo=COMPETITIVE_MODE_ELO_WIN, loss_elo=COMPETITIVE_MODE_ELO_LOSS,
        win_kur_reward_min=COMPETITIVE_MODE_KUR_REWARD_MIN, win_kur_reward_max=COMPETITIVE_MODE_KUR_REWARD_MAX,
    )


# --------------------------------------------------------------------------- #
# Backend real (Web3) vs. backend de simulación — misma interfaz pública.
# --------------------------------------------------------------------------- #

class _RealChainBackend:
    def __init__(self, rpc_url: str):
        from web3 import Web3  # import perezoso: solo si de verdad hay web3 instalado

        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 8}))
        self.nft = self.w3.eth.contract(address=Web3.to_checksum_address(NFT_CONTRACT_ADDRESS), abi=ERC721_ABI)
        self.kur = self.w3.eth.contract(address=Web3.to_checksum_address(KUR_TOKEN_ADDRESS), abi=ERC20_ABI)
        if not self.w3.is_connected():
            raise ConnectionError(f"No se pudo conectar al RPC de BSC: {rpc_url}")

    def nft_balance(self, wallet_address: str) -> int:
        from web3 import Web3

        return self.nft.functions.balanceOf(Web3.to_checksum_address(wallet_address)).call()

    def owns_token(self, wallet_address: str, token_id: int) -> bool:
        from web3 import Web3

        try:
            owner = self.nft.functions.ownerOf(token_id).call()
        except Exception:
            return False
        return owner.lower() == wallet_address.lower()

    def owned_token_ids(self, wallet_address: str) -> list[int]:
        """Solo funciona si el contrato implementa ERC721Enumerable; si no,
        devuelve [] (usar `owns_token` con un token_id concreto en su lugar,
        o un indexador externo tipo BscScan API más adelante)."""
        from web3 import Web3

        balance = self.nft_balance(wallet_address)
        ids = []
        checksum = Web3.to_checksum_address(wallet_address)
        for i in range(balance):
            try:
                ids.append(self.nft.functions.tokenOfOwnerByIndex(checksum, i).call())
            except Exception:
                return []  # contrato no enumerable: no hay forma barata de listar
        return ids

    def kur_balance(self, wallet_address: str) -> float:
        from web3 import Web3

        checksum = Web3.to_checksum_address(wallet_address)
        raw = self.kur.functions.balanceOf(checksum).call()
        decimals = self.kur.functions.decimals().call()
        return raw / (10 ** decimals)


class _SimulatedChainBackend:
    """Wallets/balances/NFTs falsos en memoria, para desarrollar y probar el
    PVP sin tocar la BSC real. `seed_wallet` deja precargar una wallet de
    prueba con NFTs y KUR para los runs del dashboard."""

    def __init__(self):
        self._nft_holdings: dict[str, set[int]] = {}
        self._kur_balances: dict[str, float] = {}

    def seed_wallet(self, wallet_address: str, token_ids: list[int], kur_balance: float) -> None:
        wallet_address = wallet_address.lower()
        self._nft_holdings[wallet_address] = set(token_ids)
        self._kur_balances[wallet_address] = kur_balance

    def nft_balance(self, wallet_address: str) -> int:
        return len(self._nft_holdings.get(wallet_address.lower(), set()))

    def owns_token(self, wallet_address: str, token_id: int) -> bool:
        return token_id in self._nft_holdings.get(wallet_address.lower(), set())

    def owned_token_ids(self, wallet_address: str) -> list[int]:
        return sorted(self._nft_holdings.get(wallet_address.lower(), set()))

    def kur_balance(self, wallet_address: str) -> float:
        return self._kur_balances.get(wallet_address.lower(), 0.0)


class BlockchainConnector:
    """Punto único de entrada para el resto del módulo PVP. Intenta un
    backend real de Web3 contra BSC; si falla por lo que sea (web3 no
    instalado, sin red, RPC caído) o se pide `simulate=True`, cae a
    simulación en memoria sin que el resto del código tenga que saberlo."""

    def __init__(self, rpc_url: str = BSC_RPC_URL, simulate: bool = False):
        self.rpc_url = rpc_url
        self.is_simulated = simulate
        self._backend = None
        if not simulate:
            try:
                self._backend = _RealChainBackend(rpc_url)
            except Exception as exc:  # web3 ausente, RPC caído, timeout, etc.
                self._connection_error = str(exc)
                self.is_simulated = True
        if self.is_simulated:
            self._backend = _SimulatedChainBackend()

    @property
    def simulated_backend(self) -> Optional[_SimulatedChainBackend]:
        """Acceso directo al backend simulado (para `seed_wallet` en tests o
        en el dashboard); None si está corriendo contra la BSC real."""
        return self._backend if self.is_simulated else None

    def nft_balance(self, wallet_address: str) -> int:
        return self._backend.nft_balance(wallet_address)

    def owns_token(self, wallet_address: str, token_id: int) -> bool:
        return self._backend.owns_token(wallet_address, token_id)

    def owned_token_ids(self, wallet_address: str) -> list[int]:
        return self._backend.owned_token_ids(wallet_address)

    def kur_balance(self, wallet_address: str) -> float:
        return self._backend.kur_balance(wallet_address)

    def best_rarity_for_wallet(self, wallet_address: str) -> NFTRarity:
        """Rareza más alta entre todos los NFT que posee la wallet (determina
        el HP base de combate, sección 2.1). NFTRarity.NONE si no tiene
        ninguno."""
        token_ids = self.owned_token_ids(wallet_address)
        if not token_ids:
            return NFTRarity.NONE
        order = [NFTRarity.LEGENDARY, NFTRarity.EPIC, NFTRarity.RARE, NFTRarity.COMMON]
        rarities = {derive_placeholder_rarity(tid) for tid in token_ids}
        for tier in order:
            if tier in rarities:
                return tier
        return NFTRarity.COMMON
