"""smart_invest —— 港股量化策略回测系统。"""
from .analysis import compute_metrics, grid_search, save_report
from .data import make_source, normalize_symbol
from .engine import BacktestConfig, BacktestEngine, BacktestResult, Context, run_backtest
from .market import HKFeeModel
from .strategy import Strategy, get_strategy, register

__version__ = "0.1.0"

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "Context",
    "HKFeeModel",
    "Strategy",
    "compute_metrics",
    "get_strategy",
    "grid_search",
    "make_source",
    "normalize_symbol",
    "register",
    "run_backtest",
    "save_report",
]
