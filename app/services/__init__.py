"""Service layer — stateful orchestration and parsing.

Routes call services; services call tools. Services are where long-running
state lives (the JobManager that tracks subprocesses, the in-memory scan
result store) and where multi-tool flows are coordinated (Evil Twin =
deauth + hostapd + dnsmasq, sequenced).
"""
