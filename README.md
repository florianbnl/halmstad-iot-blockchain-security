# 🔐 Zero-Trust IoT & Blockchain Simulation

> Simulation of a Zero-Trust IoT ecosystem leveraging **Blockchain (Ethereum/Solidity)** for identity and governance, **IPFS** for secure firmware storage, and **MQTT** for real-time telemetry and commands. The project features a **Multi-Signature** governance model for firmware updates and **Merkle Tree** anchoring for data integrity.

---

## 📋 Prerequisites

Before starting, ensure you have the following installed:

- [Docker & Docker Compose](https://docs.docker.com/get-docker/)
- [Node.js](https://nodejs.org/) (including `npm`)
- [Truffle Suite](https://trufflesuite.com/) — `npm install -g truffle`
- [Ganache](https://trufflesuite.com/ganache/) (UI or CLI)

---

## 🛠️ Installation & Setup

### 1. Smart Contract Deployment

Compile and migrate the smart contracts to Ganache before running the application:

1. Open **Ganache** and ensure it is running on port `7545`.

2. Compile the Solidity contracts:

```bash
truffle compile
```

3. Deploy the contracts to the local network:

```bash
truffle migrate --network development --reset
```

---

## ⚙️ Configuration (`.env`)

Create a `.env` file in the root directory and fill it with the information generated during the Truffle migration and Ganache setup.

### Required Environment Variables

| Variable | Description |
|---|---|
| `RPC_URL` | `http://host.docker.internal:7545` — allows Docker to communicate with Ganache on the host |
| `CONTRACT_ADDRESS` | Address of the deployed `IoTSystem` contract (found in the `truffle migrate` output) |
| `PRIVATE_KEY` | Private key of the **MANUFACTURER** account (the account that deployed the contract) |
| `MQTT_BROKER` | `mqtt-broker` — the service name defined in `docker-compose` |
| `MONGO_URI` | `mongodb://mongodb:27017/iot_gateway` |

### ABI Synchronization

After migration, copy the generated ABI from `build/contracts/IoTSystem.json` and ensure it is available as `abi.json` for the Python simulator and Gateway.

---

## 🚀 Running the Project

Once Ganache is configured and the `.env` file is ready, build and start the services using Docker Compose:

```bash
docker-compose up --build
```

---

## 🔍 System Architecture

The project is divided into three main roles:

### 1. 🏭 Manufacturer *(Root of Trust)*
Registers device hashes on the blockchain and manages Gateway authorization.

### 2. 🗳️ Admins *(Governance)*
Propose and vote on firmware updates via a **Multi-Signature** process.

### 3. 📡 IoT Devices & Gateway

| Component | Role |
|---|---|
| **Devices** | Sign telemetry data and verify firmware hashes against the blockchain |
| **Gateway** | Aggregates data, generates Merkle Trees, and anchors roots to the blockchain for auditability |

---

## 🛠 Troubleshooting

| Issue | Solution |
|---|---|
| **Connection Error (RPC)** | Ensure Ganache is set to listen on `0.0.0.0` or that `host.docker.internal` is correctly resolved by your Docker setup |
| **Nonce Issues** | If transactions fail, restart the Gateway to reset the internal nonce counter or check the manufacturer's account in Ganache |
| **MQTT Connectivity** | Ensure the `mqtt-broker` container is healthy before launching the Simulator |