from mozes.analysis import run_all
from mozes.backtest import full_report, run_up_return, walk_forward, wilson
from mozes.pit import snapshot_at
from mozes.scenarios import quantile
from mozes.testing import make_price_event


def test_run_up_math_on_synthetic_fixture():
    e = make_price_event("A", "2021-06-01", 0.01)
    r = run_up_return(snapshot_at(e, e["date"]), 30, 1)
    assert abs(r["ret"] - (1.01 ** 29 - 1)) < 1e-9
    assert abs(r["excess"] - (1.01 ** 29 - 1)) < 1e-9


def test_walk_forward_selects_parameters_on_training_years_only():
    train = [make_price_event(f"T{i}", f"2020-0{i + 1}-15", 0.01) for i in range(5)]
    test = [make_price_event(f"X{i}", f"2021-0{i + 1}-15", -0.01) for i in range(3)]
    folds = {f["year"]: f for f in walk_forward(train + test)}
    assert folds["2020"]["skipped"]
    assert tuple(folds["2021"]["chosen"]) == (60, 1)  # best on positive-drift training data
    assert folds["2021"]["test"]["ret"]["mean"] < 0      # test data did not influence the choice


def test_wilson_bounds():
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(3, 4)
    assert 0 <= lo <= 0.75 <= hi <= 1


def test_quantile():
    assert quantile([1, 2, 3, 4, 5], 0.25) == 2


def test_full_report_on_seed_dataset():
    r = run_all()
    rep = full_report(r["historical"], r["historical_events"], r["outcomes"])
    assert rep["coverage"]["n_events"] == len(r["historical_events"])
    assert sum(c["n"] for c in rep["calibration"]) == sum(
        1 for o in r["outcomes"].values() if o["clinical"] in ("success", "fail"))
    assert rep["run_up"]["n_with_price_window"] == 0  # honest: no verified price windows yet
