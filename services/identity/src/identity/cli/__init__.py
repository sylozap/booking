"""Maintenance commands.

Like ``identity.main``, this is a composition root and sits outside the layers:
it reads configuration, builds the infrastructure it needs and owns the
transaction the command runs in. Nothing imports it.
"""
