# Kurama Run — Marketplace (prototipo desacoplado)

Prototipo local del Marketplace de NFTs "Fox" de Kurama Run, inspirado en el
diseño de [kuramarun.com](https://kuramarun.com) y unificado visualmente con
`../engine/pvp_dashboard.py` vía `../kurama_theme.py`. Igual que el PVP:
pensado para iterar la interfaz en local, sin tocar el proyecto principal ni
ninguna red real.

## ⚠️ Simulación, no on-chain

No hay ningún smart contract de marketplace desplegado todavía. Todo —
catálogo, rareza, precios, wallet, compra/venta/listado — vive en memoria
(`marketplace_engine.py`, `SimulatedWallet`), igual que la
`_SimulatedChainBackend` del PVP (`../engine/blockchain_connector.py`). La
dirección de contrato NFT real de "Genesis Founders" sigue sin confirmar
on-chain en este repo — ver el aviso en `../README.md` antes de conectar
esto a BSC real.

## Estructura

```
MARKETPLACE/
  marketplace_schema.py    estructura de datos: Listing, MarketItem
  marketplace_engine.py    catálogo (1600 Fox de ../GAMENFT/), rareza y precio
                             simulados, SimulatedWallet (balance $KUR/USDT,
                             poseídos, listados, comprar/vender/cancelar)
  marketplace_app.py        panel Streamlit: pestañas Explorar / Mi Inventario
                             + modal de Detalle (comprar o listar, simulado)
  .streamlit/config.toml    tema nativo de Streamlit (debe coincidir con
                             ../kurama_theme.py y ../engine/.streamlit/config.toml)
  requirements.txt
```

La rareza de cada Fox se calcula con `derive_placeholder_rarity()` del PVP
(importado de `../engine/blockchain_connector.py`, no reimplementado) para
que un Fox legendario tenga la misma rareza en el combate PVP y en el
Marketplace.

## Cómo probarlo en local

```bash
cd PVP/MARKETPLACE
pip install -r requirements.txt
streamlit run marketplace_app.py
```

La wallet es siempre simulada (precargada con los mismos Fox #5, #6, #43,
#44 y balance de ejemplo que aparecen en las capturas de referencia de
kuramarun.com, para comparar de un vistazo) — cámbiala, añade fondos o
reiníciala desde la barra lateral.

## Vistas

1. **🛒 Explorar** — catálogo de Fox a la venta (listados simulados +
   los que tú mismo pongas a la venta), con filtro por clase
   (Común/Raro/Épico/Legendario), búsqueda por ID y orden por precio.
2. **🎒 Mi Inventario** — los Fox de la wallet activa, con badges de estado
   (Activo / En venta) y los mismos filtros que Explorar.
3. **Detalle (modal)** — al pulsar "Ver detalle" en cualquier tarjeta: si no
   lo posees y está listado, comprar (descuenta $KUR simulado); si lo
   posees, ponerlo a la venta o cancelar su venta.

## Qué falta para integrarlo con el proyecto principal

- **Contrato de marketplace real**: hoy no existe; cuando se despliegue,
  sustituir `SimulatedWallet.buy/list_for_sale/cancel_listing` por
  transacciones reales (firma de wallet + lectura on-chain), igual que
  `blockchain_connector.py` documenta para el PVP.
- **Precios reales**: `BASE_PRICE_KUR` en `marketplace_engine.py` es un
  rango placeholder por rareza; sustituir por órdenes/precios reales de
  mercado en cuanto existan.
- **Metadata/rareza real de los NFT**: mismo pendiente que el PVP — ver
  `../README.md`.
