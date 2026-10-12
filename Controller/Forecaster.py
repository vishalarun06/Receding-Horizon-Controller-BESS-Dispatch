import pandas as pd
import numpy as np

from Config import PlantConfig
from Data_Loader import DataLoader

FORECAST_COLUMNS = ["WindGen", "SolarGen1", "SolarGen2", "ExchangePrice"]
CALENDAR_COLUMNS = ["HourOfDay"]

class BuildForecast:

    def __init__(self,
                 config: PlantConfig,
                 data: pd.DataFrame,
                 current_hour,
                 FORECAST_COLUMNS = ["WindGen", "SolarGen1", "SolarGen2", "ExchangePrice"],
                 CALENDAR_COLUMNS = ["HourOfDay"]):

        self.current_hour = current_hour
        self.config = config
        
        self.data = data

        self.FORECAST_COLUMNS = FORECAST_COLUMNS
        self.CALENDAR_COLUMNS = CALENDAR_COLUMNS

    def build(self):
        arrays = {c: self.data[c].to_numpy() for c in
          ["WindGen", "SolarGen1", "SolarGen2", "ExchangePrice", "HourOfDay"]}
        
        last = self.config.n_hours - 1
        t1 = min(self.current_hour + self.config.horizon_hours, last)

        idx = np.arange(self.current_hour, t1 + 1)
        lookback_hours = self.config.forecast_lookback_days * 24

        forecast_idx = np.where(0 <= idx - lookback_hours <= self.current_hour, idx - lookback_hours,
                                np.where(0 <= idx - 24 <= self.current_hour, idx - 24, self.current_hour))
        forecast_idx[0] = self.current_hour

        out = {"t": idx, "offset": idx - self.current_hour, "HourOfDay": arrays["HourOfDay"][idx]}
        for c in ["WindGen", "SolarGen1", "SolarGen2", "ExchangePrice"]:
            out[c] = arrays[c][forecast_idx]                    # forecast, never arrays[c][idx]
        return pd.DataFrame(out)
    
