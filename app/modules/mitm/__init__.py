"""MITM module (Session 17, Phase F) — bettercap ARP/DNS spoof + inspection.

Puts the Pi in the middle of a selected private/lab target's traffic via
ARP spoofing, optionally DNS-spoofs chosen domains, and surfaces the live
event stream (DNS queries, HTTP hosts, captured credentials). Default OFF,
RFC1918-fenced, behind a typed 'mitm' confirm. A drop-in module loaded by
app.services.modules.ModuleLoader.
"""
