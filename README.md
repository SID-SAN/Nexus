# Nexus

Nexus is a lightweight distributed computing framework that allows multiple machines to collaborate on computational tasks over the internet.

It demonstrates how independent nodes can connect to a central relay server and execute distributed workloads without requiring direct peer-to-peer networking.

The system uses WebSockets for communication and dynamically discovers active nodes through the relay.

---

# Architecture

Nexus uses a relay-based architecture for routing messages between nodes.
```
Client / Node
↓
Relay Server (Render)
↓
Other Nodes
```

Each node connects to the relay via WebSocket and can send or receive tasks through the relay.

The relay acts as a **message router and node registry**, allowing nodes to discover each other without direct connections.

---

# Features

• Distributed task execution  
• Relay-based networking (no direct node connections required)  
• Dynamic node discovery  
• Internet-capable distributed system  
• Fault-tolerant message routing  
• Horizontally scalable node architecture  

---

# Project Evolution

Nexus was developed in multiple stages.

### v0 — Local Compute

Basic computation logic implemented locally.

Features:
- Task execution
- Range-based computation

---

### v1 — Bootstrap Networking

Introduced peer discovery using a bootstrap server.

Features:
- Node registration
- Peer discovery
- Heartbeat monitoring

---

### v2 — Relay Distributed System

Current architecture using a central relay server.

Features:
- WebSocket communication
- Message routing between nodes
- Node registry via relay
- Internet-based distributed execution

---

# Repository Structure
```
Nexus
│
├── relay/
│ ├── init.py
│ └── relay.py
│
├── node.py
├── relay_client.py
├── relay_task.py
├── relay_registry.py
│
├── compute.py
├── config.py
├── logger.py
│
├── requirements.txt
├── README.md
└── future_features.md
```


---

# Core Components

### Relay Server

The relay server manages node connections and routes messages between nodes.

Responsibilities:

- Maintain active node connections
- Route tasks between nodes
- Provide node registry API

---

### Node

Each node:

- Connects to the relay
- Receives distributed tasks
- Executes computation
- Returns results

---

### Relay Client

Maintains a persistent WebSocket connection with the relay server.

Handles incoming task messages and result routing.

---

### Relay Task

Responsible for sending computation tasks to remote nodes.

---

### Compute Module

Contains the computation logic.

Currently implemented:

- Range-based sum computation

---

# Running the System

## 1 Start Relay Server

The relay server is deployed on Render.

Example endpoint:
```
https://nexus-relay-5wog.onrender.com
```

---

## 2 Start Nodes

Run nodes with different IDs.

Node 1:
```
NODE_ID=node_1 uvicorn node:app --port 5001
```
Node 2:
```
NODE_ID=node_2 uvicorn node:app --port 5002
```

---

## 3 Run Distributed Task

Open:
```
http://127.0.0.1:5001/docs
```
Run:
```
POST/distributed_sum
```

The task will be split across available nodes.

---

# Example Output
```
{
"node": "node_1",
"result": 55
}
```

---

# Future Improvements

See `future_features.md` for planned upgrades.

Potential future features:

- Distributed task scheduler
- Dynamic load balancing
- Fault-tolerant node recovery
- Multi-task distributed workloads
- GPU-aware scheduling
- Large-scale cluster support

---

# Tech Stack

Python  
FastAPI  
WebSockets  
Uvicorn  
Render (deployment)

---

# Why Nexus

Nexus demonstrates core distributed systems concepts:

- distributed task execution
- message routing
- node discovery
- fault tolerance
- scalable architecture

The project serves as a learning platform for building distributed computing systems similar in concept to frameworks like Ray or Dask.
