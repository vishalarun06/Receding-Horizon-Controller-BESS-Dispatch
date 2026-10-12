import pandas as pd
import numpy as np
from ppa_class import PPA


class Kudligi:

    def __init__(self,
            generation_path,
            exchange_path,
            hybrid_conn,
            PPAs,
            limitedH_RTC,
            all_day_RTC,
            wind_capacity,
            ac_solar_capacity,
            dc_ac_overload,
            BESS_discharge_eve_start,
            BESS_discharge_eve_end,
            BESS_discharge_morn_start,
            BESS_discharge_morn_end,
            pcs_cap,
            BESS_capacity,
            max_soc_perc,
            min_soc_perc,
            rte,
            year,
            pss_gss_loss
    ):

        self.year = year

        self.generation = pd.read_excel(generation_path, sheet_name="Wind-Solar+BESS", header=2, nrows=8760)
        self.exchange = pd.read_excel(exchange_path, sheet_name="Exchange", header=1, nrows=8760)
        self.generation["Exchange Price"] = self.exchange["Year "+str(self.year)].to_numpy()

        
        self.hybrid_conn = hybrid_conn * 1000

        self.PPAs = PPAs 
        self.limitedH_RTC = limitedH_RTC * 1000
        self.wind_capacity = wind_capacity
        self.ac_solar_capacity = ac_solar_capacity
        self.dc_ac_overload = dc_ac_overload
        self.all_day_RTC = all_day_RTC * 1000
        self.BESS_discharge_eve_start = BESS_discharge_eve_start
        self.BESS_discharge_eve_end = BESS_discharge_eve_end
        self.BESS_discharge_morn_start = BESS_discharge_morn_start
        self.BESS_discharge_morn_end = BESS_discharge_morn_end
        self.pcs_cap = pcs_cap * 1000

        self.BESS_capacity = BESS_capacity
        self.max_soc = max_soc_perc * self.BESS_capacity
        self.min_soc = min_soc_perc * self.BESS_capacity
        self.rte = rte

        self.pss_gss_loss = pss_gss_loss

    def calc_total_gen(self):
        self.generation["Total Generation"] = (self.generation["Wind Generation"] +
                                               self.generation["Solar Generation"] +
                                               self.generation["Solar Generation2"])

    def calc_if_peak(self):
        hours = self.generation["Hours"]
        self.generation["LimitedH-RTC PPA"] = np.where((hours > 8) & (hours < 17), "Non-Peak", "Peak")

    def calc_limitedH_injection(self):
        self.generation["LimitedH-RTC Injection"] = np.where(
            self.generation["LimitedH-RTC PPA"] == "Peak",
            np.minimum(self.limitedH_RTC, self.generation["Total Generation"]),
            0.0,
        )

    def calc_24h_injection(self):

        self.generation["24H-RTC Injection"] = np.minimum(
            self.generation["Total Generation"] - self.generation["LimitedH-RTC Injection"],
            self.all_day_RTC,
        )

    def calc_exch_injection(self):
        total_inj = self.generation["LimitedH-RTC Injection"] + self.generation["24H-RTC Injection"]
        hours = self.generation["Hours"]
        in_window = (
            ((hours >= self.BESS_discharge_eve_start) & (hours <= self.BESS_discharge_eve_end))
            | ((hours >= self.BESS_discharge_morn_start) & (hours <= self.BESS_discharge_morn_end))
        )
        self.generation["Exchange Injection1"] = np.where(
            in_window,
            np.maximum(
                np.minimum(self.hybrid_conn - total_inj, self.generation["Total Generation"] - total_inj),
                0.0,
            ),
            0.0,
        )

    def calc_grid_excess(self):
        total_inj = (self.generation["LimitedH-RTC Injection"] +
                    self.generation["24H-RTC Injection"] +
                    self.generation["Exchange Injection1"])
        self.generation["Grid Excess"] = self.generation["Total Generation"] - total_inj

    def calc_grid_inj_total(self):
        total_inj = (self.generation["LimitedH-RTC Injection"] +
                    self.generation["24H-RTC Injection"] +
                    self.generation["Exchange Injection1"])
        self.generation["Grid Injection"] = total_inj

    def calc_grid_deficit(self):

        self.generation["Grid Deficit"] = np.maximum(0.0, self.hybrid_conn - self.generation["Grid Injection"])

    def calc_exch_injection2(self):
        self.generation["Exchange Injection (PCS Cap)"] = np.where(
            self.generation["Grid Excess"] > self.pcs_cap,
            np.minimum(self.generation["Grid Excess"] - self.pcs_cap, self.generation["Grid Deficit"]),
            0.0,
        )

    def calc_bess_dispatch(self):

        n = len(self.generation)
        excess = (self.generation["Grid Excess"] - self.generation["Exchange Injection (PCS Cap)"]).to_numpy()
        excess_raw = self.generation["Grid Excess"].to_numpy()  # used unadjusted in the Discharge formula, matching the sheet
        deficit = self.generation["Grid Deficit"].to_numpy()
        pcs_cap_minus = (self.generation["Grid Deficit"] - self.generation["Exchange Injection (PCS Cap)"]).to_numpy()
        hours = self.generation["Hours"].to_numpy()
        in_window = (
            ((hours >= self.BESS_discharge_eve_start) & (hours <= self.BESS_discharge_eve_end))
            | ((hours >= self.BESS_discharge_morn_start) & (hours <= self.BESS_discharge_morn_end))
        )

        soc = np.zeros(n)
        charge = np.zeros(n)
        discharge = np.zeros(n)

        for r in range(n):
            if r == 0:
                soc[r] = self.min_soc
                charge[r] = 0.0
                discharge[r] = 0.0
                continue

            prev_soc = soc[r - 1]
            charge_headroom = min(excess[r], self.pcs_cap, self.max_soc - prev_soc)
            soc_after_charge = min(self.max_soc, prev_soc + charge_headroom)

            if in_window[r]:
                discharge_soc = min(
                    pcs_cap_minus[r],
                    (soc_after_charge - self.min_soc) * self.rte,
                    self.pcs_cap,
                ) / self.rte
            else:
                discharge_soc = 0.0

            soc[r] = soc_after_charge - discharge_soc
            charge[r] = max(0.0, soc[r] - prev_soc)

            if in_window[r]:
                charge_headroom_s = min(excess_raw[r], self.pcs_cap, self.max_soc - prev_soc)
                soc_after_charge_s = min(self.max_soc, prev_soc + charge_headroom_s)
                discharge[r] = min(
                    deficit[r],
                    (soc_after_charge_s - self.min_soc) * self.rte,
                    self.pcs_cap,
                )
            else:
                discharge[r] = 0.0

        self.generation["SOC BESS"] = soc
        self.generation["BESS Charge"] = charge
        self.generation["BESS Discharge"] = discharge

    def calc_soc_full_availability(self):
        prev_soc = self.generation["SOC BESS"].shift(1).fillna(self.min_soc)
        available = self.generation["Grid Excess"] - self.generation["Exchange Injection (PCS Cap)"]
        headroom = np.minimum(np.minimum(available, self.pcs_cap), self.max_soc - prev_soc)
        self.generation["SOC Full Availability"] = np.maximum(0.0, available - headroom)

    def calc_exch_injection3(self):
        remaining_deficit = self.generation["Grid Deficit"] - self.generation["Exchange Injection (PCS Cap)"]
        self.generation["Exchange Injection (Battery SOC)"] = np.where(
            self.generation["SOC Full Availability"] < remaining_deficit,
            self.generation["SOC Full Availability"],
            np.maximum(0.0, remaining_deficit),
        )

    def calc_soc_loss(self):
        self.generation["SOC Loss"] = (
            self.generation["SOC Full Availability"] - self.generation["Exchange Injection (Battery SOC)"]
        )

    def calc_final_grid_deficit(self):
        served = (
            self.generation["Grid Injection"]
            + self.generation["Exchange Injection (PCS Cap)"]
            + self.generation["BESS Discharge"]
            + self.generation["Exchange Injection (Battery SOC)"]
        )
        self.generation["Final Grid Deficit"] = np.maximum(0.0, self.hybrid_conn - served)

    def calc_bess_routed_allocation(self):

        is_peak = self.generation["LimitedH-RTC PPA"] == "Peak"
        limitedh_direct = self.generation["LimitedH-RTC Injection"]
        rtc24_direct = self.generation["24H-RTC Injection"]
        discharge = self.generation["BESS Discharge"]

        limitedh_bess = np.where(
            is_peak & (limitedh_direct < self.limitedH_RTC),
            np.minimum(discharge, self.limitedH_RTC - limitedh_direct),
            0.0,
        )
        rtc24_bess = np.where(
            rtc24_direct < self.all_day_RTC,
            np.minimum(discharge - limitedh_bess, self.all_day_RTC - rtc24_direct),
            0.0,
        )
        exchange_bess = discharge - (limitedh_bess + rtc24_bess)

        self.generation["LimitedH-RTC Injection (BESS)"] = limitedh_bess
        self.generation["24H-RTC Injection (BESS)"] = rtc24_bess
        self.generation["Exchange Injection (BESS)"] = exchange_bess

    def calc_totals(self):
        self.generation["LimitedH-RTC Injection Total"] = (
            self.generation["LimitedH-RTC Injection"] + self.generation["LimitedH-RTC Injection (BESS)"]
        )

        self.generation["GSS LimitedH-RTC Injection"] = self.generation["LimitedH-RTC Injection Total"] * self.pss_gss_loss

        self.generation["24H-RTC Injection Total"] = (
            self.generation["24H-RTC Injection"] + self.generation["24H-RTC Injection (BESS)"]
        )

        self.generation["GSS 24H-RTC Injection"] = self.generation["24H-RTC Injection Total"] * self.pss_gss_loss
        
        self.generation["Exchange Injection Total"] = (
            self.generation["Exchange Injection1"]
            + self.generation["Exchange Injection (PCS Cap)"]
            + self.generation["Exchange Injection (Battery SOC)"]
            + self.generation["Exchange Injection (BESS)"]
        )

        self.generation["GSS Exchange Injection"] = self.generation["Exchange Injection Total"] * self.pss_gss_loss
        
        self.generation["Total Grid Injection"] = (
            self.generation["LimitedH-RTC Injection Total"]
            + self.generation["24H-RTC Injection Total"]
            + self.generation["Exchange Injection Total"]
        )

        self.generation["Total GSS Injection"] = (
            self.generation["GSS LimitedH-RTC Injection"] +
            self.generation["GSS 24H-RTC Injection"] +
            self.generation["GSS Exchange Injection"]
        )

    def calc_revenue(self):
        self.generation["Exchange Revenue"] = (
            self.generation["GSS Exchange Injection"] * self.generation["Exchange Price"]
        )
         
    def calc_ppa_allocation_24H(self):
        """
        The dispatch above only ever tracks 24H-RTC as ONE pooled channel
        (matching the sheet, which has no per-company injection columns).
        To get each company's own delivered energy and revenue -- and to
        check it against their contracted minimum -- the pooled annual
        total is pro-rated back out by each PPA's share of the total
        contracted RTC Quantum, and each company's own tariff is applied.
        """
        total_pool_energy = self.generation["GSS 24H-RTC Injection"].sum()
        total_contracted_energy = sum([ppa.min_contracted_energy for ppa in self.PPAs if ppa.discharge_period == "24H-RTC"])

        for ppa in self.PPAs:
            if ppa.discharge_period == "24H-RTC":
                share = ppa.min_contracted_energy / total_contracted_energy
                ppa.delivered_energy = total_pool_energy * share
                ppa.revenue = ppa.delivered_energy * ppa.tariff
                ppa.shortfall_vs_minimum = max(0.0, ppa.min_contracted_energy - ppa.delivered_energy)

    def calc_ppa_allocation_limited(self):

        total_pool_energy = self.generation["GSS LimitedH-RTC Injection"].sum()
        total_contracted_energy = sum([ppa.actual_contracted_energy for ppa in self.PPAs if ppa.discharge_period == "LimitedH-RTC"])

        for ppa in self.PPAs:
            if ppa.discharge_period == "LimitedH-RTC":
                share = ppa.actual_contracted_energy / total_contracted_energy
                ppa.delivered_energy = total_pool_energy * share
                ppa.revenue = ppa.delivered_energy * ppa.tariff
                ppa.shortfall_vs_minimum = max(0.0, ppa.min_contracted_energy - ppa.delivered_energy)

    def calc_24H_revenue(self):
        total_revenue = sum([ppa.revenue for ppa in self.PPAs if ppa.discharge_period == "24H-RTC"])
        self.total24H_rev = total_revenue

    def calc_LimitedH_revenue(self):
        total_revenue = sum([ppa.revenue for ppa in self.PPAs if ppa.discharge_period == "LimitedH-RTC"])
        self.total_limitedh_rev = total_revenue

    def ppa_summary(self):
        return pd.DataFrame([
            {
                "Company": ppa.company_name,
                "RTC Quantum (MW)": ppa.RTC_Quantum,
                "Tariff (Rs/kWh)": ppa.tariff,
                "Min Contracted Energy (kWh)": ppa.min_contracted_energy,
                "Actual Contracted Energy (kWh)": ppa.actual_contracted_energy,
                "Discharge Period" : ppa.discharge_period,
                "Delivered Energy (kWh)": ppa.delivered_energy,
                "Revenue (Rs)": ppa.revenue,
                "Shortfall vs. Minimum (kWh)": ppa.shortfall_vs_minimum,
                "Minimum Met": ppa.shortfall_vs_minimum == 0.0,
            }
            for ppa in self.PPAs
        ])

    def run(self):
        """Runs every calc_* method in the order the dispatch actually depends on."""
        self.calc_total_gen()
        self.calc_if_peak()
        self.calc_limitedH_injection()
        self.calc_24h_injection()
        self.calc_exch_injection()
        self.calc_grid_excess()
        self.calc_grid_inj_total()
        self.calc_grid_deficit()
        self.calc_exch_injection2()
        self.calc_bess_dispatch()
        self.calc_soc_full_availability()
        self.calc_exch_injection3()
        self.calc_soc_loss()
        self.calc_final_grid_deficit()
        self.calc_bess_routed_allocation()
        self.calc_totals()
        self.calc_revenue()
        self.calc_ppa_allocation_24H()
        self.calc_ppa_allocation_limited()
        self.calc_24H_revenue()
        # Bug fix: calc_LimitedH_revenue() (added alongside calc_24H_revenue()
        # when the LimitedH-RTC revenue calc was moved to be PPA-based) was
        # never wired into run(), so self.total_limitedh_rev was never set --
        # kudligi_dashboard.py's calc_revenue() reads plant.total_limitedh_rev
        # right after run() returns, so every simulation would fail with
        # AttributeError: 'Kudligi' object has no attribute 'total_limitedh_rev'.
        self.calc_LimitedH_revenue()
        return self


if __name__ == "__main__":
    ppas = [
        PPA("Linde India Ltd (AP)", 59020000, 53_118_000, 8, 3.67, "24H-RTC"),
        PPA("Linde India Ltd (Gujarat)", 27070000, 24_363_000, 5, 3.67, "24H-RTC"),
        PPA("Linde India Ltd (Uttarakhand)", 58060000, 52_254_000, 8, 3.67, "24H-RTC"),
        PPA("Linde India Ltd (Punjab)", 59890000, 53_901_000, 8, 3.67, "24H-RTC"),
        PPA("Praxair India Ltd (Telangana)", 56637273, 50_973_546.387101404, 8, 3.58, "24H-RTC"),
        PPA("Linde India Ltd (Orissa)", 388800000, 349_920_000, 68, 3.67, "24H-RTC"),
        PPA("Chemfab Alkalies (Puducherry)", 64500000, 58_050_000, 10, 3.65, "24H-RTC"),
        PPA("Green Valley (Meghalaya)", 31870000, 28_683_000, 5, 4.10, "24H-RTC"),
        PPA("New Clients (Multiple)", 550000000, 495_000_000, 94.17808219178082, 3.70, "LimitedH-RTC"),
    ]

    plant = Kudligi(
        generation_path="Spreadsheets/Kudligi Spreadsheet.xlsx",
        exchange_path="Spreadsheets/Kudligi Spreadsheet.xlsx",
        hybrid_conn=300,
        PPAs=ppas,
        limitedH_RTC=100,
        all_day_RTC=120,
        wind_capacity=300.3,
        ac_solar_capacity=275,
        dc_ac_overload=1.5,
        BESS_discharge_eve_start=20,
        BESS_discharge_eve_end=24,
        BESS_discharge_morn_start=0,
        BESS_discharge_morn_end=1,
        pcs_cap=100,
        BESS_capacity=526210.8234882968,
        max_soc_perc=1,
        min_soc_perc=0.0,
        rte=0.8477864989135973,
        year=1,
        pss_gss_loss=0.98
    )
    plant.run()

    print("=== Annual summary ===")
    print(f"LimitedH-RTC energy (Mn kWh): {plant.generation['LimitedH-RTC Injection Total'].sum() / 1e6:,.2f}")
    print(f"24H-RTC energy (Mn kWh):      {plant.generation['24H-RTC Injection Total'].sum() / 1e6:,.2f}")
    print(f"Exchange energy (Mn kWh):     {plant.generation['Exchange Injection Total'].sum() / 1e6:,.2f}")
    print(f"Total Grid Injection (Mn kWh):{plant.generation['Total Grid Injection'].sum() / 1e6:,.2f}")
    print(f"24H-RTC Revenue (Mn INR) : {plant.total24H_rev / 1e6:,.2f}")
    print(f"LimitedH-RTC revenue (Mn INR):{plant.total_limitedh_rev / 1e6:,.2f}")
    print(f"Exchange revenue (Mn INR):    {plant.generation['Exchange Revenue'].sum() / 1e6:,.2f}")
    

    plant.generation.to_csv("Kudligi_Hourly_Dispatch.csv", index=False)
    print("\nSaved: Kudligi_Hourly_Dispatch.csv")