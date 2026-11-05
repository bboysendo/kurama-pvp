"""
Esquema de datos estándar para las cartas de combate PVP de Kurama Run.

Basado en PVP_Rules/PVP KURAMARUN....md, con la revisión del creador del
02/11/2026: unicidad total — 16 cartas BASE, una por cada combinación ATK/DEF
de 0 a 3 (ataque 3/3..0/0 y su inverso defensivo), sin repeticiones. Revisión
del 03/11/2026: las 16 base cuestan SIEMPRE 1 de energía y se añaden 7 cartas
ESPECIALES de coste 2. Una especial puede compartir ATK/DEF con una base: la
unicidad se mide por (ATK, DEF, habilidad).

Reglas DEFINITIVAS de Sendo (05/11/2026) — sistema de ZONAS MÚLTIPLES:
- ATK y DEF ya no son "puntos de daño": son NÚMERO DE ZONAS. Una carta de
  ATK N marca hasta N zonas de ataque DISTINTAS (nunca 2 ataques de la misma
  carta en la misma zona); igual con DEF y las zonas de defensa. Se pueden
  dejar zonas sin usar si sobran.
- Cada ataque que entra en una zona quita ZONE_HIT_DAMAGE (5). Cada defensa
  del rival en esa zona anula UN ataque.
- Combo Tripartito: cada zona del rival donde entra un ataque queda AFECTADA
  (se acumula entre turnos); al completar las 3 zonas se suma
  +TRIPARTITE_COMBO_BONUS (3) UNA SOLA VEZ por partida.
- Especiales de ataque (TRIPARTITE_STRIKE): si TODOS sus ataques entran (usa
  todas sus zonas y ninguno es bloqueado) suman +SPECIAL_COMBO_BONUS (3),
  repetible cada vez: 3/0 -> 18, 2/0 -> 13, 1/0 -> 8 (5 + 3). Se acumula
  con el +3 del Combo Tripartito si coinciden.
- Curadoras (HEALER) 0/3, 0/2, 1/1: si cubren TODAS sus zonas de defensa,
  el jugador recibe +HEALER_HEAL_AMOUNT (3) de vida.
La lógica de combate vive en pvp_engine.py.

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
    las 7 especiales llevan TRIPARTITE_STRIKE o HEALER."""

    NONE = "none"
    # Especiales de ATAQUE (3/0, 2/0, 2/2, 1/0 — coste 2): Combo Especial. Si usa TODAS sus zonas
    # de ataque y NINGUNO de sus ataques es bloqueado: +SPECIAL_COMBO_BONUS (3) al daño base, cada vez.
    TRIPARTITE_STRIKE = "tripartite_strike"
    # Especiales de CURACIÓN (0/3, 0/2, 1/1 — coste 2): si cubren TODAS sus zonas de defensa,
    # el jugador recibe +HEALER_HEAL_AMOUNT (3) de vida (sin pasar de su vida máxima).
    HEALER = "healer"
    # Habilidades heredadas del sistema anterior (1 zona por carta). Ninguna carta las lleva y el
    # motor de zonas múltiples NO les da efecto: se conservan solo para poder leer datos antiguos.
    LIFESTEAL = "lifesteal"
    COMBO_STRIKE = "combo_strike"
    HEAL_POTION = "heal_potion"
    PIERCING = "piercing"
    COUNTER = "counter"

    @classmethod
    def _missing_(cls, value):
        # Nombres antiguos (catálogos/partidas guardados antes del 05/11/2026).
        legacy = {"clean_strike": cls.TRIPARTITE_STRIKE, "guard_energy": cls.HEALER}
        return legacy.get(value)


BASE_CARD_COST = 1      # Las 16 cartas base: coste FIJO de 1 (revisión del 03/11/2026)
SPECIAL_CARD_COST = 2   # Las 7 especiales (y las habilidades antiguas poción/perforante/counter)
SPECIAL_COST_ABILITIES = frozenset({
    Ability.TRIPARTITE_STRIKE, Ability.HEALER, Ability.HEAL_POTION, Ability.PIERCING, Ability.COUNTER,
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
    # Heredado de la antigua Guardia Vital (energía al bloquear): ya sin efecto en el motor.
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
# Gasto máximo por turno = el tope de energía (3): con 3 de energía puedes jugar
# 3 cartas de coste 1 en el mismo turno, combinando sus zonas (cada carta
# respeta su propia regla de zonas distintas). Antes era 2 (sección 2.2).
MAX_ENERGY_SPEND_PER_TURN = MAX_ENERGY
TURN_TIMER_SECONDS = 30   # Temporizador por turno (antes 20s: poco para elegir varias cartas y sus zonas)
INACTIVITY_LIMIT = 3       # Turnos SEGUIDOS sin jugar ninguna carta => derrota por inactividad
MATCHMAKING_SECONDS = 60  # Búsqueda de rival humano antes de asignar un BOT
# --- Reglas definitivas de zonas (Sendo, 05/11/2026) ---
NUM_ZONES = 3                  # Cabeza, Tronco, Piernas: máximo de zonas que puede marcar una carta
ZONE_HIT_DAMAGE = 5            # Daño base de cada ataque que entra en una zona
TRIPARTITE_COMBO_BONUS = 3     # +3 UNA vez por partida al tener afectadas (tocadas) las 3 zonas del rival
SPECIAL_COMBO_BONUS = 3        # +3 de una especial de ataque si TODOS sus ataques entran (repetible)
HEALER_HEAL_AMOUNT = 3         # +3 de vida de las curadoras si cubren TODAS sus zonas de defensa
CASHOUT_BATTLE_REQUIREMENT = 300  # Batallas PvP acumuladas para poder retirar fondos
FREE_DAILY_MATCH_LIMIT = 10        # Límite diario de partidas para usuarios Free
