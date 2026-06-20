"""Deterministic quant engine (spec §3 DETERMINISTIC QUANT ENGINE).

Every number AXIOM acts on is computed here, in pure Python. The LLM reasoning
layer never computes a price, a Greek, an IV, or a fill — if a value is not
produced by this package (or fetched by a data adapter), it does not exist.
"""
