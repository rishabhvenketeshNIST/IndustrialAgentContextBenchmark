# TEP → enterprise interpretation (quality, production, inventory, utilities, alarms)

TEP outputs are *interpreted* by enterprise modules. None of these interpretations feeds back into TEP,
with one exception: inventory, where consumption depletes storage and storage availability couples
back.

| Consumer | TEP input | Which copy | Interpretation | Feedback into TEP? |
|---|---|---|---|---|
| Quality | XMEAS 38-41 (product E, F, G, H) | **transmitted** | tests → sample PASS/FAIL → lot disposition | no |
| Production | XMEAS 17 (product flow) | **transmitted** | rate × 613.4 kg/m³ → lots, orders, OEE | no |
| Inventory | XMEAS 1-4 (feed flows) | **true** | consumption kg/h, FIFO lots | **yes**: stock → supply availability → VRNG(1-4) |
| Utility observations | XMV 10-12, XMEAS 19-22 | transmitted | CW flow, demand, utilization, return temperature; steam flow; power demand | utilization feeds the CONSTRAINED status and the alarm → inspection work order, but not TEP |
| Alarms | 16 XMEAS alarms, loop outputs, measurement quality | transmitted | ISA-18.2 alarm states | only via maintenance alarms → equipment |
| Native control | PVs of 19 loops | transmitted (during the controller call) | XMV / setpoints | yes (that is control) |

**Why true vs transmitted differs by consumer.** The implementation follows physical meaning:

* A tank drains at the real feed rate, so inventory uses true values.
* A quality result, a production report and an alarm are based on what instruments report, so they use
  transmitted values.

This means a sensor fault on XMEAS(17) biases reported production but not stock levels.

## Worked example: why the demo's quality failure came late

1. TEP's product composition responds to reactor temperature through reaction selectivity: higher
   temperature favours G. It is diluted by separator and stripper holdup.
2. The analyzer reports each result 0.25 h after the sample.
3. The high-temperature excursion (01:30–01:47) did not produce a failing sample; the samples at 01:45
   and 02:00 passed. The failing sample QS-00009 at 02:15 represents product from 02:00, during the
   post-recovery **undershoot**.

The interpretation chain was TEP composition → transmitted XMEAS(40/41) → G_MASS_PCT < 47.5 → FAIL →
lot QUARANTINE.

Source:
- `simulator/quality/__init__.py` — `QualityModule._product_sample`
- `simulator/production/__init__.py` — `ProductionModule.post_step`
- `simulator/inventory/__init__.py` — `InventoryModule.post_step`
- `simulator/alarms/__init__.py` — `AlarmModule._make_getter`
- `tests/test_enterprise.py` — `test_quality_calculation_from_tep_composition`, `test_inventory_consumption_matches_metered_feed`
