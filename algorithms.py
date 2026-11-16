import time
import random

def merge_sort(arr, key="amount"):
    """O(n log n) Divide-and-Conquer Sort."""
    if len(arr) <= 1:
        return arr

    mid = len(arr) // 2
    left = merge_sort(arr[:mid], key=key)
    right = merge_sort(arr[mid:], key=key)

    return _merge(left, right, key)

def _merge(left, right, key):
    sorted_list = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i][key] <= right[j][key]:
            sorted_list.append(left[i])
            i += 1
        else:
            sorted_list.append(right[j])
            j += 1
    sorted_list.extend(left[i:])
    sorted_list.extend(right[j:])
    return sorted_list


def insertion_sort(arr, key="amount"):
    """O(n^2) Comparison Sort."""
    items = arr.copy()
    for i in range(1, len(items)):
        current = items[i]
        j = i - 1
        while j >= 0 and items[j][key] > current[key]:
            items[j + 1] = items[j]
            j -= 1
        items[j + 1] = current
    return items


def run_benchmark(dataset_size=1000):
    """Generates dummy transactions and compares runtime performance in milliseconds."""
    dummy_data = [
        {"id": i, "category": "Test", "amount": random.uniform(10.0, 5000.0), "type": "expense", "date": "2026-11-16"}
        for i in range(dataset_size)
    ]

    # Benchmark Merge Sort
    start_merge = time.perf_counter()
    merge_sort(dummy_data, key="amount")
    merge_ms = (time.perf_counter() - start_merge) * 1000

    # Benchmark Insertion Sort
    start_insertion = time.perf_counter()
    insertion_sort(dummy_data, key="amount")
    insertion_ms = (time.perf_counter() - start_insertion) * 1000

    return {
        "dataset_size": dataset_size,
        "merge_sort_ms": round(merge_ms, 4),
        "insertion_sort_ms": round(insertion_ms, 4)
    }
