"""
Esquema de datos estándar para las cartas de combate PVP de Kurama Run.

Basado en PVP_Rules/PVP KURAMARUN....md: 27 cartas únicas en el pool
competitivo (18 Comunes/Estándar + 9 Raras/Especiales), coste de energía 1-2,
zonas de impacto Cabeza/Tronco/Piernas, daño real = max(0, ATK atacante - DEF
rival). Ver ese documento para la justificación completa de cada regla; este
módulo solo modela la ESTRUCTURA de una carta, no la lógica de combate (eso
vive en pvp_engine.py).

Este módulo es puro Python (sin dependencias externas) para poder importarse
tanto desde el generador de catálogo, el motor de combate, el conector
blockchain y el dashboard sin arrastrar nada pesado.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional


class Faction(str, Enum):
    """Bando visual/narrativo de la carta. No afecta al cálculo de daño (el
    combate es simétrico para todos, según el documento de reglas) — es solo
    para filtrar/temizar el catálogo y las barajas enemigas de los bots."""

    KURAMARUN = "kuramarun"       # Bando del jugador (zorros cibernéticos)
    TALON_ZHEN = "talon_zhen"     # Bestias/jefes enemigos (cartas ENEMIGOS*)
    NEUTRAL = "neutral"           # Placeholders genéricos sin bando asignado


class Element(str, Enum):
    """Etiqueta puramente estética/de lore (inferida del arte y nombre de cada
    carta: Lava Rage=fuego, Frost Rage=hielo, etc.). El documento de reglas no
    define mecánicas elementales (ventajas tipo piedra-papel-tijera); si el
    equipo quiere añadirlas más adelante, este campo ya está listo para
    servir de base."""

    FIRE = "fire"
    ICE = "ice"
    EARTH = "earth"
    AIR = "air"
    WATER = "water"
    NATURE = "nature"
    SHADOW = "shadow"
    ARCANE = "arcane"
    NONE = "none"


class CardCategory(str, Enum):
    """A qué bloque del pool de 27 cartas pertenece (sección 5.2 del
    documento). LEGENDARY queda FUERA del pool competitivo de 27 — son las
    piezas de arte más elaboradas que no encajan en ningún hueco de rareza
    definido por las reglas (ver cards_catalog.py); el equipo puede decidir
    más adelante si se usan como recompensa especial, carta de evento, etc."""

    STANDARD = "standard"   # 18 cartas comunes
    RARE = "rare"           # 9 cartas raras/especiales
    LEGENDARY = "legendary"  # Fuera del pool de 27, arte "firma" sin hueco


class Ability(str, Enum):
    """Habilidad especial de una carta Rara. Las cartas Estándar y las
    Simétricas de Poder (3/3, 2/2) no tienen habilidad, solo stats."""

    NONE = "none"
    HEAL_POTION = "heal_potion"   # Bloquea la zona y convierte el daño en HP
    PIERCING = "piercing"         # Ignora la defensa rival de esa zona
    COUNTER = "counter"           # Devuelve daño si el rival no ataca esa zona


def compute_energy_cost(atk: int, defense: int, ability: Ability) -> int:
    """Coste de energía (1 o 2, por el tope de la sección 2.2) a partir de las
    stats. Las cartas con habilidad especial (potion/piercing/counter) cuestan
    siempre 2 por su utilidad añadida; el resto cuesta 1 si ATK+DEF <= 2, y 2
    en caso contrario."""
    if ability != Ability.NONE:
        return 2
    return 1 if (atk + defense) <= 2 else 2


@dataclass
class Card:
    id: str
    name: str
    faction: Faction
    category: CardCategory
    atk: int
    defense: int
    ability: Ability = Ability.NONE
    element: Element = Element.NONE
    subtitle: Optional[str] = None          # p.ej. "(Talon Zhen)" en las cartas enemigas
    description: str = ""
    image_path: Optional[str] = None        # ruta relativa dentro de engine/assets/
    is_placeholder: bool = False
    # Solo relevante para ability=HEAL_POTION: HP que restaura al bloquear la zona.
    heal_amount: int = 0
    # Solo relevante para ability=COUNTER: daño que devuelve si el rival no ataca esa zona.
    counter_damage: int = 0
    cost_energy: int = field(default=0)  # se recalcula siempre en __post_init__

    def __post_init__(self) -> None:
        self.cost_energy = compute_energy_cost(self.atk, self.defense, self.ability)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["faction"] = self.faction.value
        d["category"] = self.category.value
        d["ability"] = self.ability.value
        d["element"] = self.element.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "Card":
        return Card(
            id=d["id"],
            name=d["name"],
            faction=Faction(d["faction"]),
            category=CardCategory(d["category"]),
            atk=d["atk"],
            defense=d["defense"],
            ability=Ability(d.get("ability", Ability.NONE.value)),
            element=Element(d.get("element", Element.NONE.value)),
            subtitle=d.get("subtitle"),
            description=d.get("description", ""),
            image_path=d.get("image_path"),
            is_placeholder=d.get("is_placeholder", False),
            heal_amount=d.get("heal_amount", 0),
            counter_damage=d.get("counter_damage", 0),
        )


# HP base en combate según posesión/rareza de NFT (sección 2.1).
class NFTRarity(str, Enum):
    NONE = "none"           # Free / sin NFT / bots
    COMMON = "common"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"


BASE_HP_BY_RARITY: dict[NFTRarity, int] = {
    NFTRarity.NONE: 50,
    NFTRarity.COMMON: 50,
    NFTRarity.RARE: 60,
    NFTRarity.EPIC: 80,
    NFTRarity.LEGENDARY: 100,
}

DECK_SIZE = 15          # Tamaño del mazo de combate por jugador (sección 5.1)
HAND_SIZE = 3            # Cartas robadas por turno
STARTING_ENERGY = 3      # Energía inicial (sección 2.2)
ENERGY_PER_TURN = 1      # Energía ganada por turno subsiguiente
MAX_ENERGY_SPEND_PER_TURN = 2  # Tope operativo de gasto por turno
TURN_TIMER_SECONDS = 20   # Temporizador por turno
TRIPARTITE_COMBO_BONUS = 3     # +3 HP de daño extra al completar las 3 zonas
CRITICAL_COMBO_DAMAGE = 20     # Daño directo de ataque pesado en combo máximo
CASHOUT_BATTLE_REQUIREMENT = 300  # Batallas PvP acumuladas para poder retirar fondos
FREE_DAILY_MATCH_LIMIT = 10        # Límite diario de partidas para usuarios Free
