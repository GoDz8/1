"""Data adapters (spec §6). Each adapter retries with timeout and fails CLOSED:
on unrecoverable error it returns 'unknown' (None), never a fabricated value.
"""
