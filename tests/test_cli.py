from smart_invest.cli import main


def test_cli_backtest(tmp_path, capsys):
    out = tmp_path / "rep"
    rc = main(["backtest", "-S", "sma_cross", "-s", "700", "9988", "-p", "fast=10", "slow=40",
               "--start", "2020-01-01", "--end", "2022-12-31", "-o", str(out)])
    assert rc == 0
    assert (out / "report.html").exists()
    assert "夏普比率" in capsys.readouterr().out


def test_cli_optimize_with_holdout(tmp_path, capsys):
    rc = main(["optimize", "-S", "sma_cross", "-s", "700", "9988", "-g", "fast=5,10", "slow=30,60",
               "--start", "2019-01-01", "--end", "2022-12-31", "-o", str(tmp_path / "opt.csv")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "样本外" in out
    assert (tmp_path / "opt.csv").exists()


def test_cli_bad_strategy(capsys):
    assert main(["backtest", "-S", "nope", "-s", "700"]) == 1
