"""
Simulador/panel visual del PvP de Kurama Run (Streamlit) — para probar el
motor de combate (pvp_engine.py) y la integración blockchain
(blockchain_connector.py) en local, sin tocar el proyecto principal ni la
BSC real.

Ejecución:
    cd PVP/engine
    pip install -r ../requirements.txt
    streamlit run pvp_dashboard.py

Modos:
- Vs. Bot: controlas al Jugador A, una IA mínima juega al Jugador B.
- Hotseat (2 jugadores locales): controlas A y B por turnos en el mismo
  panel — útil para probar combos y el motor sin necesitar dos sesiones.
La wallet es siempre SIMULADA por defecto (BlockchainConnector(simulate=True))
para poder jugar sin RPC ni fondos reales; hay un checkbox para intentar la
BSC real si el equipo quiere probarlo con web3 instalado.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import random
import sys
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

_ENGINE_DIR = Path(__file__).resolve().parent
_PVP_ROOT = _ENGINE_DIR.parent
_MARKETPLACE_DIR = _PVP_ROOT / "MARKETPLACE"
if str(_PVP_ROOT) not in sys.path:
    sys.path.insert(0, str(_PVP_ROOT))
if str(_MARKETPLACE_DIR) not in sys.path:
    sys.path.insert(0, str(_MARKETPLACE_DIR))

import kurama_theme  # noqa: E402
import marketplace_app  # noqa: E402  (MARKETPLACE/ — ver render_marketplace_panel() más abajo)

from economy_rules import (  # noqa: E402  (reglas de acceso / cristales / retos — servidor)
    ACCESS_BATTLES_REQUIRED,
    BOT,
    CHALLENGE_EXPIRY_SECONDS,
    CRYSTAL_ELO_LOSS,
    CRYSTAL_ELO_WIN,
    CRYSTAL_STAKE,
    CRYSTAL_WIN_KUR_REWARD,
    FREE_DAILY_RANKED_LIMIT,
    FREE_RANKED_ELO_LOSS,
    FREE_RANKED_ELO_WIN,
    HOUSE_FEE_PCT,
    MONEY_ELO_LOSS,
    MONEY_ELO_WIN,
    ChallengeKind,
    ChallengeStatus,
    Currency,
    EconomyError,
    EconomyServer,
)
from blockchain_connector import (
    DECK_STAT_BUFF_PCT,
    BlockchainConnector,
    apply_nft_buffs_to_deck,
    compute_base_hp,
    derive_placeholder_rarity,
    has_crystals,
)
from card_schema import (
    DECK_SIZE,
    ENERGY_PER_TURN,
    HAND_SIZE,
    HEALER_HEAL_AMOUNT,
    MATCHMAKING_SECONDS,
    MAX_ENERGY,
    MAX_ENERGY_SPEND_PER_TURN,
    SPECIAL_COMBO_BONUS,
    STARTING_ENERGY,
    TRIPARTITE_COMBO_BONUS,
    TURN_TIMER_SECONDS,
    ZONE_HIT_DAMAGE,
    Card,
    NFTRarity,
)
from pvp_engine import (
    EngineError,
    MatchStatus,
    Zone,
    auto_skip_turn,
    max_attack_zones,
    max_defense_zones,
    bot_choose_plays,
    build_deck_from_pool,
    end_turn,
    get_match_state,
    pass_turn,
    play_card,
    start_match,
)

ENGINE_DIR = _ENGINE_DIR
PVP_ROOT = _PVP_ROOT
DATABASE_PATH = ENGINE_DIR / "cards_database.json"
ESCENARIO_PATH = PVP_ROOT / "escenario.png"
DEFAULT_GAMENFT_ID = 0  # Fox fijo de GAMENFT/ para Bot/rival y Free sin NFT — ver gamenft_avatar_path

# Única fuente de verdad para las 4 secciones del panel — la navegación vive
# exclusivamente en el menú superior (st.popover "☰ MENÚ DE NAVEGACIÓN", ver
# más abajo), que lee y escribe st.session_state["panel_mode"]. No hay barra
# lateral: se eliminó por completo.
PANEL_OPTIONS = [
    "⚔️ Modo Batalla", "🤝 Retos 1v1", "🎓 Tutorial Interactivo",
    "📖 Catálogo de Cartas", "🛒 Marketplace NFT ($KUR)",
]


def _file_fingerprint(path: Path) -> str:
    """Hash del CONTENIDO de un archivo, para usarlo como clave de caché.
    st.cache_data solo invalida si cambian el código de la función o sus
    argumentos — no si cambia el archivo que lee. Streamlit Cloud actualiza
    el código tras un git push SIN reiniciar el proceso, así que sin esto
    seguía sirviendo desde memoria el cards_database.json antiguo."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


@st.cache_data
def _load_catalog_cached(_path_str: str, fingerprint: str) -> list[dict]:
    with open(_path_str, encoding="utf-8") as f:
        return json.load(f)


def load_catalog() -> list[dict]:
    return _load_catalog_cached(str(DATABASE_PATH), _file_fingerprint(DATABASE_PATH))


@st.cache_data
def _image_b64_cached(path_str: str, mtime_ns: int, size: int) -> str:
    return base64.b64encode(Path(path_str).read_bytes()).decode("ascii")


def _image_b64(path_str: str) -> str:
    """Imagen como data URI — para incrustarla en CSS/HTML plano (arena de
    combate) donde no vale un st.image (necesitamos <img>/background-image
    dentro de un único bloque de markdown, no un widget aparte). La clave de
    caché incluye fecha y tamaño del archivo para no servir una imagen vieja
    si se sustituye con el mismo nombre."""
    stat = Path(path_str).stat()
    return _image_b64_cached(path_str, stat.st_mtime_ns, stat.st_size)


@st.cache_data
def list_gamenft_ids() -> list[int]:
    """Todos los token_id con imagen en GAMENFT/fox_<id>.png — el selector de
    Maestro (render_wallet_and_nft_bar) ofrece variedad real en vez de
    limitarse a los 3 NFTs "owned" de la wallet simulada (sandbox local, no
    hay ownership on-chain real que respetar aquí)."""
    ids = []
    for p in (PVP_ROOT / "GAMENFT").glob("fox_*.png"):
        try:
            ids.append(int(p.stem.removeprefix("fox_")))
        except ValueError:
            continue
    return sorted(ids)


def gamenft_avatar_path(token_id: int | None) -> Path:
    """Imagen de un Fox de GAMENFT/ para montar en los marcos laterales del
    escenario (render_arena). Si no hay id o el archivo no existe, cae al
    Fox fijo DEFAULT_GAMENFT_ID (sigue siendo de GAMENFT/, nunca un sprite
    genérico de jugador) — esta es la imagen que ven el Bot/rival y un
    jugador Free sin NFT equipado."""
    for candidate in (token_id, DEFAULT_GAMENFT_ID):
        if candidate is None:
            continue
        p = PVP_ROOT / "GAMENFT" / f"fox_{candidate}.png"
        if p.exists():
            return p
    raise FileNotFoundError(f"GAMENFT/fox_{DEFAULT_GAMENFT_ID}.png no existe — revisa la carpeta GAMENFT/.")


@st.cache_data
def list_escenario_paths() -> list[Path]:
    """Todos los fondos de arena disponibles en la raíz del proyecto
    (escenario*.png) — para variedad; si solo existe escenario.png (el
    único hoy), ese es el único resultado y se comporta igual que antes."""
    return sorted(PVP_ROOT.glob("escenario*.png")) or [ESCENARIO_PATH]


def card_image_path(card_dict: dict) -> Path | None:
    rel = card_dict.get("image_path")
    if not rel:
        return None
    p = PVP_ROOT / rel
    return p if p.exists() else None


def init_state() -> None:
    if "connector" not in st.session_state:
        st.session_state.connector = BlockchainConnector(simulate=True)
        st.session_state.connector.simulated_backend.seed_wallet(
            "0xTESTWALLET000000000000000000000000AAAA", token_ids=[7, 42, 1337], kur_balance=150.0
        )
    if "wallet_address" not in st.session_state:
        st.session_state.wallet_address = "0xTESTWALLET000000000000000000000000AAAA"
    if "match" not in st.session_state:
        st.session_state.match = None
    if "vs_bot" not in st.session_state:
        st.session_state.vs_bot = True
    if "tutorial_step" not in st.session_state:
        st.session_state.tutorial_step = 0
    # Fuente de verdad para Modo/Oponente — tanto la barra de configuración
    # contextual de arriba como el "🏆 Modo de partida" de la barra lateral
    # leen y escriben estas mismas claves (mismo key= en el widget de la
    # barra lateral; la de arriba son botones que las fijan y hacen
    # st.rerun() antes de que ese widget se instancie, igual que panel_mode).
    if not st.session_state.get("match_mode"):
        # Streamlit borra el estado del widget de Modo mientras no se dibuja
        # (durante la partida): se recupera el último modo elegido para no
        # devolver a Free a quien venía de jugar Competitivo.
        st.session_state.match_mode = st.session_state.get("_match_mode_keep") or "free"
    if "opponent_choice" not in st.session_state:
        st.session_state.opponent_choice = "Bot" if st.session_state.vs_bot else "Hotseat (2 jugadores locales)"
    if st.session_state.pop("_force_opponent_bot", False):
        # "Aceptar Reto" de render_bot_challenges_feed no puede tocar
        # opponent_choice directo: en su propio rerun el widget de Modo/
        # Oponente (render_battle_mode_bar) ya se instanció antes en el
        # mismo script (StreamlitWidgetAlreadyInstantiatedError) porque la
        # columna izquierda del lobby se renderiza antes que la derecha.
        # Deja esta bandera y el próximo rerun la aplica aquí, bien arriba,
        # antes de que ese widget exista.
        st.session_state.opponent_choice = "Bot"
    if st.session_state.pop("_force_mode_competitive", False):
        # "🤝 Retos 1v1" publica un reto competitivo y salta a Batalla: el
        # modo se fija aquí, antes de que exista el widget de Modo.
        st.session_state.match_mode = "competitive"
    st.session_state["_match_mode_keep"] = st.session_state.match_mode
    # Búsqueda de rival humano (matchmaking) antes de caer a BOT, Reto 1v1
    # activo y si ya se acreditó la recompensa/pozo de la partida en curso —
    # ver render_bot_challenges_feed / render_challenges_panel más abajo.
    st.session_state.setdefault("matchmaking_start", None)
    st.session_state.setdefault("match_economy_mode", None)
    st.session_state.setdefault("match_reward_settled", False)
    # Libro mayor (economy_rules.EconomyServer): acceso NFT/300 combates,
    # cristales del jugador, cupo diario Free, escrow de retos, bots (solo
    # Free), Elo y premios.
    st.session_state.setdefault("economy", EconomyServer())
    st.session_state.setdefault("own_challenge_id", None)     # reto propio abierto (Free o Competitivo)
    st.session_state.setdefault("match_challenge_id", None)   # reto de la partida en curso
    st.session_state.setdefault("sim_challenge_meta", {})     # nombre/Fox de los retadores simulados
    # Selección táctica de la ronda actual — SELECCIÓN MÚLTIPLE: varias
    # cartas a la vez mientras su coste total quepa en la energía del turno.
    #   sel_cards_{pk}:   ids elegidos, en orden de selección (orden de juego)
    #   active_card_{pk}: la carta que editan ahora los puntos rojo/verde
    #   zones_{pk}:       {card_id: {"atk": [zonas], "def": [zonas]}} — zonas DISTINTAS,
    #                     como mucho ATK/DEF de la carta (reglas de zonas, pvp_engine.py)
    # (ver render_tactical_points/render_hand).
    for _pk in ("a", "b"):
        st.session_state.setdefault(f"sel_cards_{_pk}", [])
        st.session_state.setdefault(f"active_card_{_pk}", None)
        st.session_state.setdefault(f"zones_{_pk}", {})


def econ_server() -> EconomyServer:
    return st.session_state.economy


def player_crystals(wallet_address: str) -> int:
    """Cristales del JUGADOR (pertenecen a la cuenta, no al NFT)."""
    return econ_server().account(wallet_address).crystals


def player_has_crystals(wallet_address: str) -> bool:
    return player_crystals(wallet_address) > 0


def sync_economy(connector: BlockchainConnector, wallet_address: str) -> None:
    """Vuelca en el libro mayor lo que vive en la wallet (simulada o real):
    sus NFT (deciden el acceso y la variante de Free) y su saldo $KUR. Es un
    ajuste de servidor, no un depósito del usuario (ver sync_balance)."""
    econ = econ_server()
    econ.set_nfts(wallet_address, connector.owned_token_ids(wallet_address))
    econ.sync_balance(wallet_address, Currency.KUR, connector.kur_balance(wallet_address))


def push_kur(connector: BlockchainConnector, wallet_address: str) -> None:
    """Tras cualquier movimiento del libro mayor (escrow, premio, depósito,
    retiro) se refleja el saldo $KUR resultante en la wallet."""
    if connector.simulated_backend is not None:
        connector.simulated_backend.seed_wallet(
            wallet_address, connector.owned_token_ids(wallet_address),
            econ_server().account(wallet_address).balances[Currency.KUR.value],
        )


def player_has_nft(wallet_address: str) -> bool:
    return econ_server().account(wallet_address).has_nft


CRYSTAL_SELECTOR_OPTIONS = [0, 1, 2, 3, 4, 5]


def crystal_status_text(wallet_address: str) -> tuple[str, bool]:
    """(texto, ok) — qué variante de Free le toca a la cuenta y si puede jugarla."""
    econ = econ_server()
    n = player_crystals(wallet_address)
    if player_has_nft(wallet_address):
        if n >= CRYSTAL_STAKE:
            return f"💎 {n} cristal{'es' if n != 1 else ''} · Free con cristales: apuestas {CRYSTAL_STAKE} por partida", True
        return "⚠️ Con NFT el Free se juega con cristales: necesitas al menos 1 💎", False
    left = econ.free_ranked_remaining(wallet_address)
    extra = " (sin NFT los cristales no se usan en PvP)" if n else ""
    return f"🎟️ Free sin cristales: {left}/{FREE_DAILY_RANKED_LIMIT} partidas hoy{extra}", left > 0


def render_crystal_selector(wallet_address: str) -> None:
    """Saldo de cristales del jugador (panel izquierdo). El valor mostrado
    es SIEMPRE el del libro mayor (los cristales se apuestan y se ganan en
    Free con cristales); el selector 0-5 es una herramienta de pruebas que
    fija ese saldo al pulsarlo, nunca lo pisa en cada rerun."""
    n = player_crystals(wallet_address)
    options = sorted(set(CRYSTAL_SELECTOR_OPTIONS) | {n})
    st.session_state["player_crystals_widget"] = n

    def _store_crystals() -> None:
        v = st.session_state.get("player_crystals_widget")
        if v is not None:  # volver a pulsar el valor activo lo deselecciona: se ignora
            econ_server().set_crystals(wallet_address, int(v))

    text, ok = crystal_status_text(wallet_address)
    with st.container(key="crystal_selector"):
        st.markdown("<div class='crystal-sel-title'>💎 CRISTALES DEL JUGADOR</div>", unsafe_allow_html=True)
        st.segmented_control(
            "Cristales del jugador",
            options=options,
            key="player_crystals_widget",
            on_change=_store_crystals,
            label_visibility="collapsed",
            help="Saldo de cristales (pulsar fija el saldo — herramienta de pruebas). "
                 f"Con NFT: Free con cristales, apuestas {CRYSTAL_STAKE} 💎 por partida. "
                 f"Sin NFT: Free sin cristales, {FREE_DAILY_RANKED_LIMIT} partidas al día. "
                 "El Competitivo no usa cristales.",
        )
        st.markdown(
            f"<div class='crystal-sel-status {'unlimited' if ok else 'limited'}'>{text}</div>",
            unsafe_allow_html=True,
        )


def render_access_panel(connector: BlockchainConnector, wallet_address: str) -> None:
    """Requisito general de acceso (NFT o 300 combates) + depósito/retirada
    de $KUR, que solo funcionan con la cuenta desbloqueada."""
    econ = econ_server()
    status = econ.access_status(wallet_address)
    with st.container(key="access_panel"):
        if status["unlocked_by"] == "nft":
            st.success("🔓 Acceso completo (tienes NFT): Competitivo, depósitos y retiradas.")
        elif status["unlocked_by"] == "battles":
            st.success(f"🔓 Acceso completo ({status['battles_total']} combates): "
                       "Competitivo, depósitos y retiradas.")
        else:
            done = status["battles_total"]
            st.warning(
                f"🔒 Sin NFT y con {done}/{ACCESS_BATTLES_REQUIRED} combates: Competitivo, depósitos y "
                f"retiradas bloqueados. Juega tus {FREE_DAILY_RANKED_LIMIT} Free diarias para llegar a "
                f"{ACCESS_BATTLES_REQUIRED} (o consigue un NFT)."
            )
            st.progress(min(1.0, done / ACCESS_BATTLES_REQUIRED))
        with st.expander("💼 Depositar / Retirar $KUR"):
            amount = st.number_input("Importe ($KUR)", min_value=0.0, value=10.0, step=5.0, key="cash_amount")
            c_dep, c_wd = st.columns(2)
            dep = c_dep.button("⬇️ Depositar", key="cash_deposit", width="stretch",
                               disabled=not status["unlocked"])
            wd = c_wd.button("⬆️ Retirar", key="cash_withdraw", width="stretch",
                             disabled=not status["unlocked"])
            if not status["unlocked"]:
                st.caption(f"🔒 Necesitas un NFT o {ACCESS_BATTLES_REQUIRED} combates para mover fondos.")
            if dep or wd:
                try:
                    (econ.deposit if dep else econ.withdraw)(wallet_address, Currency.KUR, amount)
                except EconomyError as e:
                    st.error(f"⚠️ {e}")
                else:
                    push_kur(connector, wallet_address)
                    st.rerun()


def _legacy_equipped_has_crystals(connector: BlockchainConnector, wallet_address: str) -> bool:
    """¿El Maestro equipado es un NFT con cristales (Épico/Legendario)? Solo
    se usa en modo Competitivo y en Retos 1v1 — en Free los cristales no
    existen (ni se muestran ni cuentan)."""
    return has_crystals(resolve_equipped_rarity(connector, wallet_address))


def crystals_label(crystals: bool) -> str:
    return "💎 Con cristales" if crystals else "Sin cristales"


def rarity_label(rarity: NFTRarity) -> str:
    return {
        NFTRarity.NONE: "Sin NFT",
        NFTRarity.COMMON: "Común",
        NFTRarity.RARE: "Raro",
        NFTRarity.EPIC: "Épico",
        NFTRarity.LEGENDARY: "Legendario",
    }[rarity]


def resolve_equipped_rarity(connector: BlockchainConnector, wallet_address: str) -> NFTRarity:
    """Rareza que decide HP base y buff de mazo para la próxima partida: la
    del Maestro elegido en render_wallet_and_nft_bar (ahora cualquier fox de
    GAMENFT/, no solo los "owned" de la wallet simulada — ver
    list_gamenft_ids); si no hay ninguno elegido todavía, cae a la mejor
    rareza disponible en la wallet — mismo comportamiento que antes."""
    equipped_id = st.session_state.get("equipped_token_id")
    if equipped_id is not None:
        return derive_placeholder_rarity(equipped_id)
    return connector.best_rarity_for_wallet(wallet_address)


# --------------------------------------------------------------------------- #
# Galería / Catálogo de cartas
# --------------------------------------------------------------------------- #
CATEGORY_LABELS = {"standard": "Base", "special": "Especial", "rare": "Rara", "legendary": "Legendaria"}


def card_type_label(card_dict: dict) -> str:
    """Tipo de carta para filtrar la galería: deriva de su habilidad especial
    (Poción/Perforante/Contraataque) o, si no tiene, de si pega, protege o
    ambas cosas (Ataque/Defensa/Híbrida)."""
    ability = card_dict.get("ability", "none")
    if ability in ("tripartite_strike", "clean_strike"):
        return "Combo Especial"
    if ability in ("healer", "guard_energy"):
        return "Curación"
    if ability == "lifesteal":
        return "Robo de Vida"
    if ability == "combo_strike":
        return "Combo Simultáneo"
    if ability == "heal_potion":
        return "Poción"
    if ability == "piercing":
        return "Perforante"
    if ability == "counter":
        return "Contraataque"
    atk, defense = card_dict["atk"], card_dict["defense"]
    if atk > 0 and defense > 0:
        return "Híbrida"
    if atk > 0:
        return "Ataque"
    if defense > 0:
        return "Defensa"
    return "Especial"


def _zones_word(n: int) -> str:
    return "1 zona" if n == 1 else f"{n} zonas distintas"


def card_effect_text(card_dict: dict) -> str:
    """Efecto exacto en combate de una carta con las reglas definitivas de
    zonas (pvp_engine.py): cada punto de ATK = 1 zona de ataque distinta
    (5 de daño si entra), cada punto de DEF = 1 zona de defensa distinta
    (anula 1 ataque), más el efecto de su habilidad especial."""
    ability = card_dict.get("ability", "none")
    atk, defense = min(card_dict["atk"], 3), min(card_dict["defense"], 3)
    if ability in ("tripartite_strike", "clean_strike"):
        total = atk * ZONE_HIT_DAMAGE + SPECIAL_COMBO_BONUS
        return (
            f"💥 Combo Especial: ataca {_zones_word(atk)} ({ZONE_HIT_DAMAGE} por zona). Si marcas las {atk} y "
            f"ningún ataque es bloqueado: +{SPECIAL_COMBO_BONUS} → {total} de daño en total."
            if atk > 1 else
            f"💥 Combo Especial: ataca 1 zona ({ZONE_HIT_DAMAGE}). Si no la bloquean: "
            f"+{SPECIAL_COMBO_BONUS} → {total} de daño en total."
        ) + (f" Además defiende {_zones_word(defense)}." if defense else "")
    if ability in ("healer", "guard_energy"):
        return (
            f"💚 Curación: defiende {_zones_word(defense)}. Si el rival ya tiene activado su Combo "
            f"Tripartito y bloqueas sus ataques en TODAS las zonas que defiendes, recibes "
            f"+{HEALER_HEAL_AMOUNT} de vida. Sin su combo, solo defiende."
        ) + (f" Además ataca {_zones_word(atk)} ({ZONE_HIT_DAMAGE} por zona)." if atk else "")
    if atk > 0 and defense > 0:
        return (f"⚔️🛡️ Ataca hasta {_zones_word(atk)} ({ZONE_HIT_DAMAGE} de daño cada una) y defiende hasta "
                f"{_zones_word(defense)} (cada defensa anula 1 ataque).")
    if atk > 0:
        return f"⚔️ Ataca hasta {_zones_word(atk)}: {ZONE_HIT_DAMAGE} de daño por cada ataque que no bloqueen."
    if defense > 0:
        return f"🛡️ Defiende hasta {_zones_word(defense)}: cada defensa anula 1 ataque rival en esa zona."
    return "✨ Finta: ni ataca ni defiende, pero cuenta como jugada."


def render_gallery(catalog_dicts: list[dict]) -> None:
    st.header("📖 Catálogo de Cartas")
    st.caption(
        f"Las {len(catalog_dicts)} cartas del juego: una por cada combinación ATK/DEF posible, "
        "sin ninguna repetida. Tu mazo de cada partida es este set completo, barajado."
    )

    all_types = sorted({card_type_label(c) for c in catalog_dicts})

    f1, f2 = st.columns([1, 2])
    with f1:
        rarity_filter = st.radio(
            "Grupo", ["Todas", "Ataque (ATK ≥ DEF)", "Defensa (DEF > ATK)"], key="gallery_rarity_filter"
        )
    with f2:
        type_filter = st.multiselect("Tipo", all_types, default=all_types, key="gallery_type_filter")

    filtered = catalog_dicts
    if rarity_filter.startswith("Ataque"):
        filtered = [c for c in filtered if c["atk"] >= c["defense"]]
    elif rarity_filter.startswith("Defensa"):
        filtered = [c for c in filtered if c["defense"] > c["atk"]]
    filtered = [c for c in filtered if card_type_label(c) in type_filter]

    st.caption(f"Mostrando {len(filtered)} de {len(catalog_dicts)} cartas.")
    if not filtered:
        st.info("Ninguna carta coincide con los filtros seleccionados.")
        return

    st.divider()
    with st.container(key="gallery_grid"):
        cols = st.columns(4)
        for i, card_dict in enumerate(filtered):
            with cols[i % 4]:
                img = card_image_path(card_dict)
                if img:
                    st.image(str(img), width="stretch")
                else:
                    st.caption("🖼️ (sin imagen)")
                st.markdown(f"<div class='card-name'>{html.escape(card_dict['name'])}</div>", unsafe_allow_html=True)
                st.caption(f"⚡ Coste {card_dict['cost_energy']} · {card_dict['atk']} ATK / {card_dict['defense']} DEF")
                st.caption(f"🏷️ {CATEGORY_LABELS.get(card_dict['category'], card_dict['category'])} · {card_type_label(card_dict)}")
                ability_note = ""
                if card_dict.get("ability") in ("tripartite_strike", "clean_strike"):
                    _tot = min(card_dict["atk"], 3) * ZONE_HIT_DAMAGE + SPECIAL_COMBO_BONUS
                    ability_note = f"💥 Combo Especial: {_tot} de daño si entran todos sus ataques"
                elif card_dict.get("ability") in ("healer", "guard_energy"):
                    ability_note = f"💚 Con el combo rival activo: +{HEALER_HEAL_AMOUNT} de vida si bloquea todo"
                st.markdown(f"<div class='gallery-ability'>{ability_note or '&nbsp;'}</div>", unsafe_allow_html=True)
                st.markdown(
                    f"<div class='gallery-desc'>{html.escape(card_dict.get('description') or '')}</div>",
                    unsafe_allow_html=True,
                )
                if card_dict.get("is_placeholder"):
                    st.caption("🧩 Arte pendiente (placeholder)")
                st.divider()


# --------------------------------------------------------------------------- #
# Tutorial interactivo
# --------------------------------------------------------------------------- #
# Guion de la "Demostración Paso a Paso": una partida de ejemplo ya escrita a
# mano (no usa el motor en vivo, para que sea siempre la misma secuencia
# didáctica) pero con los daños calculados exactamente como los calcularía
# pvp_engine.py, para que lo que se enseña aquí sea 100% lo que pasa jugando
# de verdad. HP inicial de ejemplo: Tú 50, Rival 50.
TUTORIAL_STEPS = [
    {
        "title": "Así arranca la partida",
        "actor": None,
        "card_id": None,
        "zone": None,
        "text": (
            "Tú y el rival empezáis con la misma vida (depende de vuestra rareza de NFT — "
            f"aquí usamos 50 HP de ejemplo) y {HAND_SIZE} cartas en la mano. Empiezas con "
            f"{STARTING_ENERGY} de energía (el máximo es {MAX_ENERGY}): cada carta que juegas resta su coste y "
            f"cada turno recuperas +{ENERGY_PER_TURN}. En cada carta, **cada punto de ATK es una zona que puedes "
            "atacar y cada punto de DEF una zona que puedes defender** (Cabeza, Tronco o Piernas), "
            "siempre zonas distintas dentro de la misma carta."
        ),
        "hp_a": 50, "hp_b": 50,
        "badge": None,
    },
    {
        "title": "Turno 1 — Un ataque en 3 zonas",
        "actor": "tú",
        "card_id": "tz_shadow_claw_3x0",
        "zone": "⚔️ Cabeza · Tronco · Piernas",
        "text": (
            "Juegas **Shadow Claw** (3 ATK / 0 DEF): con 3 de ATK marcas **3 zonas distintas** del "
            f"rival. No se defendió en ninguna, así que entran los 3 ataques: **3 × {ZONE_HIT_DAMAGE} = "
            f"{3 * ZONE_HIT_DAMAGE}**. Además, al quedar afectadas sus 3 zonas se activa el **Combo "
            f"Tripartito: +{TRIPARTITE_COMBO_BONUS}** → **{3 * ZONE_HIT_DAMAGE + TRIPARTITE_COMBO_BONUS} de daño**. "
            "Ese +3 solo se cobra una vez por partida."
        ),
        "hp_a": 50, "hp_b": 50 - 3 * ZONE_HIT_DAMAGE - TRIPARTITE_COMBO_BONUS,
        "badge": "🔥 Ataque multizona + Combo Tripartito",
    },
    {
        "title": "Turno 2 — El escudo detiene el golpe",
        "actor": "rival",
        "card_id": "tz_lava_rage_0x3",
        "zone": "🛡️ Cabeza · Tronco · Piernas",
        "text": (
            "El rival juega **Lava Rage** (0 ATK / 3 DEF) y cubre sus 3 zonas. Atacas con "
            "**Cyber-Kitsune Operative** (2 ATK) en Cabeza y Tronco: **cada defensa anula un ataque** "
            "en su zona, así que los dos quedan bloqueados → **0 de daño**."
        ),
        "hp_a": 50, "hp_b": 50 - 3 * ZONE_HIT_DAMAGE - TRIPARTITE_COMBO_BONUS,
        "badge": "🛡️ Defensa / Escudo",
    },
    {
        "title": "Turno 3 — Carta híbrida",
        "actor": "tú",
        "card_id": "nft_fox_11_2x1",
        "zone": "⚔️ Tronco · Piernas  /  🛡️ Cabeza",
        "text": (
            "Juegas **Fox #11** (2/1): atacas Tronco y Piernas y defiendes tu Cabeza. El rival juega "
            "**Pyre & Cosmos** (3/1): te ataca en las 3 zonas y defiende sus Piernas. Tu defensa para "
            f"su golpe a la Cabeza: **recibes 2 × {ZONE_HIT_DAMAGE} = {2 * ZONE_HIT_DAMAGE}**. Su defensa "
            f"para tu golpe a las Piernas: **le haces {ZONE_HIT_DAMAGE}** en el Tronco."
        ),
        "hp_a": 50 - 2 * ZONE_HIT_DAMAGE, "hp_b": 50 - 4 * ZONE_HIT_DAMAGE - TRIPARTITE_COMBO_BONUS,
        "badge": "⚔️🛡️ Híbrida",
    },
    {
        "title": "Turno 4 — ¡Combo Especial!",
        "actor": "tú",
        "card_id": "sp_fox_1468_3x0",
        "zone": "⚔️ Cabeza · Tronco · Piernas",
        "text": (
            "Juegas la especial **Fox #1468 · Ronin Executioner** (3/0, coste 2) en las 3 zonas y el "
            "rival no bloquea ninguna: la especial suma su **Combo Especial** → "
            f"**3 × {ZONE_HIT_DAMAGE} + {SPECIAL_COMBO_BONUS} = {3 * ZONE_HIT_DAMAGE + SPECIAL_COMBO_BONUS} "
            f"de daño**. El Combo Tripartito ya lo cobraste en el turno 1, pero el +{SPECIAL_COMBO_BONUS} de las "
            f"especiales se repite cada vez. Las especiales 2/0 hacen {2 * ZONE_HIT_DAMAGE + SPECIAL_COMBO_BONUS} y "
            f"la 1/0 {ZONE_HIT_DAMAGE + SPECIAL_COMBO_BONUS}, siempre que marquen todas sus zonas y no les "
            "bloqueen ningún ataque."
        ),
        "hp_a": 50 - 2 * ZONE_HIT_DAMAGE,
        "hp_b": 50 - 7 * ZONE_HIT_DAMAGE - TRIPARTITE_COMBO_BONUS - SPECIAL_COMBO_BONUS,
        "badge": "💥 Combo Especial",
    },
    {
        "title": "Turno 5 — El rival se cura",
        "actor": "rival",
        "card_id": "sp_fox_963_0x2",
        "zone": "🛡️ Cabeza · Tronco",
        "text": (
            "Atacas Cabeza y Tronco con **Celestial Kitsune Operative (Duelist)** (2/2). El rival juega la curadora "
            "**Fox #963 · Blossom Ward** (0/2) defendiendo justo esas 2 zonas: bloquea tus 2 ataques. "
            "Como **tú ya activaste tu Combo Tripartito** (turno 1), su curadora acierta en todas las "
            f"zonas que defiende y le da **+{HEALER_HEAL_AMOUNT} de vida**. Si aún no tuvieras el combo, "
            "o si le hubiera entrado un ataque en alguna de esas zonas, solo habría defendido."
        ),
        "hp_a": 50 - 2 * ZONE_HIT_DAMAGE,
        "hp_b": 50 - 7 * ZONE_HIT_DAMAGE - TRIPARTITE_COMBO_BONUS - SPECIAL_COMBO_BONUS + HEALER_HEAL_AMOUNT,
        "badge": "💚 Curación",
    },
    {
        "title": "Turno 6 — Ataque y defensa total",
        "actor": "tú",
        "card_id": "kr_celestial_operative_3x3",
        "zone": "⚔️ Cabeza · Tronco · Piernas  /  🛡️ Cabeza · Tronco · Piernas",
        "text": (
            "Juegas **Celestial Kitsune Operative** (3/3): ataca las 3 zonas del rival y defiende tus 3 "
            "zonas con una sola carta. El rival te ataca con una 3/0 en las 3 zonas: todo bloqueado. Él no "
            f"se defendió: entran tus 3 ataques → **{3 * ZONE_HIT_DAMAGE} de daño**. Si juegas varias cartas "
            "en un turno, recuerda que no pueden repetir zona entre ellas."
        ),
        "hp_a": 50 - 2 * ZONE_HIT_DAMAGE, "hp_b": 0,
        "badge": "⚔️🛡️ Carta completa",
    },
    {
        "title": "Cómo se gana",
        "actor": None,
        "card_id": None,
        "zone": None,
        "text": (
            "El rival ha llegado a **0 HP**: ganas la partida. Si eres tú quien llega "
            "antes a 0, pierdes. Pasar turno es una jugada válida (guardas energía), pero si se te "
            "**agota el tiempo 3 turnos seguidos sin jugar ni pasar turno** (dejar una carta elegida "
            "a medias también cuenta), pierdes por inactividad."
        ),
        "hp_a": 50 - 2 * ZONE_HIT_DAMAGE, "hp_b": 0,
        "badge": "🏆 Victoria",
    },
]


def render_tutorial(catalog_dicts: list[dict]) -> None:
    cards_by_id = {c["id"]: c for c in catalog_dicts}

    st.header("🎓 Tutorial Interactivo — Cómo jugar al PVP de Kurama Run")
    st.caption("Todo lo que necesitas para entender una partida, explicado paso a paso y en español.")

    tab_basics, tab_zones, tab_cards, tab_combos, tab_demo = st.tabs(
        ["📖 Conceptos Básicos", "🎯 Zonas de Impacto", "🃏 Tipos de Cartas", "⚡ Combos", "👣 Demostración Paso a Paso"]
    )

    with tab_basics:
        st.subheader("Lo esencial antes de tu primera partida")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("### ❤️ Puntos de Vida (HP)")
            st.markdown(
                "Cada jugador tiene una barra de vida que depende de si tiene un NFT y de su "
                "rareza (de 50 a 100 HP). **Cuando tu HP llega a 0, pierdes la partida.** "
                "No hay vida por zona: las 3 zonas (Cabeza, Tronco, Piernas) comparten la misma barra."
            )
        with c2:
            st.markdown("### ⚡ Energía por turno")
            st.markdown(
                f"Empiezas con **{STARTING_ENERGY} de energía**. Cada carta que juegas **resta su coste** "
                f"de la barra. Al empezar el turno siguiente recuperas **+{ENERGY_PER_TURN}** sobre lo que te "
                f"quedó, sin pasar nunca de **{MAX_ENERGY}**. Ejemplo: gastas 3 → empiezas el siguiente con 1; "
                f"gastas 1 de 3 → vuelves a 3. Ahorrar energía un turno te deja jugar más fuerte el siguiente."
            )
        with c3:
            st.markdown("### 🂠 El mazo")
            st.markdown(
                f"Tu mazo de combate tiene **{DECK_SIZE} cartas** y tu mano **{HAND_SIZE}**. Las cartas que no "
                f"juegas se quedan en la mano; al acabar el turno robas solo las que hayas jugado. Si pasas "
                f"sin jugar, se descarta **1 carta al azar** de tu mano y robas otra. Tienes **{TURN_TIMER_SECONDS} segundos** para decidir — "
                f"si el mazo se queda sin cartas, la pila de descarte se baraja y vuelve a usarse."
            )
        st.info(
            "💡 **Resumen rápido**: cada punto de **ATK** de una carta es **1 zona del rival que atacas** "
            "y cada punto de **DEF** es **1 zona tuya que defiendes** — siempre zonas distintas dentro de "
            f"la misma carta (puedes dejar zonas sin usar). Cada ataque que entra quita **{ZONE_HIT_DAMAGE}**; "
            "cada defensa del rival en esa zona anula **un** ataque."
        )

    with tab_zones:
        st.subheader("Cabeza, Tronco y Piernas: elige bien dónde golpear")
        zc1, zc2, zc3 = st.columns(3)
        with zc1:
            st.markdown("## 🗣️ Cabeza")
            st.markdown("Una de las 3 zonas de impacto. Atacarla daña la Cabeza del rival; "
                        "defenderla protege la tuya contra ataques a esa misma zona.")
        with zc2:
            st.markdown("## 🫁 Tronco")
            st.markdown("La zona \"central\". Igual de válida que las otras — no hay ninguna "
                        "zona mecánicamente mejor, solo depende de lo que juegue el rival.")
        with zc3:
            st.markdown("## 🦵 Piernas")
            st.markdown("La tercera zona. Cuando has tocado las 3 zonas del rival (aunque sea en "
                        "turnos distintos) activas el Combo Tripartito (pestaña ⚡ Combos).")
        st.markdown(
            f"**¿Cómo se calcula el daño?** Cada ataque que entra en una zona quita **{ZONE_HIT_DAMAGE}**. "
            "Cada defensa que el rival tenga en esa misma zona **anula un ataque**. Ejemplo: una 3/0 en "
            f"las 3 zonas contra una 0/2 que cubre Cabeza y Tronco → solo entran las Piernas: "
            f"**{ZONE_HIT_DAMAGE} de daño**.\n\n"
            "**Reglas de selección:** con una carta de ATK 1 marcas 1 zona, con ATK 2 dos zonas "
            "**diferentes**, con ATK 3 tres zonas **diferentes** — nunca repites zona con la misma carta. "
            "Con la DEF igual. Si no quieres usar todos los ataques o defensas, puedes dejar zonas sin "
            "marcar.\n\n"
            "**Varias cartas en el mismo turno:** tampoco se repiten zonas entre tus cartas. Cada zona "
            "admite **1 ataque y 1 defensa tuyos por turno**: en cuanto una carta usa una casilla, queda "
            "bloqueada para las demás. Si ya tienes cubiertas todas las casillas y te sobran ataques o "
            "defensas, no estás obligado a usarlos: puedes jugar la carta igualmente o pasar turno."
        )

    with tab_cards:
        st.subheader("Tipos de cartas, con ejemplos reales del juego")

        def show_card(col, card_id: str, note: str = "") -> None:
            card_dict = cards_by_id.get(card_id)
            if not card_dict:
                return
            with col, st.container(key=f"tutorial_card_{card_id}"):
                img = card_image_path(card_dict)
                if img:
                    st.image(str(img), width=160)
                st.markdown(f"**{card_dict['name']}**")
                st.caption(f"{card_dict['atk']} ATK / {card_dict['defense']} DEF · Coste {card_dict['cost_energy']}")
                if note:
                    st.caption(note)

        st.markdown("#### 🃏 16 cartas base + 7 especiales")
        st.caption(
            "Las 16 cartas base (coste 1) cubren todas las combinaciones de ataque (3/3, 3/2, 3/1, 3/0, "
            "2/2, 2/1, 2/0, 1/1, 1/0, 0/0) y su inverso defensivo (2/3, 1/3, 0/3, 1/2, 0/2, 0/1). Las 7 "
            f"especiales cuestan 2: 4 de Combo Especial (+{SPECIAL_COMBO_BONUS} si entran todos sus ataques) "
            f"y 3 curadoras (+{HEALER_HEAL_AMOUNT} de vida si el rival ya tiene su Combo Tripartito y bloquean "
            "en todas sus zonas). Tu mazo de "
            f"{DECK_SIZE} cartas es el set completo."
        )
        st.markdown("##### ⚔️ Ataque")
        c1, c2, c3 = st.columns(3)
        show_card(c1, "tz_shadow_claw_3x0", f"3/0: ataca 3 zonas distintas (hasta {3 * ZONE_HIT_DAMAGE}).")
        show_card(c2, "kr_pyre_cosmos_3x1", "3/1: ataca 3 zonas y defiende 1.")
        show_card(c3, "kr_cyber_operative_2x0", f"2/0: ataca 2 zonas distintas (hasta {2 * ZONE_HIT_DAMAGE}).")

        st.markdown("##### 🛡️ Defensa")
        st.caption("Cada punto de DEF cubre una zona tuya distinta y anula un ataque rival en ella.")
        c4, c5, c6 = st.columns(3)
        show_card(c4, "tz_lava_rage_0x3", "0/3: cubre tus 3 zonas.")
        show_card(c5, "kr_celestial_flux_1x3", "1/3: cubre 3 zonas y ataca 1.")
        show_card(c6, "kr_cyber_guardian_0x2", "0/2: cubre 2 zonas distintas.")

        st.markdown("##### ⚔️🛡️ Híbridas")
        c7, c8, c9 = st.columns(3)
        show_card(c7, "kr_celestial_operative_3x3", "3/3: ataca y defiende las 3 zonas.")
        show_card(c8, "tz_raging_earthquake_1x2", "1/2: ataca 1 zona y cubre 2.")
        show_card(c9, "nft_fox_27_0x0", "Finta (0/0): ni ataca ni defiende, pero cuenta como jugada.")

    with tab_combos:
        st.subheader("Mecánicas especiales que deciden las partidas")

        with st.expander(f"🔥 Combo Tripartito (+{TRIPARTITE_COMBO_BONUS} de daño extra, una vez)", expanded=True):
            st.markdown(
                "Cada zona del rival donde **entra** uno de tus ataques queda **afectada** para el resto de la "
                "partida (un ataque bloqueado no cuenta). No hace falta que sea en el mismo turno: en cuanto "
                "tengas afectadas **Cabeza, Tronco y Piernas**, ese golpe suma "
                f"**+{TRIPARTITE_COMBO_BONUS} de daño extra**. Solo se activa **una vez por partida**; el "
                "marcador bajo el rival te muestra cuántas zonas llevas."
            )
            st.caption("Consejo: varía las zonas que atacas en vez de insistir siempre en la misma.")

        with st.expander(f"💥 Combo Especial (+{SPECIAL_COMBO_BONUS} de las cartas especiales)"):
            st.markdown(
                f"Lo activan las **especiales de ataque**. Si la carta marca **todas** sus zonas y **ninguno** "
                f"de sus ataques es bloqueado, suma **+{SPECIAL_COMBO_BONUS}** a los {ZONE_HIT_DAMAGE} por zona — "
                "**cada vez** que lo consigas:\n\n"
                f"- **3/0** → 3 zonas × {ZONE_HIT_DAMAGE} + {SPECIAL_COMBO_BONUS} = "
                f"**{3 * ZONE_HIT_DAMAGE + SPECIAL_COMBO_BONUS}**\n"
                f"- **2/0** → 2 zonas × {ZONE_HIT_DAMAGE} + {SPECIAL_COMBO_BONUS} = "
                f"**{2 * ZONE_HIT_DAMAGE + SPECIAL_COMBO_BONUS}**\n"
                f"- **1/0** → 1 zona × {ZONE_HIT_DAMAGE} + {SPECIAL_COMBO_BONUS} = "
                f"**{ZONE_HIT_DAMAGE + SPECIAL_COMBO_BONUS}**\n\n"
                "Si dejas una zona sin marcar o el rival bloquea uno de sus ataques, no hay +3. Si en el mismo "
                f"golpe completas también el Combo Tripartito, se suman los dos: una 3/0 especial que "
                f"completa las 3 zonas hace {3 * ZONE_HIT_DAMAGE} + {SPECIAL_COMBO_BONUS} + "
                f"{TRIPARTITE_COMBO_BONUS} = **{3 * ZONE_HIT_DAMAGE + SPECIAL_COMBO_BONUS + TRIPARTITE_COMBO_BONUS}**."
            )

        with st.expander(f"💚 Curación (+{HEALER_HEAL_AMOUNT} de vida)"):
            st.markdown(
                "Las **curadoras** (0/3, 0/2 y 1/1) dependen del **Combo Tripartito de tu rival**:\n\n"
                "- **Si el rival aún no lo ha activado:** solo defienden — bloquean como cualquier defensa.\n"
                "- **Si el rival ya lo activó** (en una ronda anterior): si la curadora **bloquea en todas las "
                "zonas que defiende** (el rival atacó cada una y no entró ninguno), recibes "
                f"**+{HEALER_HEAL_AMOUNT} de vida**. Si alguna zona no la atacó o le entró un ataque, no cura.\n\n"
                "Nunca dan energía y nunca superas tu vida máxima. Son tu carta de remontada cuando el rival "
                "ya ha completado su combo."
            )

        with st.expander(f"⚡ Varias cartas en el mismo turno"):
            st.markdown(
                f"Con **{MAX_ENERGY} de energía** puedes jugar **3 cartas de coste 1** a la vez y repartir sus "
                "zonas. Ninguna zona se repite: ni dentro de una carta ni entre cartas distintas del mismo "
                "turno. Cada zona admite 1 ataque y 1 defensa tuyos por turno (como mucho 3 ataques y 3 "
                "defensas). Lo que sobre, no hace falta usarlo."
            )

    with tab_demo:
        st.subheader("Demostración paso a paso")
        st.caption(
            "Una partida de ejemplo ya escrita, turno a turno, para ver las mecánicas en acción "
            "sin tener que jugar tú mismo. Pulsa Siguiente para avanzar."
        )

        total_steps = len(TUTORIAL_STEPS)
        step_idx = min(st.session_state.tutorial_step, total_steps - 1)
        step = TUTORIAL_STEPS[step_idx]

        st.progress((step_idx + 1) / total_steps, text=f"Paso {step_idx + 1} de {total_steps}")

        nav1, nav2, nav3 = st.columns([1, 1, 1])
        with nav1:
            if st.button("⬅️ Paso anterior", disabled=step_idx == 0, width="stretch"):
                st.session_state.tutorial_step = max(0, step_idx - 1)
                st.rerun()
        with nav2:
            if st.button("🔄 Reiniciar demo", width="stretch"):
                st.session_state.tutorial_step = 0
                st.rerun()
        with nav3:
            if st.button("Siguiente paso ➡️", disabled=step_idx == total_steps - 1, width="stretch", type="primary"):
                st.session_state.tutorial_step = min(total_steps - 1, step_idx + 1)
                st.rerun()

        st.divider()
        demo_col, hp_col = st.columns([2, 1])
        with demo_col:
            st.markdown(f"### {step['title']}")
            if step["badge"]:
                st.caption(step["badge"])
            st.markdown(step["text"])
            if step["card_id"]:
                card_dict = cards_by_id.get(step["card_id"])
                if card_dict:
                    img_col, info_col = st.columns([1, 2])
                    with img_col, st.container(key=f"tutorial_card_demo_{step_idx}"):
                        img = card_image_path(card_dict)
                        if img:
                            st.image(str(img), width=160)
                    with info_col:
                        quien = "Tú jugaste" if step["actor"] == "tú" else "El rival jugó"
                        st.markdown(f"**{quien}:** {card_dict['name']}")
                        st.caption(f"{card_dict['atk']} ATK / {card_dict['defense']} DEF · {step['zone']}")
        with hp_col:
            st.markdown("**Vida en este momento**")
            st.progress(max(0.0, step["hp_a"] / 50), text=f"Tú: {step['hp_a']} / 50 HP")
            st.progress(max(0.0, step["hp_b"] / 50), text=f"Rival: {step['hp_b']} / 50 HP")


# --------------------------------------------------------------------------- #
# Integración del Marketplace: el código vive 100% en MARKETPLACE/ (import
# arriba) — aquí solo se llama a su marketplace_app.render() y se sincroniza
# su wallet simulada con la de este panel (misma dirección, mismos NFTs,
# mismo balance $KUR) en los dos sentidos, para que "Modo Batalla" y
# "Marketplace NFT" cuenten siempre la misma historia dentro de la sesión.
# Ver "Integración modular" en MARKETPLACE/README.md.
# --------------------------------------------------------------------------- #
def render_marketplace_panel(connector: BlockchainConnector, wallet_address: str) -> None:
    mkt_wallet = marketplace_app.get_wallet()

    # Solo se tira (pull) del estado del PVP al ENTRAR a esta pestaña o al
    # cambiar de dirección — nunca en cada rerun. Si se sincronizara siempre,
    # el st.rerun() interno del modal de Comprar/Vender (marketplace_app.py)
    # cortaría la función antes de llegar al push-back de más abajo, y el
    # siguiente rerun pisaría la compra recién hecha con el estado viejo del
    # PVP. Con el guard, una vez sincronizada una dirección, la wallet del
    # Marketplace manda hasta que cambie la dirección, y cada rerun hace
    # push-back de su estado actual hacia el PVP sin perder nada por medio.
    if st.session_state.get("_mkt_synced_address") != wallet_address:
        mkt_wallet.sync_external_state(
            address=wallet_address,
            owned_token_ids=set(connector.owned_token_ids(wallet_address)),
            kur_balance=connector.kur_balance(wallet_address),
        )
        st.session_state["_mkt_synced_address"] = wallet_address

    marketplace_app.render(show_sidebar=False)

    # Marketplace -> PVP: cualquier compra/venta hecha arriba se refleja de
    # inmediato en la wallet simulada del PVP (mismo backend en memoria),
    # para que al volver a "Modo Batalla" se vea el NFT/balance nuevo.
    if connector.simulated_backend is not None:
        connector.simulated_backend.seed_wallet(
            wallet_address, sorted(mkt_wallet.owned_token_ids), mkt_wallet.kur_balance
        )


# --------------------------------------------------------------------------- #
# Selector de Modo/Oponente (y, en modo Competitivo, Apuesta en KUR): vive
# justo debajo del CTA "🔥 NUEVA PARTIDA" en la columna de acción/derecha
# (ver el layout de más abajo). Cada grupo es un st.segmented_control de
# selección única — mutuamente excluyente por construcción (nunca hay 0 ni 2
# activos a la vez dentro del mismo grupo) en vez de pares de botones
# independientes; el key coincide con la clave de session_state que ya lee
# el resto del panel (match_mode, opponent_choice), así el widget es la
# única fuente de verdad. El CSS de más abajo (st-key-battle_mode_bar) pinta
# la opción activa en naranja/rojo y las inactivas en gris apagado.
#
# segmented_control permite deseleccionar su propia opción activa (vuelve
# None) — para que el modo nunca quede "vacío" sin violar las reglas de
# Streamlit, la reposición al valor por defecto se hace en un callback
# on_change (el único momento en que es válido reescribir el key de un
# widget ya instanciado); hacerlo en el cuerpo del script después de crear
# el widget, como antes, disparaba StreamlitWidgetAlreadyInstantiatedError.
# --------------------------------------------------------------------------- #
def _reset_match_mode_if_empty() -> None:
    if st.session_state.get("match_mode") is None:
        st.session_state.match_mode = st.session_state.get("_match_mode_keep") or "free"
    st.session_state["_match_mode_keep"] = st.session_state.match_mode


def render_battle_mode_bar(connector: BlockchainConnector, wallet_address: str, card_pool: list[Card]) -> None:
    # Re-fijar la clave antes del widget: Streamlit borra el estado de un
    # widget que no se dibuja (durante la partida) y al volver al lobby el
    # selector salía sin ninguna opción marcada.
    st.session_state["match_mode"] = (
        st.session_state.get("match_mode") or st.session_state.get("_match_mode_keep") or "free"
    )
    with st.container(key="battle_mode_bar"):
        st.segmented_control(
            "Modo",
            options=["free", "competitive"],
            format_func=lambda v: "🆓 Free" if v == "free" else "💰 Competitivo",
            key="match_mode",
            on_change=_reset_match_mode_if_empty,
            label_visibility="collapsed",
        )

        if st.session_state.match_mode == "competitive":
            # Sin max_value ni recorte forzado de session_state aquí: un
            # max_value recalculado cada rerun a partir del saldo (que a su
            # vez puede depender de cosas que cambian con el propio stake)
            # dejaba el widget inconsistente entre reruns — a veces sin
            # responder a +/- ni a escribir un valor nuevo. El usuario puede
            # teclear o subir/bajar lo que quiera; la comprobación contra el
            # saldo pasa a hacerse solo al pulsar LANZAR RETO (ver
            # render_new_match_button), nunca mientras se edita el número.
            # A todo el ancho del panel (no en columna junto al texto de
            # economía) para que el widget tenga sitio de sobra y Streamlit
            # dibuje sus botones nativos de +/- al enfocarlo — en una columna
            # angosta (como dentro de nft_action_col) el contenedor no tiene
            # los ~150px que esos botones necesitan y quedan recortados.
            acc = econ_server().account(wallet_address)
            if not acc.access_unlocked:
                st.warning(
                    f"🔒 Competitivo bloqueado: necesitas un NFT o {ACCESS_BATTLES_REQUIRED} combates "
                    f"(llevas {acc.battles_total})."
                )
                return
            stake = st.number_input(
                "Apuesta en KUR", min_value=0.0, value=10.0, step=1.0, key="navbar_stake"
            )
            # Bote real (ambos jugadores igualan la apuesta) y comisión de la
            # casa sobre ESE bote — mismo cálculo que economy_rules.report_result.
            pot = stake * 2
            fee = round(pot * HOUSE_FEE_PCT, 2)
            st.caption(
                f"Victoria: +{MONEY_ELO_WIN} Elo · +{pot - fee:.2f} KUR (bote {pot:.2f} − comisión "
                f"{HOUSE_FEE_PCT:.0%}) · Derrota: {MONEY_ELO_LOSS} Elo y tu apuesta · Sin cristales y sin "
                f"límite diario · CERO bots: si nadie acepta en {CHALLENGE_EXPIRY_SECONDS} s se relanza "
                f"la búsqueda · Elo {acc.elo}"
            )
        else:
            acc = econ_server().account(wallet_address)
            if acc.has_nft:
                st.caption(
                    f"💎 Free con cristales: apuestas {CRYSTAL_STAKE} 💎 (tienes {acc.crystals}) · "
                    f"Victoria: te llevas los cristales en juego, +{CRYSTAL_ELO_WIN} Elo y "
                    f"+{CRYSTAL_WIN_KUR_REWARD:.0f} $KUR · Derrota: {CRYSTAL_ELO_LOSS} Elo y pierdes tu cristal · "
                    f"{CHALLENGE_EXPIRY_SECONDS} s sin rival real → Bot · Elo {acc.elo}"
                )
            else:
                left = econ_server().free_ranked_remaining(wallet_address)
                st.caption(
                    f"🎟️ Free sin cristales: te quedan {left}/{FREE_DAILY_RANKED_LIMIT} hoy · "
                    f"Victoria +{FREE_RANKED_ELO_WIN} Elo · Derrota {FREE_RANKED_ELO_LOSS} Elo · sin $KUR · "
                    f"{CHALLENGE_EXPIRY_SECONDS} s sin rival real → Bot · Elo {acc.elo}"
                )


# --------------------------------------------------------------------------- #
# CTA principal de la pantalla de Batalla: botón grande, ancho y llamativo
# (rojo/naranja vivo, ver CSS "st-key-new_match_cta" más abajo), arriba de
# todo en la columna de acción/derecha — justo debajo va render_battle_mode_bar
# (Modo/Oponente) en el mismo layout, ver más abajo. Vive aparte del bloque
# compacto de NFT (izquierda) para que destaque como la acción principal
# antes de empezar a jugar.
# --------------------------------------------------------------------------- #
def _reset_tactical_selection(player_key: str | None = None) -> None:
    """Limpia la carta/zona de ataque/zona de defensa elegidas en la arena
    (puntos tácticos) — tras arrancar partida, confirmar un turno o terminar
    uno (las cartas jugadas o rotadas salen de la mano, así que su card_id puede
    dejar de existir). `player_key=None` limpia ambos jugadores a la vez."""
    for pk in (player_key,) if player_key else ("a", "b"):
        st.session_state[f"sel_cards_{pk}"] = []
        st.session_state[f"active_card_{pk}"] = None
        st.session_state[f"zones_{pk}"] = {}


_FX_KEYS = ("energy_fx", "regen_fx", "hp_fx")
_FX_TTL_SECONDS = 2.5


def _fx_begin() -> None:
    """Inicio de un lote de avisos flotantes (gasto/daño/inactividad): si los
    avisos guardados son de una acción ANTERIOR (más de 1s), se descartan
    para no sumarlos a los nuevos. Todos los avisos de una misma acción se
    generan en el mismo rerun, a milisegundos unos de otros."""
    if time.time() - st.session_state.get("fx_time", 0.0) > 1.0:
        for k in _FX_KEYS:
            st.session_state[k] = {}
    st.session_state["fx_time"] = time.time()
    st.session_state["energy_fx_nonce"] = time.time_ns()


def _flash_energy_spend(player_key: str, amount: int) -> None:
    """Encola el feedback "-N" (texto flotante rojo bajo la barra de energía)
    para `player_key`. Se llama justo tras cada gasto real (play_card del
    jugador, bot_choose_plays del Bot) — no en end_turn, que ya ha vaciado
    staged_plays y por tanto energy_spent_this_turn vuelve a 0. render_arena
    lo pinta durante _FX_TTL_SECONDS (no lo borra al primer render: un rerun
    extra, p.ej. el clic de reintento del temporizador al cerrar el turno
    por tiempo, lo borraba a los ~100ms sin que llegara a verse)."""
    if amount <= 0:
        return
    _fx_begin()
    fx = st.session_state.setdefault("energy_fx", {})
    fx[player_key] = fx.get(player_key, 0) + amount


def _end_turn_with_fx(match, timed_out: bool = False) -> None:
    """end_turn() + encola el aviso de DAÑO recibido por cada jugador (HP
    antes vs. después) para que render_arena lo muestre igual que el gasto
    de energía. Una curación neta (+N) también se muestra, en verde."""
    hp_before = {"a": match.player_a.hp, "b": match.player_b.hp}
    # Energía que quedará tras pagar lo jugado: lo que se sume por encima es
    # la recarga del nuevo turno (burbuja "+N ⚡ ✨").
    energy_left = {
        "a": max(0, match.player_a.energy - match.player_a.energy_spent_this_turn),
        "b": max(0, match.player_b.energy - match.player_b.energy_spent_this_turn),
    }
    try:
        end_turn(match, timed_out=timed_out)
    finally:
        _fx_begin()
        fx = st.session_state.setdefault("hp_fx", {})
        for pk, pl in (("a", match.player_a), ("b", match.player_b)):
            delta = pl.hp - hp_before[pk]
            if delta:
                fx[pk] = fx.get(pk, 0) + delta
        rfx = st.session_state.setdefault("regen_fx", {})
        for pk, pl in (("a", match.player_a), ("b", match.player_b)):
            gain = pl.energy - energy_left[pk]
            if gain > 0:
                rfx[pk] = gain


def _launch_match(
    connector: BlockchainConnector, wallet_address: str, card_pool: list[Card],
    name_b: str, hp_b: int | None = None, rival_avatar_id: int | None = None,
    stake_kur: float = 0.0, challenge_id: int | None = None,
) -> None:
    """Arranca una partida ya resuelta de oponente (Bot, Hotseat o rival de
    Reto 1v1) — usado tanto por 🔥 NUEVA PARTIDA como por el desenlace de
    render_bot_challenges_feed() y render_challenges_panel()."""
    rarity = resolve_equipped_rarity(connector, wallet_address)
    base_hp = compute_base_hp(rarity)
    rng = random.Random()
    deck_a = build_deck_from_pool(card_pool, rng=rng)
    deck_b = build_deck_from_pool(card_pool, rng=rng)
    deck_a = apply_nft_buffs_to_deck(deck_a, rarity)
    if hp_b is None:
        hp_b = compute_base_hp(NFTRarity.NONE) if st.session_state.vs_bot else base_hp
    st.session_state.match = start_match(
        deck_a, deck_b, hp_a=base_hp, hp_b=hp_b, name_a="Tú (Jugador A)", name_b=name_b,
    )
    st.session_state["turn_start_time"] = time.time()
    st.session_state["match_economy_mode"] = st.session_state.match_mode
    # Apuesta del reto aceptado (o de "navbar_stake" si lo lanzamos nosotros)
    # — el bote real de la partida es el doble (ambos jugadores la igualan),
    # ver la liquidación en el bloque "state['status'] != ONGOING" más abajo.
    st.session_state["match_stake"] = stake_kur
    st.session_state["match_reward_settled"] = False
    # Reto del libro mayor (economy_rules) al que pertenece esta partida —
    # Free con/sin cristales o Competitivo: se liquida (Elo, cristales,
    # $KUR, bote) al terminar. None en Hotseat (práctica local sin economía).
    st.session_state["match_challenge_id"] = challenge_id
    # El rival/BOT no tiene "wallet" propia que consultar, así que su marco
    # no puede leer un equipped_token_id real — antes caía siempre al Fox
    # fijo DEFAULT_GAMENFT_ID (gamenft_avatar_path), por lo que el combate
    # mostraba siempre los mismos dos personajes. Si quien llama ya conoce
    # el Maestro del rival (p.ej. render_bot_challenges_feed, que lo mostró
    # en su tarjeta de reto) lo pasa explícito para que coincida; si no,
    # random.choice() sobre el catálogo completo de list_gamenft_ids().
    st.session_state["rival_avatar_id"] = (
        rival_avatar_id if rival_avatar_id is not None else rng.choice(list_gamenft_ids())
    )
    st.session_state["arena_background_path"] = str(rng.choice(list_escenario_paths()))
    _reset_tactical_selection()


def render_new_match_button(connector: BlockchainConnector, wallet_address: str, card_pool: list[Card]) -> None:
    is_competitive = st.session_state.match_mode == "competitive"
    with st.container(key="new_match_cta"):
        clicked = st.button("⚔️ LANZAR RETO ⚔️", key="navbar_new_match", width="stretch", type="primary")
    if clicked:
        # El saldo solo se comprueba AQUÍ, al pulsar — nunca mientras se
        # edita el número de arriba (eso fue lo que dejaba el widget de
        # apuesta atascado, ver el comentario en render_battle_mode_bar).
        stake = st.session_state.get("navbar_stake", 10.0) if is_competitive else 0.0
        balance = connector.kur_balance(wallet_address)
        if is_competitive and stake > balance:
            st.error(
                f"⚠️ No puedes lanzar este reto: tu apuesta ({stake:.2f} $KUR) "
                f"supera tu saldo disponible ({balance:.2f} $KUR)."
            )
        elif st.session_state.opponent_choice == "Bot":
            # Publica nuestro propio reto en el libro mayor y en la columna
            # de la derecha (render_bot_challenges_feed). Free: a los 60 s
            # sin rival real -> Bot. Competitivo: a los 60 s se relanza, nunca
            # Bot (apuesta en escrow hasta que alguien acepte o cancelemos).
            # Hotseat ya tiene un rival local conocido, no necesita buscar.
            try:
                if is_competitive:
                    ch = econ_server().create_challenge(wallet_address, ChallengeKind.MONEY, stake, Currency.KUR)
                    push_kur(connector, wallet_address)
                else:
                    ch = econ_server().create_challenge(wallet_address, ChallengeKind.FREE)
            except EconomyError as e:
                st.error(f"⚠️ {e}")
            else:
                st.session_state["own_challenge_id"] = ch.id
                st.session_state["matchmaking_start"] = ch.created_at
                st.rerun()
        else:
            # Hotseat: práctica local entre dos personas en el mismo equipo,
            # sin economía (no mueve Elo, cristales ni $KUR).
            _launch_match(connector, wallet_address, card_pool, name_b="Jugador B")
            st.rerun()


# --------------------------------------------------------------------------- #
# Columna derecha del lobby ("🔍 RETOS DISPONIBLES EN TIEMPO REAL"): combina
# nuestro propio reto (si ⚔️ LANZAR RETO está buscando rival, puesto por
# render_new_match_button) con el feed de salas de bots — no hay backend de
# matchmaking real entre wallets en este prototipo (ver _launch_match).
#
# El Maestro NFT de cada fila (propio o de bot) sale de random.choice()
# sobre TODO el catálogo de list_gamenft_ids(), no de un id fijo — antes el
# rival siempre caía al mismo Fox por defecto (DEFAULT_GAMENFT_ID, ver
# gamenft_avatar_path) porque rival_avatar_id nunca se fijaba, así que el
# tablero y esta lista repetían siempre los dos mismos personajes. La rareza
# de cada bot se deriva de ese mismo id (derive_placeholder_rarity, la misma
# función que usa el selector de Maestro propio) en vez de un pool de
# rarezas aparte, para no tener dos fuentes de verdad.
#
# Todos los retos (propio y ajenos, Free y Competitivo) viven en el libro
# mayor (economy_rules) con su expires_at real: el temporizador descuenta en
# vivo (JS cada 250ms) y al llegar a 0 un botón oculto fuerza un rerun que
# llama a process_expired():
#   - Free (con o sin cristales): 60 s sin retador real -> partida vs Bot.
#   - Competitivo: 60 s sin retador real -> se RELANZA la búsqueda; nunca Bot.
# "Cancelar Reto" o aceptar el reto de otro jugador retiran el propio y
# devuelven lo apostado (cristal, oportunidad del día o $KUR en escrow).
# --------------------------------------------------------------------------- #
SIMULATED_BOT_NAME_POOL = [
    "KitsuneMaster_99", "ShadowKitsune", "CyberFox_X", "KitsuneRogue", "FireFox_99",
    "NightVixen", "PhantomFox", "BlazeKitsune", "FrostFox_7", "VoidKitsune",
    "EmberTail", "MoonVixen", "RoninFox_21", "SakuraKit", "StormKitsune", "AshFox_88",
]
BOT_CHALLENGE_COUNT = 5
# Simulación de jugadores reales (prototipo sin backend multijugador):
# probabilidad de que un jugador simulado acepte NUESTRO reto competitivo en
# cada ventana de 60 s. Si no acepta nadie, la búsqueda se relanza; jamás
# entra un bot en Competitivo.
SIM_ACCEPT_OWN_MONEY_PROB = 0.35


def _sim_meta(rng: random.Random, name: str) -> dict:
    ids = list_gamenft_ids()
    gid = rng.choice(ids) if ids else rng.randrange(1600)
    return {"name": name, "gamenft_id": gid, "rarity": derive_placeholder_rarity(gid)}


def _settle_offscreen_sim_matches(rng: random.Random) -> None:
    """Partidas de jugadores simulados contra el bot (Free): se liquidan
    fuera de pantalla para que sus retos salgan de la lista."""
    econ = econ_server()
    for ch in list(econ.challenges.values()):
        if ch.status == ChallengeStatus.VS_BOT.value and ch.creator.startswith("sim:"):
            econ.report_result(ch.id, rng.choice([ch.creator, BOT]))


def _refresh_sim_challenges(wallet_address: str, competitive: bool) -> list:
    """Lista pública de retos de jugadores reales que `wallet_address` puede
    aceptar (economy_rules.visible_challenges). Sin backend multijugador en
    este prototipo, esos jugadores son cuentas simuladas (sim:*) del mismo
    libro mayor, creadas con la MISMA variante que le toca al jugador:
      - Free con NFT    -> retadores con NFT y cristales (apuestan 1 💎)
      - Free sin NFT    -> retadores sin NFT (Free sin cristales)
      - Competitivo     -> retadores con NFT y saldo, apuesta en $KUR
    Sus retos Free que nadie acepta en 60 s juegan contra un bot (fuera de
    pantalla); sus retos competitivos que expiran se relanzan, y para que la
    lista rote el jugador simulado se cansa y lo cancela tras un relanzamiento."""
    econ = econ_server()
    rng = random.Random()
    econ.process_expired()
    _settle_offscreen_sim_matches(rng)
    kind = ChallengeKind.MONEY if competitive else econ.account(wallet_address).free_kind
    if competitive:
        for ch in list(econ.challenges.values()):
            if (ch.creator.startswith("sim:") and ch.kind == ChallengeKind.MONEY.value
                    and ch.status == ChallengeStatus.OPEN.value and ch.relaunches >= 1):
                econ.cancel_challenge(ch.creator, ch.id)
    meta = st.session_state.sim_challenge_meta
    visible = econ.visible_challenges(wallet_address, kind)
    tries = 0
    while len(visible) < BOT_CHALLENGE_COUNT and tries < 40:
        tries += 1
        name = rng.choice(SIMULATED_BOT_NAME_POOL)
        sim_wallet = f"sim:{name.lower()}"
        if econ.open_challenge_of(sim_wallet):
            continue
        m = _sim_meta(rng, name)
        try:
            if kind is ChallengeKind.MONEY:
                econ.set_nfts(sim_wallet, [m["gamenft_id"]])
                econ.sync_balance(sim_wallet, Currency.KUR, 500.0)
                ch = econ.create_challenge(sim_wallet, ChallengeKind.MONEY, float(rng.randint(5, 50)), Currency.KUR)
            elif kind is ChallengeKind.CRYSTAL:
                econ.set_nfts(sim_wallet, [m["gamenft_id"]])
                econ.set_crystals(sim_wallet, rng.randint(1, 5))
                ch = econ.create_challenge(sim_wallet, ChallengeKind.FREE)
            else:
                econ.set_nfts(sim_wallet, [])
                ch = econ.create_challenge(sim_wallet, ChallengeKind.FREE)
        except EconomyError:
            continue  # p.ej. simulado sin NFT que ya agotó sus 10 de hoy
        age = rng.randint(0, 30)  # publicado hace un rato: temporizadores variados
        ch.created_at -= age
        ch.expires_at -= age
        meta[ch.id] = m
        visible = econ.visible_challenges(wallet_address, kind)
    return visible


def _sim_accepts_own_money_challenge(own_ch) -> None:
    """Simulación: en cada relanzamiento de NUESTRO reto competitivo, a veces
    llega un jugador real (simulado, con NFT y saldo) y lo acepta."""
    econ = econ_server()
    rng = random.Random()
    if rng.random() >= SIM_ACCEPT_OWN_MONEY_PROB:
        return
    name = rng.choice(SIMULATED_BOT_NAME_POOL)
    sim_wallet = f"sim:{name.lower()}"
    if econ.open_challenge_of(sim_wallet):
        econ.cancel_challenge(sim_wallet, econ.open_challenge_of(sim_wallet).id)
    m = _sim_meta(rng, name)
    econ.set_nfts(sim_wallet, [m["gamenft_id"]])
    econ.sync_balance(sim_wallet, Currency.KUR, max(500.0, own_ch.stake))
    econ.accept_challenge(sim_wallet, own_ch.id)
    st.session_state.sim_challenge_meta[own_ch.id] = m


def _render_challenge_row(gamenft_id: int | None, name_html: str, detail_html: str, timer_id: str, pct: int):
    """Fila compacta compartida: avatar pequeño + nombre/rareza (fila 1),
    barra de tiempo + botón de acción (fila 2, en su propio contenedor con
    key para poder alinear ese botón a la derecha por CSS sin afectar a la
    fila de arriba) — el botón en sí se devuelve sin abrir, para que cada
    caller decida su texto/key/color ("Aceptar Reto" verde o "Cancelar
    Reto" rojo)."""
    avatar_col, info_col = st.columns([1, 4])
    with avatar_col:
        st.image(str(gamenft_avatar_path(gamenft_id)), width=40)
    with info_col:
        st.markdown(name_html + detail_html, unsafe_allow_html=True)
    with st.container(key=f"challenge_action_row_{timer_id}"):
        bar_col, btn_col = st.columns([3, 2])
        with bar_col:
            st.markdown(
                f"<div class='turn-timer-wrap'>"
                f"<div class='turn-timer-track'><div class='turn-timer-fill' "
                f"id='challenge-fill-{timer_id}' style='width:{pct}%'></div></div>"
                f"</div>",
                unsafe_allow_html=True,
            )
    return btn_col


def _challenge_stake_html(ch) -> str:
    if ch.kind == ChallengeKind.MONEY.value:
        return f"<div class='challenge-rival-stake'>💰 Apuesta: {ch.stake:.2f} ${ch.currency}</div>"
    if ch.kind == ChallengeKind.CRYSTAL.value:
        return f"<div class='challenge-rival-stake'>💎 Apuesta {CRYSTAL_STAKE} cristal</div>"
    return "<div class='challenge-rival-stake'>🎟️ Free sin cristales</div>"


def render_bot_challenges_feed(connector: BlockchainConnector, wallet_address: str, card_pool: list[Card]) -> None:
    now = time.time()
    timer_specs = []
    with st.container(key="bot_challenges_feed"):
        st.subheader("🔍 RETOS DISPONIBLES EN TIEMPO REAL")

        notice = st.session_state.get("challenge_notice")
        if notice and time.time() < notice["until"]:
            st.info(notice["text"])
        elif notice:
            st.session_state.pop("challenge_notice", None)
        econ = econ_server()
        is_competitive = st.session_state.match_mode == "competitive"
        own_id = st.session_state.get("own_challenge_id")
        own_ch = econ.challenges.get(own_id) if own_id is not None else None

        if own_ch is not None and own_ch.status == ChallengeStatus.OPEN.value and \
                (own_ch.kind == ChallengeKind.MONEY.value) != is_competitive:
            # Cambió de modo con un reto abierto del otro modo: se retira y
            # se reembolsa (cristal, oportunidad del día o apuesta).
            econ.cancel_challenge(wallet_address, own_ch.id)
            push_kur(connector, wallet_address)
            st.session_state["own_challenge_id"] = own_ch = None
            st.session_state["matchmaking_start"] = None
            st.session_state["challenge_notice"] = {
                "text": "Reto retirado al cambiar de modo: se te ha devuelto lo apostado.",
                "until": time.time() + 8,
            }

        if own_ch is not None:
            econ.process_expired()
            seen_id, seen_n = st.session_state.get("own_relaunch_seen", (None, 0))
            seen_n = seen_n if seen_id == own_ch.id else 0
            if own_ch.kind == ChallengeKind.MONEY.value and own_ch.relaunches > seen_n \
                    and own_ch.status == ChallengeStatus.OPEN.value:
                st.session_state["own_relaunch_seen"] = (own_ch.id, own_ch.relaunches)
                _sim_accepts_own_money_challenge(own_ch)
            if own_ch.status in (ChallengeStatus.VS_BOT.value, ChallengeStatus.MATCHED.value):
                # Free: 60 s sin rival real -> Bot. Competitivo: solo MATCHED
                # (un jugador real aceptó); el libro mayor jamás asigna bots ahí.
                st.session_state["matchmaking_start"] = None
                st.session_state["own_challenge_id"] = None
                if own_ch.status == ChallengeStatus.VS_BOT.value:
                    _launch_match(connector, wallet_address, card_pool, name_b="Bot", challenge_id=own_ch.id)
                else:
                    m = st.session_state.sim_challenge_meta.get(own_ch.id) or _sim_meta(random.Random(), own_ch.opponent)
                    _launch_match(
                        connector, wallet_address, card_pool, name_b=m["name"],
                        hp_b=compute_base_hp(m["rarity"]), rival_avatar_id=m["gamenft_id"],
                        stake_kur=own_ch.stake, challenge_id=own_ch.id,
                    )
                st.rerun()
            if own_ch.status != ChallengeStatus.OPEN.value:
                st.session_state["matchmaking_start"] = st.session_state["own_challenge_id"] = None
                st.rerun()

            remaining = max(0.0, own_ch.expires_at - now)
            own_rarity = resolve_equipped_rarity(connector, wallet_address)
            pct = max(5, int(remaining / CHALLENGE_EXPIRY_SECONDS * 100))
            relaunch_html = (
                f"<div class='challenge-rival-stake'>🔁 Búsqueda relanzada {own_ch.relaunches} "
                f"ve{'z' if own_ch.relaunches == 1 else 'ces'} · sin bots</div>"
                if own_ch.kind == ChallengeKind.MONEY.value and own_ch.relaunches else ""
            )
            with st.container(key="challenge_row_own"):
                btn_col = _render_challenge_row(
                    st.session_state.get("equipped_token_id"),
                    "<div class='challenge-rival-name'>🦊 Tú (Jugador A)</div>",
                    f"<div class='challenge-rival-nft'>{rarity_label(own_rarity)} · "
                    f"HP {compute_base_hp(own_rarity)} · "
                    f"<span id='challenge-seconds-own'>{int(remaining)}</span>s</div>"
                    f"{_challenge_stake_html(own_ch)}{relaunch_html}",
                    "own", pct,
                )
                with btn_col, st.container(key="cancel_own_challenge_wrap"):
                    if st.button("Cancelar Reto", key="cancel_own_challenge"):
                        if own_ch.status == ChallengeStatus.OPEN.value:
                            econ.cancel_challenge(wallet_address, own_ch.id)  # devuelve lo apostado
                            push_kur(connector, wallet_address)
                        st.session_state["matchmaking_start"] = None
                        st.session_state["own_challenge_id"] = None
                        st.rerun()
            timer_specs.append(
                {"id": "own", "remainingMs": remaining * 1000, "totalMs": CHALLENGE_EXPIRY_SECONDS * 1000}
            )

        acc = econ.account(wallet_address)
        if is_competitive and not acc.access_unlocked:
            st.caption(
                f"🔒 Competitivo bloqueado: necesitas un NFT o {ACCESS_BATTLES_REQUIRED} combates "
                f"(llevas {acc.battles_total})."
            )
            visible = []
        else:
            visible = _refresh_sim_challenges(wallet_address, is_competitive)
            if not visible:
                st.caption("Ahora mismo no hay retos abiertos de tu modalidad.")
        meta = st.session_state.sim_challenge_meta
        can_free, why_free = econ.can_play_free(wallet_address)
        balance = acc.balances[Currency.KUR.value]
        for ch in visible:
            m = meta.get(ch.id, {"name": ch.creator, "gamenft_id": None, "rarity": NFTRarity.NONE})
            remaining = max(0.0, ch.expires_at - now)
            pct = max(5, int(remaining / CHALLENGE_EXPIRY_SECONDS * 100))
            tid = f"c{ch.id}"
            if is_competitive:
                blocked_label = "🚫 Sin saldo" if ch.stake > balance else None
            else:
                blocked_label = None if can_free else (
                    f"🎟️ 0/{FREE_DAILY_RANKED_LIMIT} hoy" if not acc.has_nft else "💎 Sin cristales")
            with st.container(key=f"challenge_row_{tid}"):
                btn_col = _render_challenge_row(
                    m["gamenft_id"],
                    f"<div class='challenge-rival-name'>🦊 {m['name']}</div>",
                    f"<div class='challenge-rival-nft'>Fox #{m['gamenft_id']} · "
                    f"{rarity_label(m['rarity'])} · HP {compute_base_hp(m['rarity'])} · "
                    f"<span id='challenge-seconds-{tid}'>{int(remaining)}</span>s</div>"
                    f"{_challenge_stake_html(ch)}",
                    tid, pct,
                )
                with btn_col:
                    accepted = st.button(
                        blocked_label or "Aceptar Reto", key=f"accept_challenge_{tid}",
                        disabled=blocked_label is not None,
                    )
                if accepted:
                    try:
                        if own_ch is not None and own_ch.status == ChallengeStatus.OPEN.value:
                            econ.cancel_challenge(wallet_address, own_ch.id)
                        econ.accept_challenge(wallet_address, ch.id)
                    except EconomyError as e:
                        st.error(f"⚠️ {e}")
                    else:
                        push_kur(connector, wallet_address)
                        st.session_state["matchmaking_start"] = None
                        st.session_state["own_challenge_id"] = None
                        st.session_state.vs_bot = True
                        st.session_state["_force_opponent_bot"] = True
                        _launch_match(
                            connector, wallet_address, card_pool,
                            name_b=m["name"], hp_b=compute_base_hp(m["rarity"]),
                            rival_avatar_id=m["gamenft_id"], challenge_id=ch.id,
                            stake_kur=ch.stake,
                        )
                        st.rerun()
            timer_specs.append(
                {"id": tid, "remainingMs": remaining * 1000, "totalMs": CHALLENGE_EXPIRY_SECONDS * 1000}
            )
        if not is_competitive and not can_free:
            st.caption(f"⚠️ {why_free}")

        with st.container(key="bot_challenges_refresh_trigger"):
            st.button("↻", key="bot_challenges_refresh_btn")
        components.html(
            f"""<script>
            (function () {{
                const specs = {json.dumps(timer_specs)};
                const startedAt = Date.now();
                const btn = window.parent.document.querySelector(
                    '[class*="st-key-bot_challenges_refresh_trigger"] button'
                );
                let fired = false;
                const iv = setInterval(function () {{
                    const elapsed = Date.now() - startedAt;
                    let anyExpired = false;
                    specs.forEach(function (s) {{
                        const left = Math.max(0, s.remainingMs - elapsed);
                        const pct = Math.max(0, Math.min(100, (left / s.totalMs) * 100));
                        const fill = window.parent.document.getElementById('challenge-fill-' + s.id);
                        const label = window.parent.document.getElementById('challenge-seconds-' + s.id);
                        if (fill) fill.style.width = pct + '%';
                        if (label) label.textContent = Math.ceil(left / 1000);
                        if (left <= 0) anyExpired = true;
                    }});
                    if (anyExpired && !fired) {{
                        fired = true;
                        clearInterval(iv);
                        if (btn) btn.click();
                    }}
                }}, 250);
            }})();
            </script>""",
            height=0,
        )


# --------------------------------------------------------------------------- #
# Panel "🤝 Retos 1v1": atajo para publicar un reto COMPETITIVO directo en
# $KUR. Es exactamente el mismo reto del libro mayor que "⚔️ LANZAR RETO" en
# modo Competitivo: requiere NFT o 300 combates, la apuesta queda en escrow,
# el ganador se lleva el bote menos la comisión de la casa y, si nadie lo
# acepta en 60 s, la búsqueda se relanza — jamás se juega contra un bot.
# --------------------------------------------------------------------------- #
def render_challenges_panel(connector: BlockchainConnector, wallet_address: str, card_pool: list[Card]) -> None:
    st.title("🤝 Retos 1v1")
    st.caption(
        f"Reto competitivo directo en $KUR contra un jugador real. El ganador se lleva el bote "
        f"(las dos apuestas) menos un {HOUSE_FEE_PCT:.0%} de comisión. Sin bots: si nadie acepta en "
        f"{CHALLENGE_EXPIRY_SECONDS} s, la búsqueda se relanza hasta que alguien acepte o lo canceles."
    )
    econ = econ_server()
    acc = econ.account(wallet_address)
    if not acc.access_unlocked:
        st.warning(
            f"🔒 Bloqueado: necesitas un NFT o {ACCESS_BATTLES_REQUIRED} combates (llevas {acc.battles_total})."
        )
        return

    own_id = st.session_state.get("own_challenge_id")
    own_ch = econ.challenges.get(own_id) if own_id is not None else None
    if own_ch is not None and own_ch.status == ChallengeStatus.OPEN.value:
        st.info(
            f"Ya tienes un reto abierto ({_challenge_stake_html(own_ch).split('>', 1)[1].rsplit('<', 1)[0]}). "
            "Síguelo o cancélalo en ⚔️ Modo Batalla."
        )
        if st.button("⚔️ Ir a Modo Batalla", key="challenge_goto_battle", type="primary"):
            st.session_state.panel_mode = PANEL_OPTIONS[0]
            st.rerun()
        return

    kur_balance = acc.balances[Currency.KUR.value]
    st.caption(f"Tu balance: {kur_balance:,.2f} $KUR")
    stake = st.number_input(
        "Apuesta por jugador ($KUR)", min_value=1.0, value=100.0, step=10.0, key="challenge_stake"
    )
    pot = stake * 2
    fee = round(pot * HOUSE_FEE_PCT, 2)
    st.caption(
        f"Bote: {pot:.0f} $KUR · comisión {HOUSE_FEE_PCT:.0%}: {fee:.2f} $KUR · "
        f"el ganador se lleva: {pot - fee:.2f} $KUR · Elo +{MONEY_ELO_WIN} / {MONEY_ELO_LOSS}"
    )
    insufficient = kur_balance < stake
    if insufficient:
        st.warning("Balance insuficiente para esta apuesta.")
    if st.button(
        "⚔️ Lanzar reto (buscar rival real)", key="challenge_launch", type="primary", disabled=insufficient,
    ):
        try:
            ch = econ.create_challenge(wallet_address, ChallengeKind.MONEY, stake, Currency.KUR)
        except EconomyError as e:
            st.error(f"⚠️ {e}")
            return
        push_kur(connector, wallet_address)
        st.session_state["own_challenge_id"] = ch.id
        st.session_state["matchmaking_start"] = ch.created_at
        st.session_state["opponent_choice"] = "Bot"  # = matchmaking online (en Competitivo nunca cae a bot)
        st.session_state["_force_mode_competitive"] = True
        st.session_state.panel_mode = PANEL_OPTIONS[0]
        st.rerun()


# --------------------------------------------------------------------------- #
# Barra rápida del Marketplace (navbar superior, a la derecha del menú ☰):
# balance + accesos directos a Explorar/Inventario, visible solo en el panel
# 🛒 — independiente de render_battle_mode_bar (esa vive en la página de
# Batalla, no aquí).
# --------------------------------------------------------------------------- #
def render_marketplace_quickbar(connector: BlockchainConnector, wallet_address: str) -> None:
    kur_balance = connector.kur_balance(wallet_address)
    owned_count = len(connector.owned_token_ids(wallet_address))
    info_col, explore_col, inv_col = st.columns([2.2, 1.3, 1.3])
    with info_col:
        st.markdown(
            "<div class='kr-muted'>Balance rápido</div>"
            f"<div class='kr-price' style='font-size:0.95rem'>{kur_balance:,.2f} $KUR · {owned_count} Fox</div>",
            unsafe_allow_html=True,
        )
    with explore_col:
        st.caption("​")
        if st.button("🛒 Explorar", key="navbar_mkt_explore", width="stretch"):
            st.session_state["mkt_active_tab"] = marketplace_app.TAB_EXPLORE
            st.rerun()
    with inv_col:
        st.caption("​")
        if st.button("🎒 Inventario", key="navbar_mkt_inventory", width="stretch"):
            st.session_state["mkt_active_tab"] = marketplace_app.TAB_INVENTORY
            st.rerun()


def render_wallet_and_nft_bar(
    connector: BlockchainConnector, wallet_address: str, card_pool: list[Card]
) -> None:
    """Wallet + NFT equipado: selector arriba (fuera de la tarjeta), debajo
    una fila con la tarjeta de NFT (imagen + estadísticas) a la izquierda y
    el bloque de acción de combate (CTA Lanzar Reto + Modo/Oponente) a la
    derecha, todo dentro del mismo panel — y la Dirección de wallet como pie
    de ancho completo. La rareza del NFT elegido aquí decide el HP base y el
    buff de mazo (ver resolve_equipped_rarity, usado por 🔥 NUEVA PARTIDA)."""
    owned = connector.owned_token_ids(wallet_address)
    gamenft_ids = list_gamenft_ids()
    kur_balance = connector.kur_balance(wallet_address)
    short_addr = f"{wallet_address[:6]}…{wallet_address[-4:]}" if len(wallet_address) > 12 else wallet_address

    with st.container(key="wallet_nft_bar"):
        if not gamenft_ids:
            st.session_state.equipped_token_id = None
            st.metric("🦊 Dirección", short_addr)
            w2, w3 = st.columns(2)
            w2.metric("NFTs", len(owned))
            w3.metric("Balance $KUR", f"{kur_balance:,.2f}")
            st.caption("No se encontraron imágenes en GAMENFT/ — jugarás con el HP base por defecto (sin NFT).")
            render_crystal_selector(wallet_address)
            render_access_panel(connector, wallet_address)
            render_new_match_button(connector, wallet_address, card_pool)
            render_battle_mode_bar(connector, wallet_address, card_pool)
            return

        if st.session_state.get("equipped_token_id") not in gamenft_ids:
            best = connector.best_rarity_for_wallet(wallet_address)
            st.session_state.equipped_token_id = next(
                (tid for tid in owned if derive_placeholder_rarity(tid) == best),
                owned[0] if owned else gamenft_ids[0],
            )

        equipped_id = st.selectbox(
            "🎴 Maestro equipado",
            gamenft_ids,
            format_func=lambda tid: (
                f"Fox #{tid} · {rarity_label(derive_placeholder_rarity(tid))}"
                + ("" if tid in owned else " (no en wallet)")
            ),
            key="equipped_token_id",
        )
        rarity = derive_placeholder_rarity(equipped_id)
        buff_pct = DECK_STAT_BUFF_PCT.get(rarity, 0.0)

        with st.container(key="nft_card_row"):
            nft_col, action_col = st.columns([1, 1.3], gap="medium")
            with nft_col.container(key="nft_thumb_stats_row"):
                img_col, stats_col = st.columns([1, 2], gap="small")
                with img_col:
                    fox_path = PVP_ROOT / "GAMENFT" / f"fox_{equipped_id}.png"
                    if fox_path.exists():
                        st.image(str(fox_path), width=180)
                with stats_col.container(key="nft_stats_col"):
                    st.metric("NFTs · $KUR", f"{len(owned)} · {kur_balance:,.0f}")
                    st.metric("Rareza", rarity_label(rarity))
                    st.metric("HP base", compute_base_hp(rarity))
                    st.metric("Buff mazo", f"+{buff_pct:.0%}" if buff_pct else "—")
            with nft_col:
                render_crystal_selector(wallet_address)
                render_access_panel(connector, wallet_address)
            with action_col.container(key="nft_action_col"):
                render_new_match_button(connector, wallet_address, card_pool)
                render_battle_mode_bar(connector, wallet_address, card_pool)

        st.caption(f"🦊 Dirección {short_addr}")


def render_wallet_config_expander(connector: BlockchainConnector, wallet_address: str) -> None:
    """Herramientas de testeo de la wallet simulada (cambiar dirección,
    previsualizar un NFT, recargar Token IDs/$KUR) — antes en la barra
    lateral, ahora plegadas en la página de Batalla para no molestar
    durante la partida."""
    with st.expander("🛠️ Configuración y Recarga de Wallet Simulada"):
        st.caption(f"Modo: {'SIMULACIÓN' if connector.is_simulated else 'BSC real'}")
        new_address = st.text_input("Dirección de wallet", value=wallet_address, key="battle_wallet_address_input")
        if new_address != wallet_address:
            st.session_state.wallet_address = new_address
            st.rerun()

        owned = connector.owned_token_ids(wallet_address)
        kur_balance = connector.kur_balance(wallet_address)

        if owned:
            preview_id = st.selectbox("Previsualizar NFT", owned, key="preview_nft")
            fox_path = PVP_ROOT / "GAMENFT" / f"fox_{preview_id}.png"
            if fox_path.exists():
                st.image(
                    str(fox_path),
                    caption=f"Fox #{preview_id} · {rarity_label(derive_placeholder_rarity(preview_id))}",
                    width="stretch",
                )

        st.divider()
        st.divider()
        st.caption("Recargar wallet de prueba con otros NFTs/KUR:")
        new_ids = st.text_input(
            "Token IDs (separados por coma)", value=",".join(str(i) for i in owned), key="battle_wallet_ids"
        )
        new_kur = st.number_input(
            "$KUR simulado", min_value=0.0, value=float(kur_balance), step=10.0, key="battle_wallet_kur"
        )
        econ = econ_server()
        new_battles = st.number_input(
            f"Combates jugados (pruebas · {ACCESS_BATTLES_REQUIRED} desbloquean sin NFT)", min_value=0,
            value=int(econ.account(wallet_address).battles_total), step=50, key="battle_wallet_battles",
        )
        if st.button("💾 Actualizar wallet simulada", width="stretch", key="battle_wallet_update"):
            econ.set_battles(wallet_address, int(new_battles))
            try:
                ids = [int(x.strip()) for x in new_ids.split(",") if x.strip()]
            except ValueError:
                ids = []
            connector.simulated_backend.seed_wallet(wallet_address, ids, new_kur)
            st.rerun()


def render_wallet_badge(connector: BlockchainConnector, wallet_address: str) -> None:
    kur_balance = connector.kur_balance(wallet_address)
    short_addr = f"{wallet_address[:6]}…{wallet_address[-4:]}" if len(wallet_address) > 12 else wallet_address
    with st.popover(f"🦊 {short_addr} · {kur_balance:,.2f} $KUR", width="stretch"):
        st.caption("Wallet simulada — recargar balance / NFTs (misma wallet de toda la sesión)")
        owned = connector.owned_token_ids(wallet_address)
        new_ids = st.text_input(
            "Token IDs (separados por coma)", value=",".join(str(i) for i in owned), key="navbar_wallet_ids"
        )
        new_kur = st.number_input(
            "$KUR simulado", min_value=0.0, value=float(kur_balance), step=10.0, key="navbar_wallet_kur"
        )
        if st.button("💾 Guardar", key="navbar_wallet_save", width="stretch", type="primary"):
            try:
                ids = [int(x.strip()) for x in new_ids.split(",") if x.strip()]
            except ValueError:
                ids = []
            if connector.simulated_backend is not None:
                connector.simulated_backend.seed_wallet(wallet_address, ids, new_kur)
            st.rerun()


st.set_page_config(
    page_title="Kurama Run · PVP Simulator", page_icon="⚔️", layout="wide", initial_sidebar_state="collapsed"
)
init_state()
catalog_dicts = load_catalog()
catalog = [Card.from_dict(d) for d in catalog_dicts]
# Pool de combate: 16 cartas base (una por combinación ATK/DEF) + 7 especiales; el
# mazo de cada partida es el set completo barajado, sin repeticiones (ver
# pvp_engine.build_deck_from_pool).
card_pool = list(catalog)
cards_by_id = {c.id: c for c in catalog}

# Disponibles pronto, antes de renderizar nada, para que la barra de
# configuración contextual de arriba (render_context_bar/render_wallet_badge)
# pueda leer la wallet y lanzar partidas de inmediato — todo el panel lee el
# mismo st.session_state.connector / .wallet_address, nunca se desincroniza.
connector: BlockchainConnector = st.session_state.connector
current_wallet_address = st.session_state.wallet_address
sync_economy(connector, current_wallet_address)
st.session_state.vs_bot = st.session_state.opponent_choice == "Bot"

# Tema compartido con el Marketplace (../kurama_theme.py): fondo oscuro,
# tipografías gaming y badges de rareza/estado comunes a los dos paneles.
st.markdown(kurama_theme.base_css(), unsafe_allow_html=True)

# --------------------------------------------------------------------------- #
# SFX 8-bit al interactuar (botones, zona, Resolver turno): un solo listener
# global de click en window.parent.document (mismo truco que el resto del
# JS del archivo), instalado una vez (guard en window.parent) para que no se
# duplique en cada rerun de Streamlit. El pitido se sintetiza con WebAudio
# (sin archivo de audio, sin latencia) y se calla solo si el reproductor de
# música de fondo (nav_music_player, único control de mute que existe hoy)
# está silenciado o pausado — así respeta el mismo mute que la música.
# --------------------------------------------------------------------------- #
components.html(
    """<script>
    (function () {
        const w = window.parent;
        if (w.__kuramaSfxInstalled) { return; }
        w.__kuramaSfxInstalled = true;
        let audioCtx = null;

        function isMusicMuted() {
            const el = w.document.querySelector('[class*="st-key-nav_music_player"] audio');
            return el ? (el.muted || el.volume === 0) : false;
        }

        function playBlip() {
            if (isMusicMuted()) { return; }
            try {
                const AC = w.AudioContext || w.webkitAudioContext;
                if (!audioCtx) { audioCtx = new AC(); }
                if (audioCtx.state === 'suspended') { audioCtx.resume(); }
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.type = 'square';
                osc.frequency.setValueAtTime(1100, audioCtx.currentTime);
                gain.gain.setValueAtTime(0.08, audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.08);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start();
                osc.stop(audioCtx.currentTime + 0.08);
            } catch (e) { /* navegador sin WebAudio: no interrumpe la UI */ }
        }

        w.document.addEventListener('click', function (ev) {
            const hit = ev.target.closest('button, [role="option"], [role="combobox"], [data-baseweb="select"]');
            if (hit) { playBlip(); }
        }, true);
    })();
    </script>""",
    height=0,
)

# --------------------------------------------------------------------------- #
# Navbar superior: solo menú ☰ + badge de wallet (y, en el panel 🛒, la
# barra rápida de Marketplace). Los controles de Modo/Oponente/Nueva
# partida ya no viven aquí — están más abajo, en la página de Batalla
# (render_battle_mode_bar), debajo del panel de Wallet + NFT equipado. No
# hay barra lateral: toda la navegación y configuración vive en el área
# principal.
# --------------------------------------------------------------------------- #
st.session_state.setdefault("panel_mode", PANEL_OPTIONS[0])

nav_menu_col, _nav_spacer, nav_music_col, nav_wallet_col = st.columns([1.5, 4.0, 1.5, 2.1])

with nav_menu_col:
    with st.popover("☰ MENÚ DE NAVEGACIÓN"):
        st.caption("Ir directamente a:")
        for _option in PANEL_OPTIONS:
            if st.button(
                _option,
                key=f"nav_top_{_option}",
                width="stretch",
                type="primary" if st.session_state.panel_mode == _option else "secondary",
            ):
                st.session_state.panel_mode = _option
                st.rerun()

with nav_music_col:
    pass
    # Chiptune de fondo discreto: "Spooky Dungeon" de Memoraphile (OpenGameArt,
    # CC0/CC-BY), en loop y autoplay — el reproductor nativo de Streamlit ya
    # trae su propio botón de play/pausa (sirve de silenciar). Solo se
    # renderiza en combate activo: antes de pulsar NUEVA PARTIDA o tras
    # Victoria/Derrota/Empate, match es None o su status ya no es ONGOING, y
    # el reproductor se desmonta deteniendo el audio al instante.
    # Desactivada temporalmente a petición del equipo (se reintegrará más
    # adelante) — dejo el bloque comentado en vez de borrarlo para no perder
    # la URL/ajustes cuando se reactive.
    # _active_match = st.session_state.get("match")
    # if _active_match is not None and _active_match.status == MatchStatus.ONGOING:
    #     with st.container(key="nav_music_player"):
    #         st.audio(
    #             "https://opengameart.org/sites/default/files/Memoraphile%20-%20Spooky%20Dungeon.mp3",
    #             loop=True,
    #             autoplay=True,
    #         )

with nav_wallet_col:
    render_wallet_badge(connector, current_wallet_address)

panel_mode = st.session_state.panel_mode
if panel_mode.startswith("🛒"):
    render_marketplace_quickbar(connector, current_wallet_address)

st.markdown('<div id="kurama-title"></div>', unsafe_allow_html=True)
if st.session_state.match is None:
    # Solo en el lobby (sin partida activa): durante el combate el ancla de
    # "arena-top-anchor" manda, y si este script también corriera competiría
    # con el de ahí por el scroll en cada rerun (300ms contra 150ms) y ganaba
    # el equivocado, dejando la cabecera del título tapando el tablero.
    st.markdown("""
        <script>
            setTimeout(function() {
                var el = window.parent.document.getElementById('kurama-title');
                if (el) {
                    var rect = el.getBoundingClientRect();
                    window.parent.scrollTo({ top: window.parent.scrollY + rect.top - 20, behavior: 'smooth' });
                }
            }, 300);
        </script>
    """, unsafe_allow_html=True)

# --------------------------------------------------------------------------- #
# Playmat compacto: imágenes de mano más pequeñas, nombre/habilidad a altura
# fija (para que Zona/Jugar queden siempre en la misma línea entre cartas) y
# el feed de combate flotante (ver más abajo) compactos por CSS.
# --------------------------------------------------------------------------- #
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 0.5rem !important;
        padding-bottom: 0rem !important;
    }
    header[data-testid="stHeader"] {
        background: transparent !important;
        height: 2rem !important;
    }
    /* Efecto "presionado" (:active) global para todos los botones del
       juego (⚔️ JUGAR, ℹ️ Detalles, Jugar otra partida, Resolver turno...):
       el botón se hunde y la sombra se aplana al pulsar — un <button> real
       (lo que genera cada st.button/st.popover) dispara :active igual con
       click de ratón que con tap táctil, sin JS necesario. La CTA grande
       "NUEVA PARTIDA" tiene su propio override más abajo (misma física,
       sombra a su escala). */
    .stButton > button,
    .stPopover button {
        transition: transform 0.05s ease, box-shadow 0.05s ease;
    }
    .stButton > button:active,
    .stPopover button:active {
        transform: translateY(2px) scale(0.98);
        box-shadow: inset 0 2px 6px rgba(0, 0, 0, 0.45) !important;
    }
    /* Sin barra lateral: se oculta por completo, junto con su botón de
       abrir/cerrar (kurama_theme.py lo restylea como "☰ MENÚ", pero ya hay
       un menú real arriba — este oculta ese botón huérfano también). */
    [data-testid="stSidebar"],
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="collapsedControl"],
    [data-testid="stExpandSidebarButton"],
    [data-testid="stSidebarCollapseButton"] {
        display: none !important;
    }
    /* Mano de cartas: ancho de columna fijo, imágenes TODAS al mismo
       tamaño exacto (160x240, object-fit: contain para no deformar el
       arte aunque cada PNG traiga su propio recorte/proporción — con
       "cover" se vería bien pero recortaría parte del dibujo en las
       cartas que no encajen justo en 2:3), centradas como grupo en vez de
       repartidas a los lados con huecos en blanco. */
    [class*="st-key-battle_hand"] img {
        width: 416px !important;
        height: 624px !important;
        max-width: 100% !important;
        object-fit: contain;
        background: rgba(255, 255, 255, 0.04);
        border-radius: 8px;
        display: block;
        margin: 0 auto;
    }
    [class*="st-key-battle_hand"] [data-testid="stHorizontalBlock"] {
        justify-content: center !important;
        align-items: stretch !important;
        flex-wrap: wrap;
        gap: 1.5rem !important;
    }
    [class*="st-key-battle_hand"] [data-testid="stColumn"] {
        flex: 1 1 260px !important;
        width: auto !important;
        max-width: 624px !important;
        min-width: 260px;
    }
    /* Cada carta completa (imagen + nombre + info + zona + jugar) a la
       misma altura que sus vecinas de la fila (align-items:stretch arriba
       iguala las columnas; esto reparte ese alto dentro de cada carta para
       que el botón "Jugar" quede siempre a la misma altura entre cartas,
       tengan o no línea de habilidad). */
    [class*="st-key-battle_hand"] [data-testid="stColumn"] [data-testid="stVerticalBlock"] {
        height: 100%;
    }
    [class*="st-key-card_glow_playable"],
    [class*="st-key-card_glow_locked"] {
        display: flex !important;
        flex-direction: column;
        justify-content: space-between;
        height: 100%;
    }
    /* El CSS de selección-por-clic y hover de las cartas de la mano vive
       en su propio <style> aparte, justo después de este bloque gigante
       — ver el comentario ahí para el motivo (bug de parseo detectado en
       pruebas con bloques <style> muy largos). */
    /* Panel flotante: margen superior negativo para que se superponga al
       borde inferior de escenario.png (arena_outer), rozando casi los pies
       de los 2 personajes centrales, en vez de quedar debajo del registro
       de combate — el hueco que la separa de la arena (badges de zona +
       feed de combate, ambos de alto ~fijo) es predecible, así que un
       valor fijo en px basta sin acoplar el CSS al alto exacto de la
       arena (que ya es 100% responsive, ver st-key-arena_outer). */
    [class*="st-key-battle_hand_overlay"] {
        position: relative;
        z-index: 20;
        width: fit-content;
        max-width: 94%;
        /* px fijos otra vez (no %: un intento anterior de escalarlo con el
           ancho, igual que el alto de la arena, subió demasiado el panel y
           tapó las caras de los luchadores) — historial: -340px, -385px,
           -415px. NO reducir el ancho de la arena para "arreglar" el
           scroll: eso descuadra a los Maestros NFT de las esquinas, que
           quedan encima de los luchadores del centro. */
        /* -305px -> -225px: con las cartas ~28% más pequeñas (208x312 ->
           150x225) el panel mide ~87px menos de alto; se sube menos sobre
           el escenario para que su borde inferior quede donde estaba y el
           superior ya no tape a los luchadores ni a las cápsulas del
           Combo Tripartito (que además quedan libres a los lados porque el
           panel pasa de ~706px a ~500px de ancho). */
        margin: -225px auto 0;
        background: rgba(10, 10, 14, 0.6);
        border: 1px solid rgba(255, 255, 255, 0.14);
        border-radius: 16px;
        padding: 0.2rem 0.3rem 0.15rem;
        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.45);
        /* El propio contenedor ES el stVerticalBlock (la key va en el mismo
           div) — bajar su gap aquí es lo que pega el botón "Pasar Turno"
           justo debajo de la fila de cartas, sin el hueco por defecto de
           Streamlit (~1rem) entre ambos. */
        gap: 0.15rem !important;
    }
    [class*="st-key-battle_hand_overlay"] [data-testid="stHorizontalBlock"] {
        gap: 0.2rem !important;
    }
    [class*="st-key-battle_hand_overlay"] [data-testid="stColumn"] {
        flex: 0 0 158px !important;
        width: 158px !important;
        max-width: 158px !important;
        min-width: 158px !important;
    }
    [class*="st-key-battle_hand_overlay"] img {
        /* ~28% más pequeñas que la versión anterior (208x312) y columnas
           más juntas (228px/0.35rem -> 158px/0.2rem): la mano ya no se
           mete bajo las cápsulas del Combo Tripartito de las esquinas. */
        width: 150px !important;
        height: 225px !important;
    }
    /* "Pasar Turno" siempre por encima del marco/zoom de la carta elegida
       (que sube con z-index 100, ver card_glow_sel más abajo) y con aire
       propio respecto a la fila de cartas, para que el clic nunca quede
       bloqueado por la carta expandida. */
    [class*="st-key-confirm_turn_"] {
        position: relative !important;
        z-index: 9999 !important;
        margin-top: 12px !important;
    }
    /* Mismo estilo Pro del CTA "⚔️ LANZAR RETO" (naranja neón, bordes
       redondeados, glow y sombreado — ver st-key-new_match_cta) para que
       "Pasar Turno", el CTA del combate, sea coherente con el del lobby. */
    [class*="st-key-confirm_turn_"] button {
        background: linear-gradient(135deg, #FF5500, #FF6B00) !important;
        border: none !important;
        color: #fff !important;
        font-weight: 800 !important;
        letter-spacing: 0.03em;
        text-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
        border-radius: 24px !important;
        box-shadow: 0 4px 16px rgba(255, 85, 0, 0.55);
        transition: transform 0.15s ease-in-out, box-shadow 0.15s ease-in-out;
    }
    [class*="st-key-confirm_turn_"] button:hover:not(:disabled) {
        transform: scale(1.02);
        box-shadow: 0 8px 28px rgba(255, 107, 0, 0.85);
        color: #fff !important;
    }
    [class*="st-key-confirm_turn_"] button:active:not(:disabled) {
        transform: translateY(2px) scale(0.98) !important;
        box-shadow: 0 2px 8px rgba(255, 85, 0, 0.5) !important;
        transition: transform 0.05s ease, box-shadow 0.05s ease !important;
    }
    [class*="st-key-confirm_turn_"] button:disabled {
        background: rgba(255, 85, 0, 0.3) !important;
        box-shadow: none !important;
        color: rgba(255, 255, 255, 0.6) !important;
    }
    [class*="st-key-battle_hand_overlay"] [data-testid="stCaptionContainer"] {
        font-size: 0.6rem !important;
        line-height: 1.1em !important;
    }
    /* Info de la carta (nombre, ATK/DEF/coste, habilidad, efecto,
       descripción): SIN letrero fijo, todo va en este tooltip, revelado
       solo al pasar el ratón por la carta (ver el <style> aparte de más
       abajo, que activa esto con :hover en el contenedor card_glow_* —
       el botón "select_" invisible que cubre la carta es quien de verdad
       recibe el puntero). Centrado bajo la carta; pointer-events:none para
       que el clic de selección siga pasando a través de él. */
    .card-info-panel {
        position: absolute;
        z-index: 200;
        bottom: 100%;
        left: 50%;
        transform: translateX(-50%);
        margin-bottom: 0.3rem;
        width: max-content;
        max-width: min(260px, 85vw);
        padding: 0.5rem 0.65rem;
        border-radius: 8px;
        background: rgba(20, 20, 22, 0.97);
        border: 1px solid rgba(255, 255, 255, 0.2);
        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.5);
        font-size: 0.78rem;
        line-height: 1.3em;
        opacity: 0;
        visibility: hidden;
        transition: opacity 0.08s ease;
        pointer-events: none;
        text-align: left;
    }
    .card-info-panel .card-info-name {
        font-weight: 700;
        margin-bottom: 0.2rem;
    }
    .card-info-panel .card-info-stats {
        color: rgba(255, 213, 74, 0.95);
        margin-bottom: 0.15rem;
    }
    .card-info-panel .card-info-desc {
        color: rgba(210, 210, 210, 0.85);
        margin-top: 0.15rem;
    }
    /* Catálogo de cartas: imagen a altura fija (object-fit: contain, en vez
       de estirar al ancho y dejar que la relación de aspecto de cada arte
       decida la altura de la fila) y cajas de texto de altura fija para la
       nota de habilidad y la descripción (recortada con línea de puntos si
       es más larga que otras), para que las 4 columnas de la parrilla
       queden siempre alineadas y del mismo tamaño. */
    [class*="st-key-gallery_grid"] [data-testid="stImage"] img {
        height: 230px !important;
        width: auto !important;
        max-width: 100% !important;
        object-fit: contain;
        display: block;
        margin: 0 auto;
    }
    .gallery-ability {
        font-size: 0.8rem;
        color: rgb(107, 163, 107);
        min-height: 1.3em;
        line-height: 1.3em;
        margin: 0.15rem 0;
    }
    .gallery-desc {
        font-size: 0.78rem;
        color: rgba(135, 135, 135, 0.95);
        min-height: 3.3em;
        max-height: 3.3em;
        line-height: 1.1em;
        overflow: hidden;
        display: -webkit-box;
        -webkit-line-clamp: 3;
        -webkit-box-orient: vertical;
        margin-top: 0.2rem;
    }
    /* Zoom al pasar el ratón por una carta, en el Catálogo de Cartas y el
       Tutorial Interactivo (la mano de combate NO se agranda al hover —
       pedido explícito: solo al hacer CLIC/seleccionar, ver el <style>
       aparte más abajo — el ratón solo revela ahí un tooltip de info). */
    [class*="st-key-gallery_grid"] [data-testid="stImage"] img,
    [class*="st-key-tutorial_card"] [data-testid="stImage"] img {
        transition: transform 0.25s ease-in-out, box-shadow 0.25s ease-in-out;
        cursor: pointer;
    }
    [class*="st-key-gallery_grid"] [data-testid="stImage"] img:hover,
    [class*="st-key-tutorial_card"] [data-testid="stImage"] img:hover {
        transform: scale(1.35);
        position: relative;
        z-index: 100;
        box-shadow: 0px 10px 25px rgba(0, 0, 0, 0.6);
        border-radius: 8px;
    }
    .combat-feed {
        background: rgba(135, 135, 135, 0.08);
        border: 1px solid rgba(135, 135, 135, 0.25);
        border-radius: 8px;
        padding: 6px 12px;
        font-size: 0.82rem;
        line-height: 1.35;
        height: 4.2em;
        overflow-y: auto;
        margin-bottom: 0.4rem;
    }
    /* Tarjetas del log (feed compacto y registro completo, ver
       _format_log_entry): color por bando y separador de turno, para que se
       lea como un log de TCG en vez de texto plano. */
    .log-entry {
        padding: 3px 8px;
        margin: 2px 0;
        border-radius: 6px;
        border-left: 3px solid transparent;
    }
    .log-entry.log-side-a {
        background: rgba(66, 135, 245, 0.12);
        border-left-color: #4287f5;
    }
    .log-entry.log-side-b {
        background: rgba(230, 57, 70, 0.12);
        border-left-color: #e63946;
    }
    .log-entry.log-side-neutral {
        background: rgba(135, 135, 135, 0.1);
        border-left-color: rgba(135, 135, 135, 0.4);
    }
    .log-turn-sep {
        text-align: center;
        font-weight: 700;
        text-transform: uppercase;
        font-size: 0.72rem;
        letter-spacing: 0.05em;
        color: rgba(255, 255, 255, 0.5);
        margin: 6px 0 2px;
    }
    /* Resplandor en cartas jugables (coste <= energía disponible), atenuado
       en las que no se pueden pagar, y aro dorado en la carta ELEGIDA para
       este turno — ver el key dinámico card_glow_playable_*/locked_*/sel_*
       en render_hand. */
    [class*="st-key-card_glow_playable"] img {
        box-shadow: 0 0 12px rgba(0, 255, 136, 0.7);
        border: 1px solid #00ff88;
        border-radius: 8px;
    }
    [class*="st-key-card_glow_locked"] {
        opacity: 0.7;
    }
    [class*="st-key-card_glow_sel"] img {
        box-shadow: 0 0 16px rgba(255, 200, 0, 0.9);
        border: 2px solid #ffc800;
        border-radius: 8px;
    }
    /* Selección múltiple: la ACTIVA (la que editan los puntos) con aro
       dorado intenso; las demás elegidas ("ancladas") con aro cian. */
    [class*="st-key-card_glow_sel_act"] img {
        box-shadow: 0 0 22px rgba(255, 200, 0, 1), 0 0 4px #fff;
        border: 3px solid #ffc800;
    }
    [class*="st-key-card_glow_sel_pin"] img {
        box-shadow: 0 0 14px rgba(64, 200, 255, 0.85);
        border: 3px solid #40c8ff;
    }
    /* Pastillas de estado SUPERPUESTAS sobre el pie de la propia carta
       (orden + zonas de la elegida, o "Sin energía"): fuera del flujo, para
       que nada empuje hacia abajo el botón "Pasar Turno". */
    /* Dentro de cada carta, sin gap de Streamlit (16px): sus hijos en flujo
       son solo el tooltip (alto 0) y la imagen — el resto es absoluto — y
       ese gap, más el del envoltorio stLayoutWrapper (alto 0) que Streamlit
       pone alrededor de cada st.container, era lo que abría un hueco vacío
       entre las cartas y "Pasar Turno". */
    [class*="st-key-battle_hand"] [class*="st-key-card_glow_"] {
        gap: 0 !important;
    }
    [class*="st-key-battle_hand"] [class*="st-key-card_overlay_chip_"] {
        position: absolute !important;
        left: 0; right: 0; bottom: 18px;
        top: auto !important;
        height: auto !important; /* Streamlit lo estiraba al alto de la carta */
        z-index: 150 !important;
        pointer-events: none;
        width: 100% !important;
    }
    .card-sel-chip {
        text-align: center; font-size: 0.74rem; font-weight: 700; color: #fff;
        background: rgba(0, 0, 0, 0.78); border: 1px solid rgba(255, 200, 0, 0.6);
        border-radius: 999px; padding: 1px 9px; margin: 0 auto; width: fit-content;
        max-width: 96%; white-space: normal; line-height: 1.25; pointer-events: none;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.6);
    }
    .card-sel-chip.ready { border-color: #2fb344; box-shadow: 0 0 8px rgba(47, 179, 68, 0.6); }
    .card-sel-chip.pending { animation: chip-pulse 1.2s ease-in-out infinite; }
    @keyframes chip-pulse {
        0%, 100% { box-shadow: 0 0 0 rgba(255, 200, 0, 0); }
        50% { box-shadow: 0 0 10px rgba(255, 200, 0, 0.9); }
    }
    .card-sel-chip.no-energy {
        color: #ff9a9a; border-color: rgba(255, 107, 107, 0.6);
        font-size: 0.7rem; font-weight: 600;
    }
    /* Zona ya usada por OTRA carta elegida (puntos superpuestos en la arena). */
    [class*="st-key-arena_point_pt_"][class*="_used"] .stButton button {
        outline: 3px dashed rgba(255, 255, 255, 0.8);
        outline-offset: 2px;
    }
    /* Energía disponible: destacada y grande junto a la barra de vida de
       cada jugador (antes iba metida en una caption pequeña junto a otros
       datos secundarios como mazo/descarte). */
    .energy-display {
        display: inline-block;
        font-size: 1.35rem;
        font-weight: 900;
        color: #ffd54a;
        margin: 0.15rem 0 0.1rem;
        padding: 0.1rem 0.7rem;
        border-radius: 999px;
        background: rgba(0, 0, 0, 0.45);
        border: 2px solid rgba(255, 213, 74, 0.6);
        line-height: 1.3em;
        text-shadow: 0 1px 2px #000;
    }
    .energy-display.energy-low {
        color: #ff6b6b;
        border-color: rgba(255, 107, 107, 0.7);
    }
    .deck-discard-count {
        font-size: 0.78rem;
        color: rgba(200, 200, 200, 0.75);
    }
    /* Zonas dañadas (combo tripartito): badge por zona en vez del texto
       plano anterior — intacta = gris sutil, dañada = rojo con brillo. */
    .zone-badges {
        display: flex;
        align-items: center;
        gap: 0.35rem;
        margin: 0.2rem 0 0.1rem;
        flex-wrap: wrap;
        cursor: help;
    }
    .zone-badges-right {
        justify-content: flex-end;
    }
    .zone-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        flex: 0 0 2rem;
        width: 2rem;
        height: 2rem;
        box-sizing: border-box;
        font-size: 1rem;
        padding: 0;
        border-radius: 999px;
        background: rgba(0, 0, 0, 0.35);
        border: 1px solid rgba(255, 255, 255, 0.18);
        filter: grayscale(0.9);
        opacity: 0.35;
    }
    .zone-badge-damaged {
        background: rgba(34, 197, 94, 0.2);
        border: 1px solid #22c55e;
        box-shadow: 0 0 6px rgba(34, 197, 94, 0.75);
        filter: none;
        opacity: 1;
    }
    .zone-combo-claimed {
        font-size: 0.78rem;
        font-weight: 700;
        color: #ffd54a;
        margin-left: 0.1rem;
    }
    .zone-combo-progress {
        font-size: 0.78rem;
        font-weight: 600;
        color: rgba(220, 220, 220, 0.65);
        margin-left: 0.1rem;
    }
    /* Selector global de cristales del jugador (panel izquierdo, ver
       render_crystal_selector): caja propia bien visible, botones 0-5 a
       todo el ancho y estado en verde (ilimitado) o ámbar (10 al día). */
    [class*="st-key-crystal_selector"] {
        border: 1px solid rgba(64, 200, 255, 0.55);
        background: rgba(64, 200, 255, 0.08);
        border-radius: 12px;
        padding: 0.55rem 0.7rem 0.5rem;
        margin-top: 0.4rem;
        gap: 0.35rem !important;
        box-shadow: 0 0 12px rgba(64, 200, 255, 0.18);
    }
    .crystal-sel-title {
        font-weight: 900; font-size: 0.85rem; letter-spacing: 0.06em; color: #8fd8ff;
    }
    [class*="st-key-crystal_selector"] [data-testid="stButtonGroup"] > div,
    [class*="st-key-crystal_selector"] [data-testid="stButtonGroup"] [role="radiogroup"],
    [class*="st-key-crystal_selector"] [data-testid="stButtonGroup"] > div > div {
        width: 100%; display: flex; flex-wrap: nowrap;
    }
    /* Streamlit da -16px de margen inferior a cada markdown: tapaba el título. */
    [class*="st-key-crystal_selector"] [data-testid="stMarkdownContainer"] {
        margin-bottom: 0 !important;
    }
    [class*="st-key-crystal_selector"] :is(button[data-variant="segmented_control"],
                                           button[data-testid^="stBaseButton-segmented_control"]) {
        flex: 1 1 0; min-width: 0; font-weight: 800; font-size: 1rem;
        background: rgba(8, 8, 14, 0.75) !important; color: #cfefff !important;
        border-color: rgba(64, 200, 255, 0.35) !important;
    }
    [class*="st-key-crystal_selector"] :is(button[data-variant="segmented_control"][aria-checked="true"],
                                           button[data-testid="stBaseButton-segmented_controlActive"]) {
        background: linear-gradient(135deg, #1aa7e0, #40c8ff) !important; color: #00131d !important;
        border-color: #8fe3ff !important; box-shadow: 0 0 10px rgba(64, 200, 255, 0.7);
    }
    .crystal-sel-status {
        font-size: 0.8rem; font-weight: 700; padding: 0.2rem 0.55rem; border-radius: 8px;
    }
    .crystal-sel-status.unlimited {
        color: #8cffa0; background: rgba(47, 179, 68, 0.15); border: 1px solid rgba(47, 179, 68, 0.6);
    }
    .crystal-sel-status.limited {
        color: #ffd27a; background: rgba(255, 140, 0, 0.12); border: 1px solid rgba(255, 179, 71, 0.6);
    }
    /* Reproductor de música de fondo en la navbar: discreto, no todo el
       ancho de la columna que Streamlit le da por defecto. */
    [class*="st-key-nav_music_player"] audio {
        height: 32px;
        max-width: 210px;
    }
    /* Temporizador de turno (col_vs, junto a "Turno N"): barra que se vacía
       vía JS (ver components.html más abajo) + número de segundos. El botón
       que dispara el fin de turno automático al llegar a 0 está forzado
       invisible — sigue siendo "clicable" por JS (.click() dispara su
       listener real aunque esté display:none), solo no debe verse. */
    .turn-timer-wrap {
        margin-top: 0.3rem;
    }
    .turn-timer-track {
        height: 5px;
        border-radius: 3px;
        background: rgba(135, 135, 135, 0.25);
        overflow: hidden;
    }
    .turn-timer-fill {
        height: 100%;
        background: linear-gradient(90deg, #00ff88, #ffd54a 60%, #ff6b6b);
        transition: width 0.3s linear;
    }
    .turn-timer-label {
        text-align: center;
        font-size: 0.78rem;
        color: rgba(220, 220, 220, 0.85);
        margin-top: 0.15rem;
    }
    [class*="st-key-turn_timeout_trigger"] {
        display: none !important;
    }
    /* Wallet + NFT equipado (render_wallet_and_nft_bar): una sola fila
       horizontal y compacta — imagen de NFT chica, métricas con poco
       relleno y fuente reducida para que ocupe el mínimo alto posible. */
    [class*="st-key-wallet_nft_bar"] [data-testid="stVerticalBlock"] {
        gap: 0.25rem !important;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stHorizontalBlock"] {
        gap: 0.6rem !important;
        align-items: center !important;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stMetric"] {
        padding: 0.15rem 0.3rem !important;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stMetricValue"] {
        font-size: 1rem !important;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stMetricLabel"] p {
        font-size: 0.8rem !important;
        color: rgba(204, 204, 204, 0.95) !important;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
        line-height: 1.1 !important;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stImage"] img {
        border-radius: 6px;
        max-height: 170px;
        width: auto !important;
        object-fit: contain;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stSelectbox"] label p {
        font-size: 0.78rem !important;
    }
    [class*="st-key-wallet_nft_bar"] [data-testid="stCaptionContainer"] p {
        font-size: 0.72rem !important;
        margin: 0.1rem 0 0 !important;
    }
    /* Fila unificada del panel del Maestro: tarjeta de NFT (imagen +
       estadísticas) a la izquierda y bloque de acción de combate (CTA +
       Modo/Oponente) a la derecha, ambas a ancho completo del panel. */
    [class*="st-key-nft_card_row"] [data-testid="stHorizontalBlock"] {
        width: 100% !important;
    }
    /* Dentro de la tarjeta de NFT: la columna de imagen queda fija
       (miniatura ~130px) y la de estadísticas ocupa todo el ancho restante,
       sin dejar espacio negro a los lados. */
    [class*="st-key-nft_thumb_stats_row"] [data-testid="stHorizontalBlock"] {
        width: 100% !important;
        align-items: center !important;
    }
    [class*="st-key-nft_thumb_stats_row"] [data-testid="stColumn"]:first-of-type {
        flex: 0 0 auto !important;
        width: auto !important;
        max-width: 190px !important;
        min-width: 0 !important;
    }
    [class*="st-key-nft_thumb_stats_row"] [data-testid="stColumn"]:last-of-type {
        flex: 1 1 auto !important;
        width: auto !important;
        min-width: 0 !important;
    }
    /* Columna de acción (CTA Lanzar Reto + Modo/Oponente), a la derecha de
       la tarjeta de NFT dentro del mismo panel. */
    [class*="st-key-nft_action_col"] {
        width: 100% !important;
    }
    /* Columna de estadísticas: cada fila ocupa todo el ancho disponible con
       la etiqueta pegada a la izquierda y el valor naranja a la derecha. */
    [class*="st-key-nft_stats_col"] {
        width: 100% !important;
    }
    [class*="st-key-nft_stats_col"] [data-testid="stMetric"] {
        display: flex !important;
        flex-direction: row !important;
        align-items: baseline !important;
        justify-content: space-between !important;
        width: 100% !important;
        text-align: left !important;
        padding: 0.15rem 0 !important;
        gap: 0.5rem !important;
    }
    [class*="st-key-nft_stats_col"] [data-testid="stMetricLabel"] {
        justify-content: flex-start !important;
    }
    [class*="st-key-nft_stats_col"] [data-testid="stMetricValue"] {
        text-align: right !important;
        white-space: nowrap !important;
    }
    /* Barra de Modo/Oponente/Nueva partida (render_battle_mode_bar): botones
       bastante más chicos que el tamaño por defecto de Streamlit — menos
       padding, fuente reducida y altura fija — para que no domine la
       pantalla ahora que vive debajo del panel de Wallet + NFT. */
    [class*="st-key-battle_mode_bar"] [data-testid="stVerticalBlock"] {
        gap: 0.35rem !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stHorizontalBlock"] {
        gap: 0.3rem !important;
    }
    /* Selectores de Modo (Free/Competitivo) y Oponente (Bot/Hotseat):
       st.segmented_control, de selección única y excluyente — el grupo
       ocupa todo el ancho disponible (botones grandes, sin huecos a los
       lados), en gris oscuro con borde; la opción activa
       (aria-checked="true") en naranja/rojo vivo y las inactivas en gris
       apagado. */
    [class*="st-key-battle_mode_bar"] [data-testid="stElementContainer"] {
        width: 100% !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stButtonGroup"] {
        display: flex !important;
        width: 100% !important;
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(135, 135, 135, 0.3);
        border-radius: 8px;
        padding: 3px;
        gap: 3px !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stButtonGroup"] > div {
        flex: 1 1 0 !important;
        display: flex !important;
        width: 100% !important;
        max-width: none !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stButtonGroup"] button {
        flex: 1 1 0 !important;
        width: 100% !important;
        background: transparent !important;
        border: none !important;
        color: rgba(170, 170, 170, 0.85) !important;
        font-size: 0.72rem !important;
        font-weight: 600 !important;
        padding: 0.35rem 0.2rem !important;
        min-height: 2.1rem !important;
        height: auto !important;
        border-radius: 6px !important;
        white-space: nowrap !important;
        overflow: visible !important;
        text-overflow: clip !important;
        line-height: 1.15 !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stButtonGroup"] button[aria-checked="true"] {
        background: linear-gradient(135deg, #FF5500, #FF6B00) !important;
        color: #fff !important;
        font-weight: 700 !important;
    }
    /* El bloque de Modo/Oponente es, igual que el panel de NFT, una tarjeta
       propia con borde — para que los 4 botones se lean como parte de un
       panel delimitado y no floten sueltos con huecos vacíos alrededor. */
    [class*="st-key-battle_mode_bar"] {
        border: 1px solid rgba(135, 135, 135, 0.25);
        border-radius: 10px;
        padding: 0.5rem 0.6rem;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stNumberInput"] input {
        font-size: 0.72rem !important;
        padding: 0.2rem 0.5rem !important;
        height: 1.9rem !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stNumberInput"] label p {
        font-size: 0.68rem !important;
    }
    [class*="st-key-battle_mode_bar"] [data-testid="stCaptionContainer"] p {
        font-size: 0.85rem !important;
        color: rgba(230, 230, 230, 0.92) !important;
        margin: 0.35rem 0 0 !important;
    }
    /* Panel compacto de Wallet + NFT equipado, a la izquierda: tarjeta con
       borde a juego con la de Modo/Oponente (misma línea/radio) y estirada
       a la misma altura que la columna de acción de la derecha (CTA +
       Modo/Oponente), para que ambos bloques queden simétricos. */
    [class*="st-key-left_info_panel"] {
        border: 1px solid rgba(135, 135, 135, 0.25);
        border-radius: 10px;
        padding: 0.6rem 0.8rem 0.4rem;
        height: 100%;
        box-sizing: border-box;
    }
    /* CTA principal "⚔️ LANZAR RETO" (render_new_match_button): rojo/naranja
       vivo para que sea la acción principal de la pantalla antes de empezar
       a jugar — ancho de contenido (tope 280px) y centrado en vez de ocupar
       todo el ancho de la columna, que se veía tosco; alto ligeramente
       mayor que antes (48px) para compensar y que no quede chato. */
    [class*="st-key-new_match_cta"] {
        display: flex;
        justify-content: center;
        margin: 0.4rem 0;
    }
    [class*="st-key-new_match_cta"] [data-testid="stVerticalBlock"],
    [class*="st-key-new_match_cta"] [data-testid="stElementContainer"] {
        width: 100%;
        display: flex;
        justify-content: center;
    }
    [class*="st-key-new_match_cta"] .stButton {
        width: 100%;
        max-width: 280px;
        margin: 0 auto !important;
    }
    [class*="st-key-new_match_cta"] .stButton > button {
        width: 100%;
        background: linear-gradient(135deg, #FF5500, #FF6B00) !important;
        border: none !important;
        color: #fff !important;
        font-size: 1.05rem !important;
        font-weight: 800 !important;
        letter-spacing: 0.03em;
        text-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
        white-space: normal !important;
        min-height: 48px !important;
        height: 48px !important;
        padding: 0.3rem 0.6rem !important;
        border-radius: 24px !important;
        box-shadow: 0 4px 16px rgba(255, 85, 0, 0.55);
        transition: transform 0.15s ease-in-out, box-shadow 0.15s ease-in-out;
    }
    [class*="st-key-new_match_cta"] .stButton > button:hover {
        transform: scale(1.04);
        box-shadow: 0 8px 28px rgba(255, 107, 0, 0.85);
        color: #fff !important;
    }
    [class*="st-key-new_match_cta"] .stButton > button:active {
        transform: translateY(2px) scale(0.98) !important;
        box-shadow: 0 2px 8px rgba(255, 85, 0, 0.5) !important;
        transition: transform 0.05s ease, box-shadow 0.05s ease !important;
    }
    /* Columna derecha del lobby: feed de retos de bots simulados
       (render_bot_challenges_feed) — misma estética oscura con borde sutil
       que st-key-left_info_panel, una fila por rival con su propio borde
       interno, y el botón "Aceptar Reto" en verde (único botón verde de la
       app, para distinguirlo del naranja de "Lanzar Reto"). */
    [class*="st-key-bot_challenges_feed"] {
        border: 1px solid rgba(135, 135, 135, 0.25);
        border-radius: 10px;
        padding: 0.5rem 0.6rem;
        height: 100%;
        box-sizing: border-box;
    }
    [class*="st-key-challenge_row_"] {
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        padding: 0.3rem 0.5rem;
        margin-bottom: 0.3rem;
        background: rgba(255, 255, 255, 0.03);
    }
    [class*="st-key-challenge_row_"] [data-testid="stHorizontalBlock"] {
        align-items: center !important;
        gap: 0.4rem !important;
    }
    [class*="st-key-challenge_row_"] [data-testid="stImage"] img {
        border-radius: 6px;
    }
    .challenge-rival-name {
        font-weight: 700;
        font-size: 0.82rem;
        color: #fff;
        line-height: 1.2;
    }
    .challenge-rival-nft {
        font-size: 0.7rem;
        color: rgba(255, 255, 255, 0.6);
        line-height: 1.2;
    }
    .challenge-rival-stake {
        font-size: 0.72rem;
        font-weight: 700;
        color: #ffd54a;
        line-height: 1.2;
    }
    /* Barra de tiempo + botón en la misma fila (challenge_action_row_, ver
       _render_challenge_row): la barra pierde su alto por defecto y el
       botón se alinea a la derecha, ajustado solo a lo que mide su propio
       texto ("Aceptar Reto"/"Cancelar Reto") en vez de estirarse a lo ancho
       de su columna. */
    [class*="st-key-challenge_row_"] .turn-timer-wrap {
        margin: 0;
    }
    /* La barra (1ra columna) crece para ocupar todo el espacio libre
       (flex:1) y el botón (última columna) se achica a su propio contenido
       (flex:0, ancho auto) — así quedan pegados uno al otro hasta el borde
       derecho de la tarjeta, sin el hueco muerto que dejaba el reparto fijo
       [3, 2] de st.columns cuando el botón no llenaba su columna. */
    [class*="st-key-challenge_action_row_"] [data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap !important;
    }
    [class*="st-key-challenge_action_row_"] [data-testid="stColumn"]:first-child {
        flex: 1 1 auto !important;
        width: auto !important;
        min-width: 0 !important;
    }
    [class*="st-key-challenge_action_row_"] [data-testid="stColumn"]:last-child {
        flex: 0 0 auto !important;
        width: auto !important;
        display: flex;
        justify-content: flex-end;
        align-items: center;
    }
    [class*="st-key-challenge_action_row_"] .stButton {
        width: fit-content !important;
    }
    [class*="st-key-challenge_row_"] .stButton > button {
        background: linear-gradient(135deg, #16a34a, #22c55e) !important;
        border: none !important;
        color: #fff !important;
        font-weight: 700 !important;
        box-shadow: 0 2px 10px rgba(34, 197, 94, 0.45);
        width: fit-content !important;
        height: auto !important;
        min-height: 0 !important;
        padding: 6px 14px !important;
        font-size: 0.78rem !important;
        border-radius: 999px !important;
        white-space: nowrap !important;
    }
    [class*="st-key-challenge_row_"] .stButton > button:hover {
        box-shadow: 0 4px 16px rgba(34, 197, 94, 0.7);
        color: #fff !important;
    }
    /* "🚫 Sin saldo" (apuesta del reto > balance): gris apagado en vez del
       verde de "Aceptar Reto", para que se lea como bloqueado. */
    [class*="st-key-challenge_row_"] .stButton > button:disabled {
        background: rgba(255, 255, 255, 0.08) !important;
        color: rgba(255, 255, 255, 0.5) !important;
        box-shadow: none !important;
    }
    /* "Cancelar Reto" (nuestro propio reto, challenge_row_own): mismo
       tamaño que "Aceptar Reto" pero gris/rojo apagado en vez de verde,
       para no confundirlo con aceptar el reto de un rival. Va después de
       la regla verde de arriba — misma especificidad, gana por orden. */
    [class*="st-key-cancel_own_challenge_wrap"] .stButton > button {
        background: rgba(255, 255, 255, 0.08) !important;
        border: 1px solid rgba(239, 68, 68, 0.5) !important;
        color: rgba(255, 255, 255, 0.85) !important;
        box-shadow: none !important;
    }
    [class*="st-key-cancel_own_challenge_wrap"] .stButton > button:hover {
        background: rgba(239, 68, 68, 0.18) !important;
        color: #fff !important;
    }
    [class*="st-key-bot_challenges_refresh_trigger"] {
        display: none !important;
    }
    /* Exclusivo para móvil: el escritorio se queda con el diseño original
       (fuera de esta media query no hay ningún override de estas reglas) —
       mano de cartas en carrusel horizontal scrollable en vez de apiladas,
       para que quepa de un vistazo.
       shortcut: max-width 768px no cubre móviles muy anchos en landscape
       (>768px, p.ej. algunos phablets); si hace falta, añadir aquí
       "and (max-height: 500px)" junto a un "(orientation: landscape)". */
    @media (max-width: 768px) {
        [class*="st-key-battle_hand"] [data-testid="stHorizontalBlock"] {
            flex-wrap: nowrap !important;
            overflow-x: auto !important;
            justify-content: flex-start !important;
            gap: 0.5rem !important;
            padding-bottom: 0.3rem;
            -webkit-overflow-scrolling: touch;
        }
        [class*="st-key-wallet_nft_bar"] [data-testid="stHorizontalBlock"] {
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            gap: 0.5rem !important;
        }
        [class*="st-key-wallet_nft_bar"] [data-testid="stColumn"] {
            min-width: 0 !important;
        }
        [class*="st-key-battle_hand"] [data-testid="stColumn"] {
            flex: 0 0 130px !important;
            width: 130px !important;
            max-width: 130px !important;
            min-width: 130px !important;
        }
        [class*="st-key-battle_hand"] img {
            width: 107px !important;
            height: 160px !important;
        }
        [class*="st-key-battle_hand"] .stButton > button {
            font-size: 0.72rem !important;
            padding: 0.3rem 0.4rem !important;
            min-height: 2rem !important;
        }
    }

    /* Arena de combate (render_arena): escenario.png de fondo con las
       barras de HP/energía y el turno superpuestos arriba (gradiente oscuro
       para que se lean sobre cualquier parte de la imagen) y el avatar de
       cada luchador en los marcos laterales del escenario. Todo este bloque
       es HTML plano no interactivo — los puntos tácticos clicables van
       aparte, justo debajo, como botones reales de Streamlit (ver
       render_tactical_points). */
    .arena-bg {
        position: absolute;
        inset: 0;
        background-size: cover;
        background-position: center;
    }
    /* Streamlit envuelve el markdown de .arena-bg en su propio
       stElementContainer con position:relative (para su z-index interno) —
       sin neutralizarlo, ESE div (no arena_outer) se convierte en el
       contenedor de referencia de .arena-bg (position:absolute; inset:0) y,
       como ese div no tiene alto propio (su único contenido ya está fuera
       del flujo), el alto colapsa a 0: la imagen desaparece. Lo volvemos
       position:static para que inset:0 se resuelva contra arena_outer. */
    [class*="st-key-arena_outer"] > [data-testid="stElementContainer"]:first-child {
        position: static !important;
    }
    .arena-topbar {
        position: absolute;
        top: 0; left: 0; right: 0;
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 0.5rem;
        padding: 0.6rem 0.8rem;
        background: linear-gradient(to bottom, rgba(0,0,0,0.78), rgba(0,0,0,0));
    }
    .arena-hp { flex: 1 1 38%; min-width: 0; }
    .arena-hp-right { text-align: right; }
    .arena-hp-name {
        color: #fff; font-weight: 700; font-size: 0.85rem;
        text-shadow: 0 1px 3px rgba(0,0,0,0.9);
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .arena-hp { position: relative; }
    .arena-hp-track {
        position: relative; height: 24px; border-radius: 12px; overflow: hidden;
        background: rgba(0,0,0,0.6); border: 2px solid rgba(255,255,255,0.3); margin-top: 3px;
    }
    .arena-hp-fill {
        height: 100%; background: linear-gradient(90deg, #ff4d4d, #ffb84d);
        transition: width 0.3s ease;
    }
    .arena-hp-right .arena-hp-fill { float: right; }
    .arena-hp-text {
        position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
        font-size: 0.95rem; font-weight: 800; color: #fff; letter-spacing: 0.02em;
        text-shadow: 0 0 3px #000, 0 1px 2px #000;
    }
    /* Barra de ENERGÍA gruesa (mismo estilo que Valldeande): el relleno es
       la energía que aún se puede gastar ESTE turno sobre el tope gastable
       (min(MAX_ENERGY_SPEND_PER_TURN, pool)), segmentada en unidades, con el
       número grande centrado dentro y la reserva total en un badge aparte. */
    .arena-energy-row {
        display: flex; align-items: center; gap: 6px; margin-top: 5px;
    }
    .arena-hp-right .arena-energy-row { flex-direction: row-reverse; }
    .arena-energy-track {
        position: relative; flex: 1 1 auto; height: 30px; border-radius: 15px; overflow: hidden;
        background: rgba(0,0,0,0.65); border: 2px solid rgba(255,224,102,0.55);
        box-shadow: 0 0 10px rgba(255,200,0,0.25);
    }
    .arena-energy-fill {
        height: 100%;
        background: linear-gradient(90deg, #ffb300, #ffe066);
        box-shadow: inset 0 -4px 0 rgba(0,0,0,0.18);
        transition: width 0.35s ease;
    }
    .arena-hp-right .arena-energy-fill { float: right; background: linear-gradient(270deg, #ffb300, #ffe066); }
    .arena-energy-segs {
        position: absolute; inset: 0; display: flex; pointer-events: none;
    }
    .arena-energy-segs span { flex: 1; border-right: 2px solid rgba(0,0,0,0.45); }
    .arena-energy-segs span:last-child { border-right: none; }
    .arena-energy-text {
        position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
        font-size: 1.2rem; font-weight: 900; color: #fff; letter-spacing: 0.03em;
        text-shadow: 0 0 4px #000, 0 0 2px #000, 0 1px 2px #000;
    }
    .arena-energy-track.energy-empty { border-color: rgba(255,107,107,0.75); box-shadow: 0 0 10px rgba(255,80,80,0.35); }
    .arena-energy-track.energy-empty .arena-energy-text { color: #ff8a8a; }
    .arena-energy-pool {
        flex: 0 0 auto; padding: 3px 8px; border-radius: 10px;
        background: rgba(0,0,0,0.6); border: 1px solid rgba(255,224,102,0.45);
        color: #ffe066; font-size: 0.85rem; font-weight: 800; white-space: nowrap;
        text-shadow: 0 1px 2px #000;
    }
    /* Feedback de gasto: "-N" grande en rojo que aparece justo DEBAJO de la
       barra de energía de quien gastó, cae un poco y se desvanece (una sola
       vez — ver _flash_energy_spend / render_arena, que lo consumen). */
    @keyframes energy-float {
        0%   { opacity: 0; transform: translateY(-10px) scale(0.6); }
        15%  { opacity: 1; transform: translateY(0) scale(1.25); }
        30%  { transform: translateY(2px) scale(1); }
        75%  { opacity: 1; transform: translateY(14px) scale(1); }
        100% { opacity: 0; transform: translateY(26px) scale(0.95); }
    }
    /* Avisos flotantes (-N⚡ energía, 💥-N daño) agrupados en el lado
       INTERIOR de cada barra — el más cercano al centro de la arena / al
       marcador de Ronda — en vez de pegados a los bordes y marcos
       laterales, donde casi no se veían. */
    .combat-floats {
        position: absolute; top: 100%; margin-top: 6px;
        display: flex; gap: 1rem; align-items: flex-start;
        pointer-events: none; z-index: 40; white-space: nowrap;
    }
    .arena-hp-left .combat-floats { right: 6%; flex-direction: row-reverse; }
    .arena-hp-right .combat-floats { left: 6%; }
    .energy-float {
        position: relative;
        font-size: 2.6rem; font-weight: 900; line-height: 1;
        color: #ff2e2e;
        text-shadow: 0 0 6px rgba(0,0,0,0.95), 0 0 14px rgba(255,40,40,0.8), 0 2px 0 #5a0000;
        /* Pastilla oscura: el rojo se perdía sobre el pelo naranja de los
           luchadores centrales; con fondo propio se lee sobre cualquier zona. */
        background: rgba(12, 0, 0, 0.72);
        border: 3px solid rgba(255, 60, 60, 0.85);
        border-radius: 18px;
        padding: 4px 14px 6px;
        box-shadow: 0 0 18px rgba(255, 40, 40, 0.55), 0 4px 12px rgba(0, 0, 0, 0.6);
        pointer-events: none; z-index: 40;
        animation: energy-float 1.9s ease-out forwards;
    }
    .energy-float.hp-float { color: #ff4040; animation-delay: 0.15s; opacity: 0; }
    .energy-float.regen { animation-delay: 0.3s; opacity: 0; }
    /* Popup "Carta deseleccionada" (st.dialog, ver _show_zone_dialog). */
    .zone-dialog-lead { font-size: 1.15rem; font-weight: 700; margin-bottom: 0.6rem; }
    .zone-dialog-missing {
        display: inline-block; padding: 4px 12px; border-radius: 999px;
        background: rgba(255, 60, 60, 0.15); border: 1px solid rgba(255, 80, 80, 0.7);
        color: #ff8a8a; font-weight: 800;
    }
    .zone-dialog-help {
        margin: 0.8rem 0 0.6rem; padding: 10px 14px; border-radius: 12px;
        background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.12);
        line-height: 1.7;
    }
    .energy-float.gain {
        color: #4dff7a; border-color: rgba(60, 255, 110, 0.85);
        box-shadow: 0 0 18px rgba(40, 255, 90, 0.5), 0 4px 12px rgba(0, 0, 0, 0.6);
        text-shadow: 0 0 6px rgba(0,0,0,0.95), 0 0 14px rgba(40,255,90,0.7), 0 2px 0 #00380e;
    }
    .arena-turn {
        flex: 0 0 auto; color: #fff; font-weight: 800; text-align: center; font-size: 0.85rem;
        text-shadow: 0 1px 3px rgba(0,0,0,0.9); white-space: nowrap;
    }
    .arena-timer { font-size: 0.78rem; color: #9be7ff; }
    /* Regla de inactividad: pastilla junto al nombre mientras el contador
       sea > 0 (roja y pulsante en el último aviso) + aviso bajo el reloj
       para TI cuando te queda 1 turno, + texto flotante al subir. */
    .arena-inactive {
        display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 999px;
        font-size: 0.72rem; font-weight: 800; color: #2a1600;
        background: #ffc94a; border: 1px solid #fff0b0; vertical-align: middle;
        text-shadow: none;
    }
    .arena-inactive.last {
        background: #ff3b3b; color: #fff; border-color: #ffb0b0;
        animation: inact-pulse 0.9s ease-in-out infinite;
    }
    @keyframes inact-pulse {
        0%, 100% { box-shadow: 0 0 0 rgba(255, 59, 59, 0); }
        50% { box-shadow: 0 0 12px rgba(255, 59, 59, 1); }
    }
    .arena-last-warning {
        margin-top: 4px; padding: 3px 8px; border-radius: 8px;
        background: rgba(255, 40, 40, 0.88); border: 1px solid #ffb0b0;
        font-size: 0.72rem; font-weight: 800; line-height: 1.2; white-space: nowrap;
        animation: inact-pulse 0.9s ease-in-out infinite;
    }
    /* Levitación suave de los Maestros NFT (avatares en los marcos
       laterales): sube y baja en bucle, -8px en el punto medio del ciclo.
       El avatar derecho ya viene espejado (scaleX(-1), ver abajo) — ese
       transform estático se PERDERÍA si solo la izquierda tuviera
       animation (una animación con `transform` en sus keyframes sustituye
       cualquier transform estático del mismo elemento), así que el
       espejado va horneado en sus propios keyframes (levitate-mirror) en
       vez de reutilizar @keyframes levitate tal cual. animation-delay
       negativo en el derecho para que no floten exactamente sincronizados. */
    @keyframes levitate {
        0%, 100% { transform: translateY(0px); }
        50% { transform: translateY(-8px); }
    }
    @keyframes levitate-mirror {
        0%, 100% { transform: scaleX(-1) translateY(0px); }
        50% { transform: scaleX(-1) translateY(-8px); }
    }
    .arena-portal {
        /* 10%/60% -> 28%/42%: Maestros laterales ~30% más pequeños para
           dejar ver el altar y el fondo; el borde inferior se mantiene al
           70% (siguen "apoyados" en la misma altura del escenario). */
        position: absolute; top: 28%; height: 42%; object-fit: contain;
        filter: drop-shadow(0 6px 10px rgba(0,0,0,0.6));
        border-radius: 10px;
        border: 2px solid rgba(255,255,255,0.15);
        animation: levitate 3.5s ease-in-out infinite;
    }
    .arena-portal-left { left: 1%; }
    .arena-portal-right { right: 1%; animation-name: levitate-mirror; animation-delay: -1.75s; }
    /* HUD de Combo Tripartito: UNA sola cápsula horizontal en la esquina
       inferior de cada bando — a la izquierda la pastilla de progreso
       (🔥 COMBO n/3), a la derecha las 3 zonas en fila fija (nowrap).
       font-size con clamp() para que, aun en ventanas estrechas, la
       cápsula quepa en la esquina sin invadir el panel de la mano. */
    .combo-hud {
        position: absolute; bottom: 3.5%; z-index: 5;
        display: flex; align-items: center; flex-wrap: nowrap; gap: 6px;
        width: max-content;
        padding: 4px 8px 4px 5px;
        background: rgba(8, 8, 14, 0.82);
        border: 1px solid rgba(255, 255, 255, 0.16);
        border-radius: 999px;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.55);
        font-size: clamp(0.55rem, 0.7vw, 0.74rem); line-height: 1; color: #fff;
        white-space: nowrap;
    }
    .combo-hud-left { left: 1%; box-shadow: inset 3px 0 0 #4da3ff, 0 4px 14px rgba(0,0,0,0.55); }
    .combo-hud-right { right: 1%; box-shadow: inset -3px 0 0 #ff5a5a, 0 4px 14px rgba(0,0,0,0.55); }
    .combo-hud-count {
        flex: 0 0 auto;
        padding: 3px 7px; border-radius: 999px;
        background: rgba(255, 140, 0, 0.22); border: 1px solid rgba(255, 179, 71, 0.7);
        color: #ffd27a; font-weight: 800; letter-spacing: 0.04em;
    }
    .combo-hud-count b { color: #fff; font-size: 1.1em; margin-left: 2px; }
    .combo-hud-zones { display: flex; align-items: center; flex-wrap: nowrap; gap: 4px; }
    /* Chips de zona del Combo Tripartito: SOLO 2 estados, derivados 1:1 del
       array zones_affected del motor — sin transiciones ni animaciones, para
       que una zona afectada nunca cambie de color, parpadee ni desaparezca. */
    .cz {
        padding: 2px 6px; border-radius: 999px; font-weight: 700;
        border: 1px solid transparent;
        transition: none; animation: none;
    }
    /* Afectada (en zones_affected): verde fijo, igual desde que se toca hasta el final. */
    .combo-hud .cz.aff {
        background: #1f9d4c; border-color: #8cffa0; color: #fff;
        text-shadow: 0 1px 1px rgba(0, 0, 0, 0.5);
    }
    /* Todavía no afectada. */
    .combo-hud .cz.todo {
        background: rgba(255, 255, 255, 0.06); border-color: rgba(255, 255, 255, 0.18);
        color: rgba(255, 255, 255, 0.42); font-weight: 600;
    }
    /* Marca pequeña de la ÚLTIMA ronda (⚔ entró / 🛡 bloqueado): solo texto,
       nunca cambia el color de la zona. */
    .cz-mark { margin-left: 3px; font-size: 0.85em; opacity: 0.9; }
    /* 3/3: la cápsula entera en dorado con brillo pulsante + cartel
       compacto encima (posición absoluta: no ensancha la cápsula). */
    /* Ronda en que salta un combo: solo el borde de la cápsula y el cartel;
       los chips de zona NO cambian (siguen en su verde fijo). */
    .combo-hud.is-done { border-color: #ffd54a; }
    /* Combo Tripartito ya cobrado: el contador queda en dorado fijo (3/3 ✓+3). */
    .combo-hud-count.claimed {
        background: linear-gradient(90deg, #ff8a00, #ffd54a); border-color: #fff3b0; color: #2a1600;
    }
    .combo-hud-count.claimed b { color: #2a1600; }
    @keyframes combo-glow {
        0%, 100% { box-shadow: 0 0 6px rgba(255, 200, 0, 0.45), 0 4px 14px rgba(0,0,0,0.55); }
        50% { box-shadow: 0 0 20px rgba(255, 210, 0, 0.95), 0 4px 14px rgba(0,0,0,0.55); }
    }
    .cz-short { display: none; }
    /* Ventanas estrechas: el panel flotante de la mano tiene ancho fijo
       (~706px) y deja menos esquina libre — la cápsula se compacta para no
       meterse debajo de él (ver t12: medido a 1280/1400/1600/1920px). */
    @media (max-width: 1500px) {
        .combo-hud .combo-word { display: none; }
        .combo-hud-left { left: 0.6%; }
        .combo-hud-right { right: 0.6%; }
    }
    @media (max-width: 1350px) {
        .combo-hud .cz-full { display: none; }
        .combo-hud .cz-short { display: inline; }
    }
    /* Pestaña de etiqueta encima de la cápsula (absoluta: no la ensancha),
       alineada al borde exterior de su esquina: roja = golpes que RECIBES
       (cápsula bajo tu personaje), azul = golpes que DAS (bajo el rival). */
    .combo-hud-tag {
        position: absolute; bottom: calc(100% + 3px);
        padding: 2px 8px; border-radius: 6px 6px 6px 6px;
        font-size: 0.86em; font-weight: 900; letter-spacing: 0.05em; white-space: nowrap;
        background: rgba(8, 8, 14, 0.85); text-shadow: 0 1px 2px #000;
    }
    .combo-hud-left .combo-hud-tag { left: 4px; }
    .combo-hud-right .combo-hud-tag { right: 4px; }
    .combo-hud-tag.tag-received { color: #ff9a9a; border: 1px solid rgba(255, 90, 90, 0.7); }
    .combo-hud-tag.tag-dealt { color: #8cc8ff; border: 1px solid rgba(77, 163, 255, 0.7); }
    /* Cartel de combo activado: encima de la pestaña de etiqueta. */
    .combo-hud-banner {
        position: absolute; bottom: calc(100% + 1.9em); left: 50%; transform: translateX(-50%);
        white-space: nowrap;
        padding: 3px 10px; border-radius: 6px;
        background: linear-gradient(90deg, #ff8a00, #ffd54a); color: #2a1600;
        font-weight: 900; letter-spacing: 0.06em; font-size: 0.95em;
        box-shadow: 0 0 12px rgba(255, 200, 0, 0.8);
    }

    /* Puntos tácticos (botones reales, estilo Waves Ducks): rojo = zona de
       ataque al rival, verde = zona propia a defender. El punto activo se
       resalta con fondo sólido; el resto queda en contorno tenue. */
    [class*="st-key-tactical_atk"] .stButton > button,
    [class*="st-key-tactical_def"] .stButton > button {
        border-radius: 999px !important;
        border: 2px solid rgba(255,255,255,0.25) !important;
        background: rgba(0,0,0,0.35) !important;
    }
    [class*="st-key-tactical_atk"] .stButton > button[kind="primary"] {
        background: #e5484d !important; border-color: #ff8a8a !important;
        box-shadow: 0 0 14px rgba(229,72,77,0.8) !important;
    }
    [class*="st-key-tactical_def"] .stButton > button[kind="primary"] {
        background: #2fb344 !important; border-color: #8cffa0 !important;
        box-shadow: 0 0 14px rgba(47,179,68,0.8) !important;
    }
    .tactical-label {
        text-align: center; font-weight: 700; font-size: 0.8rem; margin-bottom: 0.2rem;
    }
    .tactical-label-atk { color: #ff7a7a; }
    .tactical-label-def { color: #6bdc7f; }

    /* Contenedor de la arena: ancho 100% adaptativo + alto fijado por
       padding-top en % (la técnica clásica "responsive aspect ratio box",
       soportada en cualquier navegador — más robusta aquí que la propiedad
       moderna aspect-ratio, que en pruebas se descuadraba con el
       display:flex propio de Streamlit). 770/2042*100 = 37.71%, la misma
       proporción que escenario.png (2042x770). .arena-bg se limita a
       RELLENAR este contenedor (inset:0) en vez de tener su propio tamaño
       por separado, así este wrapper y la imagen son SIEMPRE la misma caja
       a cualquier ancho de ventana o nivel de zoom: los puntos tácticos,
       avatares y barras de HP (posicionados en % dentro de esta misma caja)
       nunca se desalinean de la imagen. gap:0 porque los 6 contenedores de
       los puntos (ver más abajo), aunque su contenido queda fuera del flujo
       normal (position:absolute), seguirían reservando el gap por defecto
       entre elementos de Streamlit si no se anula aquí. */
    [class*="st-key-arena_outer"] {
        position: relative;
        width: 100%;
        max-width: 100%;
        padding-top: 37.71%; /* 770/2042*100 — alto fijado por padding, no por aspect-ratio */
        height: 0;
        border-radius: 18px;
        overflow: hidden;
        box-shadow: 0 0 0 1px rgba(255,255,255,0.08), 0 12px 30px rgba(0,0,0,0.5);
        margin-bottom: 0.6rem;
        gap: 0 !important;
    }
    /* Puntos tácticos SUPERPUESTOS sobre la arena (overlay=True, modo Vs.
       Bot) — ver render_tactical_points. Cada punto es su propio contenedor
       con key única (arena_point_pt_<def|atk>_<jugador>_<zona>) para darle
       su propia coordenada (en %, relativa a st-key-arena_outer — se
       mantienen pegados a la imagen en cualquier zoom). Verdes junto a tu
       luchador (izquierda), rojos junto al rival (derecha), desplazados
       lateralmente hacia el hueco entre el luchador y el marco del Maestro
       (no encima del cuerpo). */
    [class*="st-key-arena_point_pt_"] {
        position: absolute !important;
        transform: translate(-50%, -50%);
        z-index: 30;
        width: auto !important;
    }
    /* Descendiente (no ">"): st.button(help=...) envuelve el <button> en
       spans de tooltip (stTooltipIcon/stTooltipHoverTarget) que rompen el
       combinador de hijo directo que sí vale para el resto de botones del
       archivo (sin `help`, sin ese envoltorio extra). */
    [class*="st-key-arena_point_pt_"] .stButton button {
        width: 42px; height: 42px; min-height: 42px; border-radius: 50% !important;
        padding: 0 !important; font-size: 1.15rem; line-height: 1;
        display: flex; align-items: center; justify-content: center;
        border: 2px solid rgba(255,255,255,0.4) !important;
        background: rgba(0,0,0,0.5) !important;
    }
    [class*="st-key-arena_point_pt_def"] .stButton button[kind="primary"] {
        background: #2fb344 !important; border-color: #8cffa0 !important;
        box-shadow: 0 0 14px rgba(47,179,68,0.9) !important;
    }
    [class*="st-key-arena_point_pt_atk"] .stButton button[kind="primary"] {
        background: #e5484d !important; border-color: #ff8a8a !important;
        box-shadow: 0 0 14px rgba(229,72,77,0.9) !important;
    }
    /* top en 20/40/60% para caer dentro del rango vertical real del avatar
       (.arena-portal: top 10% + height 60% => ocupa del 10% al 70%), antes
       24/50/78% dejaba el punto de "legs" por debajo del personaje. */
    /* 42/58/73%: alineados con cabeza, torso y piernas de los luchadores
       centrales de escenario.png (antes 20/40/60%, a la altura de los
       Maestros laterales, que quedaba por encima de los luchadores). */
    [class*="st-key-arena_point_pt_def_a_head"]  { left: 26%; top: 42%; }
    [class*="st-key-arena_point_pt_def_a_torso"] { left: 26%; top: 58%; }
    [class*="st-key-arena_point_pt_def_a_legs"]  { left: 26%; top: 73%; }
    [class*="st-key-arena_point_pt_atk_a_head"]  { left: 74%; top: 42%; }
    [class*="st-key-arena_point_pt_atk_a_torso"] { left: 74%; top: 58%; }
    [class*="st-key-arena_point_pt_atk_a_legs"]  { left: 74%; top: 73%; }

    /* Elementos flotantes nativos de Streamlit (menú hamburguesa, footer
       "Made with Streamlit", botón "Manage app"/deploy y el avatar de
       usuario que lo acompaña) tapan los botones "⚔️ JUGAR" de la derecha
       en móvil — se ocultan, y además se deja aire abajo en el contenedor
       principal por si algún widget fijo no cae bajo estos selectores. */
    #MainMenu,
    footer,
    .stAppDeployButton,
    [data-testid="stStatusWidget"],
    [data-testid="stDecoration"] {
        display: none !important;
    }
    [data-testid="stAppViewContainer"] .block-container {
        padding-bottom: 60px !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- #
# Selección por clic + hover de las cartas de la mano, en su PROPIO <style>
# aparte del bloque CSS gigante de arriba (~800 líneas): en pruebas, el
# navegador perdía justo esta regla al analizar el bloque gigante (el texto
# la contenía, pero no aparecía en el.sheet.cssRules — sin relación con
# !important ni con la selectividad; un <style> nuevo con el mismo texto
# exacto sí la analizaba bien), y el síntoma encajaba exacto con el bug
# reportado: solo la ÚLTIMA carta de la mano respondía al hover/clic, las
# demás quedaban "tapadas" porque el botón "select_" invisible, al no tener
# position:relative en su contenedor (card_glow_*), se posicionaba contra
# el ancestro posicionado más cercano DISPONIBLE — el <html> raíz — y por
# tanto cubría la PÁGINA ENTERA (confirmado con getBoundingClientRect: las
# 3 cartas daban el mismo rect de 1024x768), ganando siempre la última en
# orden de documento. Aislarlo aquí, en un bloque pequeño, evita lo que sea
# que cause esa pérdida de la regla en el bloque grande.
st.markdown(
    """
    <style>
    /* Cada carta (card_glow_playable/locked/sel) es su propio contexto de
       posicionamiento: así el botón "select_" invisible de abajo
       (position:absolute;inset:0) se confina SIEMPRE al rectángulo de SU
       PROPIA carta. La carta SELECCIONADA (clic, no hover — ver más abajo)
       sube por encima de sus vecinas mientras el zoom la saca de su caja
       normal; el hover también sube un poco el z-index para que el
       tooltip de info nunca quede tapado por la carta de al lado. */
    [class*="st-key-battle_hand"] [class*="st-key-card_glow_playable"],
    [class*="st-key-battle_hand"] [class*="st-key-card_glow_locked"],
    [class*="st-key-battle_hand"] [class*="st-key-card_glow_sel"] {
        position: relative !important;
        z-index: 1;
    }
    [class*="st-key-battle_hand"] [class*="st-key-card_glow_sel"] {
        z-index: 50 !important;
    }
    [class*="st-key-battle_hand"] [class*="st-key-card_glow_playable"]:hover,
    [class*="st-key-battle_hand"] [class*="st-key-card_glow_locked"]:hover,
    [class*="st-key-battle_hand"] [class*="st-key-card_glow_sel"]:hover {
        z-index: 60 !important;
    }
    /* Botón "Seleccionar" real (misma key/lógica de siempre) estirado
       invisible sobre toda la carta: clicar imagen la selecciona — sin
       letrero visible, el estado se lee por el borde dorado + el zoom
       (ver más abajo) de la carta elegida. */
    [class*="st-key-battle_hand"] [class*="st-key-select_"] {
        position: absolute !important;
        inset: 0 !important;
        z-index: 1;
    }
    [class*="st-key-battle_hand"] [class*="st-key-select_"] .stButton,
    [class*="st-key-battle_hand"] [class*="st-key-select_"] .stButton > button {
        width: 100% !important;
        height: 100% !important;
    }
    [class*="st-key-battle_hand"] [class*="st-key-select_"] .stButton > button {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        color: transparent !important;
        font-size: 0 !important;
        padding: 0 !important;
        cursor: pointer;
    }
    /* Sin hover de tamaño (pedido explícito): pasar el ratón NO agranda la
       carta, solo revela el tooltip .card-info-panel (nombre/ATK-DEF/coste/
       habilidad/efecto — estilo base en el bloque CSS grande; aquí solo
       la activación). Dispara con :hover en el CONTENEDOR (card_glow_*),
       no en el propio panel: el botón "select_" invisible de arriba queda
       por encima de toda la carta, así que es él quien recibe el :hover
       del ratón, pero como es descendiente del mismo contenedor, :hover
       "sube" hasta ahí y de ahí llega al tooltip hermano. */
    [class*="st-key-battle_hand"] [class*="st-key-card_glow_playable"]:hover .card-info-panel,
    [class*="st-key-battle_hand"] [class*="st-key-card_glow_locked"]:hover .card-info-panel,
    [class*="st-key-battle_hand"] [class*="st-key-card_glow_sel"]:hover .card-info-panel {
        opacity: 1;
        visibility: visible;
    }
    /* Clic = selección = zoom grande hasta elegir otra carta: esta vuelve al
       tamaño base y la nueva se agranda — ver card_glow_sel, calculado en
       render_hand según sel_cards_{jugador}/active_card_{jugador}.
       transform-origin: bottom center + scale/translateY negativo: la carta
       crece HACIA ARRIBA (hacia el escenario) en vez de crecer también hacia
       abajo (origen por defecto = centro), que era lo que hacía que el marco
       dorado invadiera y tapara el botón "Pasar Turno" de debajo. */
    [class*="st-key-battle_hand"] [data-testid="stImage"] img {
        transition: transform 0.2s ease;
    }
    /* Selección MÚLTIPLE: con varias cartas elegidas a la vez el zoom
       grande (scale 1.2) las solapaba entre sí, así que ahora la carta
       elegida solo se eleva (anclada) y la ACTIVA, además, crece un poco. */
    [class*="st-key-battle_hand"] [class*="st-key-card_glow_sel"] [data-testid="stImage"] img {
        transform-origin: bottom center;
        transform: translateY(-10px);
        position: relative;
        z-index: 100;
    }
    [class*="st-key-battle_hand"] [class*="st-key-card_glow_sel_act"] [data-testid="stImage"] img {
        transform: translateY(-12px) scale(1.06);
    }
    /* Botón ✕ (deseleccionar SOLO esta carta) en la esquina superior
       derecha de la imagen elevada. right: alineado con el borde derecho
       real de la imagen (centrada dentro de su columna): 150px de ancho en
       el panel flotante (Vs. Bot), min(416px, 100%) en Hotseat. */
    [class*="st-key-battle_hand"] [class*="st-key-desel_"] {
        position: absolute !important;
        top: -20px;
        right: max(-8px, calc(50% - 208px - 12px));
        z-index: 160 !important;
        width: auto !important;
    }
    [class*="st-key-battle_hand_overlay"] [class*="st-key-desel_"] {
        right: calc(50% - 75px - 12px);
    }
    [class*="st-key-battle_hand"] [class*="st-key-desel_"] .stButton button,
    [class*="st-key-battle_hand"] [class*="st-key-desel_"] button {
        width: 34px !important; height: 34px !important; min-height: 34px !important;
        border-radius: 50% !important; padding: 0 !important;
        background: #e5484d !important; color: #fff !important;
        border: 2px solid #fff !important;
        font-size: 1.05rem !important; font-weight: 900 !important; line-height: 1 !important;
        display: flex; align-items: center; justify-content: center;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.6);
        cursor: pointer;
    }
    [class*="st-key-battle_hand"] [class*="st-key-desel_"] button:hover {
        background: #ff2e2e !important; transform: scale(1.1);
    }
    </style>
    """,
    unsafe_allow_html=True,
)


if panel_mode.startswith("🤝"):
    render_challenges_panel(connector, current_wallet_address, card_pool)
    st.stop()

if panel_mode.startswith("🎓"):
    render_tutorial(catalog_dicts)
    st.stop()

if panel_mode.startswith("📖"):
    render_gallery(catalog_dicts)
    st.stop()

if panel_mode.startswith("🛒"):
    render_marketplace_panel(connector, current_wallet_address)
    st.stop()

# --------------------------------------------------------------------------- #
# Página de Batalla: tarjeta de estado de la wallet + NFT equipado, barra
# compacta de Modo/Oponente/Nueva partida justo debajo, y herramientas de
# testeo plegadas en un expander para que no estorben durante la partida —
# ver render_wallet_and_nft_bar / render_battle_mode_bar /
# render_wallet_config_expander más arriba (antes vivían en la barra lateral
# o en el navbar superior).
# --------------------------------------------------------------------------- #
game_started = st.session_state.match is not None
if not game_started:
    # Lobby de Matchmaking en 2 columnas (nada de esto se renderiza una vez
    # arrancada la partida, para que el tablero suba y ocupe toda la
    # pantalla — ver el "if not game_started" original, ahora envuelve todo
    # el lobby en vez de solo el expander de Configuración).
    # Izquierda: tu NFT equipado + panel de Lanzar Reto (botón + Modo/
    # Oponente), apilados verticalmente. Derecha: feed de retos de bots
    # simulados (render_bot_challenges_feed) — "Aceptar Reto" ahí arranca la
    # partida directo, sin pasar por el botón de la izquierda.
    lobby_left, lobby_right = st.columns([1, 1])
    with lobby_left:
        with st.container(key="left_info_panel"):
            render_wallet_and_nft_bar(connector, current_wallet_address, card_pool)
    with lobby_right:
        render_bot_challenges_feed(connector, current_wallet_address, card_pool)
    render_wallet_config_expander(connector, current_wallet_address)

match = st.session_state.match
if match is None:
    # La búsqueda de rival (matchmaking_start) ya se muestra arriba, como
    # nuestra propia fila en el feed de la derecha (render_bot_challenges_feed)
    # — no hay nada más que mostrar aquí en ese caso.
    if st.session_state.get("matchmaking_start") is None:
        st.info("Pulsa **⚔️ LANZAR RETO ⚔️** o acepta un reto del panel de la derecha para empezar a jugar.")
    st.stop()


_ZONE_ICONS = {"head": "🗣️", "torso": "👕", "legs": "🦵"}
_ZONE_LABELS = {"head": "Cabeza", "torso": "Torso", "legs": "Piernas"}


# --------------------------------------------------------------------------- #
# Temporizador de turno (TURN_TIMER_SECONDS = 30s, card_schema.py): si pasa
# el tiempo sin que el jugador actúe, el turno se resuelve solo (motor:
# auto_skip_turn + end_turn, ya existían en pvp_engine.py sin usar desde
# ningún sitio). st.session_state.turn_start_time se arma en NUEVA PARTIDA y
# se reinicia en cada end_turn() (los 2 sitios donde ya se llamaba, más este
# de aquí) — así el reloj siempre refleja el inicio real del turno actual,
# aunque Streamlit solo re-ejecute este script ante una interacción.
# --------------------------------------------------------------------------- #
st.session_state.setdefault("turn_start_time", time.time())

def _pressed_pass_this_run(player_key: str) -> bool:
    """¿Este rerun lo ha provocado el botón PASAR TURNO de este jugador? En
    Streamlit el script corre de arriba abajo y este bloque del temporizador
    va ANTES que los botones: sin esta comprobación, pulsarlo justo cuando el
    reloj llega a 0 (o con 1 s de desfase navegador/servidor) se tomaba como
    "tiempo agotado". Es la única acción limpia que puede llegar tarde; elegir
    carta o marcar zona NO cuentan (ver regla estricta más abajo)."""
    return st.session_state.get(f"confirm_turn_{player_key}") is True


# REGLA ESTRICTA DE INACTIVIDAD: si el reloj llega a 0, suma +1 a todo
# jugador que no haya hecho una acción LIMPIA este turno — pulsar PASAR
# TURNO o jugar cartas completas (con sus zonas). Tener una carta elegida
# sin zonas, o elegida pero sin confirmar, es dejar el turno colgado: SÍ
# cuenta como inactividad, y esa selección no se juega.
if match.status == MatchStatus.ONGOING:
    elapsed = time.time() - st.session_state.turn_start_time
    if elapsed >= TURN_TIMER_SECONDS and any(_pressed_pass_this_run(pk) for pk in ("a", "b")):
        # Pasar Turno pulsado justo al expirar: se procesa más abajo con
        # normalidad (el propio botón resuelve el turno y reinicia el reloj).
        print("[TIMEOUT] Pasar Turno pulsado al expirar: se procesa como acción válida.")
    elif elapsed >= TURN_TIMER_SECONDS:
        print(f"[TIMEOUT] Ronda {match.turn_number}: temporizador expirado, resolviendo turno.")
        try:
            # Selección a medias o sin confirmar = turno colgado: no se juega
            # y no perdona la inactividad (end_turn(timed_out=True) la suma).
            for _pk in ("a", "b"):
                if st.session_state.get(f"sel_cards_{_pk}"):
                    match.log.append(
                        f"⌛ {(match.player_a if _pk == 'a' else match.player_b).name}: "
                        f"se agotó el tiempo con una jugada sin terminar; no se juega."
                    )
            if st.session_state.vs_bot:
                if not match.player_a.acted_this_turn:
                    auto_skip_turn(match, "a")
                try:
                    bot_choose_plays(match, "b")
                    _flash_energy_spend("b", match.player_b.energy_spent_this_turn)
                except Exception as e:
                    # No dejar el round a medio resolver si el Bot falla al
                    # elegir jugada: igualmente hay que cerrar el turno, si
                    # no la partida queda congelada en esta misma ronda.
                    print(f"[TIMEOUT] bot_choose_plays fallo: {e!r}")
                    match.log.append(f"El Bot no pudo jugar cartas este turno ({e}).")
            _end_turn_with_fx(match, timed_out=True)
        except EngineError as e:
            print(f"[TIMEOUT] end_turn fallo: {e!r}")
            match.log.append(f"Error al resolver el turno automáticamente: {e}")
            st.error(str(e))
        except Exception as e:
            print(f"[TIMEOUT] end_turn fallo: {e!r}")
            match.log.append(f"Error al resolver el turno automáticamente: {e}")
        finally:
            # Resetear SIEMPRE, incluso si falló: si no, el timer ya expirado
            # sigue en 0 en el próximo rerun y el error se repite sin fin.
            st.session_state.turn_start_time = time.time()
            _reset_tactical_selection()
            print(f"[TIMEOUT] turno reseteado (Ronda {match.turn_number}), forzando rerun.")
        st.rerun()

state = get_match_state(match)


# --------------------------------------------------------------------------- #
# Cabecera: HP / energía / estado (contenedor con key propia para poder
# compactarla por CSS sin afectar a cabeceras de otras secciones). El ancla
# "arena-top-anchor" vive justo aquí (no antes, donde todavía podía quedar
# por encima el panel de Configuración animándose al colapsar) para que el
# auto-scroll alinee el borde superior de la ventana justo encima de las
# barras de HP / TURNO.
#
# Se re-ejecuta en CADA rerun mientras la partida esté en curso (no solo al
# pulsar NUEVA PARTIDA): cada "Pasar Turno" / timeout de ronda dispara su
# propio rerun y por lo tanto un nuevo scroll — ya no depende de ningún flag
# de sesión. scrollIntoView (no el cálculo de coordenadas -20px que se usaba
# antes) porque con los expanders de Configuración/Wallet colapsados por
# completo durante el combate (ver game_started más arriba) el tablero
# entero vuelve a caber en pantalla, así que alinear el borde superior ya no
# deja la barra naranja tapada.
# --------------------------------------------------------------------------- #
st.markdown('<div id="arena-top-anchor"></div>', unsafe_allow_html=True)
if match.status == MatchStatus.ONGOING:
    components.html(
        f"""<script>
        function scrollToArena() {{
            var el = window.parent.document.getElementById('arena-top-anchor') || document.getElementById('arena-top-anchor');
            if (el) {{ el.scrollIntoView({{ behavior: 'smooth', block: 'start' }}); }}
        }}
        // shortcut: 3 intentos (150/500/1000ms) en vez de MutationObserver —
        // en la Ronda 1 las imagenes de cartas/avatares aun estan cargando y
        // desplazan el layout tras el primer intento; en rondas siguientes
        // (todo cacheado) los intentos de mas son no-op.
        setTimeout(scrollToArena, 150);
        setTimeout(scrollToArena, 500);
        setTimeout(scrollToArena, 1000);
        </script><!-- turno {match.turn_number} -->""",
        height=0,
    )

ZONE_ORDER = (Zone.HEAD, Zone.TORSO, Zone.LEGS)
ZONE_ICON = {"head": "🗣️", "torso": "👕", "legs": "🦵"}
ZONE_NAME = {"head": "Cabeza", "torso": "Tronco", "legs": "Piernas"}


def _card_zone_requirements(card_dict: dict) -> tuple[int, int]:
    """(nº máx. de zonas de ATAQUE, nº máx. de zonas de DEFENSA) de la carta:
    1 zona distinta por cada punto de ATK/DEF (tope 3, ver
    pvp_engine.max_attack_zones / max_defense_zones)."""
    return min(max(card_dict["atk"], 0), len(Zone)), min(max(card_dict["defense"], 0), len(Zone))


def _selected_card(player_key: str) -> dict | None:
    """La carta ACTIVA (la que están configurando ahora los puntos
    tácticos) dentro de la selección múltiple."""
    sel_id = st.session_state.get(f"active_card_{player_key}")
    if sel_id is None:
        return None
    hand = state[f"player_{player_key}"]["hand"]
    return next((c for c in hand if c["id"] == sel_id), None)


def _selected_cards(player_key: str) -> list[dict]:
    """Todas las cartas seleccionadas este turno, en orden de selección."""
    hand = {c["id"]: c for c in state[f"player_{player_key}"]["hand"]}
    return [hand[cid] for cid in st.session_state[f"sel_cards_{player_key}"] if cid in hand]


def _card_zones(player_key: str, card_id: str) -> dict:
    zones = st.session_state[f"zones_{player_key}"].setdefault(card_id, {"atk": [], "def": []})
    for k in ("atk", "def"):  # sesiones abiertas con la versión de 1 zona por carta
        if not isinstance(zones.get(k), list):
            zones[k] = [zones[k]] if zones.get(k) else []
    return zones


def _zones_taken_by_others(player_key: str, card_id: str) -> dict[str, dict[str, str]]:
    """Casillas ya ocupadas este turno por las OTRAS cartas elegidas:
    {"atk": {zona: nombre_carta}, "def": {zona: nombre_carta}}. Regla de
    zonas únicas por turno (pvp_engine.play_card): cada zona admite 1 ataque
    y 1 defensa tuyos por turno, sumando todas tus cartas."""
    taken: dict[str, dict[str, str]] = {"atk": {}, "def": {}}
    for other in _selected_cards(player_key):
        if other["id"] == card_id:
            continue
        oz = _card_zones(player_key, other["id"])
        for k in ("atk", "def"):
            for z in oz[k]:
                taken[k].setdefault(z, other["name"])
    return taken


def _free_zones(player_key: str, card_dict: dict) -> tuple[int, int]:
    """(casillas de ataque libres, de defensa libres) que la carta podría usar
    todavía: 0 si la carta no tiene ese tipo, si no 3 − ocupadas por otras."""
    n_atk, n_def = _card_zone_requirements(card_dict)
    taken = _zones_taken_by_others(player_key, card_dict["id"])
    return (len(Zone) - len(taken["atk"]) if n_atk else 0,
            len(Zone) - len(taken["def"]) if n_def else 0)


def _card_ready(player_key: str, card_dict: dict) -> bool:
    """Lista para jugar si marcó al menos una zona (se pueden dejar zonas sin
    usar), si la carta no tiene ninguna zona que marcar (la 0/0) o si las
    casillas que podría usar ya están todas ocupadas por tus otras cartas del
    turno (te sobran ataques/defensas: no estás obligado a usarlos)."""
    n_atk, n_def = _card_zone_requirements(card_dict)
    if not n_atk and not n_def:
        return True
    zones = _card_zones(player_key, card_dict["id"])
    if zones["atk"] or zones["def"]:
        return True
    return _free_zones(player_key, card_dict) == (0, 0)


def _toggle_zone(player_key: str, card_dict: dict, kind: str, zone_value: str) -> None:
    """Clic en una casilla: la marca o desmarca para la carta activa. Nunca
    repite zona dentro de la misma carta NI con otra carta tuya del turno (esa
    casilla está bloqueada); si la carta ya tiene todas sus zonas de ese tipo
    marcadas, la más antigua se sustituye por la nueva."""
    limit = _card_zone_requirements(card_dict)[0 if kind == "atk" else 1]
    if limit <= 0:
        return
    chosen = _card_zones(player_key, card_dict["id"])[kind]
    if zone_value in chosen:
        chosen.remove(zone_value)
        return
    if zone_value in _zones_taken_by_others(player_key, card_dict["id"])[kind]:
        return  # ocupada por otra carta tuya este turno
    if len(chosen) >= limit:
        if limit > 1:
            # Carta de 2-3 zonas ya completa: NO se sustituye en silencio la
            # más antigua (parecía que el ataque/defensa "no se registraba");
            # hay que desmarcar una antes. Con 1 sola zona sí se cambia
            # directamente (comportamiento tipo radio, intuitivo).
            return
        chosen.pop(0)
    chosen.append(zone_value)


def _zones_chip(chosen: list, limit: int) -> str:
    short = {"head": "Cab", "torso": "Tron", "legs": "Pier"}
    names = ", ".join(short[z] for z in chosen) if chosen else "—"
    return f"{names} ({len(chosen)}/{limit})"


def _select_card(player_key: str, card_id: str) -> None:
    """Clic en una carta: si no estaba elegida se AÑADE a la selección (sin
    soltar las demás); en ambos casos pasa a ser la carta activa."""
    sel = st.session_state[f"sel_cards_{player_key}"]
    if card_id not in sel:
        sel.append(card_id)
        st.session_state[f"zones_{player_key}"][card_id] = {"atk": [], "def": []}
    st.session_state[f"active_card_{player_key}"] = card_id


def _deselect_card(player_key: str, card_id: str) -> None:
    """Botón ✕ de una carta: la quita SOLO a ella (libera su energía) y deja
    intactas las demás; si era la activa, pasa a serlo la última que quede."""
    sel = st.session_state[f"sel_cards_{player_key}"]
    if card_id in sel:
        sel.remove(card_id)
    st.session_state[f"zones_{player_key}"].pop(card_id, None)
    if st.session_state[f"active_card_{player_key}"] == card_id:
        st.session_state[f"active_card_{player_key}"] = sel[-1] if sel else None


def render_tactical_points(player_key: str, overlay: bool = False) -> None:
    """Puntos tácticos estilo Waves Ducks: verde = zonas PROPIAS a defender,
    rojo = zonas del RIVAL a atacar. Reglas de zonas (pvp_engine.py):
    la carta activa marca hasta ATK zonas rojas y hasta DEF zonas verdes,
    todas DISTINTAS dentro de la carta (clic = marcar/desmarcar; si ya están
    todas, la más antigua se sustituye). Se pueden dejar zonas sin marcar.
    overlay=True (modo Vs. Bot): 6 puntos circulares superpuestos sobre
    escenario.png dentro de render_arena — verdes junto a tu luchador
    (izquierda), rojos junto al rival (derecha); el CSS .arena-point-* del
    bloque de estilos los posiciona con position:absolute.
    overlay=False (Hotseat): la fila ancha de botones de siempre, debajo de
    la mano. Un grupo que la carta activa no puede usar (0 ATK o 0 DEF) se
    deshabilita — y sin carta elegida todavía, los 2 grupos quedan
    deshabilitados: hay que elegir carta primero."""
    # Selección múltiple: los puntos editan SOLO la carta activa (la última
    # clicada). Las casillas ya ocupadas por las OTRAS cartas elegidas este
    # turno quedan BLOQUEADAS (contorno discontinuo "_used" + deshabilitadas):
    # cada zona admite 1 ataque y 1 defensa tuyos por turno.
    sel_card = _selected_card(player_key)
    n_atk, n_def = _card_zone_requirements(sel_card) if sel_card else (0, 0)
    active_zones = _card_zones(player_key, sel_card["id"]) if sel_card else {"atk": [], "def": []}
    if sel_card:
        taken_by = _zones_taken_by_others(player_key, sel_card["id"])
    else:
        taken_by = {"atk": {}, "def": {}}
        for other in _selected_cards(player_key):
            oz = _card_zones(player_key, other["id"])
            for k in ("atk", "def"):
                for z in oz[k]:
                    taken_by[k].setdefault(z, other["name"])
    used_by_others = {k: set(v) for k, v in taken_by.items()}
    specs = (
        ("def", "🟢 Defender", "tactical-label-def", n_def),
        ("atk", "🔴 Atacar", "tactical-label-atk", n_atk),
    )

    def _click(prefix: str, zone_value: str) -> None:
        if sel_card:
            _toggle_zone(player_key, sel_card, prefix, zone_value)

    def _help(prefix: str, z: Zone, limit: int) -> str:
        # Etiqueta mínima y fija: solo acción + zona (sin nombres de carta,
        # contadores ni explicaciones — el estado ya se ve en el color/borde).
        return f"{'Defender' if prefix == 'def' else 'Atacar'} {ZONE_NAME[z.value]}"

    def _disabled(prefix: str, z: Zone, limit: int) -> bool:
        selected = z.value in active_zones[prefix]
        if selected:
            return False  # siempre se puede desmarcar
        if limit <= 0 or z.value in used_by_others[prefix]:
            return True
        # Carta de 2-3 zonas ya completa: hay que desmarcar antes de elegir otra.
        return limit > 1 and len(active_zones[prefix]) >= limit

    if overlay:
        for prefix, _label, _cls, limit in specs:
            for z in ZONE_ORDER:
                selected = z.value in active_zones[prefix]
                used = z.value in used_by_others[prefix]
                point_key = f"pt_{prefix}_{player_key}_{z.value}"
                with st.container(key=f"arena_point_{point_key}{'_used' if used else ''}"):
                    if st.button(
                        ZONE_ICON[z.value], key=point_key,
                        type="primary" if selected else "secondary",
                        disabled=_disabled(prefix, z, limit),
                        help=_help(prefix, z, limit),
                    ):
                        _click(prefix, z.value)
                        st.rerun()
        return

    col_def, col_atk = st.columns(2)
    for col, (prefix, label_txt, label_cls, limit) in zip((col_def, col_atk), specs):
        with col:
            counter = f" · {len(active_zones[prefix])}/{limit}" if limit > 0 else ""
            st.markdown(f"<div class='tactical-label {label_cls}'>{label_txt}{counter}</div>", unsafe_allow_html=True)
            with st.container(key=f"tactical_{prefix}_{player_key}"):
                for z in ZONE_ORDER:
                    selected = z.value in active_zones[prefix]
                    used = z.value in used_by_others[prefix]
                    if st.button(
                        f"{ZONE_ICON[z.value]} {_help(prefix, z, limit)}",
                        key=f"{prefix}_pt_{player_key}_{z.value}",
                        type="primary" if selected else "secondary",
                        width="stretch",
                        disabled=_disabled(prefix, z, limit),
                        help=_help(prefix, z, limit),
                    ):
                        _click(prefix, z.value)
                        st.rerun()


def render_arena(state: dict) -> None:
    """Arena de combate: escenario.png de fondo con las barras de HP/Energía
    y el contador de ronda superpuestos arriba, el Maestro de cada luchador
    montado en los marcos laterales (GAMENFT/, ver gamenft_avatar_path), y —
    en modo Vs. Bot— los 6 puntos tácticos (verde/rojo) superpuestos
    directamente sobre la imagen, junto a cada luchador. En Hotseat los
    puntos no se superponen aquí (cada jugador tiene los suyos debajo de su
    propia mano — ver render_tactical_points(overlay=False))."""
    bg = _image_b64(st.session_state.get("arena_background_path") or str(ESCENARIO_PATH))
    av_a = _image_b64(str(gamenft_avatar_path(st.session_state.get("equipped_token_id"))))
    av_b = _image_b64(str(gamenft_avatar_path(st.session_state.get("rival_avatar_id"))))
    pa, pb = state["player_a"], state["player_b"]
    ongoing = state["status"] == MatchStatus.ONGOING.value
    # Feedback de gasto pendiente (ver _flash_energy_spend): se consume aquí
    # para que el "-N" se anime una única vez.
    # Avisos flotantes vigentes durante _FX_TTL_SECONDS: se pintan con el
    # MISMO HTML (mismo nonce) en los reruns de ese intervalo — Streamlit no
    # re-crea un markdown idéntico, así que la animación no se reinicia — y
    # pasado ese tiempo se descartan.
    if time.time() - st.session_state.get("fx_time", 0.0) > _FX_TTL_SECONDS:
        for _k in _FX_KEYS:
            st.session_state[_k] = {}
    energy_fx = st.session_state.get("energy_fx") or {}
    hp_fx = st.session_state.get("hp_fx") or {}
    regen_fx = st.session_state.get("regen_fx") or {}
    inact_limit = state.get("inactivity_limit", 3)
    fx_nonce = st.session_state.get("energy_fx_nonce", 0)

    def hp_bar(p: dict, align: str, player_key: str) -> str:
        pct = max(0.0, min(1.0, p["hp"] / p["max_hp"])) * 100
        # Barra siempre sobre el tope (3): se vacía según lo que gastas este
        # turno y arranca el siguiente con lo que quedó +1 (ver end_turn).
        energy_cap = MAX_ENERGY
        # Gasto EN TIEMPO REAL: lo ya jugado (staged) + las cartas que el
        # jugador tiene seleccionadas ahora mismo (aún sin confirmar). Antes
        # solo contaba lo jugado, y en Vs. Bot jugar y cerrar el turno es un
        # único clic, así que la barra nunca se veía bajar.
        committed = (
            sum(c["cost_energy"] for c in _selected_cards(player_key))
            if state["status"] == MatchStatus.ONGOING.value else 0
        )
        used = p["energy_spent_this_turn"] + committed
        energy_left = max(0, min(MAX_ENERGY_SPEND_PER_TURN, p["energy"]) - used)
        energy_pct = energy_left / energy_cap * 100
        energy_next = min(MAX_ENERGY, max(0, p["energy"] - used) + ENERGY_PER_TURN)
        segs = "<span></span>" * energy_cap
        empty_cls = " energy-empty" if energy_left <= 0 else ""
        spent = energy_fx.get(player_key, 0)
        hp_delta = hp_fx.get(player_key, 0)
        floats = []
        # Burbujas minimalistas: solo número + icono. Rojo con "-" si se
        # pierde, verde con "+" y emoticono si se recupera.
        regen = regen_fx.get(player_key, 0)
        if hp_delta < 0:
            floats.append(f"<div class='energy-float hp-float'>-{-hp_delta} 🩸</div>")
        elif hp_delta > 0:
            floats.append(f"<div class='energy-float hp-float gain'>+{hp_delta} 🩸 💚</div>")
        if spent > 0:
            floats.append(f"<div class='energy-float'>-{spent} ⚡</div>")
        if regen > 0:
            floats.append(f"<div class='energy-float gain regen'>+{regen} ⚡ ✨</div>")
        idle = p.get("inactive_turns", 0)
        idle_pill = (
            f" <span class='arena-inactive{' last' if idle >= inact_limit - 1 else ''}' "
            f"title='Turnos seguidos en que se agotó el tiempo sin jugar ni pasar turno. Al llegar a "
            f"{inact_limit}, derrota por inactividad. Pasar turno no cuenta.'>"
            f"⚠️ Inactivo {idle}/{inact_limit}</span>"
            if idle > 0 and state["status"] == MatchStatus.ONGOING.value else ""
        )
        float_html = (
            f"<div class='combat-floats' data-n='{fx_nonce}'>{''.join(floats)}</div>" if floats else ""
        )
        return (
            f"<div class='arena-hp arena-hp-{align}'>"
            f"<div class='arena-hp-name'>{html.escape(p['name'])}{idle_pill}</div>"
            f"<div class='arena-hp-track'><div class='arena-hp-fill' style='width:{pct:.1f}%'></div>"
            f"<span class='arena-hp-text'>❤️ {p['hp']}/{p['max_hp']}</span></div>"
            f"<div class='arena-energy-row'>"
            f"<div class='arena-energy-track{empty_cls}'>"
            f"<div class='arena-energy-fill' style='width:{energy_pct:.1f}%'></div>"
            f"<div class='arena-energy-segs'>{segs}</div>"
            f"<span class='arena-energy-text'>⚡ {energy_left}/{energy_cap}</span></div>"
            f"<div class='arena-energy-pool' title='Energía con la que empezarás el próximo turno "
            f"(lo que te quede +{ENERGY_PER_TURN}, máximo {MAX_ENERGY})'>🔋 {energy_next}</div>"
            f"</div>"
            f"{float_html}"
            f"</div>"
        )

    def zone_report_html(player_key: str, align: str) -> str:
        """Marcador del Combo Tripartito bajo cada bando para quien ATACA
        (`player_key`). FUENTE ÚNICA: el array `zones_affected` que devuelve el
        motor en get_match_state() — el mismo conjunto que usa
        pvp_engine._resolve_direction para decidir el combo:
        - cada chip de zona tiene solo 2 estados: verde fijo si la zona está en
          zones_affected (desde la ronda en que se tocó hasta el final de la
          partida) o gris si no; nada de lo que pase en la ronda actual
          (bloqueos, combos) cambia ese color;
        - el contador «🔥 X/3» es len(zones_affected), siempre (también con el
          combo ya cobrado: «3/3 ✓+3»);
        - la última ronda solo añade una marca de texto: ⚔ entró / 🛡 bloqueado."""
        pst = state[f"player_{player_key}"]
        last = pst.get("last_turn") or {}
        hits, blocked = last.get("hits", {}), last.get("blocked", {})
        affected = [z for z in ("head", "torso", "legs") if z in set(pst.get("zones_affected", []))]
        claimed = bool(pst.get("tripartite_claimed", False))
        if claimed and len(affected) != 3:  # nunca debería pasar: el motor solo cobra con las 3
            print(f"[COMBO_HUD] incoherencia {player_key}: claimed con zones_affected={affected}")
        specials = last.get("combos", [])
        tri_now = last.get("tripartite", False)
        short = {"head": "Cab.", "torso": "Tron.", "legs": "Pier."}
        zone_chips = []
        for z in ("head", "torso", "legs"):
            # Nombre completo + abreviado: el CSS muestra uno u otro según el
            # ancho de ventana (ver @media de .combo-hud).
            label = f"<span class='cz-full'>{ZONE_NAME[z]}</span><span class='cz-short'>{short[z]}</span>"
            if hits.get(z):
                mark = "<span class='cz-mark' title='Entró un ataque en la última ronda'>⚔</span>"
            elif blocked.get(z):
                mark = "<span class='cz-mark' title='Bloqueado en la última ronda'>🛡</span>"
            else:
                mark = ""
            if z in affected:
                zone_chips.append(f"<span class='cz aff' data-zone='{z}'>✓ {label}{mark}</span>")
            else:
                zone_chips.append(f"<span class='cz todo' data-zone='{z}'>{label}{mark}</span>")
        # player_key = quién ATACA. Cada cápsula se pinta bajo el personaje que
        # RECIBE esos golpes (ver la llamada en render_arena).
        if player_key == "a":
            tag, tag_cls = "⚔️ TUS GOLPES", "tag-dealt"
            tip = (f"Zonas del rival que has tocado (verde = afectada, se queda así toda la partida). Al tener "
                   f"las 3 activas el Combo Tripartito: +{TRIPARTITE_COMBO_BONUS}, una vez por partida. "
                   "⚔ / 🛡 = qué pasó en la última ronda.")
            mine = True
        else:
            tag, tag_cls = "💢 GOLPES RECIBIDOS", "tag-received"
            tip = (f"Zonas de TU cuerpo que el rival ya ha tocado (verde = afectada). Con las 3 activa su "
                   f"Combo Tripartito (+{TRIPARTITE_COMBO_BONUS}, una vez). ⚔ / 🛡 = última ronda.")
            mine = False
        if tri_now:
            banner_txt = "¡TU COMBO TRIPARTITO!" if mine else "¡COMBO TRIPARTITO RIVAL!"
        elif specials:
            banner_txt = "¡TU COMBO ESPECIAL!" if mine else "¡COMBO ESPECIAL RIVAL!"
        else:
            banner_txt = ""
        banner = f"<span class='combo-hud-banner'>{banner_txt}</span>" if banner_txt else ""
        done_cls = " is-done" if banner_txt else ""
        count_cls = "combo-hud-count claimed" if claimed else "combo-hud-count"
        tri_txt = f"{len(affected)}/3" + (f" ✓+{TRIPARTITE_COMBO_BONUS}" if claimed else "")
        return (
            f"<div class='combo-hud combo-hud-{align}{done_cls}' data-affected='{','.join(affected)}' "
            f"title='{html.escape(tip, quote=True)}'>"
            f"<span class='combo-hud-tag {tag_cls}'>{tag}</span>"
            f"{banner}"
            f"<span class='{count_cls}'>🔥<span class='combo-word'> COMBO</span> <b>{tri_txt}</b></span>"
            f"<span class='combo-hud-zones'>{''.join(zone_chips)}</span>"
            f"</div>"
        )

    if ongoing:
        remaining = max(0.0, TURN_TIMER_SECONDS - (time.time() - st.session_state.turn_start_time))
        timer_html = (
            f"<div class='arena-timer'>⏱️ <span id='arena-timer-seconds'>{int(remaining)}</span>s</div>"
        )
    else:
        remaining = None
        timer_html = ""

    my_idle = pa.get("inactive_turns", 0)
    last_warning_html = (
        "<div class='arena-last-warning'>⚠️ ¡Juega o pasa turno<br>antes de que acabe el tiempo!</div>"
        if ongoing and my_idle >= state.get("inactivity_limit", 3) - 1 else ""
    )

    with st.container(key="arena_outer"):
        st.markdown(
            f"""
            <div class="arena-bg" style="background-image:url(data:image/png;base64,{bg});">
                <div class="arena-topbar">
                    {hp_bar(pa, 'left', 'a')}
                    <div class="arena-turn">Ronda {state['turn_number']}{timer_html}{last_warning_html}</div>
                    {hp_bar(pb, 'right', 'b')}
                </div>
                <img class="arena-portal arena-portal-left" src="data:image/png;base64,{av_a}">
                <img class="arena-portal arena-portal-right" src="data:image/png;base64,{av_b}">
                {zone_report_html('b', 'left')}{zone_report_html('a', 'right')}
            </div>
            """,
            unsafe_allow_html=True,
        )

        if remaining is not None:
            with st.container(key="turn_timeout_trigger"):
                st.button("⏱", key="turn_timeout_btn")
            components.html(
                f"""<script>
                (function () {{
                    const totalMs = {TURN_TIMER_SECONDS * 1000};
                    const remainingMs = {remaining * 1000:.0f};
                    const startedAt = Date.now();
                    // btn/label se buscan de nuevo en CADA tick (no una sola vez al
                    // montar el iframe), y una vez agotado el tiempo se REINTENTA el
                    // clic en cada tick (no uno solo): un clic aislado a veces no
                    // llegaba a registrarse (el botón real de Streamlit tarda en
                    // quedar listo para recibir el evento) y, al no reintentarse,
                    // la partida se quedaba congelada en "0s" hasta que el jugador
                    // tocaba cualquier otra cosa y forzaba un rerun manual. Reintentar
                    // no tiene coste: en cuanto un clic SÍ funciona, Streamlit vuelve
                    // a renderizar la ronda siguiente y este iframe entero desaparece,
                    // lo que detiene el intervalo solo.
                    const iv = setInterval(function () {{
                        const left = Math.max(0, remainingMs - (Date.now() - startedAt));
                        const label = window.parent.document.getElementById('arena-timer-seconds');
                        if (label) label.textContent = Math.ceil(left / 1000);
                        if (left <= 0) {{
                            const btn = window.parent.document.querySelector(
                                '[class*="st-key-turn_timeout_trigger"] button'
                            );
                            if (btn) btn.click();
                        }}
                    }}, 250);
                }})();
                </script>""",
                height=0,
            )

        if ongoing and st.session_state.vs_bot:
            render_tactical_points("a", overlay=True)


render_arena(state)

if state["status"] != MatchStatus.ONGOING.value:
    # Liquidación de la economía de la partida (Reto 1v1 o recompensa
    # Competitivo/Cristal) — una sola vez por partida, de ahí el flag
    # match_reward_settled (el script entero se re-ejecuta en cada rerun
    # mientras se muestra el banner de victoria/derrota).
    if not st.session_state.match_reward_settled:
        mcid = st.session_state.get("match_challenge_id")
        econ_ch = econ_server().challenges.get(mcid) if mcid is not None else None
        if econ_ch is not None and econ_ch.status in (ChallengeStatus.MATCHED.value, ChallengeStatus.VS_BOT.value):
            acc = econ_server().account(current_wallet_address)
            kur0, elo0, cry0 = acc.balances["KUR"], acc.elo, acc.crystals
            winner = {
                MatchStatus.PLAYER_A_WINS.value: current_wallet_address,
                MatchStatus.PLAYER_B_WINS.value: econ_ch.opponent if econ_ch.creator == acc.wallet else econ_ch.creator,
            }.get(state["status"])
            econ_server().report_result(econ_ch.id, winner)
            push_kur(connector, current_wallet_address)
            d_kur, d_elo, d_cry = round(acc.balances["KUR"] - kur0, 2), acc.elo - elo0, acc.crystals - cry0
            won = state["status"] == MatchStatus.PLAYER_A_WINS.value
            draw = state["status"] == MatchStatus.DRAW.value
            parts = [f"📈 Elo {d_elo:+d} (ahora {acc.elo})"]
            if econ_ch.kind == ChallengeKind.MONEY.value:
                pot = econ_ch.stake * 2
                if won:
                    parts.append(f"💰 +{d_kur:.2f} $KUR (bote {pot:.2f} − comisión {pot * HOUSE_FEE_PCT:.2f})")
                elif draw:
                    parts.append(f"🤝 Empate: se te devuelven {econ_ch.stake:.2f} $KUR")
                else:
                    parts.append(f"💸 Pierdes tu apuesta de {econ_ch.stake:.2f} $KUR")
            elif econ_ch.kind == ChallengeKind.CRYSTAL.value:
                if won:
                    parts.append(f"💎 +{d_cry + CRYSTAL_STAKE} cristales en juego (neto {d_cry:+d}) · "
                                 f"+{d_kur:.0f} $KUR")
                elif draw:
                    parts.append("💎 Empate: recuperas tu cristal")
                else:
                    parts.append(f"💎 Pierdes tu cristal apostado (−{CRYSTAL_STAKE})")
            else:
                parts.append(f"🎟️ Free sin cristales · quedan "
                             f"{econ_server().free_ranked_remaining(current_wallet_address)}/{FREE_DAILY_RANKED_LIMIT} hoy")
            parts.append(f"⚔️ Combates: {acc.battles_total}")
            (st.success if won else st.info)(" · ".join(parts))
        st.session_state["match_challenge_id"] = None
        st.session_state.match_reward_settled = True

    banner = {
        MatchStatus.PLAYER_A_WINS.value: f"🏆 ¡Gana {state['player_a']['name']}!",
        MatchStatus.PLAYER_B_WINS.value: f"🏆 ¡Gana {state['player_b']['name']}!",
        MatchStatus.DRAW.value: "🤝 Empate.",
    }[state["status"]]
    if state.get("end_reason") == "inactivity":
        # Ganador (o empate) por la regla de inactividad: mismo cartel con
        # el motivo explícito, para que no parezca una victoria por vida.
        banner = {
            MatchStatus.PLAYER_A_WINS.value: f"⛔ ¡Gana {state['player_a']['name']} por inactividad del rival! "
                                             f"({state['inactivity_limit']} turnos seguidos sin actuar)",
            MatchStatus.PLAYER_B_WINS.value: f"⛔ Derrota por inactividad: se te agotó el tiempo {state['inactivity_limit']} "
                                             f"turnos seguidos sin actuar. Gana {state['player_b']['name']}.",
            MatchStatus.DRAW.value: "⛔ Empate por inactividad: a ningún jugador le dio tiempo a actuar en "
                                    f"{state['inactivity_limit']} turnos seguidos.",
        }[state["status"]]
        if state["status"] == MatchStatus.PLAYER_B_WINS.value:
            st.error(banner)
        else:
            st.warning(banner)
    else:
        st.success(banner)
    if st.button("🔄 Jugar otra partida", type="primary"):
        st.session_state.match = None
        st.session_state.match_reward_settled = False
        st.rerun()

def _format_log_entry(line: str, name_a: str, name_b: str) -> str:
    """Una línea del log como tarjeta con color por bando (🔵 TÚ / 🔴 BOT) y un
    emoji de acción (⚔️ daño/combo, 🛡️ curación/bloqueo/contraataque), para
    que se lea como un log de TCG en vez de texto plano."""
    if line.startswith("--- Turno"):
        return f"<div class='log-turn-sep'>{html.escape(line)}</div>"
    # El motor siempre pone el nombre de quien actúa primero en la frase
    # (ej. "Bot inflige ... de Tú (Jugador A)."), así que cuando aparecen los
    # dos nombres gana el que sale antes en el texto, no "name_a in line".
    idx_a = line.find(name_a)
    idx_b = line.find(name_b)
    if idx_a != -1 and (idx_b == -1 or idx_a < idx_b):
        side_class, side_icon = "log-side-a", "🔵"
    elif idx_b != -1:
        side_class, side_icon = "log-side-b", "🔴"
    else:
        side_class, side_icon = "log-side-neutral", "ℹ️"
    if " · 🔴 " in line or line.startswith(("📊", "❤️", "🔥", "💥", "💚", "✖️", "🛡️")):
        # Desglose por zona, totales y combos: ya traen sus propios iconos (🔴 ataques, 🔵 defensas, 💥 daño).
        action_icon = ""
    elif "inflige" in line or "Combo" in line:
        action_icon = "⚔️ "
    elif "cura" in line or "bloquea" in line or "contraataca" in line:
        action_icon = "🛡️ "
    else:
        action_icon = ""
    return f"<div class='log-entry {side_class}'>{side_icon} {action_icon}{html.escape(line)}</div>"


# Feed de combate compacto (solo el último evento, o los 2 más recientes),
# en un contenedor de altura fija justo sobre las cartas para que NO empuje
# la mano ni los botones de "Jugar" al acumularse líneas — el registro
# completo (más abajo, colapsado) conserva el historial entero.
if state["log"]:
    recent_events = [line for line in state["log"] if not line.startswith("--- Turno")]
    recent_lines = "".join(
        _format_log_entry(line, state["player_a"]["name"], state["player_b"]["name"])
        for line in reversed(recent_events[-2:])
    )
    if recent_lines:
        st.markdown(f'<div class="combat-feed">{recent_lines}</div>', unsafe_allow_html=True)

@st.dialog("⚠️ Carta deseleccionada", width="medium")
def _show_zone_dialog(cards: list[dict]) -> None:
    """Popup modal centrado: explica que la(s) carta(s) se quitaron de la
    selección por no tener asignadas sus zonas, y que el turno NO ha pasado."""
    plural = len(cards) > 1
    st.markdown(
        f"<div class='zone-dialog-lead'>No has marcado ninguna zona para "
        f"{'estas cartas' if plural else 'esta carta'}, así que "
        f"{'se han deseleccionado' if plural else 'se ha deseleccionado'} automáticamente.</div>",
        unsafe_allow_html=True,
    )
    for c in cards:
        img_col, txt_col = st.columns([1, 2.2], vertical_alignment="center")
        with img_col:
            if c.get("image"):
                st.image(c["image"], width="stretch")
        with txt_col:
            st.markdown(f"**{html.escape(c['name'])}**")
            st.markdown(
                f"<div class='zone-dialog-missing'>{html.escape(c['missing']).capitalize()}</div>",
                unsafe_allow_html=True,
            )
    st.markdown(
        "<div class='zone-dialog-help'>"
        "✅ <b>El turno NO ha pasado</b>: replantea tu jugada con libertad.<br>"
        "🔴 Elige la carta otra vez y marca sus <b>zonas de ataque</b> (puntos rojos, en el rival:"
        " tantas como su ATK, todas distintas) y/o 🟢 sus <b>zonas de defensa</b> (puntos verdes,"
        " en tu lado: tantas como su DEF). Puedes dejar zonas sin marcar.<br>"
        "⏭️ O pulsa <b>Pasar Turno</b> para ceder el turno sin jugarla.<br>"
        "⏱️ <b>El reloj sigue corriendo:</b> si llega a 0 sin que juegues o pases, "
        "cuenta como inactividad."
        "</div>",
        unsafe_allow_html=True,
    )
    if st.button("Entendido, replantear jugada", type="primary", width="stretch", key="zone_dialog_ok"):
        st.rerun()


def _missing_zones_text(player_key: str, card_dict: dict) -> str:
    n_atk, n_def = _card_zone_requirements(card_dict)
    opts = []
    if n_atk:
        opts.append(f"🔴 hasta {n_atk}")
    if n_def:
        opts.append(f"🟢 hasta {n_def}")
    return "marca al menos una zona (" + " · ".join(opts) + ")"


def _confirm_turn(player_key: str, ready_cards: list[dict], auto_resolve: bool) -> None:
    """Acción del botón PASAR TURNO. Nunca puede dejar el turno atascado:
    1) juega una a una las cartas listas con sus listas de zonas de ataque y
       de defensa (el motor vuelve a validar la regla de zonas únicas por
       carta); si una carta falla por cualquier motivo, se omite con un aviso
       y se siguen jugando las demás;
    2) en Vs. Bot, el Bot responde y el turno SE RESUELVE SIEMPRE, aunque
       algo anterior haya fallado;
    3) la selección se limpia pase lo que pase."""
    skipped: list[str] = []
    try:
        for card_dict in ready_cards:
            zc = _card_zones(player_key, card_dict["id"])
            try:
                play_card(match, player_key, card_dict["id"], list(zc["atk"]), list(zc["def"]))
            except Exception as e:  # EngineError, zona inválida, carta ya no en mano...
                print(f"[CONFIRM] {card_dict['id']} omitida: {e!r}")
                skipped.append(f"{card_dict['name']}: {e}")
                continue
            _flash_energy_spend(player_key, card_dict["cost_energy"])
        # Pulsar el botón es SIEMPRE una jugada válida: nunca inactividad.
        pass_turn(match, player_key)
        if auto_resolve and match.status == MatchStatus.ONGOING:
            try:
                bot_choose_plays(match, "b")
                _flash_energy_spend("b", match.player_b.energy_spent_this_turn)
            except Exception as e:
                print(f"[CONFIRM] bot_choose_plays fallo: {e!r}")
            _end_turn_with_fx(match)
            st.session_state.turn_start_time = time.time()
    except Exception as e:
        print(f"[CONFIRM] fallo al pasar turno: {e!r}")
        skipped.append(str(e))
    finally:
        _reset_tactical_selection(player_key)
    if skipped:
        st.session_state["confirm_warning"] = "No se pudo jugar: " + " · ".join(skipped)


def render_hand(player_key: str, label: str, auto_resolve: bool = False) -> None:
    """Mano de 3 cartas: solo imagen, coste y tooltip de efecto al pasar el
    cursor (ℹ️ Info) — clicar una carta la SELECCIONA (no la juega al
    instante); hace falta marcar también sus zonas de ataque/defensa en
    render_tactical_points() (hasta ATK/DEF zonas distintas — ver
    _card_zone_requirements) y pulsar "✅ Pasar Turno" para resolver.
    auto_resolve=True (modo Vs. Bot): confirmar hace responder al Bot y
    resuelve el turno entero en un solo paso; en Hotseat la jugada solo queda
    preparada hasta pulsar "⏭️ Resolver turno" (ambos jugadores deciden antes
    de aplicar el turno a la vez, sección 2.4 de PVP_Rules).
    auto_resolve=True también cambia la key del contenedor de la mano a
    "battle_hand_overlay_*" en vez de "battle_hand_*": el bloque CSS de
    render_arena (.arena de combate) la engancha para flotarla superpuesta
    sobre el borde inferior del escenario (ver comentario ahí) en vez de
    dejarla en su sitio normal, debajo del registro de combate — solo tiene
    sentido con una única mano (Vs. Bot); en Hotseat, con 2 manos a la vez,
    se queda con el grid de siempre."""
    p = state[f"player_{player_key}"]
    remaining_energy = max(0, min(MAX_ENERGY_SPEND_PER_TURN, p["energy"]) - p["energy_spent_this_turn"])
    energy_css_class = "energy-low" if remaining_energy <= 0 else ""
    _committed_now = sum(c["cost_energy"] for c in _selected_cards(player_key))
    _free_now = max(0, remaining_energy - _committed_now)
    energy_css_class = "energy-low" if _free_now <= 0 else ""
    st.markdown(
        f"**Mano de {label}** · <span class='energy-display {energy_css_class}'>"
        f"⚡ {_free_now}/{MAX_ENERGY} disponible{'s' if _free_now != 1 else ''}</span> · "
        f"<span class='deck-discard-count'>🂠 Mazo: {p['deck_remaining']} · 🗑️ Descarte: {p['discard_count']}</span>",
        unsafe_allow_html=True,
    )
    _warn = st.session_state.pop("confirm_warning", None)
    if _warn:
        st.warning(_warn)
    _zone_cards = st.session_state.pop("zone_dialog_cards", None)
    if _zone_cards:
        _show_zone_dialog(_zone_cards)
    if not p["hand"]:
        st.caption("Sin cartas en mano.")
        return

    # Poda: las cartas jugadas/rotadas salen de la mano, así que cualquier id elegido que
    # ya no esté en ella se descarta (y con él sus zonas).
    hand_ids = {c["id"] for c in p["hand"]}
    sel_ids = [cid for cid in st.session_state[f"sel_cards_{player_key}"] if cid in hand_ids]
    st.session_state[f"sel_cards_{player_key}"] = sel_ids
    if st.session_state[f"active_card_{player_key}"] not in sel_ids:
        st.session_state[f"active_card_{player_key}"] = sel_ids[-1] if sel_ids else None
    active_id = st.session_state[f"active_card_{player_key}"]
    selected_cards = _selected_cards(player_key)
    committed_energy = sum(c["cost_energy"] for c in selected_cards)
    free_energy = remaining_energy - committed_energy

    hand_container_key = f"battle_hand_overlay_{player_key}" if auto_resolve else f"battle_hand_{player_key}"
    with st.container(key=hand_container_key):
        cols = st.columns(len(p["hand"]))
        for col, card_dict in zip(cols, p["hand"]):
            selected = card_dict["id"] in sel_ids
            is_active = card_dict["id"] == active_id
            # Una carta NO elegida solo es asequible si cabe en la energía
            # que queda libre tras las ya elegidas; las elegidas siempre son
            # clicables (para volver a activarlas y editar sus zonas).
            affordable = selected or card_dict["cost_energy"] <= free_energy
            if selected:
                glow_state = "sel_act" if is_active else "sel_pin"
            else:
                glow_state = "playable" if affordable else "locked"
            glow_key = f"card_glow_{glow_state}_{player_key}_{card_dict['id']}"
            with col, st.container(key=glow_key):
                # Sin letreros fijos (nombre/ATK-DEF/coste/habilidad/Info):
                # todo vive en este tooltip, revelado solo al pasar el
                # ratón por la carta (ver el <style> aparte, más arriba,
                # que activa .card-info-panel con :hover en el contenedor
                # card_glow_*) — libera alto vertical para que "Pasar Turno"
                # quepa sin scroll pese a las cartas más grandes. Va ANTES
                # que st.image a propósito: Streamlit envuelve cada
                # st.markdown en su propio div position:relative, y ese
                # envoltorio (altura ~0, el contenido real es
                # position:absolute) es el ancestro posicionado más cercano
                # para .card-info-panel — colocándolo primero en el flujo
                # flex (arriba del todo, antes de la imagen) el tooltip con
                # bottom:100% sube por encima de TODA la carta en vez de
                # quedar anclado a mitad/abajo (donde caería si el <div>
                # fuese el 2º hijo, entre imagen y botón).
                ability = card_dict.get("ability")
                ability_line = (f"<div>✨ {html.escape(card_type_label(card_dict))}</div>"
                                if ability and ability != "none" else "")
                desc_html = (
                    f"<div class='card-info-desc'>{html.escape(card_dict['description'])}</div>"
                    if card_dict.get("description")
                    else ""
                )
                st.markdown(
                    "<div class='card-info-panel'>"
                    f"<div class='card-info-name'>{html.escape(card_dict['name'])} · {card_type_label(card_dict)}</div>"
                    f"<div class='card-info-stats'>{card_dict['atk']} ATK / {card_dict['defense']} DEF · "
                    f"Coste {card_dict['cost_energy']}</div>"
                    f"{ability_line}"
                    f"<div>{card_effect_text(card_dict)}</div>"
                    f"{desc_html}"
                    "</div>",
                    unsafe_allow_html=True,
                )
                img = card_image_path(card_dict)
                if img:
                    st.image(str(img), width="stretch")
                if selected:
                    # ✕ en la esquina superior derecha: deselecciona SOLO
                    # esta carta y libera su energía (ver _deselect_card).
                    if st.button("✕", key=f"desel_{player_key}_{card_dict['id']}", help="Quitar esta carta"):
                        _deselect_card(player_key, card_dict["id"])
                        st.rerun()
                    # Chip con el orden de juego + zonas fijadas de la carta.
                    order = sel_ids.index(card_dict["id"]) + 1
                    zc = _card_zones(player_key, card_dict["id"])
                    n_atk, n_def = _card_zone_requirements(card_dict)
                    parts = []
                    if n_atk:
                        parts.append(f"🔴 {_zones_chip(zc['atk'], n_atk)}")
                    if n_def:
                        parts.append(f"🟢 {_zones_chip(zc['def'], n_def)}")
                    if not parts:
                        parts.append("sin zonas")
                    elif not (zc["atk"] or zc["def"]) and _free_zones(player_key, card_dict) == (0, 0):
                        parts = ["sin casillas libres: se juega sin zonas"]
                    chip_cls = "card-sel-chip ready" if _card_ready(player_key, card_dict) else "card-sel-chip"
                    if is_active and not _card_ready(player_key, card_dict):
                        chip_cls += " pending"
                    # Contenedor con key propia => el CSS lo saca del flujo
                    # (position:absolute sobre el pie de la imagen): no
                    # añade alto entre las cartas y "Pasar Turno".
                    with st.container(key=f"card_overlay_chip_{player_key}_{card_dict['id']}"):
                        st.markdown(
                            f"<div class='{chip_cls}'><b>#{order}</b> {' · '.join(parts)}</div>",
                            unsafe_allow_html=True,
                        )
                btn_label = ("✅ Activa" if is_active else "✅ Elegida") if selected else "Seleccionar"
                if st.button(
                    btn_label,
                    key=f"select_{player_key}_{card_dict['id']}",
                    width="stretch",
                    type="primary" if selected else "secondary",
                    disabled=not affordable,
                ):
                    _select_card(player_key, card_dict["id"])
                    st.rerun()
                if not affordable:
                    with st.container(key=f"card_overlay_chip_noen_{player_key}_{card_dict['id']}"):
                        st.markdown("<div class='card-sel-chip no-energy'>⚡ Sin energía</div>", unsafe_allow_html=True)

        # El resto del flujo (zonas + "Pasar Turno") vive DENTRO del mismo
        # contenedor que las 3 cartas (hand_container_key), justo debajo —
        # un solo bloque compacto (en Vs. Bot, el panel flotante sobre el
        # escenario) en vez de la barra naranja gigante aparte de antes,
        # que obligaba a hacer scroll para llegar a ella.
        if not auto_resolve:
            # En Vs. Bot los puntos ya están superpuestos sobre la arena (ver
            # render_arena -> render_tactical_points(overlay=True)); en
            # Hotseat no hay una única imagen que superponer para los 2
            # jugadores a la vez, así que cada uno tiene su fila aquí.
            render_tactical_points(player_key)

        selected_cards = _selected_cards(player_key)
        # PASAR TURNO siempre activo: juega las cartas elegidas que tengan sus
        # zonas completas y, si no hay ninguna (o no puedes pagar ninguna),
        # cede el turno igualmente. La energía se recarga +1 en end_turn.
        ready_cards = [c for c in selected_cards if _card_ready(player_key, c)]
        ready_energy = sum(c["cost_energy"] for c in ready_cards)
        if ready_cards:
            confirm_label = "✅ Pasar Turno" + (
                f" ({len(ready_cards)} cartas · ⚡{ready_energy})" if len(ready_cards) > 1 else ""
            )
        else:
            confirm_label = "⏭️ Pasar Turno"
        incomplete_cards = [c for c in selected_cards if not _card_ready(player_key, c)]
        hand_cost_min = min((c["cost_energy"] for c in p["hand"]), default=0)
        if incomplete_cards:
            st.caption(
                "⚠️ " + " · ".join(
                    (f"#{sel_ids.index(c['id']) + 1} " if len(selected_cards) > 1 else "")
                    + _missing_zones_text(player_key, c).capitalize()
                    for c in incomplete_cards
                )
            )
        elif p["hand"] and hand_cost_min > remaining_energy:
            st.caption(
                f"⚡ No te llega la energía para ninguna carta: pasa turno y recargarás +{ENERGY_PER_TURN} ⚡."
            )
        if st.button(
            confirm_label, key=f"confirm_turn_{player_key}",
            type="primary", width="stretch",
        ):
            if incomplete_cards:
                # No se juega a ciegas ni se penaliza: se quitan de la
                # selección las cartas sin zonas y se abre un popup modal que
                # lo explica; el turno NO pasa todavía.
                st.session_state["zone_dialog_cards"] = [
                    {
                        "name": c["name"],
                        "missing": _missing_zones_text(player_key, c),
                        "image": str(card_image_path(c) or ""),
                    }
                    for c in incomplete_cards
                ]
                for c in incomplete_cards:
                    _deselect_card(player_key, c["id"])
            else:
                _confirm_turn(player_key, ready_cards, auto_resolve)
            st.rerun()

        if p["staged_plays"]:
            staged_txt = " · ".join(
                f"#{i} ⚔️ {', '.join(ZONE_NAME[z] for z in sp['attack_zones']) or '—'}"
                f" 🛡️ {', '.join(ZONE_NAME[z] for z in sp['defense_zones']) or '—'}"
                for i, sp in enumerate(p["staged_plays"], 1)
            )
            st.caption(f"Jugadas preparadas este turno: {staged_txt}")


if state["status"] == MatchStatus.ONGOING.value:
    if st.session_state.vs_bot:
        render_hand("a", state["player_a"]["name"], auto_resolve=True)
    else:
        render_hand("a", state["player_a"]["name"], auto_resolve=False)
        render_hand("b", state["player_b"]["name"], auto_resolve=False)
        if st.button("⏭️ Resolver turno", type="primary", width="stretch"):
            try:
                _end_turn_with_fx(match)
            except EngineError as e:
                st.error(str(e))
            else:
                _reset_tactical_selection()
            st.session_state.turn_start_time = time.time()
            st.rerun()

with st.expander("📜 Registro completo de la partida", expanded=False):
    log_html = "".join(
        _format_log_entry(line, state["player_a"]["name"], state["player_b"]["name"])
        for line in reversed(state["log"][-40:])
    )
    st.markdown(log_html, unsafe_allow_html=True)

_atk_cards = [c for c in catalog_dicts if c["atk"] >= c["defense"]]
_def_cards = [c for c in catalog_dicts if c["defense"] > c["atk"]]
with st.expander(f"🗂️ Catálogo de cartas ({len(catalog_dicts)} únicas, una por combinación)"):
    tabs = st.tabs([f"Ataque ({len(_atk_cards)})", f"Defensa ({len(_def_cards)})"])
    buckets = {0: _atk_cards, 1: _def_cards}
    for idx, tab in enumerate(tabs):
        with tab:
            bucket = buckets[idx]
            cols = st.columns(6)
            for i, card_dict in enumerate(bucket):
                with cols[i % 6]:
                    img = card_image_path(card_dict)
                    if img:
                        st.image(str(img), width="stretch")
                    st.caption(f"**{card_dict['name']}**")
                    st.caption(f"{card_dict['atk']}/{card_dict['defense']} · coste {card_dict['cost_energy']}")
