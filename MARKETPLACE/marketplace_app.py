"""
Marketplace simulado de Kurama Run (Streamlit) — catálogo de los 1600 Fox
de GAMENFT/, inventario de la wallet simulada y compra/venta simulada.

Ejecución:
    cd PVP/MARKETPLACE
    pip install -r requirements.txt
    streamlit run marketplace_app.py

Todo en memoria (ver marketplace_engine.py): ninguna transacción real, igual
que el PVP (engine/blockchain_connector.py con simulate=True). Comparte
paleta/tipografía con el PVP vía ../kurama_theme.py para que ambos paneles
se vean como un único producto.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

MARKETPLACE_ROOT = Path(__file__).resolve().parent
PVP_ROOT = MARKETPLACE_ROOT.parent

if str(PVP_ROOT) not in sys.path:
    sys.path.insert(0, str(PVP_ROOT))

import kurama_theme  # noqa: E402

from marketplace_engine import (  # noqa: E402
    NFT_COLLECTION_SUPPLY,
    SIMULATED_KUR_USDT_RATE,
    SimulatedWallet,
    simulated_price_kur,
)
from marketplace_schema import MarketItem  # noqa: E402

from card_schema import NFTRarity  # noqa: E402  (resuelto vía engine/, insertado por marketplace_engine)

PAGE_SIZE = 20
GRID_COLUMNS = 4

RARITY_FILTER_OPTIONS = ["all", *[r.value for r in (NFTRarity.COMMON, NFTRarity.RARE, NFTRarity.EPIC, NFTRarity.LEGENDARY)]]
RARITY_FILTER_LABELS = {"all": "Todas las clases", **kurama_theme.RARITY_LABELS}

MARKETPLACE_CSS = """
<style>
[class*="st-key-mkt_card_"] {
    background: #17171B;
    border: 1px solid rgba(255, 85, 0, 0.18);
    border-radius: 16px;
    padding: 0.6rem 0.6rem 0.9rem;
    transition: box-shadow 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
}
[class*="st-key-mkt_card_"]:hover {
    border-color: #FF5500;
    box-shadow: 0 0 22px rgba(255, 85, 0, 0.25);
    transform: translateY(-2px);
}
[class*="st-key-mkt_card_"] img {
    border-radius: 12px;
    object-fit: contain;
    max-height: 210px;
}
</style>
"""


def get_wallet() -> SimulatedWallet:
    if "wallet" not in st.session_state:
        st.session_state.wallet = SimulatedWallet()
    return st.session_state.wallet


@st.dialog("Detalle del Fox")
def show_detail_dialog(token_id: int) -> None:
    wallet = get_wallet()
    item = wallet.market_item(token_id)

    col_img, col_info = st.columns([1, 1])
    with col_img:
        img_path = Path(item.image_path)
        if img_path.exists():
            st.image(str(img_path), width="stretch")
        else:
            st.warning("Imagen no encontrada en GAMENFT/.")
    with col_info:
        st.markdown(f"<div class='kr-heading' style='font-size:1.4rem'>Fox #{token_id}</div>", unsafe_allow_html=True)
        badges = kurama_theme.rarity_badge_html(item.rarity)
        if item.owned_by_wallet:
            badges += kurama_theme.status_badge_html("EN TU WALLET", "owned")
        st.markdown(badges, unsafe_allow_html=True)
        if item.listing:
            st.markdown(
                f"<div class='kr-price'>{item.listing.price_kur:,.2f} $KUR</div>"
                f"<div class='kr-price-sub'>≈ {item.listing.price_kur * SIMULATED_KUR_USDT_RATE:,.2f} USDT (simulado)</div>",
                unsafe_allow_html=True,
            )
        st.caption("Simulación — ninguna transacción toca la BSC real (ver README.md del repo).")

    st.divider()

    if item.owned_by_wallet:
        if item.listing is not None and item.listing.seller == "me":
            st.info(f"Listado por **{item.listing.price_kur:,.2f} $KUR**.")
            if st.button("❌ Cancelar venta", width="stretch"):
                ok, msg = wallet.cancel_listing(token_id)
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()
        else:
            suggested = simulated_price_kur(token_id, wallet.rarity_of(token_id))
            price = st.number_input("Precio de venta ($KUR)", min_value=1.0, value=suggested, step=10.0)
            if st.button("🏷️ Poner a la venta", type="primary", width="stretch"):
                ok, msg = wallet.list_for_sale(token_id, price)
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()
    elif item.listing is not None:
        st.caption(f"Vendedor: `{item.listing.seller}`")
        can_afford = wallet.kur_balance >= item.listing.price_kur
        if not can_afford:
            st.error(f"Saldo insuficiente — tienes {wallet.kur_balance:,.2f} $KUR.")
        if st.button(
            f"⚡ Comprar por {item.listing.price_kur:,.2f} $KUR",
            type="primary",
            width="stretch",
            disabled=not can_afford,
        ):
            ok, msg = wallet.buy(token_id)
            (st.success if ok else st.error)(msg)
            if ok:
                st.rerun()
    else:
        st.caption("Este Fox no está a la venta ahora mismo.")


def _rarity_filter_control(key: str) -> str:
    return st.selectbox(
        "Clase", RARITY_FILTER_OPTIONS, format_func=lambda v: RARITY_FILTER_LABELS.get(v, v), key=key
    )


def _filtered_sorted(token_ids: list[int], wallet: SimulatedWallet, rarity_filter: str, search: str, sort_key: str) -> list[MarketItem]:
    items = [wallet.market_item(tid) for tid in token_ids]
    if rarity_filter != "all":
        items = [it for it in items if it.rarity == rarity_filter]
    search = search.strip()
    if search:
        try:
            wanted = int(search)
        except ValueError:
            items = []
        else:
            items = [it for it in items if it.token_id == wanted]
    if sort_key == "price_asc":
        items.sort(key=lambda it: it.listing.price_kur if it.listing else float("inf"))
    elif sort_key == "price_desc":
        items.sort(key=lambda it: it.listing.price_kur if it.listing else -1.0, reverse=True)
    else:
        items.sort(key=lambda it: it.token_id)
    return items


def _paginate_controls(total_items: int, page_key: str) -> int:
    total_pages = max(1, -(-total_items // PAGE_SIZE))
    page = min(st.session_state.get(page_key, 0), total_pages - 1)
    col_prev, col_label, col_next = st.columns([1, 2, 1])
    with col_prev:
        if st.button("⬅️ Anterior", key=f"{page_key}_prev", disabled=page <= 0, width="stretch"):
            page -= 1
    with col_label:
        st.markdown(f"<div style='text-align:center' class='kr-muted'>Página {page + 1} / {total_pages}</div>", unsafe_allow_html=True)
    with col_next:
        if st.button("Siguiente ➡️", key=f"{page_key}_next", disabled=page >= total_pages - 1, width="stretch"):
            page += 1
    st.session_state[page_key] = page
    return page


def render_card_grid(items: list[MarketItem], page_key: str, empty_message: str) -> None:
    if not items:
        st.info(empty_message)
        return
    page = _paginate_controls(len(items), page_key)
    page_items = items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    cols = st.columns(GRID_COLUMNS)
    for i, item in enumerate(page_items):
        with cols[i % GRID_COLUMNS]:
            with st.container(key=f"mkt_card_{page_key}_{item.token_id}"):
                img_path = Path(item.image_path)
                if img_path.exists():
                    st.image(str(img_path), width="stretch")
                st.markdown(f"<div class='kr-heading' style='font-size:0.95rem'>Fox #{item.token_id}</div>", unsafe_allow_html=True)
                badges = kurama_theme.rarity_badge_html(item.rarity)
                if item.owned_by_wallet:
                    badges += kurama_theme.status_badge_html("ACTIVO", "active")
                if item.listing is not None and item.listing.seller == "me":
                    badges += kurama_theme.status_badge_html("EN VENTA", "listed")
                st.markdown(badges, unsafe_allow_html=True)
                if item.listing is not None:
                    st.markdown(
                        f"<div class='kr-price'>{item.listing.price_kur:,.2f} $KUR</div>"
                        f"<div class='kr-price-sub'>≈ {item.listing.price_kur * SIMULATED_KUR_USDT_RATE:,.2f} USDT</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown("<div class='kr-price-sub'>No listado</div>", unsafe_allow_html=True)
                if st.button("Ver detalle", key=f"detail_{page_key}_{item.token_id}", width="stretch"):
                    show_detail_dialog(item.token_id)


def render_explore(wallet: SimulatedWallet) -> None:
    st.markdown("<div class='kr-heading' style='font-size:1.1rem'>Explorar — Catálogo GAMENFT</div>", unsafe_allow_html=True)
    col_a, col_b, col_c = st.columns([1.2, 1, 1])
    with col_a:
        rarity_filter = _rarity_filter_control("explore_rarity")
    with col_b:
        search = st.text_input("Buscar por ID", key="explore_search", placeholder="p. ej. 43")
    with col_c:
        sort_key = st.selectbox(
            "Ordenar",
            ["id", "price_asc", "price_desc"],
            format_func=lambda v: {"id": "ID", "price_asc": "Precio ↑", "price_desc": "Precio ↓"}[v],
            key="explore_sort",
        )

    listed_ids = sorted(set(wallet.other_listings) | set(wallet.my_listings))
    items = _filtered_sorted(listed_ids, wallet, rarity_filter, search, sort_key)
    render_card_grid(items, "explore", "No hay ningún Fox a la venta con estos filtros.")


def render_inventory(wallet: SimulatedWallet) -> None:
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Assets", len(wallet.owned_token_ids))
    col2.metric("En venta", len(wallet.my_listings))
    col3.metric("Balance $KUR", f"{wallet.kur_balance:,.2f}")

    col_a, col_b = st.columns([1.2, 1])
    with col_a:
        rarity_filter = _rarity_filter_control("inventory_rarity")
    with col_b:
        search = st.text_input("Buscar por ID", key="inventory_search", placeholder="p. ej. 5")

    owned_ids = sorted(wallet.owned_token_ids)
    items = _filtered_sorted(owned_ids, wallet, rarity_filter, search, "id")
    render_card_grid(items, "inventory", "Todavía no posees ningún Fox — cómpralo en la pestaña Explorar.")


def render_sidebar(wallet: SimulatedWallet) -> None:
    with st.sidebar:
        st.header("🦊 Wallet (simulada)")
        st.caption("Modo: SIMULACIÓN — sin contratos reales.")
        wallet.address = st.text_input("Dirección", value=wallet.address)
        st.metric("Balance $KUR", f"{wallet.kur_balance:,.2f}")
        st.metric("Balance USDT", f"{wallet.usdt_balance:,.2f}")
        st.metric("Fox poseídos", len(wallet.owned_token_ids))
        st.metric("En venta", len(wallet.my_listings))

        st.divider()
        st.caption("Añadir fondos simulados:")
        add_kur = st.number_input("+ $KUR", min_value=0.0, value=0.0, step=50.0, key="add_kur")
        add_usdt = st.number_input("+ USDT", min_value=0.0, value=0.0, step=10.0, key="add_usdt")
        if st.button("💰 Añadir fondos", width="stretch"):
            wallet.add_funds(kur=add_kur, usdt=add_usdt)
            st.rerun()

        st.divider()
        if st.button("↩️ Reiniciar wallet simulada", width="stretch"):
            wallet.reset()
            st.rerun()


st.set_page_config(page_title="Kurama Run · Marketplace", page_icon="🛒", layout="wide")
st.markdown(kurama_theme.base_css(), unsafe_allow_html=True)
st.markdown(MARKETPLACE_CSS, unsafe_allow_html=True)

active_wallet = get_wallet()
render_sidebar(active_wallet)

st.title("🛒 Kurama Run — Marketplace")
st.caption(
    "Prototipo local y desacoplado — catálogo + wallet 100% SIMULADOS, sin contratos reales. "
    f"{len(active_wallet.other_listings) + len(active_wallet.my_listings)} Fox a la venta de {NFT_COLLECTION_SUPPLY}."
)

tab_explore, tab_inventory = st.tabs(["🛒 Explorar", "🎒 Mi Inventario"])
with tab_explore:
    render_explore(active_wallet)
with tab_inventory:
    render_inventory(active_wallet)
