"""Optional on-device runtimes, and the bits that install them.

Nothing in here is imported by a normal `vocalize speak`: the Kokoro
provider reaches for the manifest only once a chain actually names it,
and the worker script is never imported at all — it runs under uv's own
Python, as a subprocess.
"""
