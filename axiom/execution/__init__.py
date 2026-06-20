"""Execution layer: pre-trade guard + paper executor (spec §5.3 / §8 Step 8).

Phase 1 contains NO live-order path. The only executor here simulates fills.
The live executor (review_option_order -> place_option_order) is Phase 3, gated
on explicit operator approval.
"""
