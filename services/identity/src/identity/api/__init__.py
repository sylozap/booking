"""API: routers, request and response schemas, FastAPI dependencies.

May import ``application``, ``domain`` and the chassis. Must never import
``infrastructure`` directly: concrete adapters are wired in the composition
root and reach the routers as dependencies (CODING_STANDARDS 2.2).
"""

__all__: list[str] = []
