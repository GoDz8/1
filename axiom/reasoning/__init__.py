"""Reasoning layer (spec §3 / §4 Step 5). The ONLY generative step.

The LLM reads computed context, assesses regime/catalysts, SELECTS among
pre-validated EV-positive candidates, scores conviction, and emits the §8 JSON.
It never computes a number. A deterministic stub reasoner mirrors the same
contract so the system runs end-to-end offline.
"""
