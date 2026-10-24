"""Resource samples come from procfs where it exists and from portable counters elsewhere."""

from trade_graph.kernel.operational_diagnostics import _resources


def test_resource_sample_reports_threads_and_memory_without_procfs(tmp_path):
    sample = _resources(status_path=tmp_path / "missing-status")
    assert sample["threads"] >= 1
    assert sample["rss_bytes"] > 0


def test_resource_sample_prefers_procfs_when_present(tmp_path):
    status = tmp_path / "status"
    status.write_text("Name:\tpython\nThreads:\t7\nVmRSS:\t   2048 kB\n")
    assert _resources(status_path=status) == {"threads": 7, "rss_bytes": 2048 * 1024}
