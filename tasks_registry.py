# tasks_registry.py

from compute import compute_range_sum as compute_sum

# Example future tasks
# from tasks import compute_prime_range
# from tasks import matrix_multiply_chunk

TASK_REGISTRY = {
    "sum": compute_sum,
    # "prime": compute_prime_range,
    # "matrix": matrix_multiply_chunk,
}


def get_task(task_name):
    return TASK_REGISTRY.get(task_name)