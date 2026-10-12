"""
Motor de simulación del Marketplace de Kurama Run.

Catálogo de los 1600 Fox (`../GAMENFT/`), rareza/precio simulados y una
wallet en memoria (balance $KUR + USDT, NFTs poseídos, listados propios).
Mismo espíritu que `engine/blockchain_connector.py`: NINGUNA transacción
real, todo en memoria — listo para sustituir por lectura on-chain real
cuando exista el contrato de marketplace (ver el aviso sobre la dirección
del contrato NFT en `../README.md`: la "Genesis Founders" real vive en
mint.kuramarun.com, aún sin confirmar on-chain aquí).

Reutiliza `derive_placeholder_rarity` del PVP (vía `engine/`) en vez de
reimplementar el hash, para que la rareza de un Fox sea LA MISMA en el
combate PVP y en el Marketplace — un Fox legendario en el ring es el mismo
Fox legendario en venta.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MARKETPLACE_ROOT = Path(__file__).resolve().parent
PVP_ROOT = MARKETPLACE_ROOT.parent
ENGINE_DIR = PVP_ROOT / "engine"
GAMENFT_DIR = PVP_ROOT / "GAMENFT"

if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from blockchain_connector import derive_placeholder_rarity  # noqa: E402
from card_schema import NFTRarity  # noqa: E402

from marketplace_schema import Listing, MarketItem  # noqa: E402

NFT_COLLECTION_SUPPLY = 1600  # "Genesis Founders" — igual que blockchain_connector.py

# Precio base simulado por rareza, en $KUR — PLACEHOLDER razonable (no hay
# marketplace on-chain todavía, solo el mint directo de mint.kuramarun.com a
# 10 USDT); sustituir por los precios oficiales/órdenes reales en cuanto el
# equipo despliegue el contrato de marketplace.
BASE_PRICE_KUR: dict[NFTRarity, tuple[float, float]] = {
    NFTRarity.NONE: (120.0, 280.0),
    NFTRarity.COMMON: (120.0, 280.0),
    NFTRarity.RARE: (300.0, 650.0),
    NFTRarity.EPIC: (800.0, 1800.0),
    NFTRarity.LEGENDARY: (2500.0, 6000.0),
}

# Tipo de cambio simulado $KUR -> USDT, solo para mostrar un equivalente en
# el catálogo (no hay pool de liquidez real todavía).
SIMULATED_KUR_USDT_RATE = 0.045

# Fracción de la colección (excluyendo lo que posee la wallet activa) que
# aparece "En venta" en el catálogo — determinista por token_id (no al
# azar), para que el catálogo no cambie de listados entre reruns.
LISTED_FRACTION = 0.3

# Wallet de ejemplo precargada con los mismos Fox / balance que el usuario
# ya tiene en kuramarun.com (ver capturas de referencia), para poder
# comparar el panel simulado con la cuenta real de un vistazo.
DEFAULT_WALLET_ADDRESS = "0xC9...33a6"
DEFAULT_OWNED_TOKEN_IDS = [5, 6, 43, 44]
DEFAULT_KUR_BALANCE = 5795.0
DEFAULT_USDT_BALANCE = 100.0


def _price_fraction(token_id: int, salt: str) -> float:
    """Float determinista en [0, 1) a partir de token_id + salt (mismo
    patrón que `derive_placeholder_rarity`, con una sal distinta para que
    el precio y el "¿está listado?" no estén correlados trivialmente)."""
    digest = hashlib.sha256(f"kuramarun-fox-{salt}-{token_id}".encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def simulated_price_kur(token_id: int, rarity: NFTRarity) -> float:
    lo, hi = BASE_PRICE_KUR.get(rarity, BASE_PRICE_KUR[NFTRarity.COMMON])
    frac = _price_fraction(token_id, "price")
    return round(lo + frac * (hi - lo), 2)


def is_listed_by_default(token_id: int) -> bool:
    return _price_fraction(token_id, "listed") < LISTED_FRACTION


def fox_image_path(token_id: int) -> Path:
    return GAMENFT_DIR / f"fox_{token_id}.png"


def catalog_token_ids() -> list[int]:
    return list(range(NFT_COLLECTION_SUPPLY))


@dataclass
class SimulatedWallet:
    """Wallet 100% en memoria: balance $KUR/USDT, NFTs poseídos y listados
    propios. Igual que `_SimulatedChainBackend` en blockchain_connector.py
    — ninguna acción aquí toca una red real ni firma nada."""

    address: str = DEFAULT_WALLET_ADDRESS
    kur_balance: float = DEFAULT_KUR_BALANCE
    usdt_balance: float = DEFAULT_USDT_BALANCE
    owned_token_ids: set = field(default_factory=lambda: set(DEFAULT_OWNED_TOKEN_IDS))
    my_listings: dict = field(default_factory=dict)  # token_id -> precio $KUR
    other_listings: dict = field(default_factory=dict)  # token_id -> precio $KUR (vendedores simulados)

    def __post_init__(self) -> None:
        if not self.other_listings:
            self.other_listings = {
                tid: simulated_price_kur(tid, derive_placeholder_rarity(tid))
                for tid in catalog_token_ids()
                if tid not in self.owned_token_ids and is_listed_by_default(tid)
            }

    def rarity_of(self, token_id: int) -> NFTRarity:
        return derive_placeholder_rarity(token_id)

    def listing_for(self, token_id: int) -> Optional[Listing]:
        if token_id in self.my_listings:
            return Listing(token_id, self.my_listings[token_id], seller="me")
        if token_id in self.other_listings:
            return Listing(token_id, self.other_listings[token_id], seller=f"0x{token_id:04x}…sim")
        return None

    def market_item(self, token_id: int) -> MarketItem:
        return MarketItem(
            token_id=token_id,
            rarity=self.rarity_of(token_id).value,
            image_path=str(fox_image_path(token_id)),
            owned_by_wallet=token_id in self.owned_token_ids,
            listing=self.listing_for(token_id),
        )

    # --- Acciones simuladas (sin tocar ninguna red real) ----------------- #

    def buy(self, token_id: int) -> tuple[bool, str]:
        if token_id in self.owned_token_ids:
            return False, "Ya posees este Fox."
        listing = self.listing_for(token_id)
        if listing is None:
            return False, "Este Fox no está a la venta."
        if self.kur_balance < listing.price_kur:
            return False, f"Saldo insuficiente: necesitas {listing.price_kur:,.2f} $KUR."
        self.kur_balance = round(self.kur_balance - listing.price_kur, 2)
        self.owned_token_ids.add(token_id)
        self.other_listings.pop(token_id, None)
        self.my_listings.pop(token_id, None)
        return True, f"Compra simulada: Fox #{token_id} por {listing.price_kur:,.2f} $KUR."

    def list_for_sale(self, token_id: int, price_kur: float) -> tuple[bool, str]:
        if token_id not in self.owned_token_ids:
            return False, "Solo puedes poner a la venta un Fox que posees."
        if price_kur <= 0:
            return False, "El precio debe ser mayor que 0."
        self.my_listings[token_id] = round(price_kur, 2)
        return True, f"Fox #{token_id} listado por {price_kur:,.2f} $KUR."

    def cancel_listing(self, token_id: int) -> tuple[bool, str]:
        if token_id not in self.my_listings:
            return False, "Este Fox no está listado."
        del self.my_listings[token_id]
        return True, f"Listado de Fox #{token_id} cancelado."

    def add_funds(self, kur: float = 0.0, usdt: float = 0.0) -> None:
        self.kur_balance = round(self.kur_balance + max(0.0, kur), 2)
        self.usdt_balance = round(self.usdt_balance + max(0.0, usdt), 2)

    def reset(self) -> None:
        self.address = DEFAULT_WALLET_ADDRESS
        self.kur_balance = DEFAULT_KUR_BALANCE
        self.usdt_balance = DEFAULT_USDT_BALANCE
        self.owned_token_ids = set(DEFAULT_OWNED_TOKEN_IDS)
        self.my_listings = {}
        self.other_listings = {
            tid: simulated_price_kur(tid, derive_placeholder_rarity(tid))
            for tid in catalog_token_ids()
            if tid not in self.owned_token_ids and is_listed_by_default(tid)
        }
