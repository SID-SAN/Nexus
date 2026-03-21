from contextlib import redirect_stdout, redirect_stderr
from node.docker_runner import run_in_docker

def execute_job(job_path, chunk_id, total_chunks):
    return run_in_docker(job_path, chunk_id, total_chunks)