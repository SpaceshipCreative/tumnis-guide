# Recorded packets

Each `*.packet.json` is one `TaskPacket` (R-24) a skill case sends to Hermes: its
`prompt_text` goes to Hermes byte for byte, and its `body` is what the case's rules and
`equals_input` checks read.

The bodies are built from the backend models in `tumnis/modules/agents/skill_io.py`, and
`prompt_text` from `packet_builder.render_prompt`. `test_recorded_packets_match_the_models`
(backend, agents contract tests) fails when a schema change leaves them stale; regenerate them
in the same PR. P1-08 and P1-11 replace them with packets from their own builders over the
seed set.
