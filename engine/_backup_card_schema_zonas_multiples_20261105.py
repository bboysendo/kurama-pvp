"""
Esquema de datos estándar para las cartas de combate PVP de Kurama Run.

Basado en PVP_Rules/PVP KURAMARUN....md, con la revisión del creador del
02/11/2026: unicidad total — 16 cartas BASE, una por cada combinación ATK/DEF
de 0 a 3 (ataque 3/3..0/0 y su inverso defensivo), sin repeticiones. Revisión
del 03/11/2026: las 16 base cuestan SIEMPRE 1 de energía y se añaden 7 cartas
ESPECIALES de coste 2 (4 de Golpe Limpio y 3 femeninas de Guardia Vital, ver
Ability). Una especial puede compartir ATK/DEF con una base: la unicidad se
mide por (ATK, DEF, habilidad). Coste de energía 1-2, zonas de impacto
Cabeza/Tronco/Piernas, daño real = max(0, ATK atacante - DEF rival). Ver ese documento para la justificación completa de cada regla; este
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
    """Bloque del pool al que pertenece la carta.

    - STANDARD: las 16 cartas del juego (una por combinación ATK/DEF).
    - SPECIAL / RARE / LEGENDARY: se conservan solo para poder leer
      catálogos antiguos; el catálogo actual no las usa."""

    STANDARD = "standard"
    SPECIAL = "special"
    RARE = "rare"
    LEGENDARY = "legendary"


class Ability(str, Enum):
    """Habilidad especial. Las 16 cartas base no llevan ninguna (solo stats);
    las 7 especiales llevan CLEAN_STRIKE o GUARD_ENERGY. El resto se conserva
    por si se asigna a alguna carta más adelante."""

    NONE = "none"
    # Especiales de ATAQUE (coste 2): si su ATK entra entero porque el rival NO tiene defensa en esa
    # zona, el golpe hace CLEAN_STRIKE_BASE_DAMAGE + CLEAN_STRIKE_BONUS (10 + 3 = 13) en vez de su ATK.
    # Si el rival la bloquea (cualquier DEF en esa zona), el combo se anula: daño normal ATK − DEF.
    CLEAN_STRIKE = "clean_strike"
    # Especiales FEMENINAS de Defensa/Vida (coste 2): si el rival ataca su zona de defensa y el bloqueo
    # es TOTAL (0 daño en esa zona), el jugador recupera `energy_bonus` de energía (sin pasar de MAX_ENERGY).
    GUARD_ENERGY = "guard_energy"
    LIFESTEAL = "lifesteal"       # Cura al atacante según el daño que hace en su zona
    COMBO_STRIKE = "combo_strike"  # +daño si ese turno se dañan 2 o 3 zonas a la vez
    HEAL_POTION = "heal_potion"   # (sin cartas) Bloquea la zona y convierte el daño en HP
    PIERCING = "piercing"         # (sin cartas) Ignora la defensa rival de esa zona
    COUNTER = "counter"           # (sin cartas) Devuelve daño si el rival no ataca esa zona


BASE_CARD_COST = 1      # Las 16 cartas base: coste FIJO de 1 (revisión del 03/11/2026)
SPECIAL_CARD_COST = 2   # Las 7 especiales (y las habilidades antiguas poción/perforante/counter)
SPECIAL_COST_ABILITIES = frozenset({
    Ability.CLEAN_STRIKE, Ability.GUARD_ENERGY, Ability.HEAL_POTION, Ability.PIERCING, Ability.COUNTER,
})


def compute_energy_cost(atk: int, defense: int, ability: Ability) -> int:
    """Coste de energía: 2 para las especiales (SPECIAL_COST_ABILITIES) y 1 FIJO para todas las
    demás, sean cuales sean sus stats (antes dependía de ATK+DEF). Robo de Vida / Combo Simultáneo
    (sin cartas hoy) cuestan 1 para poder jugarse junto a otras y dañar 2-3 zonas en el mismo turno."""
    if ability in SPECIAL_COST_ABILITIES:
        return SPECIAL_CARD_COST
    return BASE_CARD_COST


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
    image_path: Optional[str] = None        # ruta relativa a la raíz del proyecto PVP
    is_placeholder: bool = False
    # ability=LIFESTEAL: tope de HP que recupera el atacante por turno con esta carta.
    # ability=HEAL_POTION: HP mínimo que restaura al bloquear la zona.
    heal_amount: int = 0
    # Solo relevante para ability=COUNTER: daño que devuelve si el rival no ataca esa zona.
    counter_damage: int = 0
    # Solo relevante para ability=GUARD_ENERGY: energía que recupera al bloquear del todo un ataque.
    energy_bonus: int = 0
    cost_energy: int = field(default=0)  # se recalcula siempre en __post_init__
    # Token del NFT (GAMENFT/fox_<id>.png) usado como ilustración, si aplica.
    nft_token_id: Optional[int] = None

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
            energy_bonus=d.get("energy_bonus", 0),
            nft_token_id=d.get("nft_token_id"),
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

# Tamaño del mazo: las 16 cartas base + las 7 especiales, todas en cada partida (antes 16).
DECK_SIZE = 23
HAND_SIZE = 3            # Cartas robadas por turno
STARTING_ENERGY = 3      # Energía inicial (sección 2.2)
MAX_ENERGY = 3           # Tope ABSOLUTO del acumulador de energía: nunca puede superar 3
ENERGY_PER_TURN = 1      # Energía ganada por turno subsiguiente (sin pasar de MAX_ENERGY)
# Gasto máximo por turno = el tope de energía (3): permite jugar hasta 3 cartas
# de coste 1 en las 3 zonas a la vez (necesario para el Combo Simultáneo de 3
# zonas). Antes era 2 (sección 2.2 del documento original).
MAX_ENERGY_SPEND_PER_TURN = MAX_ENERGY
TURN_TIMER_SECONDS = 30   # Temporizador por turno (antes 20s: poco para elegir varias cartas y sus zonas)
INACTIVITY_LIMIT = 3       # Turnos SEGUIDOS sin jugar ninguna carta => derrota por inactividad
MATCHMAKING_SECONDS = 60  # Búsqueda de rival humano antes de asignar un BOT
TRIPARTITE_COMBO_BONUS = 3     # +3 HP de daño extra al completar las 3 zonas
# Cartas especiales COMBO_STRIKE: daño extra (una vez por turno) si ese mismo
# turno el jugador daña 2 o 3 zonas distintas del rival a la vez.
SIMULTANEOUS_COMBO_BONUS = {2: 2, 3: 4}
# Especiales de Golpe Limpio (CLEAN_STRIKE): daño de un golpe que entra sin ninguna defensa.
CLEAN_STRIKE_BASE_DAMAGE = 10
CLEAN_STRIKE_BONUS = 3          # «plus de regalo»: 10 + 3 = 13 en total
CRITICAL_COMBO_DAMAGE = 20     # Daño directo de ataque pesado en combo máximo
CASHOUT_BATTLE_REQUIREMENT = 300  # Batallas PvP acumuladas para poder retirar fondos
FREE_DAILY_MATCH_LIMIT = 10        # Límite diario de partidas para usuarios Free
