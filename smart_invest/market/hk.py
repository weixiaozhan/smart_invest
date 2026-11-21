"""港股市场规则：价位表（最小变动价位）、每手股数、交易费用。

费率为编写时的公开标准，可能随监管调整，均可通过 ``HKFeeModel`` 参数覆盖。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 港交所价位表（第一部分，适用于股票）: (价格上限(含), 最小变动价位)
_TICK_TABLE: list[tuple[float, float]] = [
    (0.25, 0.001),
    (0.50, 0.005),
    (10.00, 0.01),
    (20.00, 0.02),
    (100.00, 0.05),
    (200.00, 0.10),
    (500.00, 0.20),
    (1000.00, 0.50),
    (2000.00, 1.00),
    (5000.00, 2.00),
    (float("inf"), 5.00),
]

_EPS = 1e-9


def tick_size(price: float) -> float:
    """返回某价格所在区间的最小变动价位。"""
    if price <= 0:
        raise ValueError(f"价格必须为正数: {price}")
    for upper, tick in _TICK_TABLE:
        if price <= upper + _EPS:
            return tick
    return _TICK_TABLE[-1][1]  # pragma: no cover


def round_to_tick(price: float, direction: str = "nearest") -> float:
    """把价格对齐到合法价位。direction: 'up' / 'down' / 'nearest'。"""
    tick = tick_size(price)
    n = price / tick
    if direction == "up":
        n = math.ceil(n - _EPS)
    elif direction == "down":
        n = math.floor(n + _EPS)
    elif direction == "nearest":
        n = round(n)
    else:
        raise ValueError(f"未知取整方向: {direction}")
    return round(max(n, 1) * tick, 3)


def shift_ticks(price: float, n_ticks: int) -> float:
    """在合法价位上把价格移动 n 个价位（正数向上，负数向下）。"""
    p = round_to_tick(price, "nearest")
    step = 1 if n_ticks > 0 else -1
    for _ in range(abs(n_ticks)):
        if step > 0:
            # 向上移动时用上方区间的价位（如 200.00 的上一档是 200.20）
            p = round(p + tick_size(p * (1 + 1e-7)), 3)
        else:
            # 向下移动时用下方区间的价位，避免跨档位出错
            p = round(p - tick_size(max(p * (1 - 1e-7), 0.001)), 3)
            p = max(p, 0.001)
    return p


# 常见港股的每手股数（可能因公司行动而变化，以券商/港交所公布为准）
LOT_SIZES: dict[str, int] = {
    "00001": 500,   # 长和
    "00005": 400,   # 汇丰控股
    "00011": 100,   # 恒生银行
    "00016": 500,   # 新鸿基地产
    "00027": 1000,  # 银河娱乐
    "00388": 100,   # 香港交易所
    "00700": 100,   # 腾讯控股
    "00883": 1000,  # 中国海洋石油
    "00939": 1000,  # 建设银行
    "00941": 500,   # 中国移动
    "01024": 100,   # 快手
    "01211": 500,   # 比亚迪股份
    "01299": 200,   # 友邦保险
    "01398": 1000,  # 工商银行
    "01810": 200,   # 小米集团
    "02318": 500,   # 中国平安
    "02800": 500,   # 盈富基金
    "02828": 200,   # 恒生中国企业
    "03033": 200,   # 南方恒生科技
    "03690": 100,   # 美团
    "09618": 50,    # 京东集团
    "09988": 100,   # 阿里巴巴
    "09999": 100,   # 网易
}

DEFAULT_LOT_SIZE = 100

# ETF 等豁免印花税的品种（不完整，可通过 HKFeeModel.stamp_duty_exempt 扩充）
STAMP_DUTY_EXEMPT: set[str] = {"02800", "02828", "03033", "03067", "02833", "03188"}


class LotSizeTable:
    """每手股数查询，未知代码回退到默认值并提示一次。"""

    def __init__(self, overrides: dict[str, int] | None = None, default: int = DEFAULT_LOT_SIZE):
        self._table = dict(LOT_SIZES)
        if overrides:
            self._table.update(overrides)
        self.default = default
        self._warned: set[str] = set()

    def get(self, code: str) -> int:
        if code in self._table:
            return self._table[code]
        if code not in self._warned:
            logger.warning("未知 %s 的每手股数，使用默认 %d 股/手，可用 --lot %s=N 指定", code, self.default, code)
            self._warned.add(code)
        return self.default


@dataclass
class FeeBreakdown:
    commission: float = 0.0
    platform_fee: float = 0.0
    stamp_duty: float = 0.0
    trading_fee: float = 0.0
    sfc_levy: float = 0.0
    afrc_levy: float = 0.0
    settlement_fee: float = 0.0

    @property
    def total(self) -> float:
        return round(
            self.commission + self.platform_fee + self.stamp_duty + self.trading_fee
            + self.sfc_levy + self.afrc_levy + self.settlement_fee,
            2,
        )


@dataclass
class HKFeeModel:
    """港股交易费用模型（买卖双边均收取）。

    - 佣金: 券商自定，默认 0.03%，最低 3 港元
    - 平台费: 券商自定，默认 0（部分券商每笔 15 港元）
    - 印花税: 0.1%，不足 1 港元按 1 港元计（向上取整），ETF 豁免
    - 交易费（港交所）: 0.00565%
    - 交易征费（证监会）: 0.0027%
    - 会财局征费: 0.00015%
    - 股份交收费: 0.002%，最低 2、最高 100 港元
    """

    commission_rate: float = 0.0003
    min_commission: float = 3.0
    platform_fee: float = 0.0
    stamp_duty_rate: float = 0.001
    trading_fee_rate: float = 0.0000565
    sfc_levy_rate: float = 0.000027
    afrc_levy_rate: float = 0.0000015
    settlement_rate: float = 0.00002
    min_settlement: float = 2.0
    max_settlement: float = 100.0
    stamp_duty_exempt: set[str] = field(default_factory=lambda: set(STAMP_DUTY_EXEMPT))

    def calculate(self, value: float, code: str | None = None) -> FeeBreakdown:
        """计算一笔成交金额为 value 的交易费用。"""
        if value <= 0:
            return FeeBreakdown()
        stamp = 0.0
        if self.stamp_duty_rate > 0 and code not in self.stamp_duty_exempt:
            stamp = float(math.ceil(value * self.stamp_duty_rate - _EPS))
        settlement = 0.0
        if self.settlement_rate > 0:
            settlement = min(max(value * self.settlement_rate, self.min_settlement), self.max_settlement)
        return FeeBreakdown(
            commission=round(max(value * self.commission_rate, self.min_commission), 2),
            platform_fee=round(self.platform_fee, 2),
            stamp_duty=stamp,
            trading_fee=round(max(value * self.trading_fee_rate, 0.01), 2) if self.trading_fee_rate > 0 else 0.0,
            sfc_levy=round(value * self.sfc_levy_rate, 2),
            afrc_levy=round(value * self.afrc_levy_rate, 2),
            settlement_fee=round(settlement, 2),
        )

    def total(self, value: float, code: str | None = None) -> float:
        return self.calculate(value, code).total

    @classmethod
    def zero(cls) -> "HKFeeModel":
        """零费用模型，用于对比或测试。"""
        return cls(
            commission_rate=0, min_commission=0, platform_fee=0, stamp_duty_rate=0,
            trading_fee_rate=0, sfc_levy_rate=0, afrc_levy_rate=0, settlement_rate=0,
            min_settlement=0, max_settlement=0,
        )
