# Nexus v4.3.0

### Generic Distributed Compute Platform with Docker Sandbox & Verification Engine

Nexus is a lightweight distributed computing framework that enables multiple machines (nodes) to collaboratively execute **arbitrary user-defined code** over the internet.

With **v4.3.0**, Nexus evolves into a **secure, generic, and verifiable compute platform**, capable of running any workload using containerized execution and multi-node validation.

---

# Key Highlights (v4.3.0)

## Generic Execution Engine

- Run **any user-defined code** via `task.py`
- No hardcoded task types
- Fully flexible compute model
- Supports simulations, data processing, and custom workloads

---

##Docker Sandboxed Execution

- Each chunk runs inside a **Docker container**
- CPU & memory limits enforced
- Network isolation (`--network none`)
- Safe execution of untrusted code

---

## Multi-Node Verification System

- Each chunk is executed on **multiple nodes**
- Results must match before acceptance
- Automatic retry on mismatch
- Prevents fake results / credit exploitation

---

## Smart Chunking System

Supports multiple chunking strategies:

- Range-based
- File-based
- Default chunk IDs

Configured via `config.json`

---

## Credit-Based Economy

- Users pay credits to submit jobs
- Nodes earn credits per verified chunk
- Rewards split across verifying nodes
- Refunds for cancelled jobs

---

## Multi-User System

- Email + password authentication
- API key-based access
- Per-user job isolation
- Secure job ownership & cancellation

---

## Intelligent Scheduler (Upgraded)

- Progress-aware job prioritization
- Fairness across users
- Load-aware scheduling
- Duplicate chunk assignment for verification
- Prevents starvation & stuck jobs

---

## Fault Tolerance & Retry System

- Automatic retry on:
  - Node failure
  - Timeout
  - Execution error
- Max retry limit enforcement
- Job-level failure detection

---

## Execution Insights

- Real-time job progress
- Chunk-level logs & errors
- Job duration tracking
- Execution speed (chunks/sec)

---

## Real-Time Dashboard

- Submit jobs via UI
- Monitor cluster & nodes
- View logs and results
- Track credits

---

## Executable Node

- Run nodes via `.exe` (no Python required)
- Simple CLI interface
- Plug-and-play setup

---

# Architecture

## 🔹 Relay Server (FastAPI)

Central coordinator.

### Responsibilities:

- Accept job submissions
- Parse job config & generate chunks
- Assign work to nodes
- Track execution & verification
- Aggregate results
- Manage credits

---

## 🔹 Worker Nodes

Distributed compute units.

### Features:

- Connect via WebSocket
- Execute chunks inside Docker
- Send results + logs
- Participate in verification
- Earn credits

---

## 🔹 Dashboard (Frontend)

User interface.

### Features:

- User login
- Job submission
- Cluster monitoring
- Live job tracking
- Credit management

---

# Execution Flow

1. User logs in  
2. Uploads `job.zip` via dashboard  
3. Relay:
   - deducts credits
   - parses config
   - generates chunks  
4. Nodes:
   - request chunks
   - execute in Docker
   - return results  
5. Relay:
   - verifies results (multi-node)
   - retries if mismatch
   - distributes rewards  
6. Job completes → result aggregated  

---

# Job Format

```

job.zip
├── task.py
├── config.json (optional)
└── requirements.txt (optional)

````

---

## 🔹 task.py

```python
import sys

chunk = int(sys.argv[1])

# your logic here
print(result)
````
> ⚠️ Important: Tasks must be **chunk-aware**.  
> Each chunk should process a **specific portion of data**, not the entire dataset.  
> For range-based jobs, use `start` and `end` arguments provided via `config.json`.

---

## 🔹 config.json

### Range-based

```json
{
  "chunk_type": "range",
  "start": 0,
  "end": 100000,
  "chunk_size": 1000
}
```

### File-based

```json
{
  "chunk_type": "file_list",
  "files": ["file1.csv", "file2.csv"]
}
```

---

## 🔹 requirements.txt (Optional)

```
numpy
pandas
```

Dependencies are installed once per job.

---

# Core Model

Nexus follows a:

**MAP → VERIFY → REDUCE**

* Map → execute chunks
* Verify → validate across nodes
* Reduce → aggregate results

---

## Supported Reducers

* `sum`
* `avg`
* `min`
* `max`
* `list`

---

# Running a Node

## Using EXE

```bash
nexus-node.exe start --node-id PC_1 --api-key YOUR_API_KEY
```

## Using Python

```bash
python nexus_node.py start --node-id PC_1 --api-key YOUR_API_KEY
```

---

# Getting API Key

1. Open dashboard
2. Create account
3. Login
4. API key is auto-managed

---

# Project Structure

```
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

* Single relay (centralized)
* No GPU scheduling yet
* Basic reducers
* No distributed storage

---

# Roadmap

* GPU compute support
* Advanced scheduling
* Multi-relay architecture
* Distributed storage
* Job marketplace
* P2P node discovery

---

# Vision

> Build a decentralized, secure, and intelligent compute network
> where anyone can contribute compute and earn.

---

# Contributing

* Open issues
* Suggest features
* Submit PRs

---

# ⭐ Final Note

Nexus v4.3.0 transforms the system into a **secure, generic distributed compute platform** with real-world execution capabilities.

If you found this interesting, consider ⭐ starring the repo!