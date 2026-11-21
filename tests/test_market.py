import pytest

from smart_invest.market import HKFeeModel, LotSizeTable, round_to_tick, shift_ticks, tick_size


@pytest.mark.parametrize("price,tick", [
    (0.20, 0.001), (0.25, 0.001), (0.30, 0.005), (5.0, 0.01), (10.0, 0.01), (10.02, 0.02),
    (50.0, 0.05), (150.0, 0.10), (300.0, 0.20), (600.0, 0.50), (1500.0, 1.0), (3000.0, 2.0), (6000.0, 5.0),
])
def test_tick_size(price, tick):
    assert tick_size(price) == tick


def test_round_to_tick():
    assert round_to_tick(388.3, "nearest") == 388.4
    assert round_to_tick(388.3, "down") == 388.2
    assert round_to_tick(388.3, "up") == 388.4
    assert round_to_tick(12.345, "down") == 12.34
    assert round_to_tick(388.4) == 388.4


def test_shift_ticks():
    assert shift_ticks(388.4, 1) == 388.6
    assert shift_ticks(388.4, -2) == 388.0
    # 跨越 200 元档位：200.0 向上一档为 200.2，向下一档为 199.9
    assert shift_ticks(200.0, 1) == 200.2
    assert shift_ticks(200.0, -1) == 199.9


def test_fee_model_typical_trade():
    fees = HKFeeModel().calculate(100_000, "00700")
    assert fees.commission == 30.0
    assert fees.stamp_duty == 100.0
    assert fees.trading_fee == 5.65
    assert fees.sfc_levy == 2.7
    assert fees.afrc_levy == 0.15
    assert fees.settlement_fee == 2.0
    assert fees.total == pytest.approx(140.5)


def test_fee_minimums_and_stamp_rounding():
    fees = HKFeeModel().calculate(1_234, "00005")
    assert fees.commission == 3.0          # 最低佣金
    assert fees.stamp_duty == 2.0          # 1.234 向上取整
    assert fees.settlement_fee == 2.0      # 最低交收费
    big = HKFeeModel().calculate(10_000_000, "00005")
    assert big.settlement_fee == 100.0     # 最高交收费


def test_etf_stamp_duty_exempt():
    assert HKFeeModel().calculate(100_000, "02800").stamp_duty == 0


def test_zero_fee_model():
    assert HKFeeModel.zero().total(1_000_000, "00700") == 0


def test_lot_size_table():
    lots = LotSizeTable({"00700": 200})
    assert lots.get("00700") == 200
    assert lots.get("00005") == 400
    assert lots.get("08888") == 100
