# Nexus v6.0.0

> Intelligent Distributed Computing Framework

Nexus is a distributed computing platform that enables machines across the internet to collaboratively execute workloads in parallel.

Nodes automatically discover peers, claim work, execute tasks inside isolated Docker containers, verify results, recover from failures, and synchronize job metadata across the network.

Nexus combines:

- Distributed Computing
- Peer-to-Peer Networking
- Fault Tolerance
- Verification Systems
- Resource-Aware Scheduling
- Compute Marketplace Foundations

into a single platform.

---

# Features

## Distributed Execution

- Automatic workload chunking
- Parallel execution across multiple nodes
- Dynamic chunk claiming
- Result aggregation
- Range-based workloads
- File-based workloads

---

## Peer-to-Peer Coordination

- Direct node-to-node communication
- Peer discovery
- Peer metadata sharing
- Manifest synchronization
- Package replication
- Package recovery

---

## Fault Tolerance

- Chunk retry mechanisms
- Claim recovery
- Runtime persistence
- Peer cache persistence
- Verification persistence
- Relay failover support
- Job recovery after node failures

---

## Secure Execution

- Docker sandboxing
- CPU limits
- Memory limits
- Execution timeouts
- Dependency installation support
- Network-isolated execution

---

## Verification Layer

- Multi-node result verification
- Duplicate completion detection
- Verification timeout handling
- Mismatch tracking
- Recovery-aware execution

---

## Resource-Aware Scheduling

- CPU monitoring
- RAM monitoring
- Node health tracking
- Dynamic chunk assignment
- Load-aware scheduling

---

## Built-in Dashboard

- Job submission
- Cluster monitoring
- Node monitoring
- Job logs
- Result viewing
- User management
- Credit tracking

---

## Chaos Testing Framework

Built-in distributed systems testing:

- Message drops
- Message duplication
- Message delays
- Network partitions
- Relay disconnects
- Execution freezes
- Node crashes

Used to validate resilience under real-world distributed system failures.

---

# Architecture

```text
                   ┌──────────────────┐
                   │      Relay       │
                   │ Authentication   │
                   │ Job Registry     │
                   │ Credit System    │
                   └────────┬─────────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
          ▼                 ▼                 ▼

     ┌─────────┐      ┌─────────┐      ┌─────────┐
     │ Node A  │◄────►│ Node B  │◄────►│ Node C  │
     └─────────┘      └─────────┘      └─────────┘
          ▲                 ▲                 ▲
          └──── Peer Mesh / Package Sync ────┘
```

---

# Public Relay

Nexus provides a public relay for testing and development:

```text
https://nexus-relay-5wog.onrender.com
```

Dashboard:

```text
https://nexus-relay-5wog.onrender.com/dashboard
```

You can join the network without hosting your own relay.

---

# Quick Start

## 1. Clone Repository

```bash
git clone https://github.com/SID-SAN/Nexus.git

cd Nexus
```

---

## 2. Create Environment

```bash
conda create -n nexus python=3.11

conda activate nexus
```

---

## 3. Install Dependencies

```bash
pip install -r requirements.txt
```

---

## 4. Install Docker

Nexus executes workloads inside Docker containers.

Verify installation:

```bash
docker --version
```

---

## 5. Build Worker Image

```bash
docker build -t nexus-base .
```

---

# Create User

Open:

```text
https://nexus-relay-5wog.onrender.com/dashboard
```

Click:

```text
Login
↓
Create User
```

Enter:

```text
Email
Password
```

Example:

```text
Email: alice@example.com
Password: mypassword
```

You will receive an API key:

```text
user_A_key
```

Save this key.

It is required to start worker nodes.

---

# Start a Worker Node

Using Python:

```bash
python nexus_node.py start --node-id PC_1 --api-key user_A_key
```

Example:

```bash
python nexus_node.py start --node-id PC_1 --api-key user_A_key
```

Expected output:

```text
[Relay] Connected to https://nexus-relay-5wog.onrender.com
[PeerMesh] Direct peer server started
[System] Nexus node active
```

---

# Start Multiple Nodes

Example:

Terminal 1

```bash
python nexus_node.py start --node-id PC_1 --api-key user_A_key
```

Terminal 2

```bash
python nexus_node.py start --node-id PC_2 --api-key user_B_key
```

Terminal 3

```bash
python nexus_node.py start --node-id PC_3 --api-key user_C_key
```

Nodes will automatically:

- Discover peers
- Synchronize manifests
- Exchange package metadata
- Execute workloads
- Verify results

---

# Using the Packaged CLI

If Nexus is installed as an executable:

```bash
nexus-node.exe start --node-id PC_1 --api-key user_A_key
```

---

# Creating a Job

Create:

```text
task.py
```

Example:

```python
import sys

chunk_id = int(sys.argv[1])
start = int(sys.argv[2])
end = int(sys.argv[3])

total = 0

for i in range(start, end):
    total += i

print(total)
```

---

Create:

```text
config.json
```

```json
{
    "chunk_type": "range",
    "start": 1,
    "end": 1000000,
    "chunk_size": 10000
}
```

---

Project Structure:

```text
job.zip
│
├── task.py
└── config.json
```

Compress:

```text
task.py
config.json
```

into:

```text
job.zip
```

---

# Submit Job

Open Dashboard:

```text
https://nexus-relay-5wog.onrender.com/dashboard
```

Login.

Click:

```text
+ Submit Job
```

Upload:

```text
job.zip
```

Choose reducer:

```text
sum
```

Submit.

---

# What Happens Internally

```text
Upload Job
      │
      ▼

Create Manifest
      │
      ▼

Distribute Chunks
      │
      ▼

Nodes Claim Work
      │
      ▼

Execute in Docker
      │
      ▼

Verify Results
      │
      ▼

Aggregate Output
      │
      ▼

Return Final Result
```

---

# Example Result

For:

```python
sum(range(1,1000000))
```

Expected output:

```text
499999500000
```

---

# Dashboard Features

## Cluster Metrics

- Connected Nodes
- Active Jobs
- Active Claims
- Active Chunks
- Verification Success Rate
- Verification Mismatches
- Claim Recoveries
- Duplicate Completions

---

## Node Metrics

- Node Status
- Active Chunks
- Known Peers
- Last Seen

---

## Job Metrics

- Progress
- Duration
- Throughput
- Recovery Count
- Verification Count
- Logs
- Results

---

# Security

## Docker Isolation

Every workload executes inside an isolated Docker container.

Restrictions:

- No network access
- CPU limits
- Memory limits
- Execution timeout

---

## Path Traversal Protection

Nexus validates:

- Job IDs
- File paths
- Zip contents

before extraction or execution.

---

## Verification System

Completed work may be independently verified by other nodes before acceptance.

This prevents:

- Incorrect results
- Duplicate completions
- Faulty nodes

---

# Runtime State Persistence

Nexus persists:

```text
node/runtime_state/

├── peers.json
├── claims.json
├── verifications.json
```

This allows:

- Peer restoration
- Claim recovery
- Verification recovery

after restarts.

---

# Version History

## V0

Local Distributed Engine

- Multi-process simulation
- Chunk splitting
- Aggregation

---

## V1

Internet Connected Nodes

- Relay server
- Node registration
- API key authentication

---

## V2

Intelligent Scheduling

- CPU tracking
- RAM tracking
- Load-aware scheduling

---

## V3

Secure Execution Layer

- Docker sandbox
- Dependency management
- Isolation

---

## V4

Reliable Compute Network

- Verification
- Retry logic
- Credit system
- Dashboard

---

## V5

Hybrid Decentralized Network

- Multi-relay support
- Peer sharing
- Metadata replication
- Failure-aware routing

---

## V6

Peer Recovery & Replication

- Peer mesh networking
- Manifest synchronization
- Package replication
- Package recovery
- Runtime persistence
- Relay failover support

---

# Roadmap

## V7 — Compute Marketplace

- Node reputation
- Dynamic pricing
- Marketplace scheduling
- Compute bidding

---

## V8 — Trustless Verification

- Multi-party verification
- Reputation penalties
- Fraud detection

---

## V9 — Distributed GPU Compute

- GPU discovery
- GPU scheduling
- CUDA workloads
- AI inference

---

## V10 — Federated Learning

- Distributed model training
- Gradient aggregation
- Model registry

---

# Vision

Nexus aims to become a decentralized cloud computing platform where anyone can contribute compute resources and anyone can execute workloads across a globally distributed network.

Think:

```text
Distributed Computing
+
Peer-to-Peer Networking
+
Cloud Infrastructure
+
Compute Marketplace
```

## One Network. Unlimited Compute.