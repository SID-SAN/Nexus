# Nexus v4.0.0

### Intelligent Distributed Computing Framework

Nexus is a lightweight distributed computing framework that enables multiple machines (nodes) to collaboratively execute computational tasks in parallel using a Map-Reduce inspired model.

With v4.0.0, Nexus evolves from a basic distributed executor into a **self-optimizing, resource-aware compute system**.

---

# Key Highlights (v4.0.0)

* **Adaptive Chunk Execution**

  * Nodes dynamically process multiple chunks based on available resources

* **Auto Chunking**

  * System automatically determines optimal number of chunks based on cluster capacity

* **Batch Scheduling**

  * Relay assigns multiple chunks per request to reduce communication overhead

* **Concurrency Control**

  * Nodes limit parallel execution using semaphores to prevent overload

* **Stable WebSocket Architecture**

  * Single sender queue ensures safe, reliable communication

* **Docker-Based Execution**

  * Secure and isolated execution environment for user jobs

* **Persistent Job Storage**

  * Jobs survive relay restarts

* **Real-Time Monitoring**

  * CPU/RAM tracking + live logs + job progress

---

# Architecture

## 1️⃣ Relay Server (FastAPI)

* Central coordinator
* Handles job submission, scheduling, and aggregation
* Maintains cluster state

### Responsibilities:

* Accept job uploads
* Split and schedule tasks
* Track node resources
* Aggregate results (reducers)
* Serve APIs for UI

---

## 2️⃣ Worker Nodes

* Connect to relay via WebSocket
* Pull work dynamically
* Execute jobs in Docker containers

### Features:

* Adaptive batching
* Concurrency control
* Resource monitoring
* Fault-tolerant execution

---

## 3️⃣ Execution Flow

1. User uploads `job.zip`
2. Relay determines chunk count (auto/manual)
3. Nodes request work
4. Relay assigns chunks (batched)
5. Nodes:

   * download job
   * execute in Docker
   * send results + logs
6. Relay:

   * aggregates results
   * updates job status
7. UI displays progress + results

---

# Job Format

Each job must include a `main.py` file:

```python
def run(chunk_id, total_chunks):
    # user logic
    return result
```

OR CLI-style:

```python
if __name__ == "__main__":
    # parse args and call run()
```

### Important Rule:

* The **last printed line must be the result**
* All previous prints are treated as logs

---

# Core Concept

Nexus follows a **MAP → REDUCE** model:

* Each chunk processes a subset of data
* Final result is aggregated using reducers

### Supported Reducers:

* `sum`
* `avg`
* `min`
* `max`
* `list`

---

# Execution Environment

Each chunk runs inside:

* Isolated Docker container
* Limited CPU & RAM
* Mounted job directory

---

# Stability Improvements (v4)

* Async-safe WebSocket communication (single sender queue)
* Background execution using asyncio
* Concurrency limits per node
* Adaptive batching
* Retry handling for failed chunks

---

# Dashboard Features

* Upload jobs (ZIP)
* Select reducer
* View cluster nodes
* Track job progress
* View logs (per chunk)
* View final results

---

# CLI Usage

### Start a node

```bash
nexus-node start
```

### Custom node

```bash
nexus-node start --node-id node_1 --port 5001
```

---

# Project Structure

```
Nexus/
├── relay/
├── node/
├── dashboard/
├── jobs/
├── nexus_cli.py
└── README.md
```

---

# Current Limitations

* Relay is a single point of coordination
* No authentication system
* No GPU scheduling
* Static scheduling heuristics (partially adaptive)

---

# Future Roadmap

* Adaptive concurrency (dynamic tuning)
* Node reputation system
* Predictive scheduling
* Multi-relay architecture
* GPU-aware execution
* Distributed storage integration

---

# Vision

Transform Nexus into:

> A lightweight, intelligent, and extensible distributed compute platform accessible to developers and students alike.

---

# Contributing

Contributions are welcome!

* Open issues
* Suggest features
* Submit PRs

---

# Final Note

Nexus v4.0.0 marks the transition from a working prototype to an **intelligent distributed system**.

If you found this useful, consider ⭐ starring the repository!
