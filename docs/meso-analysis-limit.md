# MESO retained source exceeds the analysis bound

Event `EDGE-fe3b6ebc9a7186e22f7d78b8` is accession
`0001345099-26-000087`, first seen October 13. Read-only diagnostics on
October 20 found 170 analysis attempts and a generic error. Its SEC index
and directory return HTTP 200 and select Exhibit 99.1
`exhibit991annualreportag.htm`, with a reported size of 1,092,359 bytes.
The Worker rejects documents over 1,048,576 characters. The former catch
converted that deterministic limit into a generic retryable failure.
Runner 39427184530 fetched the actual exhibit with HTTP 200 and measured
1,092,465 UTF-8-decoded characters, confirming that the limit is exceeded.

Keep the size bound. A size-limit failure now retains the event and attempt
history as `analysis_status=blocked`, without a retry deadline, completion
timestamp, change, delivery or ACK. It remains in `analysis_pending`, is also
counted in `analysis_blocked`, and makes public health PARTIAL even when
transport checks succeed. Other HTTP/network failures keep their retries.
No schema migration is needed: analysis_status is an unconstrained text field.

The isolated regression uses a synthetic body matching the reported length;
it is not presented as the annual report's content. It proves the pre-fix hourly
retry behavior and post-fix retained blocking state, without production calls.

Do not deploy without explicit approval. This does not claim the source was
analyzed, irrelevant or delivered. Human source review and any future
unblocking strategy remain separate decisions; do not enlarge the limit,
truncate evidence and silently classify it, or manufacture an ACK.
