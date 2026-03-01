# logger.py

import logging

def setup_logger(node_id: str):
    logger = logging.getLogger(node_id)
    logger.setLevel(logging.INFO)

    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        f"%(asctime)s | {node_id} | %(levelname)s | %(message)s"
    )
    handler.setFormatter(formatter)

    if not logger.handlers:
        logger.addHandler(handler)

    return logger