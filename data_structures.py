class CategoryHashMap:
    """Custom Hash Table with chaining for O(1) category expense lookups."""
    def __init__(self, size=16):
        self.size = size
        self.buckets = [[] for _ in range(self.size)]

    def _hash(self, key):
        return sum(ord(ch) for ch in key) % self.size

    def add_expense(self, category, amount):
        index = self._hash(category)
        for pair in self.buckets[index]:
            if pair[0] == category:
                pair[1] += amount
                return
        self.buckets[index].append([category, amount])

    def get_all(self):
        result = {}
        for bucket in self.buckets:
            for category, total in bucket:
                result[category] = total
        return result

    def clear(self):
        self.buckets = [[] for _ in range(self.size)]


class UndoStack:
    """Custom LIFO Stack for undoing recent transaction actions."""
    def __init__(self):
        self._items = []

    def push(self, item):
        self._items.append(item)

    def pop(self):
        if self.is_empty():
            return None
        return self._items.pop()

    def is_empty(self):
        return len(self._items) == 0


class BillPriorityQueue:
    """Custom Binary Min-Heap ordering upcoming bills by earliest due date."""
    def __init__(self):
        self._heap = []

    def insert(self, bill):
        # bill format: {"name": str, "amount": float, "due_date": "YYYY-MM-DD"}
        self._heap.append(bill)
        self._sift_up(len(self._heap) - 1)

    def _sift_up(self, idx):
        parent = (idx - 1) // 2
        while idx > 0 and self._heap[idx]["due_date"] < self._heap[parent]["due_date"]:
            self._heap[idx], self._heap[parent] = self._heap[parent], self._heap[idx]
            idx = parent
            parent = (idx - 1) // 2

    def get_sorted_bills(self):
        return sorted(self._heap, key=lambda b: b["due_date"])
