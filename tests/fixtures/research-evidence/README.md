# Fixed evidence contract samples

These synthetic samples and expected decisions were fixed before implementing the
evidence helper. They exercise provenance and invalidation, not model research
quality, semantic entailment, source independence or investment performance.

`cases.json` pins the bytes of three local records. The two Markdown sources are
invented host study notes; they are not external citations. The JSON record is a
negative algorithm result. Tests independently reproduce it with `bisect_left`
and a linear scan before using its digest as experiment evidence.

All times are explicit UTC values. Expiry is exclusive: evidence is no longer
current at `expires_at`. Missing or changed counterevidence requires review even
if supporting material remains current. A hypothesis stays a hypothesis, and a
claim with unresolved counterevidence stays contested. A revised or expired source
affects its dependents, while the unrelated research branch remains linked.

These are small public regression fixtures, not a held-out model benchmark. A
separate unseen task set is still needed to measure research quality.
