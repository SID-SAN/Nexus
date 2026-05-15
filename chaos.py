import os
import random
import asyncio

# =========================================================
# CHAOS MODE ENABLE FLAG
# =========================================================

CHAOS_ENABLED = os.getenv("NEXUS_CHAOS", "0") == "1"

# =========================================================
# NETWORK CHAOS
# =========================================================

DROP_MESSAGE_PROBABILITY = float(
    os.getenv("NEXUS_DROP_MESSAGE_PROBABILITY", "0.0")
)

DUPLICATE_MESSAGE_PROBABILITY = float(
    os.getenv("NEXUS_DUPLICATE_MESSAGE_PROBABILITY", "0.0")
)

MESSAGE_DELAY_PROBABILITY = float(
    os.getenv("NEXUS_MESSAGE_DELAY_PROBABILITY", "0.0")
)

MAX_DELAY_SECONDS = float(
    os.getenv("NEXUS_MAX_DELAY_SECONDS", "5")
)

# =========================================================
# EXECUTION CHAOS
# =========================================================

NODE_CRASH_PROBABILITY = float(
    os.getenv("NEXUS_NODE_CRASH_PROBABILITY", "0.0")
)

EXECUTION_FREEZE_PROBABILITY = float(
    os.getenv("NEXUS_EXECUTION_FREEZE_PROBABILITY", "0.0")
)

MAX_EXECUTION_FREEZE_SECONDS = float(
    os.getenv("NEXUS_MAX_EXECUTION_FREEZE_SECONDS", "30")
)

# =========================================================
# RELAY CHAOS
# =========================================================

RELAY_DISCONNECT_PROBABILITY = float(
    os.getenv("NEXUS_RELAY_DISCONNECT_PROBABILITY", "0.0")
)

# =========================================================
# HELPER FUNCTIONS
# =========================================================

def chaos_enabled():
    return CHAOS_ENABLED


def should_trigger(probability: float) -> bool:

    if not CHAOS_ENABLED:
        return False

    return random.random() < probability


async def random_delay(max_seconds: float):

    if max_seconds <= 0:
        return

    delay = random.uniform(0, max_seconds)

    await asyncio.sleep(delay)