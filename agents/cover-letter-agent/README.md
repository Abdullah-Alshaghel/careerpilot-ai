# cover-letter-agent

**Status:** planned — implemented in Phase 7.

Personalized cover letters grounded in verified facts and the actual job posting.

All agents: typed input/output schemas (Pydantic), validated outputs, `run_id`/`trace_id`/`user_id` on every run, time/cost/retry limits, and job/document text treated as untrusted data (prompt-injection defense). Agents run server-side as background workers and keep running when clients are closed.
