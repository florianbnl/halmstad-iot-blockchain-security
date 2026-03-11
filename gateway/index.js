require('dotenv').config();
const { ethers } = require('ethers');
const mqtt = require('mqtt');
const { MerkleTree } = require('merkletreejs');
const keccak256 = require('keccak256');
const mongoose = require('mongoose');

// --- METRICS & BENCHMARKS ---
let totalDataReceived = 0;
let totalLatency = 0;
let totalProcessedItems = 0;
let totalMerkleGenTime = 0;
let totalBatchesCreated = 0;

// --- MONGODB CONFIGURATION ---
const MONGO_URI = process.env.MONGO_URI || 'mongodb://mongodb:27017/iot_gateway';
mongoose.connect(MONGO_URI)
    .then(() => console.log(`[DB] Connected to MongoDB. ${totalDataReceived} data points received so far.`))
    .catch(err => console.error("[DB] Connection error:", err));

// Schema for data waiting to be anchored
const PendingDataSchema = new mongoose.Schema({
    rawData: Object,
    signature: String,
    receivedAt: { type: Number, default: () => Date.now() }
});
const PendingData = mongoose.model('PendingData', PendingDataSchema);

// Schema for anchored batches on the blockchain
const BatchSchema = new mongoose.Schema({
    merkleRoot: String,
    transactionHash: String,
    timestamp: { type: Date, default: Date.now },
    data: Array, // Contains: { rawData: {...}, signature: "0x..." }
    status: String
});
const Batch = mongoose.model('Batch', BatchSchema);

// --- BLOCKCHAIN CONFIGURATION ---
const provider = new ethers.JsonRpcProvider(process.env.RPC_URL);
// The gateway uses its own wallet to sign incoming data
const gatewayWallet = new ethers.Wallet(process.env.PRIVATE_KEY, provider);

const contractABI = JSON.parse(process.env.CONTRACT_ABI);
const contract = new ethers.Contract(process.env.CONTRACT_ADDRESS, contractABI, gatewayWallet);

// --- MQTT CONFIGURATION ---
const mqttClient = mqtt.connect(process.env.MQTT_BROKER_URL);

// --- IOT TO BLOCKCHAIN LOGIC ---
mqttClient.on('connect', () => {
    mqttClient.subscribe('iot/data');
    mqttClient.subscribe('gateway/command/audit');
});

const BATCH_SIZE = 100; // Number of data points per blockchain anchor
let isProcessing = false; // Flag to prevent concurrent processing conflicts

/**
 * Periodically checks the database for pending data.
 * If the BATCH_SIZE is reached, it extracts the data and triggers the anchoring process.
 */
async function processQueue() {
    if (isProcessing) return;

    // 1. Lock processing
    isProcessing = true;

    try {
        console.log(`[QUEUE] Checking queue...`);

        // 2. Check if enough data is available
        const count = await PendingData.countDocuments();
        if (count < BATCH_SIZE) {
            isProcessing = false;
            return;
        }

        console.log(`[QUEUE] Processing batch of ${BATCH_SIZE} items...`);

        // 3. Atomic Retrieval and Deletion
        const itemsToDelete = await PendingData.find()
            .sort({ receivedAt: 1 })
            .limit(BATCH_SIZE);

        const idsToDelete = itemsToDelete.map(item => item._id);

        // Delete immediately to prevent other processes from picking them up
        await PendingData.deleteMany({ _id: { $in: idsToDelete } });

        // 4. Start anchoring with the retrieved data
        await anchorData(itemsToDelete);

        console.log(`[QUEUE] Batch anchored and removed from pending queue.`);

    } catch (err) {
        console.error("[QUEUE] Error during processing:", err);
    } finally {
        // 5. Release lock
        isProcessing = false;

        // Check if enough data remains for another consecutive batch
        const remaining = await PendingData.countDocuments();
        if (remaining >= BATCH_SIZE) {
            processQueue();
        }
    }
}

mqttClient.on('message', async (topic, message) => {
    if (topic === 'iot/data') {
        try {
            totalDataReceived++;
            const payload = JSON.parse(message.toString());

            // Structure data and sign it with Gateway key for non-repudiation
            const cleanedData = getStructuredData(payload.data);
            const dataString = JSON.stringify(cleanedData);
            const signature = await gatewayWallet.signMessage(dataString);

            // 1. --- PERSISTENT STORAGE IN MONGODB ---
            await PendingData.create({
                rawData: {
                    data: cleanedData,
                    signature: payload.signature // Original device signature
                },
                signature: signature // Gateway signature
            });

            // 2. --- TRIGGER QUEUE PROCESSING ---
            const count = await PendingData.countDocuments();
            if (count >= BATCH_SIZE && !isProcessing) {
                processQueue();
            }

        } catch (e) {
            console.error("[ERROR] MQTT message processing failed:", e);
        }
    } else if (topic === 'gateway/command/audit') {
        console.log("\n[MQTT CMD] Audit command received!");
        await verifyAllMerkleRoots();
    } else if (topic === 'gateway/command/audit-missing') {
        console.log("\n[MQTT CMD] Gap analysis command received!");
        await checkMissingData();
    }
});

let currentNonce = null;

/**
 * Generates a Merkle Tree from a batch of data, calculates the root,
 * and anchors this root on the blockchain.
 * @param {Array} batch - Array of data objects from the database.
 */
async function anchorData(batch) {
    console.log("[GATEWAY] Generating Merkle Tree with signatures...");
    console.log("[GATEWAY] --- STARTING ANCHORING ---");

    // 1. Calculate leaves: HASH(Data + Device Signature + Gateway Signature)
    const leaves = batch.map((item) => {
        const cleanedData = getStructuredData(item.rawData.data);
        const sigObjet = item.rawData.signature;
        const sigGateway = item.signature;
        const dataString = JSON.stringify(cleanedData);

        const combinedString = dataString + sigObjet + sigGateway;
        const leafHash = keccak256(combinedString);

        return leafHash;
    });

    const startTimeAncrage = Date.now();
    const tree = new MerkleTree(leaves, keccak256, { sortPairs: true });
    const root = tree.getHexRoot();
    const endTimeAncrage = Date.now();

    // Benchmark calculations
    const currentGenTime = endTimeAncrage - startTimeAncrage;
    totalMerkleGenTime += currentGenTime;
    totalBatchesCreated++;
    const avgMerkleGenTime = totalMerkleGenTime / totalBatchesCreated;

    // End-to-end Latency (Device to Root Generation)
    batch.forEach(item => {
        const latency = endTimeAncrage - item.receivedAt;
        totalLatency += latency;
        totalProcessedItems++;
    });

    const averageLatency = totalLatency / totalProcessedItems;

    console.log(`[BENCHMARK] --- BATCH ANALYSIS #${totalBatchesCreated} ---`);
    console.log(`[BENCHMARK] Calculation time (this batch): ${currentGenTime}ms`);
    console.log(`[BENCHMARK] Average calculation time: ${avgMerkleGenTime.toFixed(2)}ms`);
    console.log(`[BENCHMARK] Total tests: ${totalBatchesCreated}`);

    try {
        const signer = contract.runner;
        const provider = signer.provider;

        // 2. Initialize Nonce to handle rapid transactions
        if (currentNonce === null) {
            currentNonce = await provider.getTransactionCount(signer.address);
        }

        const feeData = await provider.getFeeData();
        const gasPriceWithMargin = (feeData.gasPrice * BigInt(150)) / BigInt(100);

        console.log(`[BLOCKCHAIN] Sending root: ${root} with nonce: ${currentNonce}`);

        // 3. Send transaction to Smart Contract
        const tx = await contract.updateGlobalDataRoot(root, {
            gasPrice: gasPriceWithMargin,
            nonce: currentNonce
        });

        currentNonce++;

        const receipt = await tx.wait();
        console.log(`[BLOCKCHAIN] Success! Tx: ${receipt.hash}`);

        // 4. Save anchored batch to DB for future audits
        const batchToSave = batch.map(item => item.toObject());
        const newBatch = new Batch({
            merkleRoot: root,
            transactionHash: receipt.hash,
            data: batchToSave,
            status: "anchored"
        });
        await newBatch.save();
        console.log("[DB] Batch with signatures successfully saved.");

    } catch (err) {
        console.error("[ERROR] Anchoring failed:", err.message);
        currentNonce = null; // Reset nonce on error
    }
}

/**
 * Audit function: Re-calculates Merkle Roots for all stored batches 
 * and compares them with the roots stored in the database.
 */
async function verifyAllMerkleRoots() {
    try {
        const allBatches = await Batch.find({ status: "anchored" });
        console.log(`[AUDIT] Starting integrity check on ${allBatches.length} batches...`);

        for (const batch of allBatches) {
            const leaves = batch.data.map((item) => {
                const cleanedData = getStructuredData(item.rawData.data);
                const dataString = JSON.stringify(cleanedData);
                const sigObjet = item.rawData.signature;
                const sigGateway = item.signature;

                const combinedString = dataString + sigObjet + sigGateway;
                return keccak256(combinedString);
            });

            const tree = new MerkleTree(leaves, keccak256, { sortPairs: true });
            const computedRoot = tree.getHexRoot();

            if (computedRoot === batch.merkleRoot) {
                console.log(`[AUDIT] Batch ${batch.merkleRoot.slice(0, 10)}...: ✅ VALID`);
            } else {
                console.error(`[AUDIT] Batch ${batch.merkleRoot.slice(0, 10)}...: ❌ CORRUPTED (Root mismatch)`);
            }
        }
    } catch (err) {
        console.error("[ERROR] Audit failed:", err);
    }
}

/**
 * Ensures data consistency by forcing field types and alphabetical order 
 * before hashing or signing.
 */
const getStructuredData = (rawData) => {
    const structured = {
        dev: rawData.dev,
        ts: parseInt(rawData.ts),
        val: rawData.val.toString()
    };
    return Object.keys(structured).sort().reduce((acc, key) => {
        acc[key] = structured[key];
        return acc;
    }, {});
};

// --- WEB API SECTION ---
const express = require('express');
const app = express();
app.use(express.json());
const cors = require('cors');
app.use(cors({ origin: 'http://localhost:5173' }));

/**
 * Endpoint to send a command to a device.
 * Requires cryptographic signature verification of the user and Smart Contract authorization check.
 */
app.post('/api/send-command', async (req, res) => {
    const { userAddress, devAddress, message, userSignature, ts } = req.body;

    try {
        // --- CRYPTOGRAPHIC VERIFICATION ---
        const messageToVerify = JSON.stringify({ devAddress, message, ts });
        const recoveredAddress = ethers.verifyMessage(messageToVerify, userSignature);

        if (recoveredAddress.toLowerCase() !== userAddress.toLowerCase()) {
            console.log(`[SECURITY] Invalid signature. Received: ${userAddress}, Recovered: ${recoveredAddress}`);
            return res.status(401).json({ error: "Invalid signature." });
        }
        console.log(`[SECURITY] Valid signature from ${userAddress}`);

        // --- BLOCKCHAIN AUTHORIZATION CHECK ---
        const authorized = await contract.authorizations(devAddress, userAddress);

        if (!authorized) {
            console.log(`[SECURITY] Unauthorized access attempt by ${userAddress} on ${devAddress}`);
            return res.status(403).json({ error: "Access denied by Smart Contract." });
        }

        // Sign command by Gateway and publish to MQTT
        const commandPayload = { target: devAddress, msg: message, ts: ts };
        const signature = await gatewayWallet.signMessage(JSON.stringify(commandPayload));

        mqttClient.publish(`iot/command/${devAddress}`, JSON.stringify({ ...commandPayload, signature }));

        res.json({ status: "Command validated by Blockchain and sent." });

    } catch (error) {
        console.error(error);
        res.status(500).json({ error: "Blockchain verification error." });
    }
});

/**
 * Admin endpoint: Add a new administrator to the Smart Contract.
 */
app.post('/api/add-admin', async (req, res) => {
    const { adminAddress, newAdmin } = req.body;
    try {
        const isAdmin = await contract.isAdmin(adminAddress);
        if (!isAdmin) return res.status(403).json({ error: "Unauthorized" });

        const tx = await contract.addAdmin(newAdmin);
        await tx.wait();
        res.json({ status: "Admin successfully added" });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

/**
 * Firmware update endpoint: Checks validity on blockchain and sends WAKEUP signal via MQTT.
 */
app.post('/api/update-firmware', async (req, res) => {
    const { devAddress, version } = req.body;

    try {
        const firmware = await contract.firmwareRepo(version);
        if (!firmware.isValid) {
            return res.status(403).json({ error: "Firmware not validated by admins (Multi-sig pending)." });
        }

        console.log(`[GATEWAY] 🚀 Firmware ${version} ready. Sending WAKEUP signal to ${devAddress}...`);

        const wakeupPayload = {
            action: "WAKEUP_FOR_FOTA",
            version: version,
            hash: firmware.fileHash,
            cid: firmware.ipfsCID
        };

        mqttClient.publish(`iot/wakeup/${devAddress}`, JSON.stringify(wakeupPayload));
        res.json({ status: "Wakeup signal sent. Waiting for device acceptance." });

    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

/**
 * Audit function: Detects gaps in telemetry values to ensure no data was lost between MQTT and DB.
 */
async function checkMissingData() {
    console.log("\n[AUDIT] 🔍 Starting missing data gap analysis...");

    try {
        const allBatches = await Batch.find({ status: "anchored" });
        let allValues = [];

        allBatches.forEach(batch => {
            batch.data.forEach(item => {
                const val = parseInt(item.rawData.data.val);
                if (!isNaN(val)) {
                    allValues.push(val);
                }
            });
        });

        if (allValues.length === 0) {
            console.log("[AUDIT] No data found in database.");
            return;
        }

        allValues.sort((a, b) => a - b);
        const min = allValues[0];
        const max = allValues[allValues.length - 1];
        let missingValues = [];
        let setOfValues = new Set(allValues);

        for (let i = min; i <= max; i++) {
            if (!setOfValues.has(i)) {
                missingValues.push(i);
            }
        }

        console.log(`[AUDIT] Total values anchored: ${allValues.length}`);
        console.log(`[AUDIT] Range: ${min} - ${max}`);

        if (missingValues.length === 0) {
            console.log("[AUDIT] ✅ No missing data detected!");
        } else {
            console.error(`[AUDIT] ❌ ${missingValues.length} missing values detected.`);
            console.error(`[AUDIT] Sample of missing values: ${missingValues.slice(0, 20).join(', ')}...`);
        }

        console.log(`[BENCHMARK] Total received (MQTT): ${totalDataReceived}`);
        console.log(`[BENCHMARK] Total anchored (DB): ${allValues.length}`);

    } catch (err) {
        console.error("[ERROR] Gap analysis failed:", err);
    }
}

/**
 * Blockchain Event Listener: Triggered when a new firmware is fully validated by Multi-sig.
 * Broadcasts the update signal to all devices.
 */
contract.on("FirmwarePublished", async (version, fileHash) => {
    try {
        console.log(`\n[EVENT] 🔔 Blockchain: Firmware ${version} validated via multi-sig.`);

        const fwDetails = await contract.firmwareRepo(version);

        if (fwDetails.isValid) {
            const wakeupPayload = {
                action: "WAKEUP_FOR_FOTA",
                version: version,
                hash: fileHash,
                ipfs_cid: fwDetails.ipfsCID
            };

            // Broadcast to all active devices
            mqttClient.publish(`iot/wakeup/broadcast`, JSON.stringify(wakeupPayload));

            console.log(`[AUTO-FOTA] 📡 Signal broadcasted for version ${version}`);
            console.log(`[IPFS] Associated CID: ${fwDetails.ipfsCID}`);
        }
    } catch (error) {
        console.error("[AUTO-FOTA] ❌ Event processing error:", error);
    }
});

app.listen(3001, '0.0.0.0', () => console.log("[GATEWAY] Web API launched on port 3001"));