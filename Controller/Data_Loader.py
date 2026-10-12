import pandas as pd
from Config import PlantConfig

class DataLoader:

    def __init__(self, config):
        """
        This object's function is to take the template of generation and exchange price data and load it into 
        a pandas data frame that can be functionally handled easier
        """
        self.config = config
        self.ground_truth = None

    def load_ground_truth(self):
        
        # Load the Generation Data then rename the columns
        gen = pd.read_excel(
            self.config.spreadsheet_path,
            sheet_name="Wind-Solar+BESS",
            header=2,
            nrows=self.config.n_hours,
        )

        gen = gen.rename(columns={
            "Hours": "HourOfDay",
            "Wind Generation": "WindGen",
            "Solar Generation": "SolarGen1",
            "Solar Generation2": "SolarGen2",
        })
        gen = gen[["Month", "Day", "HourOfDay", "WindGen", "SolarGen1", "SolarGen2"]].copy()

        # Load the exchange data
        exch = pd.read_excel(
            self.config.spreadsheet_path,
            sheet_name="Exchange",
            header=1,
            nrows=self.config.n_hours,
        )

        # Locate the price column
        price_col = self.config.exchange_price_column
        if price_col not in exch.columns:

            raise KeyError(
                f"Exchange price column '{price_col}' not found in the "
                f"'Exchange' sheet. Available columns: {list(exch.columns)}"
            )

        # Check the exchange and generation columns have the same number of hours
        if len(gen) != self.config.n_hours or len(exch) != self.config.n_hours:
            raise ValueError(
                f"Expected {self.config.n_hours} rows from each sheet, got "
                f"{len(gen)} (generation) and {len(exch)} (exchange)."
            )

        ground_truth = gen.reset_index(drop=True).copy()
        ground_truth["ExchangePrice"] = exch[price_col].reset_index(drop=True).to_numpy()

        # Add a fresh index indexing the year by hours
        ground_truth.insert(0, "t", range(self.config.n_hours))

        self.ground_truth = ground_truth
        return ground_truth


if __name__ == "__main__":

    cfg = PlantConfig()
    dl = DataLoader(cfg)
    df = dl.load_ground_truth()
    print(df.head(10).to_string(index=False))
    print()
    print("Row count:", len(df))
    print("WindGen (MW) mean/max:", df["WindGen"].mean() / 1000, df["WindGen"].max() / 1000)
    print("SolarGen1+2 (MW) mean/max:", (df['SolarGen1']+df['SolarGen2']).mean()/1000, (df['SolarGen1']+df['SolarGen2']).max()/1000)
    print("ExchangePrice (Rs/kWh) mean/min/max:",
          df["ExchangePrice"].mean(), df["ExchangePrice"].min(), df["ExchangePrice"].max())
