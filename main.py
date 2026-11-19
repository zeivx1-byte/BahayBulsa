from storage import load_data, save_data
from data_structures import CategoryHashMap, UndoStack, BillPriorityQueue
from algorithms import merge_sort, insertion_sort, run_benchmark


class BudgetOrganizerAPI:
    """
    Manages the transaction ledger, upcoming bills, and summary totals.
    """

    def __init__(self, *, persist=True):
        # 1. Load saved records from local JSON storage
        self.persist = persist
        saved_state = load_data() if persist else {}
        self.transactions = saved_state.get("transactions", [])
        self.savings_goal = saved_state.get("savings_goal", 10000.0)
        self._next_transaction_id = max((tx["id"] for tx in self.transactions), default=0) + 1

        # 2. Initialize your 3 syllabus data structures
        self.category_map = CategoryHashMap()       # Data Structure 1: Hash Map
        self.undo_stack = UndoStack()               # Data Structure 2: Stack (LIFO)
        self.bill_queue = BillPriorityQueue()       # Data Structure 3: Priority Queue

        # 3. Populate data structures from loaded records
        self._rebuild_structures()

    def _rebuild_structures(self):
        """Syncs in-memory data structures with the loaded transaction list."""
        self.category_map.clear()
        for tx in self.transactions:
            if tx["type"] == "expense":
                self.category_map.add_expense(tx["category"], tx["amount"])

    # ---------------------------------------------------------
    # CORE WORKFLOW ENDPOINTS (Called by HTML/JS buttons)
    # ---------------------------------------------------------

    def get_dashboard_data(self):
        """Returns summary totals, transactions, category breakdown, and alerts."""
        total_income = sum(t["amount"] for t in self.transactions if t["type"] == "income")
        total_expense = sum(t["amount"] for t in self.transactions if t["type"] == "expense")
        balance = total_income - total_expense

        return {
            "total_income": total_income,
            "total_expense": total_expense,
            "balance": balance,
            "savings_goal": self.savings_goal,
            "transactions": self.transactions,
            "category_totals": self.category_map.get_all(),
            "upcoming_bills": self.bill_queue.get_sorted_bills(),
            "can_undo": not self.undo_stack.is_empty()
        }

    def add_transaction(self, category, amount, tx_type, date, note=""):
        """Adds a new income or expense entry and pushes action to UndoStack."""
        tx = {
            "id": self._next_transaction_id,
            "category": category,
            "amount": float(amount),
            "type": tx_type,  # "income" or "expense"
            "date": date,
            "note": note
        }

        self._next_transaction_id += 1
        self.transactions.append(tx)
        if tx_type == "expense":
            self.category_map.add_expense(category, tx["amount"])

        # Push action onto Stack for Undo support
        self.undo_stack.push({"action": "ADD", "transaction": tx})
        self._persist()
        return self.get_dashboard_data()

    def delete_transaction(self, tx_id):
        """Deletes a transaction by ID and records it on the UndoStack."""
        target = next((t for t in self.transactions if t["id"] == tx_id), None)
        if target:
            self.transactions.remove(target)
            self._rebuild_structures()
            self.undo_stack.push({"action": "DELETE", "transaction": target})
            self._persist()
        return self.get_dashboard_data()

    def undo_last_action(self):
        """Pops the last action from the UndoStack and reverts the state."""
        last_op = self.undo_stack.pop()
        if not last_op:
            return self.get_dashboard_data()

        if last_op["action"] == "ADD":
            self.transactions = [
                t for t in self.transactions if t["id"] != last_op["transaction"]["id"]
            ]
        elif last_op["action"] == "DELETE":
            self.transactions.append(last_op["transaction"])

        self._rebuild_structures()
        self._persist()
        return self.get_dashboard_data()

    def add_upcoming_bill(self, name, amount, due_date):
        """Inserts an upcoming utility bill into the Priority Queue (Min-Heap)."""
        self.bill_queue.insert({"name": name, "amount": float(amount), "due_date": due_date})
        return self.get_dashboard_data()

    # ---------------------------------------------------------
    # ALGORITHM & BENCHMARK ENDPOINTS
    # ---------------------------------------------------------

    def sort_transactions(self, algorithm="merge", key="amount"):
        """Sorts transactions using one of your two chosen algorithms."""
        if algorithm == "merge":
            self.transactions = merge_sort(self.transactions, key=key)
        else:
            self.transactions = insertion_sort(self.transactions, key=key)
        return self.get_dashboard_data()

    def benchmark_runtime(self, dataset_size=1000):
        """Runs both algorithms on generated test data and returns runtime in ms."""
        return run_benchmark(int(dataset_size))

    def _persist(self):
        """Saves current transactions and savings goal to disk."""
        if self.persist:
            save_data({
                "transactions": self.transactions,
                "savings_goal": self.savings_goal
            })


# ---------------------------------------------------------
# DESKTOP WINDOW ENTRY POINT
# ---------------------------------------------------------
if __name__ == "__main__":
    import webview

    api = BudgetOrganizerAPI()

    window = webview.create_window(
        title="Household Expense Budget Organizer",
        url="web/index.html",
        js_api=api,
        width=1150,
        height=750,
        min_size=(900, 600)
    )

    webview.start(debug=True)  # Set debug=False for final submission
