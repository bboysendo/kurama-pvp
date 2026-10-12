"""
Tipos de datos del Marketplace simulado de Kurama Run.

Mismo criterio que engine/card_schema.py: puro Python, sin dependencias
pesadas, para poder importarse desde el motor y el panel sin arrastrar
Streamlit a los tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Listing:
    """Un Fox puesto a la venta — `seller` es "me" si lo listó la wallet
    activa, o una dirección simulada de otro vendedor si viene del catálogo
    inicial generado por el motor."""

    token_id: int
    price_kur: float
    seller: str


@dataclass
class MarketItem:
    """Vista combinada de un Fox NFT para el catálogo o el inventario: su
    rareza (misma fuente que el PVP, `derive_placeholder_rarity`), si lo
    posee la wallet activa y si está a la venta."""

    token_id: int
    rarity: str  # NFTRarity.value
    image_path: str
    owned_by_wallet: bool
    listing: Optional[Listing]
