from mozes.backtest_v2 import all_runup_grids, runup_one


def test_all_grid_is_reported_not_only_best():
    rows=[{"stock":[{"date":f"2026-01-{i:02d}","close":100+i} for i in range(1,32)],"benchmark":[]}]
    g=all_runup_grids(rows)
    assert len(g) > 10
    assert {(x["entry"],x["exit"]) for x in g}


def test_runup_benchmark_alignment_can_be_missing_without_wrong_row_alignment():
    s=[{"date":f"2026-01-{i:02d}","close":100+i} for i in range(1,32)]
    b=[{"date":f"2026-02-{i:02d}","close":100+i} for i in range(1,20)]
    r=runup_one(s,b,14,1)
    assert r["excess"] is None
