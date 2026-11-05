"""Conversion sources for Desk channel impact (MC-1152; spec section 7).

`base.py` is the interface, `registry.py` the data that maps a source id to its
adapter. One module per adapter (GitHub in P3, one per analytics provider in
P5). Importing this package performs no collection and no network call.
"""
