# Areas

Eight areas. Five come from the TEP flowsheet (origin TEP) and three were added by the enterprise
layer (origin ENTERPRISE). Areas have no state of their own; they group equipment, and the API rolls
up their status (`EnterpriseView.element_status`).

| Area | Origin | Contains | TEP streams / variables | What happens here |
|---|---|---|---|---|
| `AREA-FEED` Feed Area | TEP | PU-FEED: EM-FEED-A/D/E/AC, valves FV-01..04, flow loops FC-A/D/E/AC | streams 1-4; XMEAS 1-4; XMV 1-4 | reactant feed metering; enterprise storage availability couples here (`*_feed_max_flow`) |
| `AREA-REACTION` Reaction Area | TEP | PU-REACTOR: reactor R-101, cooling bundle, agitator, feed analyzer AT-06 | XMEAS 6-9, 21, 23-28; XMV 10, 12 | exothermic reactions; reactor CW utility couples here (`reactor_cw_*`) |
| `AREA-SEPARATION` Separation Area | TEP | PU-SEPARATION: condenser E-101, separator V-102 | XMEAS 11-14, 22; XMV 7, 11 | condensation and separation; condenser CW couples here |
| `AREA-RECYCLE` Recycle and Purge Area | TEP | PU-RECYCLE: compressor K-101, purge system, purge analyzer AT-09 | XMEAS 5, 10, 20, 29-36; XMV 5, 6 | recycle gas and inert purge; compressor capability couples here (`compressor_max_flow`) |
| `AREA-STRIPPING` Stripping Area | TEP | PU-STRIPPER: stripper C-101, steam reboiler, product analyzer AT-11 | XMEAS 15-19, 37-41; XMV 8, 9 | product stripping; steam utility couples here |
| `AREA-PRODUCT` Product Storage and Quality Area | ENTERPRISE | SZ-PRODUCT (TK-501 rundown, TK-502 released, TK-503 off-spec), WC-QC (QC lab) | — | lot storage, QC disposition, dispatch |
| `AREA-RAW` Raw Material Storage Area | ENTERPRISE | SZ-RAW (four feed storages), SZ-WAREHOUSE (MRO, finished-goods dispatch) | feeds streams 1-4 | inventory, replenishment, spare parts |
| `AREA-UTILITIES` Utilities Area | ENTERPRISE | WC-CW (cooling tower, four CW pumps), WC-STEAM (boiler), WC-POWER (transformer, MCC) | — | supplies the four utility services |

The per-element detail is in [isa95_entity_table.md](isa95_entity_table.md).

Source:
- `configs/site.yaml`
- `simulator/enterprise/__init__.py` — `EnterpriseView.elements`
