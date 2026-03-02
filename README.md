# Nexus v1.0.0
## Internet-Aware Distributed Compute Network (Direct HTTP Model)

---

## Vision

Nexus is a distributed computing framework designed to allow multiple student machines to pool their hardware resources and collaboratively execute computational workloads.

The goal is to transform idle personal computers into a shared compute network.

Version **v1.0.0** marks the first stable milestone:  
Internet-backed peer discovery with distributed execution.

---

##  Architecture Overview

Nexus v1.0.0 consists of two main layers:

###  Distributed Compute Layer (Runs on Student Machines)

Each node:
- Runs a FastAPI server
- Registers itself with a cloud bootstrap service
- Fetches active peer list dynamically
- Splits computational tasks
- Sends chunks to peers
- Aggregates results
- Handles peer failures gracefully

###  Cloud Bootstrap Layer (Discovery Service)

The bootstrap server:
- Maintains a registry of active nodes
- Accepts node registrations
- Removes inactive nodes via heartbeat timeout
- Returns current peer list on request

Bootstrap does NOT:
- Execute tasks
- Store computation results
- Coordinate execution
- Act as a relay

It is purely a discovery layer.


## Communication Model (v1.0.0)
Node A → HTTP → Node B
<br>
Node B → HTTP → Node C


- Direct REST-based peer-to-peer communication
- Nodes communicate using HTTP POST requests
- Distributed task chunks are executed across peers

⚠ Limitation:<br>
Nodes must be network-reachable.  
Machines behind NAT without port forwarding cannot accept inbound traffic.

This limitation will be addressed in v2.

---

## Features

- Multi-node distributed computation
- Dynamic peer discovery via cloud
- Heartbeat-based liveness detection
- Automatic chunk splitting
- Fault-tolerant execution
- Peer fallback on failure
- Local fallback execution
- Structured logging
- Scalable node count

---

## Running Nexus v1.0.0

###  Ensure Bootstrap Server Is Live

Example:
```
https://your-bootstrap-service.onrender.com
```


---

###  Start Multiple Nodes

Open separate terminals:
<br>
uvicorn node:app --port 5001
<br>
uvicorn node:app --port 5002
<br>
uvicorn node:app --port 5003


Each node:
- Registers to bootstrap
- Fetches peer list
- Starts heartbeat loop

---

###  Verify Active Peers

Open:
```
http://localhost:5001/docs
```


Execute:

POST `/distributed_sum`

Expected behavior:
- Each node processes a different chunk
- Logs appear in all terminals
- Results are aggregated
- Final result returned by coordinator

---

## Fault Tolerance

If a peer fails during execution:

- The system detects failure
- Reassigns chunk to another peer
- Falls back to local execution if necessary
- Final result remains correct

Correctness is guaranteed even with partial node failure.

---

## Version History

| Version | Description |
|----------|-------------|
| v0.0.0 | Local multi-process distributed execution |
| v1.0.0 | Internet-backed peer discovery + direct HTTP distributed execution |

---

## Current Status

Nexus v1.0.0 is stable and frozen.

Discovery Layer: Cloud bootstrap  
Transport Layer: Direct HTTP  
Compute Layer: Distributed chunk execution  

Next Evolution:
Relay-based transport layer to enable NAT-safe internet-wide student networking.

---

Nexus is evolving from a distributed experiment into a real peer compute platform.