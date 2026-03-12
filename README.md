# Nexus

### Distributed Computing Framework

Nexus is a lightweight distributed computing framework that allows multiple machines to collaborate on computational tasks over the internet.

Nodes connect to a central relay server and distribute workloads across available machines based on real-time resource availability.

---

# Version

Current Release: **v3.1.0**

---

# Overview

Nexus enables a group of computers to act as a small compute cluster.

Instead of running heavy computations on one machine, Nexus splits the workload into smaller chunks and distributes them across connected nodes.

Each node executes its assigned task and sends the result back to the requesting node.

---

# Architecture

```
                 ┌─────────────────┐
                 │   Relay Server  │
                 │ (WebSocket Hub) │
                 └────────┬────────┘
                          │
          ┌───────────────┼───────────────┐
          │               │               │
     ┌─────────┐     ┌─────────┐     ┌─────────┐
     │ Node 1  │     │ Node 2  │     │ Node 3  │
     │Compute  │     │Compute  │     │Compute  │
     └─────────┘     └─────────┘     └─────────┘
```

Components:

**Relay Server**

* WebSocket message router
* Node registry
* Cluster resource monitor

**Nodes**

* Compute execution engine
* Task registry
* Distributed scheduler
* Resource monitor

---

# Key Features

### Distributed Task Execution

Tasks are divided into chunks and executed across multiple nodes.

### Parallel Processing

Nodes execute workloads simultaneously for faster computation.

### Resource Monitoring

Each node periodically reports CPU and RAM usage to the relay server.

### Resource-Aware Scheduling

Tasks are assigned to nodes with the lowest CPU and RAM utilization.

### Task Registry

Nexus supports multiple compute tasks through a modular registry system.

### Fault Tolerant Execution

Nexus automatically retries distributed tasks if a worker node fails during execution.  
Chunks are reassigned to available nodes to ensure job completion.

### Organized API Documentation

FastAPI endpoints are grouped using tags, providing a clean and structured API interface in `/docs`.

---

# Supported Distributed Tasks

| Task            | Description                     |
| --------------- | ------------------------------- |
| `sum`           | Sum of numbers in a range       |
| `prime_count`   | Counts prime numbers in a range |
| `vector_sum`    | Sum of squared numbers          |
| `factorial_sum` | Sum of factorial values         |
| `fibonacci_sum` | Sum of fibonacci numbers        |
| `power_sum`     | Sum of fifth powers             |


New tasks can be easily added through the **task registry**.

---

# Project Structure

```
Nexus/
│
├── relay/
│   ├── relay.py
│   └── __init__.py
│
├── node.py
├── relay_client.py
├── relay_task.py
├── relay_registry.py
├── scheduler.py
├── resource_monitor.py
├── tasks_registry.py
├── compute.py
├── config.py
├── logger.py
├── requirements.txt
└── README.md
```

---

# Running the Relay Server

Start the relay server:

```
uvicorn relay.relay:app --host 0.0.0.0 --port 9000
```

Relay responsibilities:

* Node communication
* Task routing
* Resource tracking
* Cluster status

---

# Running Nexus Nodes

Each computer runs a Nexus node.

Example:

```
NODE_ID=node_1 python -m uvicorn node:app --port 5001
```

Second machine:

```
NODE_ID=node_2 python -m uvicorn node:app --port 5002
```

Nodes automatically:

* connect to relay
* report resources
* receive compute tasks

---

# Executing a Distributed Task

Open the API documentation:

```
http://localhost:5001/docs
```

Use the endpoint:

```
POST /distributed_task
```

Example request:

```json
{
  "task": "prime_count",
  "start": 1,
  "end": 100000
}
```

The workload will automatically be distributed across connected nodes.

---
## Running Nexus Node (Executable)

Nexus nodes can be started using the standalone executable without installing Python or cloning the repository.

### Step 1 — Download

Download the `nexus-node.exe` file from the repository.

### Step 2 — Start a Node

Run the following command:

```bash
.\nexus-node.exe start --node-id node_1 --port 5001
```

Example:

```bash
.\nexus-node.exe start --node-id node_1 --port 5001
```

### Parameters

| Parameter | Description |
|----------|-------------|
| `--node-id` | Unique identifier for the node |
| `--port` | Port on which the node will run |

Example for a second node:

```bash
.\nexus-node.exe start --node-id node_2 --port 5002
```

### Access Node API

After starting the node, open:

```
http://localhost:PORT/docs
```

Example:

```
http://localhost:5001/docs
```

This opens the interactive API where you can trigger distributed tasks.

### Example Distributed Task

From Node 1:

```
POST /distributed_task
```

Payload example:

```json
{
  "task": "sum",
  "start": 1,
  "end": 100000000
}
```

The workload will automatically be distributed across available Nexus nodes.

---

### Example Network Setup

Machine 1:

```
.\nexus-node.exe start --node-id node_1 --port 5001
```

Machine 2:

```
.\nexus-node.exe start --node-id node_2 --port 5002
```

Both nodes automatically connect to the Nexus relay server and become part of the compute network.
---
# Cluster Monitoring

Relay endpoints:

```
/nodes
/resources
/cluster_status
```

Example:

```
https://nexus-relay-5wog.onrender.com/cluster_status
```

Returns connected nodes and their resource usage.

---

# Example Workflow

1. Start relay server
2. Start multiple Nexus nodes
3. Submit distributed task
4. Scheduler selects best nodes
5. Nodes execute chunks
6. Results are aggregated

---

# Roadmap

### v1

Local distributed computation

### v2

Internet-connected nodes using relay transport

### v3

Resource-aware distributed compute framework with retry-based fault tolerance

### v4 (Planned)

Secure sandboxed execution of arbitrary workloads

---

# Use Cases

* Distributed mathematical computation
* Parallel simulations
* CPU-intensive research workloads
* Experimental distributed computing systems
* Educational distributed systems platform
