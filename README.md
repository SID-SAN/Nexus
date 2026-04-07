# Nexus v4.2.0

### Distributed Compute Platform with Credit Economy & Intelligent Scheduling

Nexus is a lightweight distributed computing framework that enables multiple machines (nodes) to collaboratively execute computational tasks over the internet.

With **v4.2.0**, Nexus evolves into a **reliable, user-aware compute platform** with improved scheduling, job lifecycle management, and enhanced execution visibility.

---

# Key Highlights (v4.2.0)

## Distributed Execution Engine

* Parallel task execution across multiple nodes
* Map-Reduce inspired architecture
* Automatic chunking & aggregation

---

## Internet-Ready Network

* Nodes connect from different machines/networks
* Central relay for coordination
* WebSocket-based communication

---

## Credit-Based Economy

* Users pay credits to submit jobs
* Nodes earn credits per completed chunk
* Automatic refund on job cancellation
* Real-time credit tracking via dashboard

---

## Multi-User System

* Email + password login
* API key-based authentication
* Multiple users supported simultaneously

---

## Smarter Scheduling

* Progress-aware job selection
* Size-aware prioritization (faster completion of small jobs)
* Randomized scoring to prevent job starvation
* Adaptive batch allocation based on node capacity

---

## Job Lifecycle Management

* Cancel running jobs anytime
* Automatic refund for unused work
* Failed job detection (after retry exhaustion)
* Clean job termination (no stuck jobs)

---

## Execution Insights

* Real-time progress tracking
* Job duration tracking (accurate lifecycle timing)
* Execution speed (chunks/sec)
* Per-chunk logs and error visibility

---

## Real-Time Dashboard

* Submit jobs via web UI
* Monitor nodes & cluster
* Track job progress and logs
* View credit balance

---

## Executable Node

* Run nodes using `.exe` (no Python required)
* Simple CLI interface
* Easy deployment across machines

---

# Architecture

## Relay Server (FastAPI)

Central coordinator for the network.

### Responsibilities:

* Accept job submissions
* Split jobs into chunks
* Assign work to nodes
* Track execution state
* Aggregate results
* Manage user credits

---

## Worker Nodes

Distributed compute units.

### Features:

* Connect via WebSocket
* Execute chunks in parallel
* Send results + logs
* Earn credits

---

## Dashboard (Frontend)

User interface for interacting with the system.

### Features:

* User login
* Job submission
* Cluster monitoring
* Credit tracking
* Live job updates

---

# Execution Flow

1. User logs in
2. User uploads `job.zip` at
   ```
   https://nexus-relay-5wog.onrender.com/dashboard
   ```
3. Relay:

   * deducts credits
   * splits job into chunks
4. Nodes:

   * request work
   * execute chunks
   * send results
5. Relay:

   * aggregates results
   * distributes credits to nodes
6. Dashboard updates in real-time

---

# Job Format

Each job must include a `main.py` file:

```python
def run(chunk_id, total_chunks):
    return result
```

OR CLI-style:

```python
if __name__ == "__main__":
    # execute logic
```

### Important:

* The **last printed line = result**
* All previous prints = logs

---

# Core Model

Nexus follows a **MAP → REDUCE** pattern:

* Each chunk processes part of data
* Final result is aggregated

### Supported Reducers:

* `sum`
* `avg`
* `min`
* `max`
* `list`

---

# Running a Node

## Using EXE (Recommended)

```bash
nexus-node.exe start --node-id PC_1 --api-key YOUR_API_KEY
```

---

## Using Python

```bash
python nexus_node.py start --node-id PC_1 --api-key YOUR_API_KEY
```

---

# Getting API Key

1. Open dashboard
2. Create account
3. Login
4. API key is handled automatically

---

# Project Structure

```
Nexus/
├── relay/          # FastAPI relay server
├── node/           # Worker node logic
├── dashboard/      # Frontend UI
├── nexus_node.py   # CLI entry point
├── dist/           # EXE build output
└── README.md
```

---

# Current Limitations

* Single relay server (centralized)
* No GPU scheduling
* Basic scheduling heuristics
* No advanced security (yet)

---

# Roadmap

* CLI improvements & config system
* Better packaging & distribution
* Real-time updates (remove polling)
* Multi-relay architecture
* GPU compute support
* Distributed storage layer

---

# Vision

> Build a decentralized, accessible, and intelligent compute network
> where anyone can contribute compute and earn.

---

# Contributing

Contributions are welcome!

* Open issues
* Suggest features
* Submit PRs

---

# Final Note

Nexus v4.2.0 transforms the system into a **reliable distributed compute platform** with intelligent scheduling, proper lifecycle control, and real economic incentives.

If you found this interesting, consider ⭐ starring the repo!
