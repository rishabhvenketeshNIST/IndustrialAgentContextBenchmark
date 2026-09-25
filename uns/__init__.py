"""ISA-95-based Unified Namespace (UNS) for the enterprise simulator, implemented over MQTT.

The UNS is a *projection* of the canonical context model (contract/context_model.yaml): it publishes
the operational information the simulator produces, through the operational boundary
(api/operational.py), onto an MQTT broker. It is not a source of truth and adds no semantics.

    uns/namespace.py  topics derived from the simulator's ISA-95 hierarchy and the context model
    uns/publisher.py  simulator -> MQTT synchronisation (state, measurements, events, lifecycle)
    uns/broker.py     starts a local Eclipse Mosquitto broker (development and tests)

Documentation: docs/UNS.md, docs/UNS_MQTT_NAMESPACE.md, docs/UNS_MQTT_SEMANTICS.md,
docs/UNS_OPERATING_MODEL.md.
"""

SCHEMA = "acme-uns/1"
DEFAULT_ROOT = "uns/v1"
