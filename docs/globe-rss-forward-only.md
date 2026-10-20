# Official GlobeNewswire RSS transport, with no historical replay

Configured `www.globenewswire.com/RssFeed/...` requests returned HTTP 403 from
the production runner. Read-only runner 39428765777 verified HTTP 200 and RSS XML
for the same industry and clinical-study paths on `rss.globenewswire.com`, the
official RSS host already used by the Edge detector. A local TLS failure was not
bypassed; the successful runner result is the evidence for the production path.

Switch only these two official endpoints. Keep source names, canonical article
identities, item budgets and issuer verification unchanged. On the first valid
feed check, persist a transport-specific forward-only timestamp. Suppress items
published before that timestamp, including the retained RAPP release, without
creating changes, notifications, delivery receipts or ACKs. Subsequent checks
reuse the timestamp and deduplicate normally. Invalid XML, untrusted article URLs
or failed requests cannot establish activation evidence. Invalid stored activation
evidence fails closed and is never silently reset. Other wire feeds are unchanged.

This deliberately does not backfill missed releases. Those can be proposed as
separate research records, never replayed as new notifications. It also does not
repair access to exact wire article bodies or prove the RAPP release was present
in a previously retrieved configured feed window. SEC evidence independently proves
that RAPP reached Edge classification before its queue delay.

Merge/activation requires explicit approval because it changes live source
collection and creates the forward-only observation in the authoritative monitor
database. No Worker deployment, database reset, historical import or new scheduler.
