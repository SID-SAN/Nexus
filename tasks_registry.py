from compute import compute_range_sum
from compute import count_primes
from compute import vector_sum
from compute import factorial_sum
from compute import fibonacci_sum
from compute import power_sum

TASK_REGISTRY = {

    "sum": compute_range_sum,

    "prime_count": count_primes,

    "vector_sum": vector_sum,

    "factorial_sum": factorial_sum,

    "fibonacci_sum": fibonacci_sum,

    "power_sum": power_sum

}


def get_task(task_name):

    return TASK_REGISTRY.get(task_name)