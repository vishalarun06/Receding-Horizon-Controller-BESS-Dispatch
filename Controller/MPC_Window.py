import pyomo.environ as pyo
from Config import PlantConfig


class KudligiMPCWindow:

    def __init__(
        self,
        config: PlantConfig,
        window_df,                     # from forecasting.build_forecast_window()
        init_soc_kwh: float,           # battery's real SoC at the start of this window
        ppa_window_requirements: dict, # company_name -> kWh required this window (pacing.py)
        enforce_terminal_soc = True,
    ):
        self.config = config
        self.window_df = window_df.reset_index(drop=True)
        self.init_soc_kwh = init_soc_kwh
        self.ppa_window_requirements = ppa_window_requirements
        self.n_periods = len(self.window_df)
        self.model = None
        self.results = None


        self.enforce_terminal_soc = enforce_terminal_soc


    def build_model(self):
        cfg = self.config
        # Builds model in pyomo
        mo = pyo.ConcreteModel(name="Kudligi MPC Window")
        self.model = mo

        mo.T = pyo.RangeSet(0, self.n_periods - 1)


        wind = self.window_df["WindGen"].to_dict()
        solar1 = self.window_df["SolarGen1"].to_dict()
        solar2 = self.window_df["SolarGen2"].to_dict()
        price = self.window_df["ExchangePrice"].to_dict()

        # Generation and Exchange Parameters
        mo.WindGen = pyo.Param(mo.T, initialize=wind)
        mo.SolarGen1 = pyo.Param(mo.T, initialize=solar1)
        mo.SolarGen2 = pyo.Param(mo.T, initialize=solar2)
        mo.ExchangePrice = pyo.Param(mo.T, initialize=price)


        self._hour_of_day = self.window_df["HourOfDay"].to_dict()

        # Battery efficiency parameter loading
        mo.RTE_ch = pyo.Param(initialize=cfg.rte_ch)
        mo.RTE_disch = pyo.Param(initialize=cfg.rte_disch)
        mo.PSS_GSS = pyo.Param(initialize=cfg.pss_gss_factor)

        # Battery decision variables
        mo.BESS_Charge = pyo.Var(mo.T, domain=pyo.NonNegativeReals)
        mo.BESS_Discharge = pyo.Var(mo.T, domain=pyo.NonNegativeReals)
        mo.BESS_SoC = pyo.Var(mo.T, bounds=(cfg.min_soc_kwh, cfg.max_soc_kwh))

        # PPA contract compliance: same 24H-RTC / LimitedH-RTC
        ppas_24h = [p for p in cfg.ppas if p.discharge_period == "24H-RTC"]
        ppas_limited = [p for p in cfg.ppas if p.discharge_period == "LimitedH-RTC"]
        mo.PPA_24H = pyo.Set(initialize=[p.company_name for p in ppas_24h])
        mo.PPA_LimitedH = pyo.Set(initialize=[p.company_name for p in ppas_limited])

        # Injection at the pooling substation (PSS)
        mo.LimitedH_inject = pyo.Var(mo.PPA_LimitedH, mo.T, domain=pyo.NonNegativeReals)
        mo.AllDay_inject = pyo.Var(mo.PPA_24H, mo.T, domain=pyo.NonNegativeReals)
        mo.Exchange_inject = pyo.Var(mo.T, domain=pyo.NonNegativeReals)

        # After transmission loss, injection at the grid substation (GSS)
        mo.LimitedH_GSS = pyo.Var(mo.PPA_LimitedH, mo.T, domain=pyo.NonNegativeReals)
        mo.AllDay_GSS = pyo.Var(mo.PPA_24H, mo.T, domain=pyo.NonNegativeReals)
        mo.Exchange_GSS = pyo.Var(mo.T, domain=pyo.NonNegativeReals)

        def _ppa_param(ppa_list, attr, scale=1):
            return {p.company_name: getattr(p, attr) * scale for p in ppa_list}

        # RTC Quantum is contracted in MW in the PPA object so converts to kW
        mo.RTC_Quantum_24H = pyo.Param(mo.PPA_24H, initialize=_ppa_param(ppas_24h, "RTC_Quantum", 1000))
        mo.Tariff_24H = pyo.Param(mo.PPA_24H, initialize=_ppa_param(ppas_24h, "tariff"))
        mo.RTC_Quantum_LimitedH = pyo.Param(mo.PPA_LimitedH, initialize=_ppa_param(ppas_limited, "RTC_Quantum", 1000))
        mo.Tariff_LimitedH = pyo.Param(mo.PPA_LimitedH, initialize=_ppa_param(ppas_limited, "tariff"))

        # Per PPA we have a shortfall variable, so that we are penalising when the algorithm is 
        # falling short of the PPAs ensuring that annualy it supplies enough energy
        all_ppa_names = [p.company_name for p in cfg.ppas]
        mo.PPA_Shortfall = pyo.Var(pyo.Set(initialize=all_ppa_names), domain=pyo.NonNegativeReals)

        # Revenue accounting variables
        mo.PPA_24H_Revenue = pyo.Var(mo.PPA_24H, domain=pyo.NonNegativeReals)
        mo.PPA_LimitedH_Revenue = pyo.Var(mo.PPA_LimitedH, domain=pyo.NonNegativeReals)
        mo.Exchange_Revenue = pyo.Var(mo.T, domain=pyo.NonNegativeReals)

        mo.Curtailed = pyo.Var(mo.T, domain=pyo.NonNegativeReals)

    # PCS cap maximum amount of energy that you are able to charge or discharge from BESS in an hour
    def add_pcs_cap(self):
        """Battery inverters (PCS) can only push/pull so much power at once."""
        mo = self.model
        cap = self.config.bess_pcs_cap_kw

        def charge_cap(mo, t):
            return mo.BESS_Charge[t] <= cap
        mo.pcs_charge_cap = pyo.Constraint(mo.T, rule=charge_cap)

        def discharge_cap(mo, t):
            return mo.BESS_Discharge[t] <= cap
        mo.pcs_discharge_cap = pyo.Constraint(mo.T, rule=discharge_cap)

    # Make sure that the battery SoC stays consisten with how much is being charged and discharged
    def add_soc_balance(self):

        mo = self.model

        def soc_balance(mo, t):
            if t == mo.T.first():
                return mo.BESS_SoC[t] == (
                    self.init_soc_kwh
                    + mo.RTE_ch * mo.BESS_Charge[t]
                    - mo.BESS_Discharge[t] / mo.RTE_disch
                )
            else:
                prev = mo.T.prev(t)
                return mo.BESS_SoC[t] == (
                    mo.BESS_SoC[prev]
                    + mo.RTE_ch * mo.BESS_Charge[t]
                    - mo.BESS_Discharge[t] / mo.RTE_disch
                )
        mo.soc_balance = pyo.Constraint(mo.T, rule=soc_balance)

    # Ensures the final SoC is the same as initial SoC so that the 48 hour plan doesn't dump energy at the end of the 48 hours
    def add_terminal_soc(self):

        mo = self.model

        def terminal_rule(mo):
            last = mo.T.last()
            return mo.BESS_SoC[last] >= self.init_soc_kwh
        mo.terminal_soc = pyo.Constraint(rule=terminal_rule)

    # Constraints to show the max grid connectivity from the PPAs
    def add_grid_connectivity(self):

        mo = self.model
        hybrid_conn = self.config.hybrid_conn_kw
        hour_of_day = self._hour_of_day

        def max_grid_connectivity(mo, t):
            return (
                sum(mo.LimitedH_inject[p, t] for p in mo.PPA_LimitedH)
                + sum(mo.AllDay_inject[p, t] for p in mo.PPA_24H)
                + mo.Exchange_inject[t]
                <= hybrid_conn
            )
        mo.max_grid_connectivity = pyo.Constraint(mo.T, rule=max_grid_connectivity)

        def max_limitedh(mo, ppa, t):
            h = hour_of_day[t]
            if (h > 16) or (h < 9):
                return mo.LimitedH_inject[ppa, t] <= mo.RTC_Quantum_LimitedH[ppa]
            else:
                return mo.LimitedH_inject[ppa, t] == 0
        mo.max_limitedh = pyo.Constraint(mo.PPA_LimitedH, mo.T, rule=max_limitedh)

        def max_allday(mo, ppa, t):
            return mo.AllDay_inject[ppa, t] <= mo.RTC_Quantum_24H[ppa]
        mo.max_allday = pyo.Constraint(mo.PPA_24H, mo.T, rule=max_allday)

    # Fundamental constraint that only the generated energy is used and we aren't creating energy
    def add_generation_balance(self):

        mo = self.model

        def generation_balance(mo, t):
            return (
                mo.WindGen[t] + mo.SolarGen1[t] + mo.SolarGen2[t] + mo.BESS_Discharge[t]
                == sum(mo.AllDay_inject[p, t] for p in mo.PPA_24H)
                + sum(mo.LimitedH_inject[p, t] for p in mo.PPA_LimitedH)
                + mo.Exchange_inject[t]
                + mo.BESS_Charge[t]
                + mo.Curtailed[t]
            )
        mo.generation_balance = pyo.Constraint(mo.T, rule=generation_balance)

    # Pooling sub station to grid substation loss
    def add_pss_gss_loss(self):
        mo = self.model

        def limitedh_loss(mo, p, t):
            return mo.LimitedH_GSS[p, t] == mo.LimitedH_inject[p, t] * mo.PSS_GSS
        mo.limitedh_loss = pyo.Constraint(mo.PPA_LimitedH, mo.T, rule=limitedh_loss)

        def allday_loss(mo, p, t):
            return mo.AllDay_GSS[p, t] == mo.AllDay_inject[p, t] * mo.PSS_GSS
        mo.allday_loss = pyo.Constraint(mo.PPA_24H, mo.T, rule=allday_loss)

        def exchange_loss(mo, t):
            return mo.Exchange_GSS[t] == mo.Exchange_inject[t] * mo.PSS_GSS
        mo.exchange_loss = pyo.Constraint(mo.T, rule=exchange_loss)

    # Constraint to pace the PPAs according to the targets from PPA_Pacer.py
    def add_ppa_pacing(self):

        mo = self.model
        all_ppas = [p.company_name for p in self.config.ppas]

        def pacing_rule(mo, ppa):
            if ppa in mo.PPA_24H:
                delivered_this_window = sum(mo.AllDay_GSS[ppa, t] for t in mo.T)
            else:
                delivered_this_window = sum(mo.LimitedH_GSS[ppa, t] for t in mo.T)
            requirement = self.ppa_window_requirements.get(ppa, 0.0)
            return delivered_this_window + mo.PPA_Shortfall[ppa] >= requirement
        mo.ppa_pacing = pyo.Constraint(all_ppas, rule=pacing_rule)

    # Constraint to calculate revenues from PPAs and exchange revenue
    def add_revenues(self):

        mo = self.model

        def limitedh_rev(mo, ppa):
            return mo.PPA_LimitedH_Revenue[ppa] == (
                sum(mo.LimitedH_GSS[ppa, t] for t in mo.T) * mo.Tariff_LimitedH[ppa]
            )
        mo.limitedh_rev = pyo.Constraint(mo.PPA_LimitedH, rule=limitedh_rev)

        def allday_rev(mo, ppa):
            return mo.PPA_24H_Revenue[ppa] == (
                sum(mo.AllDay_GSS[ppa, t] for t in mo.T) * mo.Tariff_24H[ppa]
            )
        mo.allday_rev = pyo.Constraint(mo.PPA_24H, rule=allday_rev)

        def exchange_rev(mo, t):
            return mo.Exchange_Revenue[t] == mo.Exchange_GSS[t] * mo.ExchangePrice[t]
        mo.exchange_rev = pyo.Constraint(mo.T, rule=exchange_rev)

    # Objective function is to maximise revenue
    def add_objective(self):

        mo = self.model
        penalty = self.config.ppa_shortfall_penalty_rs_per_kwh
        all_ppas = [p.company_name for p in self.config.ppas]

        def objective_rule(mo):
            revenue = (
                sum(mo.PPA_LimitedH_Revenue[p] for p in mo.PPA_LimitedH)
                + sum(mo.PPA_24H_Revenue[p] for p in mo.PPA_24H)
                + sum(mo.Exchange_Revenue[t] for t in mo.T)
            )
            shortfall_cost = penalty * sum(mo.PPA_Shortfall[p] for p in all_ppas)
            return revenue - shortfall_cost
        mo.objective = pyo.Objective(rule=objective_rule, sense=pyo.maximize)


    # Build the model, add all the constraints and solves
    def build_and_solve(self, tee: bool = False):

        self.build_model()
        self.add_pcs_cap()
        self.add_soc_balance()
        if self.enforce_terminal_soc:
            self.add_terminal_soc()
        self.add_grid_connectivity()
        self.add_generation_balance()
        self.add_pss_gss_loss()
        self.add_ppa_pacing()
        self.add_revenues()
        self.add_objective()

        solver = pyo.SolverFactory(self.config.solver_name)
        self.results = solver.solve(self.model, tee=tee)
        term = str(self.results.solver.termination_condition)
        if term not in ("optimal", "feasible"):
            raise RuntimeError(
                f"MPC window solve did not reach optimality "
                f"(termination={term}). Window covers absolute hours "
                f"{int(self.window_df['t'].iloc[0])}-{int(self.window_df['t'].iloc[-1])}."
            )
        return self

    # For the rolling horzion we just need to extract the first decision made
    def extract_hour_zero_decision(self) -> dict:

        mo = self.model
        cfg = self.config
        t0 = mo.T.first()  # always 0
        all_ppas = [p.company_name for p in cfg.ppas]

        def ppa_inject(ppa):
            return pyo.value(mo.AllDay_inject[ppa, t0]) if ppa in mo.PPA_24H else pyo.value(mo.LimitedH_inject[ppa, t0])

        def ppa_gss(ppa):
            return pyo.value(mo.AllDay_GSS[ppa, t0]) if ppa in mo.PPA_24H else pyo.value(mo.LimitedH_GSS[ppa, t0])

        return {
            "t_abs": int(self.window_df["t"].iloc[0]),
            "WindGen": pyo.value(mo.WindGen[t0]),
            "SolarGen1": pyo.value(mo.SolarGen1[t0]),
            "SolarGen2": pyo.value(mo.SolarGen2[t0]),
            "ExchangePrice": pyo.value(mo.ExchangePrice[t0]),
            "BESS_Charge": pyo.value(mo.BESS_Charge[t0]),
            "BESS_Discharge": pyo.value(mo.BESS_Discharge[t0]),
            "BESS_SoC": pyo.value(mo.BESS_SoC[t0]),
            "Curtailed": pyo.value(mo.Curtailed[t0]),
            "Exchange_Inject": pyo.value(mo.Exchange_inject[t0]),
            "Exchange_GSS": pyo.value(mo.Exchange_GSS[t0]),
            "PPA_Inject": {ppa: ppa_inject(ppa) for ppa in all_ppas},
            "PPA_GSS": {ppa: ppa_gss(ppa) for ppa in all_ppas},
            "PPA_Shortfall_this_window": {ppa: pyo.value(mo.PPA_Shortfall[ppa]) for ppa in all_ppas},
        }


if __name__ == "__main__":

    from Config import PlantConfig
    from Data_Loader import DataLoader
    from Forecaster import BuildForecast
    from PPA_Pacer import PPA_Pacer

    cfg = PlantConfig()
    gt = DataLoader(cfg)
    ppa_pacing = PPA_Pacer(cfg)
    gt = gt.load_ground_truth()
    ppa_states = ppa_pacing.init_ppa_states()

    test_t = 100
    window = BuildForecast(cfg, gt, test_t, cfg)
    window_end_t = int(window["t"].iloc[-1])

    ppa_pacing.build_pacing_book(window_end_t)
    requirements = ppa_pacing.pacing_book

    model = KudligiMPCWindow(
        config=cfg,
        window_df=window,
        init_soc_kwh=cfg.min_soc_kwh,
        ppa_window_requirements=requirements,
    )
    model.build_and_solve()
    decision = model.extract_hour_zero_decision()

    print(f"Solved window standing at hour {test_t}. Hour-0 decision:")
    for k, v in decision.items():
        print(f"  {k}: {v}")
