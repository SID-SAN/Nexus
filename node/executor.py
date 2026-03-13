# node/executor.py

import importlib.util
import os


def execute_job(job_dir, chunk_id, total_chunks):

    job_file = os.path.join(job_dir, "main.py")

    spec = importlib.util.spec_from_file_location("job_module", job_file)
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    if not hasattr(module, "run"):
        raise Exception("Job must define run(chunk_id, total_chunks)")

    result = module.run(chunk_id, total_chunks)

    return result