# 🚀 Nexus

## Version: v0.0.0  
### Distributed Execution Core

---

## 🧠 Overview

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

## 🏗 Architecture

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

## ⚙️ How It Works

1. A node receives a distributed computation request.
2. It determines total number of nodes (`peers + self`).
3. It splits the task into equal chunks.
4. It dispatches chunks to peers.
5. If a peer fails, the chunk is reassigned.
6. If all peers fail, the chunk executes locally.
7. Results are aggregated.
8. Final result is returned.

---

## ✅ Features Implemented in v0

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

## ❌ Not Included (By Design)

- Internet-wide node discovery
- Bootstrap server
- Resource-aware scheduling
- Docker sandboxing
- Security layer
- Credit system
- Decentralized governance

These are planned for future versions.

---

## 🧪 How To Run

### 1️⃣ Create Conda Environment

```bash
conda create -n nexus python=3.10
conda activate nexus
pip install fastapi uvicorn requests