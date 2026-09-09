"""XBRLBench: a benchmark for evaluating LLM financial reasoning over real
SEC XBRL filing data.

Two version identifiers are tracked separately (see docs/SCHEMA.md once it
exists for the policy):

- ``__version__`` is the code/package version.
- ``BENCHMARK_VERSION`` is the benchmark *content* version — it only changes
  when question wording, gold values, or schema fields materially change.
"""

__version__ = "0.1.0"
BENCHMARK_VERSION = "v0.1"
