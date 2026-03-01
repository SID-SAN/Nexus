# Nexus

## Version: v0.0.0  
### Distributed Execution Core

---

## Overview

Nexus v0 is a local multi-process distributed compute engine built from scratch.

It demonstrates:

- Multi-node distributed computation
- Dynamic task splitting
- Result aggregation
- Peer-to-peer communication
- Graceful failure handling
- Chunk reassignment on peer failure
- Local fallback execution

This version focuses purely on building a stable distributed execution core.

---

## Architecture

- Peer-based model
- Temporary coordinator per task
- HTTP communication using FastAPI
- Config-driven node identity
- Dynamic chunk partitioning
- Fault-tolerant execution
- Structured logging

Each node:
- Runs independently
- Listens on its own port
- Can execute chunks
- Can coordinate tasks
- Can recover from peer failure

---

## How It Works

1. A node receives a distributed computation request.
2. It determines total number of nodes (`peers + self`).
3. It splits the task into equal chunks.
4. It dispatches chunks to peers.
5. If a peer fails, the chunk is reassigned.
6. If all peers fail, the chunk executes locally.
7. Results are aggregated.
8. Final result is returned.

---

## Features Implemented in v0

- Multi-process distributed nodes
- Dynamic chunk partitioning
- Peer communication via HTTP
- Graceful failure handling
- Chunk reassignment on failure
- Guaranteed correctness
- Local fallback execution
- Configurable node identity
- Clean repository structure

---

## Not Included (By Design)

- Internet-wide node discovery
- Bootstrap server
- Resource-aware scheduling
- Docker sandboxing
- Security layer
- Credit system
- Decentralized governance

These are planned for future versions.

---

##  How To Run

### Create Conda Environment

```bash
conda create -n nexus python=3.10
conda activate nexus
pip install fastapi uvicorn requests
```

### Start Multiple Nodes
Open three separate terminals inside the project directory.<br>
Terminal 1 — Node 1 (Coordinator)
```
$env:NODE_ID="node_1"
python -m uvicorn node:app --port 5001
```
Terminal 2 — Node 2 (Coordinator)
```
$env:NODE_ID="node_1"
python -m uvicorn node:app --port 5002
```
Terminal 3 — Node 3 (Coordinator)
```
$env:NODE_ID="node_1"
python -m uvicorn node:app --port 5003
```
You should see:
```
Uvicorn running on http://127.0.0.1:500X
```
### Trigger Distributed Computation
Open your browser and go to:
```
http://127.0.0.1:5001/docs
```
Then:

1 Click POST /distributed_sum

2 Click Try it out

3 Click Execute

---

## What You Should Observe

- Each node executes a different chunk.
- Logs appear in all active terminals.
- Results are aggregated.
- The final result is returned by Node 1.

This demonstrates real distributed execution.

---

## Testing Fault Tolerance

To test system resilience:

1. Stop one node (Ctrl + C in its terminal).
2. Trigger `/distributed_sum` again.

The system will:

- Detect the failure  
- Reassign the chunk  
- Fall back to local execution if needed  
- Still return the correct final result  

Nexus v0 guarantees correctness even when peers fail.

---

## Features in v0

- Multi-process distributed execution  
- Dynamic scaling based on peer count  
- Automatic chunk reassignment  
- Fault recovery  
- Local fallback execution  
- Structured logging  

---

## Not Included (By Design)

The following features are intentionally excluded from v0:

- Internet-wide discovery  
- Bootstrap server  
- Resource-aware scheduling  
- Docker sandboxing  
- Security layer  
- Credit system  
- Decentralized governance  

These are planned for future versions.

---

## 🗺 Roadmap

| Version | Focus |
|----------|--------|
| v0 | Distributed execution core |
| v1 | Internet-ready hybrid peer model |
| v2 | Resource-aware scheduling |
| v3 | Secure sandbox execution |
| v4 | Decentralized governance |

---

## Status

Nexus v0 is stable and frozen.

Future versions will build on this core without architectural rewrites.