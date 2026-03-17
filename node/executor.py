import importlib.util
import sys
import os
import io
from contextlib import redirect_stdout, redirect_stderr


def execute_job(job_path, chunk_id, total_chunks):

    main_file = os.path.join(job_path, "main.py")

    spec = importlib.util.spec_from_file_location("job_module", main_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "run"):
        raise Exception("main.py must define run(chunk_id, total_chunks)")

    # capture logs
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    try:
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            result = module.run(chunk_id, total_chunks)

        logs = stdout_buffer.getvalue()
        errors = stderr_buffer.getvalue()

        return {
            "result": result,
            "logs": logs,
            "error": errors
        }

    except Exception as e:
        return {
            "result": None,
            "logs": stdout_buffer.getvalue(),
            "error": str(e)
        }