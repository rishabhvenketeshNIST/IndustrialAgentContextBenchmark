# Enterprise (ENT-ACME)

**Represents:** ACME Manufacturing, the company owning the site. It is the ISA-95 root.

**Why it exists:** ISA-95 requires a single root, and future layers need an enterprise-level anchor.

**State it owns:** none of its own. `EnterpriseView.element_status` computes a *roll-up* on request
(worst status of all descendants plus the count of active alarms). The UI tree shows this as a status
dot and an alarm badge. The roll-up is computed per API call and never stored.

**Inputs / outputs:** none. **Causes of change:** none. **Does not control:** anything.

There is exactly one enterprise and one site. Multi-site is not implemented.

Enterprise-level KPIs such as OEE live in the production summary (`state.production`), not on this
element ([production](production.md)).

Source:
- `configs/site.yaml`
- `simulator/enterprise/__init__.py` — `EnterpriseView.enterprise`, `EnterpriseView.element_status`
- `api/service.py` — `SimulatorService.get_enterprise`
