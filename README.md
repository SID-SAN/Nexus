# Nexus v5.0.0

### Intelligent Distributed Compute Network with Decentralized Verification & Credit Economy

Nexus is a lightweight distributed computing framework that enables multiple machines (nodes) to collaboratively execute **arbitrary user-defined workloads** over the internet using secure Docker sandboxing, decentralized verification, and a distributed reward system. 

With **v5.0.0**, Nexus evolves beyond a simple distributed executor into a **fully asynchronous distributed compute network** featuring:

* decentralized chunk scheduling
* peer verification
* node reputation tracking
* distributed reward distribution
* resilient retry/recovery systems
* real-time observability
* secure execution isolation

---

# Key Highlights (v5.0.0)

## Decentralized Verification Engine

Nexus now uses a true distributed verification pipeline.

### Flow

```text
Executor Node
    ↓
Relay forwards verification
    ↓
Independent Verifier Node
    ↓
Consensus (match/mismatch)
```

### Features

* Executor and verifier are different nodes
* Relay performs orchestration only
* Automatic mismatch retries
* Verification timeout recovery
* Retry limits for unstable chunks
* Fraud-resistant execution model

---

## Fully Asynchronous Execution Model

Workers no longer block while waiting for verification.

### Benefits

* Higher throughput
* Faster chunk processing
* Better cluster utilization
* Reduced node idle time
* Scales efficiently with more nodes

---

## Distributed Credit Economy

Nexus now includes a functioning compute reward system.

### Features

* Users spend credits to submit jobs
* Executor nodes earn credits
* Verifier nodes earn credits
* Rewards distributed automatically
* Timeout-safe reward handling
* Persistent node ownership tracking
* Reward duplication protection

---

## Intelligent Node Reputation System

Relay tracks node quality and reliability.

### Metrics

* successful executions
* failed executions
* mismatches
* verification timeouts

### Benefits

* Future-proof trust scoring
* Foundation for reputation-aware scheduling
* Fraud detection support

---

## Secure Docker Sandboxing

Every chunk executes inside an isolated Docker container.

### Security Features

* CPU & memory limits
* Network isolation (`--network none`)
* Temporary execution environment
* Safe execution of untrusted workloads

---

## Generic Distributed Execution

Run any custom workload using `task.py`.

### Supported Workloads

* simulations
* distributed math
* file processing
* data pipelines
* scientific workloads
* custom compute jobs

---

## Smart Chunking System

Supports multiple chunking strategies.

### Supported Modes

* range-based chunking
* file-list chunking
* automatic chunk generation

Configured via `config.json`.

---

## Fault Tolerance & Recovery

Nexus is designed to survive unreliable nodes.

### Automatic Recovery

* node disconnect recovery
* stale node cleanup
* verification retry system
* execution retries
* timeout handling
* forced completion fallback
* failed chunk recovery

---

## Real-Time Cluster Visibility

Nodes and jobs are now fully observable.

### Features

* live execution logs
* verification logs
* reward logs
* cluster resource monitoring
* chunk-level status tracking
* execution speed tracking
* node resource visibility

---

## Persistent Job Recovery

Jobs survive relay restarts safely.

### Features

* periodic job persistence
* atomic job saves
* crash-safe restoration
* reconnect-safe ownership snapshots

---

## Real-Time Dashboard

Modern web dashboard for monitoring and management.

### Features

* user authentication
* live cluster monitoring
* job submission
* progress tracking
* chunk logs & errors
* credit tracking
* node visibility

---

## Executable Worker Nodes

Run nodes without requiring Python installation.

### Supported Modes

#### EXE Mode

```bash
nexus-node.exe start --node-id PC_1 --api-key YOUR_API_KEY
```

#### Python Mode

```bash
python nexus_node.py start --node-id PC_1 --api-key YOUR_API_KEY
```

---

# Architecture

## Relay Server (FastAPI)

The relay is now primarily an orchestration layer.

### Responsibilities

* manage jobs
* coordinate verification
* track node state
* distribute rewards
* maintain cluster state
* aggregate final results
* persist job state

### Important

The relay performs:

```text
NO actual computation
```

This allows deployment on lightweight/free-tier infrastructure.

---

## Worker Nodes

Distributed compute participants.

### Features

* execute chunks
* verify peer computations
* sandbox execution in Docker
* monitor resources
* earn credits
* perform decentralized verification

---

## Dashboard

Frontend management interface.

### Features

* login/signup
* submit jobs
* monitor jobs
* inspect logs
* track credits
* monitor cluster health

---

# Execution Flow

1. User logs into dashboard
2. User uploads `job.zip`
3. Relay:

   * deducts credits
   * parses config
   * creates chunks
   * broadcasts manifest
4. Nodes:

   * schedule chunks locally
   * execute chunks
   * submit results
5. Relay:

   * forwards verification
6. Verifier node:

   * independently re-executes chunk
   * submits verification result
7. Relay:

   * validates match/mismatch
   * retries failed verifications
   * distributes rewards
8. Final reducer computes output
9. Job completes

---

# Core Compute Model

Nexus follows:

# MAP → VERIFY → REDUCE

### MAP

Distributed chunk execution.

### VERIFY

Independent peer validation.

### REDUCE

Aggregation of verified outputs.

---

# Supported Reducers

* `sum`
* `avg`
* `min`
* `max`
* `list`

---

# Job Format

```text
job.zip
├── task.py
├── config.json (optional)
└── requirements.txt (optional)
```

---

## task.py

```python
import sys

chunk_id = sys.argv[1]

print(result)
```

### Important

Tasks must be chunk-aware.

For range jobs, Nexus provides:

```text
start end
```

arguments automatically.

---

## config.json

### Range-Based Chunking

```json
{
  "chunk_type": "range",
  "start": 0,
  "end": 100000,
  "chunk_size": 1000
}
```

---

### File-Based Chunking

```json
{
  "chunk_type": "file_list",
  "files": [
    "file1.csv",
    "file2.csv"
  ]
}
```

---

## requirements.txt

Optional Python dependencies.

```text
numpy
pandas
scipy
```

Dependencies install automatically inside containers.

---

# Resource-Aware Scheduling

Nodes continuously report:

* CPU usage
* RAM usage

Nexus uses this data for:

* chunk balancing
* node selection
* load-aware execution

---

# Reliability Features

## Verification Retry System

Mismatch handling includes:

* automatic retries
* chunk requeueing
* permanent failure detection

---

## Timeout Recovery

If verification stalls:

* relay force-completes trusted results
* rewards still distribute correctly
* timeout statistics update automatically

---

## Crash Safety

* atomic persistence
* reconnect-safe ownership tracking
* safe cleanup system
* download locking
* secure extraction logic

---

# Security Features

## Safe ZIP Extraction

Prevents:

* path traversal
* overwrite attacks
* unsafe extraction

---

## Sandboxed Execution

Every task executes in isolated containers.

---

## Input Validation

Includes:

* job ID validation
* payload validation
* safe resource parsing
* protected file handling

---

# Project Structure

```text
Nexus/
├── relay/
├── node/
├── frontend/
├── nexus_node.py
├── dist/
└── README.md
```

---

# Current Limitations

* Single relay architecture
* No GPU scheduling yet
* Basic reducers only
* No distributed storage
* Verification uses executor + 1 verifier

---

# v6 Roadmap

* Relay-less architecture
* P2P coordination
* Advanced consensus verification
* GPU compute support
* Reputation-aware scheduling
* Marketplace economics
* Distributed storage layer
* Multi-region networking

---

# Vision

> Build a decentralized, secure, intelligent compute network
> where anyone can contribute compute power and earn.

---

# Contributing

* Open issues
* Suggest improvements
* Submit pull requests

---

# Final Note

Nexus v5.0.0 transforms the project into a real distributed compute network with decentralized execution, verification, recovery, and reward distribution.

If you found this interesting, consider starring the repository ⭐