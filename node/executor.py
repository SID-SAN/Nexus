import importlib.util
import os
import io
import asyncio
from contextlib import redirect_stdout, redirect_stderr


def execute_job(job_path, chunk_id, total_chunks):

    main_file = os.path.join(job_path, "main.py")

    spec = importlib.util.spec_from_file_location("job_module", main_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "run"):
        raise Exception("main.py must define run(chunk_id, total_chunks)")

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    try:
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            result = module.run(chunk_id, total_chunks)

        return {
            "result": result,
            "logs": stdout_buffer.getvalue(),
            "error": stderr_buffer.getvalue()
        }

    except Exception as e:
        return {
            "result": None,
            "logs": stdout_buffer.getvalue(),
            "error": str(e)
        }


def execute_job_with_timeout(job_path, chunk_id, total_chunks, timeout=60):

    async def run_with_timeout():
        return await asyncio.to_thread(
            execute_job, job_path, chunk_id, total_chunks
        )

    try:
        return asyncio.run(
            asyncio.wait_for(run_with_timeout(), timeout=timeout)
        )
    except asyncio.TimeoutError:
        return {
            "result": None,
            "logs": "",
            "error": f"Execution timed out after {timeout} seconds"
        }