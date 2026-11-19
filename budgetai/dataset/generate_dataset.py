"""Generates the BudgetAI dataset: realistic Philippine household scenarios,
each with the expected budget calculated by the budgeting rules.

Run from the BudgetAI_Model folder (virtual environment active):
    python dataset/generate_dataset.py

The same SEED always produces exactly the same files, so the dataset can be
rebuilt at any time. Output:
    dataset/budgeting_examples.jsonl   300 scenarios for examples and tuning
    dataset/test_scenarios.jsonl       100 held-out scenarios + 10 hand-checked
                                       "golden" scenarios, for fair evaluation

Income bands are approximate 2026 equivalents (about +30% for inflation) of
the PIDS family-income brackets for a family of five (2018 prices); see
dataset/README.md for the sources.
"""
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from budgeting_engine.calculations import summarize  # noqa: E402
from budgeting_engine.planner import make_plan       # noqa: E402
from model.schemas import FinancialProfile           # noqa: E402

SEED = 2026
TRAIN_FILE = ROOT / "dataset" / "budgeting_examples.jsonl"
TEST_FILE = ROOT / "dataset" / "test_scenarios.jsonl"

# ------------------------------------------------------------------ realistic ranges (PHP per month)
INCOME_BANDS = {
    "low": (14000, 28000),
    "lower_middle": (28000, 57000),
    "middle": (57000, 100000),
    "upper_middle": (100000, 170000),
    "high": (170000, 300000),
}
BAND_LABEL = {"low": "low", "lower_middle": "lower-middle", "middle": "middle",
              "upper_middle": "upper-middle", "high": "high"}
BAND_ORDER = list(INCOME_BANDS)
RENT_PROB = {"low": .55, "lower_middle": .60, "middle": .55, "upper_middle": .45, "high": .35}
RENT_SHARE = {"low": (.15, .28), "lower_middle": (.14, .26), "middle": (.12, .22), "upper_middle": (.10, .18), "high": (.07, .14)}
FOOD_PER_PERSON = {"low": (1800, 2800), "lower_middle": (2500, 3800), "middle": (3500, 5500),
                   "upper_middle": (5000, 8000), "high": (7000, 12000)}
ELECTRICITY = {"low": (900, 2000), "lower_middle": (1800, 3500), "middle": (3000, 6000),
               "upper_middle": (5000, 10000), "high": (8000, 18000)}
WATER = {"low": (300, 700), "lower_middle": (500, 1000), "middle": (700, 1500), "upper_middle": (1000, 2000), "high": (1500, 3000)}
INTERNET = {"low": (.4, 999, 1299), "lower_middle": (.75, 1299, 1699), "middle": (.95, 1499, 2699),
            "upper_middle": (1, 1999, 3499), "high": (1, 2499, 4999)}
LOAD_PER_ADULT = {"low": (200, 500), "lower_middle": (300, 700), "middle": (500, 1200), "upper_middle": (999, 1999), "high": (1499, 2999)}
COMMUTE_PER_EARNER = {"low": (1200, 3000), "lower_middle": (2000, 4000), "middle": (3000, 5000)}
CAR = {"middle": (.4, 5000, 10000), "upper_middle": (.7, 8000, 15000), "high": (.9, 12000, 25000)}
HEALTH = {"low": (300, 1500), "lower_middle": (800, 2500), "middle": (1500, 4000), "upper_middle": (3000, 7000), "high": (5000, 12000)}
SCHOOL_PER_CHILD = {"low": (1000, 2500), "lower_middle": (1500, 4500), "middle": (3000, 9000),
                    "upper_middle": (8000, 18000), "high": (15000, 35000)}
PADALA = {"low": (500, 2000), "lower_middle": (1000, 4000), "middle": (2000, 7000), "upper_middle": (4000, 12000), "high": (6000, 20000)}
LIFE_INSURANCE = {"middle": (.4, 1500, 4000), "upper_middle": (.6, 3000, 8000), "high": (.7, 5000, 15000)}
WANTS_SHARE = {"frugal": (.02, .06), "moderate": (.07, .14), "spender": (.15, .28)}
BAND_SCALE = {"low": .4, "lower_middle": .7, "middle": 1.0, "upper_middle": 1.6, "high": 2.4}

CITIES = ["Quezon City", "Manila", "Caloocan", "Pasig", "Taguig", "Makati", "Cebu City", "Davao City", "Iloilo City",
          "Bacolod", "Cagayan de Oro", "Baguio", "Antipolo", "Dasmarinas", "Calamba", "General Santos", "Zamboanga City",
          "Batangas City", "Angeles", "San Fernando, Pampanga", "Tacloban", "Lipa", "Naga", "Iligan", "Valenzuela"]
SURNAMES = ["Santos", "Reyes", "Cruz", "Bautista", "Ocampo", "Garcia", "Mendoza", "Torres", "Flores", "Villanueva",
            "Ramos", "Aquino", "Castillo", "Rivera", "Navarro", "Dizon", "Pascual", "Salazar", "Domingo", "Fernandez",
            "Lim", "Tan", "Gonzales", "Mercado", "Soriano", "Manalo", "Aguilar", "Del Rosario", "De Leon", "Panganiban"]
FIRST_NAMES = ["Ana", "Mark", "Joy", "Paolo", "Kim", "Rica", "JM", "Bea", "Carlo", "Liza", "Migs", "Trish", "Jerome", "Nica"]
IRREGULAR_SOURCES = ["Freelance projects", "Sari-sari store sales", "Tricycle driving", "Online selling",
                     "Remittance from abroad", "Real estate commissions", "Ride-hailing driving", "Construction work"]
WANT_ITEMS = [("Dining out and food delivery", "dining_out"), ("Online shopping", "shopping"), ("Clothes", "shopping"),
              ("Movies and outings", "entertainment"), ("Netflix and Spotify", "subscriptions"),
              ("Mobile games", "entertainment"), ("Salon and grooming", "personal_care"), ("Coffee shops", "dining_out"),
              ("Hobbies", "other")]
GOALS = [("New laptop", 30000, 80000), ("Phone upgrade", 15000, 60000), ("Family vacation", 20000, 150000),
         ("Christmas fund", 10000, 40000), ("Tuition for next school year", 20000, 120000),
         ("House down payment", 150000, 800000), ("Wedding", 150000, 600000), ("Motorcycle down payment", 15000, 40000),
         ("Business capital", 20000, 150000), ("Baby on the way", 30000, 100000), ("Car down payment", 100000, 400000)]
HIGH_INTEREST_DEBTS = {"credit_card", "lending_app", "personal_loan", "motorcycle"}
DEBTS_BY_BAND = {
    "low": ["lending_app", "sss_loan", "motorcycle", "family_utang", "credit_card"],
    "lower_middle": ["credit_card", "lending_app", "sss_loan", "pagibig_mpl", "personal_loan", "motorcycle", "family_utang"],
    "middle": ["credit_card", "pagibig_mpl", "personal_loan", "car_loan", "housing_loan", "sss_loan"],
    "upper_middle": ["credit_card", "personal_loan", "car_loan", "housing_loan", "pagibig_mpl"],
    "high": ["credit_card", "car_loan", "housing_loan", "personal_loan"],
}


def round_to(value, step=50):
    return int(round(value / step) * step)


def goal_name_for(target, rng):
    """A goal name that fits the size of the target."""
    if target >= 300000:
        return rng.choice(["House down payment", "Car down payment", "Wedding", "Business capital"])
    if target >= 80000:
        return rng.choice(["Family vacation", "Tuition for next school year", "Baby on the way", "Business capital"])
    return rng.choice(["New laptop", "Phone upgrade", "Christmas fund", "Motorcycle down payment"])


# ------------------------------------------------------------------ building blocks
class Builder:
    """Creates one household profile with realistic, internally consistent numbers."""

    def __init__(self, rng: random.Random):
        self.rng = rng

    def u(self, low, high):
        return self.rng.uniform(low, high)

    def household(self, band, size=None, habit=None, irregular=False, debt_mode="random", goal_mode="random",
                  ef_mode="random", fixed_level="normal", renting=None):
        r = self.rng
        if size is None:
            size = r.choice([1] + [2, 3, 4, 5, 6, 7] * 2) if band in ("low", "lower_middle") else r.choice([1, 2, 3, 4, 5, 6])
        adults = 1 if size == 1 else min(size, r.choice([2, 2, 2, 3]))
        earners = 1 if adults == 1 else r.choice([1, 2, 2] if band != "low" else [1, 1, 2])
        habit = habit or r.choice(["frugal", "moderate", "moderate", "spender"])
        total = round_to(self.u(*INCOME_BANDS[band]), 100)

        incomes = self.incomes(total, earners, irregular)
        spend_base = total
        if irregular:   # people with irregular income spend somewhere between their low and usual month
            low_total = sum(i.get("minimum_monthly_amount", i["monthly_amount"]) for i in incomes)
            spend_base = round_to(self.u(low_total, total), 100)
        renting = (r.random() < RENT_PROB[band]) if renting is None else renting
        expenses = self.expenses(band, spend_base, size, adults, earners, habit, fixed_level, renting, irregular)
        debts = self.debts(band, debt_mode, renting)
        profile = {"incomes": incomes, "expenses": expenses, "debts": debts, "household_size": size,
                   "location": r.choice(CITIES)}
        facts = summarize(FinancialProfile.model_validate(profile))
        profile["emergency_fund_balance"] = self.emergency_fund(ef_mode, facts.required_spending)
        profile["savings_goals"] = self.goals(band, goal_mode, facts)
        meta = {"band": band, "size": size, "habit": habit,
                "name": r.choice(FIRST_NAMES) if size == 1 else r.choice(SURNAMES)}
        return profile, meta

    def incomes(self, total, earners, irregular):
        r = self.rng
        shares = [total] if earners == 1 else [round_to(total * self.u(.5, .7), 100)]
        if earners == 2:
            shares.append(total - shares[0])
        names = ["Main salary", "Spouse's salary"]
        out = []
        for i, amount in enumerate(shares):
            if irregular and i == 0:
                out.append({"name": r.choice(IRREGULAR_SOURCES), "monthly_amount": amount, "regular": False,
                            "minimum_monthly_amount": round_to(amount * self.u(.4, .8), 100)})
            else:
                out.append({"name": names[i], "monthly_amount": amount})
        return out

    def expenses(self, band, total, size, adults, earners, habit, fixed_level, renting, self_employed):
        r = self.rng
        out = []

        def add(name, category, amount, **extra):
            if amount > 0:
                out.append({"name": name, "category": category, "monthly_amount": round_to(amount), **extra})

        if renting:
            share = self.u(*RENT_SHARE[band]) * (1.5 if fixed_level == "high" else 1)
            add(r.choice(["Rent", "Apartment rent", "House rent"] if size > 1 else ["Room rent", "Bedspace rent"]),
                "housing", total * share)
        elif band in ("middle", "upper_middle", "high") and r.random() < .5:
            add("Association dues", "housing", self.u(500, 3000))
        add(r.choice(["Groceries", "Groceries and palengke", "Food and groceries"]), "food",
            self.u(*FOOD_PER_PERSON[band]) * size)
        add("Electricity", "utilities", self.u(*ELECTRICITY[band]) * (0.8 + 0.1 * size))
        add("Water", "utilities", self.u(*WATER[band]))
        prob, low, high = INTERNET[band]
        if r.random() < prob:
            add("Internet", "utilities", self.u(low, high))
        add("LPG", "utilities", self.u(1000, 1400) if band in ("low", "lower_middle") else self.u(1200, 2400))
        add("Mobile load and plans", "utilities", self.u(*LOAD_PER_ADULT[band]) * adults)
        car = CAR.get(band)
        if car and r.random() < car[0]:
            add("Car fuel, parking and tolls", "transportation", self.u(car[1], car[2]))
        else:
            add("Commute fare", "transportation", self.u(*COMMUTE_PER_EARNER.get(band, (3000, 6000))) * earners)
        add("Medicines and check-ups", "health", self.u(*HEALTH[band]))
        kids = size - adults
        if kids > 0:
            per_child = self.u(*SCHOOL_PER_CHILD[band]) * (1.8 if fixed_level == "high" else 1)
            add("Tuition and school fees" if per_child > 4000 else "Baon and school supplies", "education", per_child * kids)
        if self_employed:
            add("SSS, PhilHealth and Pag-IBIG (voluntary)", "insurance_contributions", self.u(800, 2500))
        life = LIFE_INSURANCE.get(band)
        if life and r.random() < life[0]:
            add("Life insurance premium", "insurance_contributions", self.u(life[1], life[2]))
        if r.random() < .5:
            add(r.choice(["Padala to parents", "Padala sa probinsya", "Allowance for parents"]), "family_support",
                self.u(*PADALA[band]))
        if band in ("upper_middle", "high") and r.random() < .5:
            add("Kasambahay salary", "other", self.u(7800, 10000), kind="obligation", fixed=True)
        wants_total = total * self.u(*WANTS_SHARE[habit])
        items = r.sample(WANT_ITEMS, r.randint(2, 5))
        weights = [self.u(1, 3) for _ in items]
        for (name, category), weight in zip(items, weights):
            add(name, category, wants_total * weight / sum(weights))
        return out

    def debts(self, band, mode, renting):
        r = self.rng
        if mode == "none" or (mode == "random" and r.random() < .45):
            return []
        kinds = list(DEBTS_BY_BAND[band])
        if renting:
            kinds = [k for k in kinds if k != "housing_loan"]
        chosen = r.sample(kinds, min(len(kinds), r.choice([1, 1, 2, 3])))
        if mode == "high" and not HIGH_INTEREST_DEBTS & set(chosen):
            chosen[0] = r.choice([k for k in kinds if k in HIGH_INTEREST_DEBTS])
        return [self.debt(kind, band) for kind in chosen]

    def debt(self, kind, band):
        r, scale = self.rng, BAND_SCALE[band]
        if kind == "credit_card":
            balance = round_to(self.u(10000, 150000) * scale, 100)
            return {"name": "Credit card", "balance": balance, "minimum_payment": round_to(max(500, balance * .03)),
                    "annual_interest_rate_percent": 36}
        if kind == "lending_app":
            balance = round_to(self.u(3000, 25000), 100)
            debt = {"name": r.choice(["Online lending app", "Loan app", "Cash loan app"]), "balance": balance,
                    "minimum_payment": round_to(balance * self.u(.15, .3))}
            debt["annual_interest_rate_percent"] = r.choice([120, 180, 240])
            return debt
        table = {
            "sss_loan": ("SSS salary loan", (8000, 40000), (800, 2000), 10),
            "pagibig_mpl": ("Pag-IBIG multi-purpose loan", (15000, 80000), (900, 3000), 10.5),
            "housing_loan": ("Pag-IBIG housing loan", (400000, 2500000), (5000, 22000), 6.25),
            "car_loan": ("Car loan", (250000, 1200000), (9000, 28000), 9),
            "personal_loan": ("Personal bank loan", (50000, 300000), (3000, 12000), 24),
            "motorcycle": ("Motorcycle installment", (30000, 90000), (2500, 5000), 25),
            "family_utang": ("Utang to relatives", (5000, 40000), (500, 2000), 0),
        }
        name, balance_range, payment_range, rate = table[kind]
        balance = round_to(self.u(*balance_range), 100)
        payment = min(round_to(self.u(*payment_range)), balance)
        return {"name": name, "balance": balance, "minimum_payment": payment, "annual_interest_rate_percent": rate}

    def emergency_fund(self, mode, required):
        r = self.rng
        if mode == "none" or required <= 0:
            return 0
        if mode == "healthy":
            months = self.u(6.5, 12)
        else:
            months = r.choice([0, 0, self.u(.1, .9), self.u(.1, .9), self.u(1, 3), self.u(1, 3), self.u(3, 6), self.u(6, 12)])
        return round_to(months * required, 500)

    def goals(self, band, mode, facts):
        r = self.rng
        if mode == "none" or (mode == "random" and r.random() < .5):
            return []
        out = []
        for name, low, high in r.sample(GOALS, 1 if mode != "many" else 4):
            target = round_to(self.u(low, high) * BAND_SCALE[band] / 1.0, 1000)
            months = r.choice([3, 6, 8, 12, 12, 18, 24, 36, None])
            if mode == "aggressive":
                months = r.choice([3, 4, 6, 8])
                available = max(facts.balance_after_required, 1000)
                target = round_to(available * self.u(1.0, 2.5) * months, 1000)
                name = goal_name_for(target, r)
            if mode == "many" and months is None:
                months = 12
            goal = {"name": name, "target_amount": max(target, 1000),
                    "current_amount": round_to(target * self.u(0, .4), 500)}
            if months:
                goal["months_left"] = months
            out.append(goal)
        return out


# ------------------------------------------------------------------ scenario types
def titled(meta, extra):
    where = f"in {meta['city']}"
    who = (f"{meta['name']}, living alone {where}" if meta["size"] == 1
           else f"{meta['name']} family of {meta['size']} {where}")
    return f"{who}: {BAND_LABEL[meta['band']]} income, {extra}"


def accept(profile):
    return summarize(FinancialProfile.model_validate(profile))


def make_scenario(rng, kind, index):
    """Build one scenario of the given kind. Returns (profile, title)."""
    b = Builder(rng)
    for _ in range(200):
        if kind == "low_income":
            profile, meta = b.household("low")
            facts, extra = accept(profile), "making ends meet"
            if facts.is_deficit:
                continue
        elif kind == "middle_income":
            profile, meta = b.household(rng.choice(["lower_middle", "middle"]))
            facts, extra = accept(profile), "typical middle-class budget"
            if facts.is_deficit:
                continue
        elif kind == "high_income":
            profile, meta = b.household(rng.choice(["upper_middle", "high"]))
            facts, extra = accept(profile), "comfortable income"
            if facts.is_deficit:
                continue
        elif kind == "debt_heavy":
            profile, meta = b.household(rng.choice(BAND_ORDER), debt_mode="high")
            facts, extra = accept(profile), "carrying high-interest debt"
            if facts.is_deficit:
                continue
        elif kind == "irregular_income":
            profile, meta = b.household(rng.choice(BAND_ORDER[:4]), irregular=True)
            facts, extra = accept(profile), "irregular income"
            if index % 2 == 0 and facts.is_deficit:   # keep a balanced mix of deficit and non-deficit cases
                continue
        elif kind == "high_fixed_expenses":
            profile, meta = b.household(rng.choice(BAND_ORDER), fixed_level="high", renting=True,
                                        size=rng.choice([3, 4, 5, 6]))
            facts, extra = accept(profile), "high fixed expenses (rent and tuition)"
            if facts.is_deficit or facts.fixed_expenses < .45 * facts.planning_income:
                continue
        elif kind == "aggressive_savings":
            profile, meta = b.household(rng.choice(BAND_ORDER[1:]), goal_mode="aggressive")
            facts, extra = accept(profile), "aggressive savings goal"
            if facts.is_deficit or facts.balance_after_required < 2000:
                continue
        elif kind == "deficit":
            profile, meta = b.household(rng.choice(BAND_ORDER[:3]))
            facts = accept(profile)
            if not facts.is_deficit:
                target = facts.planning_income * rng.uniform(1.03, 1.35) - facts.minimum_debt_payments
                core = facts.essential_expenses + facts.obligation_expenses
                if target <= 0 or core <= 0:
                    continue
                factor = target / core
                for e in profile["expenses"]:
                    if e["category"] in ("housing", "food", "utilities", "transportation", "health", "education",
                                         "insurance_contributions", "family_support"):
                        e["monthly_amount"] = round_to(e["monthly_amount"] * factor)
                facts = accept(profile)
            extra = "income does not cover essentials"
            if not facts.is_deficit:
                continue
        elif kind == "spending_habits":
            habit = "frugal" if index % 2 == 0 else "spender"
            profile, meta = b.household(rng.choice(BAND_ORDER), habit=habit)
            facts, extra = accept(profile), f"{habit} spender"
            if facts.is_deficit:
                continue
        elif kind == "incomplete":
            variant = index % 4
            profile, meta = b.household(rng.choice(BAND_ORDER[:3]))
            if variant == 0:
                profile = {"incomes": profile["incomes"], "location": profile["location"]}
                extra = "only income is known"
            elif variant == 1:
                profile.pop("household_size")
                profile["debts"] = [b.debt("lending_app", meta["band"])]
                profile["debts"][0].pop("annual_interest_rate_percent")
                extra = "household size and an interest rate are missing"
            elif variant == 2:
                profile["incomes"][0] = {"name": rng.choice(IRREGULAR_SOURCES),
                                         "monthly_amount": profile["incomes"][0]["monthly_amount"], "regular": False}
                extra = "irregular income without its low month"
            else:
                profile["savings_goals"] = [{"name": "Family vacation", "target_amount": 50000, "current_amount": 5000}]
                extra = "a savings goal without a deadline"
            facts = accept(profile)
        elif kind == "edge_case":
            profile, meta, extra = edge_case(b, rng, index % 10)
            facts = accept(profile)
        else:
            raise ValueError(kind)
        meta["city"] = profile.get("location", "an unnamed city")
        return profile, titled(meta, extra)
    raise RuntimeError(f"could not build a '{kind}' scenario")


def edge_case(b, rng, variant):
    """Unusual but valid households that test the edges of the rules."""
    if variant == 0:
        profile, meta = b.household("middle", debt_mode="none", goal_mode="none", ef_mode="healthy")
        return profile, meta, "6+ months of emergency fund and no debt"
    if variant == 1:
        profile, meta = b.household("lower_middle")
        profile["expenses"] = [e for e in profile["expenses"] if e["category"] not in
                               ("dining_out", "shopping", "entertainment", "subscriptions", "personal_care", "other")]
        return profile, meta, "no non-essential spending at all"
    if variant == 2:
        profile, meta = b.household("low", debt_mode="none", goal_mode="none")
        required = accept(profile).required_spending
        profile["incomes"] = [{"name": "Main salary", "monthly_amount": required}]
        return profile, meta, "income exactly equals required spending"
    if variant == 3:
        profile, meta = b.household("upper_middle", goal_mode="many")
        return profile, meta, "four savings goals at once"
    if variant == 4:
        profile, meta = b.household("middle", renting=False, debt_mode="none", goal_mode="none")
        required = accept(profile).required_spending + 15000
        profile["debts"] = [{"name": "Pag-IBIG housing loan", "balance": 1800000, "minimum_payment": 15000,
                             "annual_interest_rate_percent": 6.25}]
        profile["emergency_fund_balance"] = round_to(required * 3, 500)
        return profile, meta, "low-interest housing loan only"
    if variant == 5:
        profile, meta = b.household("lower_middle")
        profile["expenses"].append({"name": "Padala to parents (non-negotiable)", "category": "family_support",
                                    "monthly_amount": 3000, "kind": "essential"})
        profile["expenses"].append({"name": "Gym (doctor's advice)", "category": "entertainment",
                                    "monthly_amount": 1500, "kind": "essential"})
        return profile, meta, "user marked some expenses as essential"
    if variant == 6:
        profile = {"incomes": [{"name": "Minimum-wage salary (755 x 26 days)", "monthly_amount": 19630}],
                   "expenses": [{"name": "Bedspace rent", "category": "housing", "monthly_amount": round_to(b.u(2500, 4500))},
                                {"name": "Food", "category": "food", "monthly_amount": round_to(b.u(5000, 7000))},
                                {"name": "Commute fare", "category": "transportation", "monthly_amount": round_to(b.u(1800, 3000))},
                                {"name": "Mobile load", "category": "utilities", "monthly_amount": round_to(b.u(300, 600))},
                                {"name": "Padala sa probinsya", "category": "family_support", "monthly_amount": round_to(b.u(2000, 4000))},
                                {"name": "Milk tea and snacks", "category": "dining_out", "monthly_amount": round_to(b.u(600, 1800))}],
                   "household_size": 1, "location": rng.choice(["Manila", "Quezon City", "Caloocan", "Pasig", "Valenzuela"]),
                   "emergency_fund_balance": rng.choice([0, 0, 2000, 5000])}
        return profile, {"band": "low", "size": 1, "name": rng.choice(FIRST_NAMES)}, "NCR minimum-wage earner"
    if variant == 7:
        profile, meta = b.household("lower_middle", debt_mode="none")
        profile["debts"] = [{"name": "Loan from a friend's app", "balance": 12000, "minimum_payment": 2000}]
        return profile, meta, "a debt with an unknown interest rate"
    if variant == 8:
        profile, meta = b.household("middle")
        profile["expenses"].append({"name": "Paused gym membership", "category": "entertainment", "monthly_amount": 0})
        profile["expenses"].append({"name": "Water refill (shared, paid by landlord)", "category": "food", "monthly_amount": 0})
        return profile, meta, "some expenses are zero this month"
    profile, meta = b.household("high", habit="spender", ef_mode="healthy")
    return profile, meta, "big spender with a healthy emergency fund"


SCENARIO_COUNTS = {   # (train, test)
    "low_income": (30, 10), "middle_income": (30, 10), "high_income": (30, 10), "debt_heavy": (30, 10),
    "irregular_income": (30, 10), "high_fixed_expenses": (30, 10), "aggressive_savings": (30, 10),
    "deficit": (30, 10), "spending_habits": (30, 10), "incomplete": (15, 7), "edge_case": (15, 5),
}


# ------------------------------------------------------------------ golden scenarios (amounts worked out by hand)
GOLDEN = [
    ("High urgency: emergency fund under 1 month and a 36% credit card", {
        "incomes": [{"name": "Salary", "monthly_amount": 30000}],
        "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 10000},
                     {"name": "Food", "category": "food", "monthly_amount": 8000},
                     {"name": "Tuition", "category": "education", "monthly_amount": 2000},
                     {"name": "Dining out", "category": "dining_out", "monthly_amount": 3000},
                     {"name": "Netflix", "category": "subscriptions", "monthly_amount": 500}],
        "debts": [{"name": "Credit card", "balance": 20000, "minimum_payment": 1000, "annual_interest_rate_percent": 36}],
        "savings_goals": [{"name": "Laptop", "target_amount": 30000, "current_amount": 6000, "months_left": 8}],
        "emergency_fund_balance": 5000, "household_size": 3},
     # required 21000; left 9000; wants min(3500, 40% of 9000) = 3500 kept; future 5500 split 2750 / 2750
     {"Emergency fund": 2750, "Extra payment: Credit card": 2750, "Dining out": 3000, "Netflix": 500}),
    ("Santos family: wants lowered 46%", {
        "incomes": [{"name": "Juan's salary", "monthly_amount": 32000}, {"name": "Maria's salary", "monthly_amount": 18000}],
        "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 12000},
                     {"name": "Groceries and palengke", "category": "food", "monthly_amount": 11000},
                     {"name": "Meralco", "category": "utilities", "monthly_amount": 3200},
                     {"name": "Water", "category": "utilities", "monthly_amount": 600},
                     {"name": "Internet", "category": "utilities", "monthly_amount": 1699},
                     {"name": "Fare and gas", "category": "transportation", "monthly_amount": 3500},
                     {"name": "Tuition installment", "category": "education", "monthly_amount": 3000},
                     {"name": "Padala to parents", "category": "family_support", "monthly_amount": 2000},
                     {"name": "Dining out and food delivery", "category": "dining_out", "monthly_amount": 4000},
                     {"name": "Netflix and Spotify", "category": "subscriptions", "monthly_amount": 700},
                     {"name": "Online shopping", "category": "shopping", "monthly_amount": 3000}],
        "debts": [{"name": "Credit card", "balance": 65000, "minimum_payment": 2600, "annual_interest_rate_percent": 36}],
        "emergency_fund_balance": 10000, "household_size": 4, "location": "Quezon City"},
     # left 10401; wants 40% = 4160.40 shared by 4000/700/3000 (+2 spare centavos); future 6240.60 split in half
     {"Emergency fund": 3120.30, "Extra payment: Credit card": 3120.30, "Dining out and food delivery": 2161.25,
      "Netflix and Spotify": 378.22, "Online shopping": 1620.93}),
    ("Medium urgency: goal first, then emergency fund to 3 months", {
        "incomes": [{"name": "Salary", "monthly_amount": 40000}],
        "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 12000},
                     {"name": "Food", "category": "food", "monthly_amount": 8000},
                     {"name": "Dining out", "category": "dining_out", "monthly_amount": 5000}],
        "savings_goals": [{"name": "Trip", "target_amount": 24000, "months_left": 12}],
        "emergency_fund_balance": 40000, "household_size": 2},
     # left 20000; wants 5000 kept; future 15000: goal 2000/month, emergency fund 13000
     {"Savings goal: Trip": 2000, "Emergency fund": 13000, "Dining out": 5000}),
    ("Low urgency: wants kept, the rest saved", {
        "incomes": [{"name": "Salary", "monthly_amount": 50000}],
        "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 15000},
                     {"name": "Food", "category": "food", "monthly_amount": 10000},
                     {"name": "Dining out", "category": "dining_out", "monthly_amount": 4000}],
        "emergency_fund_balance": 200000, "household_size": 2},
     {"Dining out": 4000, "Extra savings": 21000}),
    ("Low-interest loan after 3 months of emergency fund", {
        "incomes": [{"name": "Salary", "monthly_amount": 60000}],
        "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 20000},
                     {"name": "Food", "category": "food", "monthly_amount": 10000}],
        "debts": [{"name": "Pag-IBIG loan", "balance": 100000, "minimum_payment": 1000, "annual_interest_rate_percent": 6}],
        "emergency_fund_balance": 93000, "household_size": 2},
     {"Extra payment: Pag-IBIG loan": 29000}),
    ("Deficit: essentials whole, obligations at 50%", {
        "incomes": [{"name": "Salary", "monthly_amount": 19500}],
        "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 10000},
                     {"name": "Food", "category": "food", "monthly_amount": 8000},
                     {"name": "Tuition", "category": "education", "monthly_amount": 2000},
                     {"name": "Dining out", "category": "dining_out", "monthly_amount": 3000}],
        "debts": [{"name": "Credit card", "balance": 20000, "minimum_payment": 1000, "annual_interest_rate_percent": 36}],
        "household_size": 3},
     {"Rent": 10000, "Food": 8000, "Tuition": 1000, "Credit card (minimum payment)": 500}),
    ("Deficit: even essentials only at 50%", {
        "incomes": [{"name": "Salary", "monthly_amount": 9000}],
        "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 10000},
                     {"name": "Food", "category": "food", "monthly_amount": 8000},
                     {"name": "Tuition", "category": "education", "monthly_amount": 2000}],
        "debts": [{"name": "Credit card", "balance": 20000, "minimum_payment": 1000, "annual_interest_rate_percent": 36}],
        "household_size": 3},
     {"Rent": 5000, "Food": 4000, "Tuition": 0, "Credit card (minimum payment)": 0}),
    ("Reyes household: essentials at 91%", {
        "incomes": [{"name": "Salary", "monthly_amount": 16000}],
        "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 5500},
                     {"name": "Food", "category": "food", "monthly_amount": 7500},
                     {"name": "Electricity and water", "category": "utilities", "monthly_amount": 2200},
                     {"name": "Mobile load", "category": "utilities", "monthly_amount": 300},
                     {"name": "Jeep and tricycle fare", "category": "transportation", "monthly_amount": 2000},
                     {"name": "Padala sa probinsya", "category": "family_support", "monthly_amount": 1500},
                     {"name": "Snacks and fast food", "category": "dining_out", "monthly_amount": 800}],
        "debts": [{"name": "Online lending app", "balance": 8000, "minimum_payment": 1600, "annual_interest_rate_percent": 120}],
        "household_size": 3, "location": "Caloocan"},
     # 16000 / 17500 of essentials = 91.43%, rounded down to centavos, +2 spare centavos to the first lines
     {"Rent": 5028.58, "Food": 6857.15, "Electricity and water": 2011.42, "Mobile load": 274.28,
      "Jeep and tricycle fare": 1828.57, "Padala sa probinsya": 0, "Online lending app (minimum payment)": 0}),
    ("Ana: irregular income, wants lowered 92%", {
        "incomes": [{"name": "Freelance projects", "monthly_amount": 38000, "regular": False, "minimum_monthly_amount": 22000}],
        "expenses": [{"name": "Room rent", "category": "housing", "monthly_amount": 9000},
                     {"name": "Food", "category": "food", "monthly_amount": 6000},
                     {"name": "Electricity and internet", "category": "utilities", "monthly_amount": 2500},
                     {"name": "Transportation", "category": "transportation", "monthly_amount": 1500},
                     {"name": "PhilHealth and Pag-IBIG (voluntary)", "category": "insurance_contributions", "monthly_amount": 700},
                     {"name": "Design software subscription", "category": "subscriptions", "monthly_amount": 1200, "kind": "essential"},
                     {"name": "Coffee shops and dining", "category": "dining_out", "monthly_amount": 3500},
                     {"name": "Shopping", "category": "shopping", "monthly_amount": 2000}],
        "savings_goals": [{"name": "New laptop for work", "target_amount": 60000, "current_amount": 15000, "months_left": 8}],
        "emergency_fund_balance": 20000, "household_size": 1, "location": "Cebu City"},
     # planning income 22000; left 1100; wants 40% = 440 (280 + 160); emergency fund 660; laptop gets nothing
     {"Emergency fund": 660, "Coffee shops and dining": 280, "Shopping": 160}),
    ("Only income is known", {"incomes": [{"name": "Salary", "monthly_amount": 25000}]}, {}),
]


# ------------------------------------------------------------------ writing the files
def profile_hash(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()[:16]


def record(scenario_id, split, kind, title, profile, hand_checked=None):
    plan = make_plan(profile)
    rec = {"id": scenario_id, "split": split, "scenario_type": kind, "title": title,
           "rules_version": plan.rules_version, "profile": profile,
           "expected": plan.model_dump(mode="json", exclude={"rules_version"})}
    if hand_checked is not None:
        rec["hand_checked"] = hand_checked
    return rec


def generate(seed=SEED):
    rng = random.Random(seed)
    train, test, seen = [], [], set()
    number = 0
    for kind, (n_train, n_test) in SCENARIO_COUNTS.items():
        made = duplicates = 0
        while made < n_train + n_test:
            profile, title = make_scenario(rng, kind, made + duplicates)
            key = profile_hash(profile)
            if key in seen:
                duplicates += 1
                if duplicates > 50:
                    raise RuntimeError(f"too many duplicate '{kind}' scenarios")
                continue
            seen.add(key)
            number += 1
            split = "train" if made < n_train else "test"
            (train if split == "train" else test).append(record(f"BA-{number:04d}", split, kind, title, profile))
            made += 1
    for i, (title, profile, amounts) in enumerate(GOLDEN, 1):
        test.append(record(f"BA-G{i:02d}", "test", "golden", title, profile, hand_checked=amounts))
    return train, test


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    train, test = generate()
    write_jsonl(TRAIN_FILE, train)
    write_jsonl(TEST_FILE, test)
    print(f"Wrote {len(train)} scenarios to {TRAIN_FILE.relative_to(ROOT)}")
    print(f"Wrote {len(test)} scenarios to {TEST_FILE.relative_to(ROOT)} (including {len(GOLDEN)} hand-checked)")
    print("Next: python dataset/validate_dataset.py")


if __name__ == "__main__":
    main()
