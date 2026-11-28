"""全局配置。可通过环境变量覆盖默认值。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("SMART_INVEST_DATA_DIR", "data"))
    initial_cash: float = float(os.getenv("SMART_INVEST_INITIAL_CASH", "100000"))
    commission_rate: float = float(os.getenv("SMART_INVEST_COMMISSION", "0.0003"))


settings = Settings()
