"""Transport-neutral projections of the canonical context model.

``projection.operational.OperationalProjection`` is the operational context projection shared by its
consumers: the MQTT Unified Namespace (uns/) today, the Historian later. It decides *what* is
operationally observable and how it is structured (docs/CONTEXT_PROJECTION_PRINCIPLES.md); each
consumer decides only how to transport or store it.
"""
