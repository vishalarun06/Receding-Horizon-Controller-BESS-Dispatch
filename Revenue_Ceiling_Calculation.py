import pandas as pd
import numpy as np
from ppa_class import PPA
import pyomo.environ as pyo
import numpy as np

class KudligiPerfectOpt:

    def __init__(self, 

                 generation_path,
                 exchange_path,

                 hybrid_connectivity=300,
                 PPAs=None,

                 no_wind_turbines=91,
                 ac_solar_capacity=175,
                 dc_ac_overload=1.5,

                 bi_d_invertor_size=2.5,
                 no_bi_d_invertors=40,

                 battery_block_size_mwh=6.25,
                 battery_blocks_per_pcs=2,

                 RTE=84.77865,
                 pss_gss_loss=2,

                 max_SoC_perc=100,
                 min_SoC_perc=0,

                 n_hours=8760,

                 charging_capacity_extra=1.05242164697659,
                 solver_name = "appsi_highs"):

        self.generation = pd.read_excel(generation_path)
        self.exchange = pd.read_excel(exchange_path)

        self.PPAs = PPAs if PPAs is not None else [
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

        self.hybrid_conn = hybrid_connectivity * 1000

        self.wind_capacity = no_wind_turbines * 3.3
        self.ac_solar_capacity = ac_solar_capacity
        self.dc_solar_capacity = ac_solar_capacity * dc_ac_overload

        self.BESS_pcs_cap = bi_d_invertor_size * no_bi_d_invertors * 1000
        self.BESS_installed_capacity = battery_block_size_mwh * battery_blocks_per_pcs * no_bi_d_invertors * 1000

        self.BESS_charging_capacity = self.BESS_installed_capacity * charging_capacity_extra

        self.maxSoC = self.BESS_charging_capacity * max_SoC_perc / 100
        self.minSoC = self.BESS_charging_capacity * min_SoC_perc / 100
        self.initSoC = self.minSoC

        self.RTE = RTE / 100
        self.pss_gss_loss = (100 - pss_gss_loss) / 100

        self.model = None
        self.n_hours = n_hours

        self.solver_name = solver_name


    def build_model(self):
        mo = pyo.ConcreteModel(name="Kudligi Perfect Dispatch")
        self.model = mo

        # ----- Index Hourly Dispatch by Month, Date, Hour ------
        time_tuples = list(self.generation.set_index(["Month","Day","Hour"]).index)
        mo.T = pyo.Set(dimen=3, initialize=time_tuples)

        # ---------- Index Generation + Exchange Data  ----------
        self.generation_indexed = self.generation.set_index(["Month", "Day", "Hour"])
        self.exchange_indexed = self.exchange.set_index(["Month", "Day", "Hour"])
        wind_gen = self.generation_indexed["Wind Generation"].to_dict()
        solar_gen1 = self.generation_indexed["Solar Generation1"].to_dict()
        solar_gen2 = self.generation_indexed["Solar Generation2"].to_dict()
        exchange_price = self.exchange_indexed["Exchange Price"].to_dict()

        # -------- Load Generation + Exchange Parameters --------
        mo.WindGen = pyo.Param(mo.T, initialize=wind_gen)
        mo.SolarGen1 = pyo.Param(mo.T, initialize=solar_gen1)
        mo.SolarGen2 = pyo.Param(mo.T, initialize=solar_gen2)
        mo.exchange = pyo.Param(mo.T, initialize=exchange_price)

        # ---------------- Load Loss Parameters ------------------
        mo.RTE = pyo.Param(default=self.RTE)

        def split_RTE_ch_disch(mo):
            return np.sqrt(pyo.value(mo.RTE))
        
        mo.RTE_ch = pyo.Param(initialize=split_RTE_ch_disch)
        mo.RTE_disch = pyo.Param(initialize=split_RTE_ch_disch)

        mo.PSS_GSS_loss = pyo.Param(default=self.pss_gss_loss)

        # --------------- BESS Dispatch Variables ---------------
        mo.BESS_Charge = pyo.Var(mo.T, domain=pyo.NonNegativeReals)
        mo.BESS_Discharge = pyo.Var(mo.T, domain=pyo.NonNegativeReals)
        mo.BESS_SoC = pyo.Var(mo.T, bounds=(self.minSoC, self.maxSoC))

        # ---------- Index Injection Variables by PPAs ----------
        ppas_limitedh = [ppa for ppa in self.PPAs if ppa.discharge_period == "LimitedH-RTC"]
        ppas_24h = [ppa for ppa in self.PPAs if ppa.discharge_period == "24H-RTC"]

        mo.PPA_24H = pyo.Set(initialize=[p.company_name for p in ppas_24h])
        mo.PPA_LimitedH = pyo.Set(initialize=[p.company_name for p in ppas_limitedh])

        # --------------- Grid Injection Variables --------------
        mo.LimitedH_RTC_inject = pyo.Var(mo.PPA_LimitedH, mo.T, domain=pyo.NonNegativeReals)
        mo.all_day_RTC_inject = pyo.Var(mo.PPA_24H, mo.T, domain=pyo.NonNegativeReals)
        mo.exchange_RTC_inject = pyo.Var(mo.T, domain=pyo.NonNegativeReals)

        # ------------ GSS Injection Loss Variables -------------
        mo.LimitedH_RTC_GSS = pyo.Var(mo.PPA_LimitedH, mo.T, domain=pyo.NonNegativeReals)
        mo.all_day_RTC_GSS = pyo.Var(mo.PPA_24H, mo.T, domain=pyo.NonNegativeReals)
        mo.exchange_RTC_GSS = pyo.Var(mo.T, domain=pyo.NonNegativeReals)

        # ---- Function to initialise parameters of the PPAs ----
        def _ppa_param(ppa_list, attr, scale=1):
            return {ppa.company_name: getattr(ppa, attr) * scale for ppa in ppa_list}

        # ----- Store the Contract Terms of each PPA (24H) ------
        mo.RTC_Quantum_24H = pyo.Param(mo.PPA_24H, initialize=_ppa_param(ppas_24h, "RTC_Quantum", scale=1000))
        mo.Tariff_24H = pyo.Param(mo.PPA_24H, initialize=_ppa_param(ppas_24h, "tariff"))
        mo.MinEnergy_24H = pyo.Param(mo.PPA_24H, initialize=_ppa_param(ppas_24h, "min_contracted_energy"))

        # --- Store the Contract Terms of each PPA (LimitedH) ---
        mo.RTC_Quantum_LimitedH = pyo.Param(mo.PPA_LimitedH, initialize=_ppa_param(ppas_limitedh, "RTC_Quantum", scale=1000))
        mo.Tariff_LimitedH = pyo.Param(mo.PPA_LimitedH, initialize=_ppa_param(ppas_limitedh, "tariff"))
        mo.MinEnergy_LimitedH = pyo.Param(mo.PPA_LimitedH, initialize=_ppa_param(ppas_limitedh, "min_contracted_energy"))

        # --------------------- Revenues ------------------------
        mo.PPA_24H_Revenue = pyo.Var(mo.PPA_24H, domain=pyo.NonNegativeReals)
        mo.PPA_LimitedH_Revenue = pyo.Var(mo.PPA_LimitedH, domain=pyo.NonNegativeReals)
        mo.Exchange_Revenue = pyo.Var(mo.T, domain=pyo.NonNegativeReals)

        # ------------------- Curtailment Tracker ---------------
        mo.Curtailed_Energy = pyo.Var(mo.T, domain=pyo.NonNegativeReals)

    def add_pcs_cap(self):

        mo = self.model

        def pcs_charge_cap(mo, m, d, h):
            return mo.BESS_Charge[m, d, h] <= self.BESS_pcs_cap
        mo.pcs_charge_cap = pyo.Constraint(mo.T, rule=pcs_charge_cap)

        def pcs_discharge_cap(mo, m, d, h):
            return mo.BESS_Discharge[m, d, h] <= self.BESS_pcs_cap
        mo.pcs_discharge_cap = pyo.Constraint(mo.T, rule=pcs_discharge_cap)

    def grid_connectivity(self):

        mo = self.model

        def max_grid_connectivity(mo, m, d, h):
            return (sum(mo.LimitedH_RTC_inject[p, m, d, h] for p in mo.PPA_LimitedH) + 
                    sum(mo.all_day_RTC_inject[p, m, d, h] for p in mo.PPA_24H) + 
                    mo.exchange_RTC_inject[m, d, h]
                     <= self.hybrid_conn)
        mo.max_grid_connectivity = pyo.Constraint(mo.T, rule=max_grid_connectivity)

        def max_limitedh_RTC(mo, ppa, m, d, h):
            if (h > 16) or (h < 9):
                return mo.LimitedH_RTC_inject[ppa, m, d, h] <= mo.RTC_Quantum_LimitedH[ppa]
            else:
                return mo.LimitedH_RTC_inject[ppa, m, d, h]  == 0
        mo.max_limitedh_RTC = pyo.Constraint(mo.PPA_LimitedH, mo.T, rule=max_limitedh_RTC)

        def max_allday_RTC(mo, ppa, m, d, h):
            return mo.all_day_RTC_inject[ppa, m, d, h] <= mo.RTC_Quantum_24H[ppa]
        mo.max_allday_RTC = pyo.Constraint(mo.PPA_24H, mo.T, rule=max_allday_RTC)

    def add_bess_charg_disch(self):

        mo = self.model

        def soc_balance(mo, m, d, h):
            current_time = (m, d, h)

            if current_time == mo.T.first():
                return mo.BESS_SoC[current_time] == (self.initSoC + mo.RTE_ch * mo.BESS_Charge[current_time] 
                                                     - mo.RTE_disch * mo.BESS_Discharge[current_time])
            else:
                prev_time = mo.T.prev(current_time)
                return mo.BESS_SoC[current_time] == (mo.BESS_SoC[prev_time] + mo.RTE_ch * mo.BESS_Charge[current_time] 
                                                     - mo.RTE_disch * mo.BESS_Discharge[current_time])
        mo.soc_balance = pyo.Constraint(mo.T, rule=soc_balance)

    def add_generation_constr(self):
        mo = self.model

        def generation(mo, m, d, h):
            return (mo.WindGen[m, d, h] + mo.SolarGen1[m, d, h] + mo.SolarGen2[m, d, h] + 
                    mo.BESS_Discharge[m, d, h] == 
                    sum(mo.all_day_RTC_inject[p, m, d, h] for p in mo.PPA_24H) + 
                    sum(mo.LimitedH_RTC_inject[p, m, d, h] for p in mo.PPA_LimitedH) + 
                    mo.exchange_RTC_inject[m, d, h]
                    + mo.BESS_Charge[m, d, h] + mo.Curtailed_Energy[m, d, h])
        mo.generation = pyo.Constraint(mo.T, rule=generation)

    def add_pss_gss_loss(self):

        mo = self.model
        def pss_gss_loss_limitedh(mo, p, m, d, h):
            return mo.LimitedH_RTC_GSS[p, m, d, h] == mo.LimitedH_RTC_inject[p, m, d, h] * mo.PSS_GSS_loss
        mo.pss_gss_limitedh = pyo.Constraint(mo.PPA_LimitedH, mo.T, rule=pss_gss_loss_limitedh)

        def pss_gss_loss_all_day(mo, p, m, d, h):
            return mo.all_day_RTC_GSS[p, m, d, h] == mo.all_day_RTC_inject[p, m, d, h] * mo.PSS_GSS_loss
        mo.pss_gss_allday = pyo.Constraint(mo.PPA_24H, mo.T, rule=pss_gss_loss_all_day)

        def pss_gss_loss_exchange(mo, m, d, h):
            return mo.exchange_RTC_GSS[m, d, h] == mo.exchange_RTC_inject[m, d, h] * mo.PSS_GSS_loss
        mo.pss_gss_exchange = pyo.Constraint(mo.T, rule=pss_gss_loss_exchange)

    def add_ppa_minimums(self):

        mo = self.model

        def limitedh_ppa_rule(mo, ppa):
            return sum(mo.LimitedH_RTC_GSS[ppa, m, d, h] for m, d, h in mo.T) >= mo.MinEnergy_LimitedH[ppa]
        mo.limitedh_ppa = pyo.Constraint(mo.PPA_LimitedH, rule=limitedh_ppa_rule)

        def all_day_ppa_rule(mo, ppa):
            return sum(mo.all_day_RTC_GSS[ppa, m, d, h] for m, d, h in mo.T) >= mo.MinEnergy_24H[ppa]
        mo.allday_ppa = pyo.Constraint(mo.PPA_24H, rule=all_day_ppa_rule)

    def add_revenues(self):

        mo = self.model

        def add_limitedh_rev(mo, ppa):
            return mo.PPA_LimitedH_Revenue[ppa] == (sum(mo.LimitedH_RTC_GSS[ppa, m, d, h] for m, d, h in mo.T) 
                                                    * mo.Tariff_LimitedH[ppa])
        mo.limitedh_rev = pyo.Constraint(mo.PPA_LimitedH, rule=add_limitedh_rev)

        def add_24h_rev(mo, ppa):
            return mo.PPA_24H_Revenue[ppa] == (sum(mo.all_day_RTC_GSS[ppa, m, d, h] for m, d, h in mo.T)
                                               * mo.Tariff_24H[ppa])
        mo.all_day_rev = pyo.Constraint(mo.PPA_24H, rule=add_24h_rev)

        def add_exchange_rev(mo, m, d, h):
            return mo.Exchange_Revenue[m, d, h] == mo.exchange_RTC_GSS[m, d, h] * mo.exchange[m, d, h]
        mo.exchange_rev = pyo.Constraint(mo.T, rule=add_exchange_rev)

    def add_objective(self):

        mo = self.model

        def objective(mo):
            return (sum(mo.PPA_LimitedH_Revenue[ppa] for ppa in mo.PPA_LimitedH) +
                    sum(mo.PPA_24H_Revenue[ppa] for ppa in mo.PPA_24H) +
                    sum(mo.Exchange_Revenue[m, d, h] for m, d, h in mo.T))
        mo.rev_maxx = pyo.Objective(rule=objective, sense=pyo.maximize)

    def solve(self, tee: bool = False):

        mo = self.model
        solver = pyo.SolverFactory(self.solver_name)
        self.results = solver.solve(self.model, tee=tee)
        term = str(self.results.solver.termination_condition)
        if term not in ("optimal", "feasible"):
            raise RuntimeError(f"Solver did not reach optimality (termination={term}).")
        return self

    def hourly_dispatch(self):
        mo = self.model
        rows = []
        for m, d, h in mo.T:
            rows.append({
                "Month": m,
                "Day" : d,
                "Hour" : h,
                "Wind Generation": pyo.value(mo.WindGen[m, d, h]),
                "Solar Generation 1": pyo.value(mo.SolarGen1[m, d, h]),
                "Solar Generation 2": pyo.value(mo.SolarGen2[m, d, h]),
                "BESS State of Charge" : pyo.value(mo.BESS_SoC[m, d, h]),
                "BESS Charge": pyo.value(mo.BESS_Charge[m, d, h]),
                "BESS Discharge": pyo.value(mo.BESS_Discharge[m, d, h]),
                "PSS LimitedH Injection": pyo.value(sum(mo.LimitedH_RTC_inject[p, m, d, h] for p in mo.PPA_LimitedH)),
                "PSS 24H Injection" : pyo.value(sum(mo.all_day_RTC_inject[p, m, d, h] for p in mo.PPA_24H)),
                "PSS Exchange Injection" : pyo.value(mo.exchange_RTC_inject[m, d, h]),
                "GSS LimitedH Injection": pyo.value(sum(mo.LimitedH_RTC_GSS[p, m, d, h] for p in mo.PPA_LimitedH)),
                "GSS 24H Injection" : pyo.value(sum(mo.all_day_RTC_GSS[p, m, d, h] for p in mo.PPA_24H)),
                "GSS Exchange Injection" : pyo.value(mo.exchange_RTC_GSS[m, d, h]),
                "Curtailed Energy": pyo.value(mo.Curtailed_Energy[m, d, h]),
                "Total PSS Injection": (pyo.value(sum(mo.LimitedH_RTC_inject[p, m, d, h] for p in mo.PPA_LimitedH))
                                         + pyo.value(sum(mo.all_day_RTC_inject[p, m, d, h] for p in mo.PPA_24H))
                                         + pyo.value(mo.exchange_RTC_inject[m, d, h])),
                "Total GSS Injection": (pyo.value(sum(mo.LimitedH_RTC_GSS[p, m, d, h] for p in mo.PPA_LimitedH)) +
                                        pyo.value(sum(mo.all_day_RTC_GSS[p, m, d, h] for p in mo.PPA_24H)) + 
                                        pyo.value(mo.exchange_RTC_GSS[m, d, h]))
            })
        self._results_df = pd.DataFrame(rows)
        return self._results_df

    def export_to_excel(self, path: str):
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            self.hourly_dispatch().to_excel(writer, sheet_name="Hour-by-Hour Dispatch", index=False)

    def run_plant(self):
        self.build_model()
        self.add_pcs_cap()
        self.grid_connectivity()
        self.add_bess_charg_disch()
        self.add_generation_constr()
        self.add_pss_gss_loss()
        self.add_ppa_minimums()
        self.add_revenues()
        self.add_objective()
        self.solve()
        self.hourly_dispatch()
        self.export_to_excel("Optimised Dispatch.xlsx")
        return pyo.value(self.model.rev_maxx)

if __name__ == "__main__":
    Kudligi = KudligiPerfectOpt("Kudligi_Generation.xlsx", "Kudligi_Exchange.xlsx")
    revenue = Kudligi.run_plant() / (10 ** 6)

    print("Solver termination:", Kudligi.results.solver.termination_condition)
    print(f"Total Revenue: {revenue} Mn INR")

    print("\nSaved: Optimized_BESS_Settlement_Schedule.xlsx")
    



    

        


