Frames the previous daemon release (P1-04, runner protocol 1) sends, for T-P2-07-11.

Recorded from the protocol-1 daemon package (`daemon/tumnis_daemon` at the P1-04 merge) with
its own builders: `register` (profiles `tumnis-master` and `acme-site`), one `heartbeat`, and
the `result` it builds from `daemon/tests/recordings/stream_json/enrich_ok.jsonl`. Scrubbed:
the host is `hermes.example.org`; ids and times are the random ones the builders drew. Keep
these files unchanged while the server still accepts protocol 1 (REL-4, one release).
