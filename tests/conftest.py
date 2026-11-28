import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def prices() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2024-01-01", periods=120)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, len(dates)))
    return pd.DataFrame({"close": close}, index=pd.Index(dates, name="date"))
