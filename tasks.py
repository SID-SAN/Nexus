# tasks.py

def compute_range_sum(start: int, end: int) -> int:
    total = 0
    for i in range(start, end):
        total += i
    return total