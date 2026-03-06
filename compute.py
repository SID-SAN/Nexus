def compute_range_sum(start: int, end: int) -> int:
    total = 0

    for i in range(start, end + 1):
        total += i

    return total
def count_primes(start: int, end: int) -> int:

    def is_prime(n):
        if n < 2:
            return False
        for i in range(2, int(n ** 0.5) + 1):
            if n % i == 0:
                return False
        return True

    count = 0

    for i in range(start, end + 1):
        if is_prime(i):
            count += 1

    return count


def vector_sum(start: int, end: int) -> int:
    total = 0

    for i in range(start, end + 1):
        total += i * i

    return total


def factorial_sum(start: int, end: int) -> int:

    def factorial(n):
        result = 1
        for i in range(1, n + 1):
            result *= i
        return result

    total = 0

    for i in range(start, end + 1):
        total += factorial(i)

    return total


def fibonacci_sum(start: int, end: int) -> int:

    def fibonacci(n):
        if n <= 1:
            return n
        a, b = 0, 1
        for _ in range(n - 1):
            a, b = b, a + b
        return b

    total = 0

    for i in range(start, end + 1):
        total += fibonacci(i)

    return total