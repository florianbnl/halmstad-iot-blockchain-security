require('dotenv').config();
const { ethers } = require('ethers');
const mqtt = require('mqtt');
const { MerkleTree } = require('merkletreejs');
const keccak256 = require('keccak256');
const mongoose = require('mongoose');

let totalDataReceived = 0;
let totalLatency = 0;
let totalProcessedItems = 0;
let totalMerkleGenTime = 0;
let totalBatchesCreated = 0;

// --- CONFIGURATION MONGODB ---
const MONGO_URI = process.env.MONGO_URI || 'mongodb://mongodb:27017/iot_gateway';
mongoose.connect(MONGO_URI).then(() => console.log(`[DB] Connecté à MongoDB et ${totalDataReceived} données reçues`)).catch(err => console.error("[DB] Erreur de connexion", err));

// Schéma mis à jour
const PendingDataSchema = new mongoose.Schema({
    rawData: Object,
    signature: String,
    receivedAt: { type: Number, default: () => Date.now() }

});
const PendingData = mongoose.model('PendingData', PendingDataSchema);

const BatchSchema = new mongoose.Schema({
    merkleRoot: String,
    transactionHash: String,
    timestamp: { type: Date, default: Date.now },
    // data contient maintenant: { rawData: {...}, signature: "0x..." }
    data: Array,
    status: String
});
const Batch = mongoose.model('Batch', BatchSchema);

// --- CONFIGURATION BLOCKCHAIN ---
const provider = new ethers.JsonRpcProvider(process.env.RPC_URL);
// La gateway doit avoir sa propre clé pour signer les données reçues
const gatewayWallet = new ethers.Wallet(process.env.PRIVATE_KEY, provider);

const contractABI = JSON.parse(process.env.CONTRACT_ABI);
const contract = new ethers.Contract(process.env.CONTRACT_ADDRESS, contractABI, gatewayWallet);

// --- CONFIGURATION MQTT ---
const mqttClient = mqtt.connect(process.env.MQTT_BROKER_URL);
// --- LOGIQUE IOT -> BLOCKCHAIN ---
mqttClient.on('connect', () => {
    mqttClient.subscribe('iot/data');
    mqttClient.subscribe('gateway/command/audit');
});

const BATCH_SIZE = 100; // Nombre de données par ancrage
let dataBuffer = [];
let isProcessing = false; // Flag pour éviter les conflits

// --- MODIFICATION DANS index.js (Gateway) ---

async function processQueue() {
    if (isProcessing) return;

    // 1. Verrouiller immédiatement
    isProcessing = true;

    try {
        console.log(`[QUEUE] Vérification de la queue...`);

        // 2. Vérifier si on a assez de données
        const count = await PendingData.countDocuments();
        if (count < BATCH_SIZE) {
            isProcessing = false; // Relâcher le verrou
            return;
        }

        console.log(`[QUEUE] Traitement de ${BATCH_SIZE} éléments...`);

        // 3. RÉCUPÉRER ET SUPPRIMER ATOMIQUEMENT (la méthode la plus sûre)
        // On récupère les plus anciennes pour les supprimer
        const itemsToDelete = await PendingData.find()
            .sort({ receivedAt: 1 })
            .limit(BATCH_SIZE);

        const idsToDelete = itemsToDelete.map(item => item._id);

        // Supprimer directement pour éviter que d'autres processus ne les prennent
        await PendingData.deleteMany({ _id: { $in: idsToDelete } });

        // 4. Lancer l'ancrage avec les données récupérées
        await anchorData(itemsToDelete);

        console.log(`[QUEUE] Batch ancré et supprimé de la queue.`);

    } catch (err) {
        console.error("[QUEUE] Erreur lors du traitement", err);
        // En cas d'erreur, il faudrait idéalement remettre les données dans la DB,
        // mais pour Ganache, on va se concentrer sur la structure.
    } finally {
        // 5. Relâcher le verrou quoi qu'il arrive
        isProcessing = false;

        // Vérifier s'il reste assez de données pour un autre batch
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

            const cleanedData = getStructuredData(payload.data);
            const dataString = JSON.stringify(cleanedData);
            const signature = await gatewayWallet.signMessage(dataString);

            // 1. --- SAUVEGARDE PERSISTANTE DANS MONGODB ---
            await PendingData.create({
                rawData: {
                    data: cleanedData,
                    signature: payload.signature
                },
                signature: signature
            });

            // console.log(`[QUEUE] Data persistée. Total reçus: ${totalDataReceived}`);

            // 2. --- DÉCLENCHER LE TRAITEMENT SI ASSEZ DE DATA ---
            const count = await PendingData.countDocuments();
            if (count >= BATCH_SIZE && !isProcessing) {
                processQueue();
            }

        } catch (e) {
            console.error("[ERROR] Traitement message MQTT", e);
        }
    } else if (topic === 'gateway/command/audit') {
        console.log("\n[MQTT CMD] Commande d'audit reçue !");
        await verifyAllMerkleRoots();
    } else if (topic === 'gateway/command/audit-missing') {
        await checkMissingData();
    }
});

let currentNonce = null;

async function anchorData(batch) {

    console.log("[GATEWAY] Génération du Merkle Tree avec signatures...");

    // 1. Calcul des feuilles: HASH(Donnée + Signature)
    console.log("[GATEWAY] --- DÉBUT ANCRAGE ---");

    const leaves = batch.map((item, index) => {
        // Nettoyage pour garantir l'ordre
        const cleanedData = getStructuredData(item.rawData.data);
        const sigObjet = item.rawData.signature;
        const sigGateway = item.signature;
        const dataString = JSON.stringify(cleanedData);

        const combinedString = dataString + sigObjet + sigGateway;
        const leafHash = keccak256(combinedString);

        return leafHash;
    });
    const startTimeAncrage = Date.now(); // Moment où on commence la génération
    const tree = new MerkleTree(leaves, keccak256, { sortPairs: true });
    const root = tree.getHexRoot();

    const endTimeAncrage = Date.now();

    const currentGenTime = endTimeAncrage - startTimeAncrage;
    totalMerkleGenTime += currentGenTime;
    totalBatchesCreated++;
    const avgMerkleGenTime = totalMerkleGenTime / totalBatchesCreated;

    // Latence de bout-en-bout (Objet -> Root)
    batch.forEach(item => {
        const latency = endTimeAncrage - item.receivedAt;
        totalLatency += latency;
        totalProcessedItems++;
    });

    const averageLatency = totalLatency / totalProcessedItems;

    console.log(`[BENCHMARK] --- ANALYSE BATCH N°${totalBatchesCreated} ---`);
    console.log(`[BENCHMARK] Temps de calcul (ce batch) : ${currentGenTime}ms`);
    console.log(`[BENCHMARK] Temps de calcul MOYEN : ${avgMerkleGenTime.toFixed(2)}ms`);
    console.log(`[BENCHMARK] number of tests : ${totalBatchesCreated}`);
    // ----------------------------

    try {
        const signer = contract.runner;
        const provider = signer.provider;

        // 2. Initialiser le nonce si nécessaire
        if (currentNonce === null) {
            currentNonce = await provider.getTransactionCount(signer.address);
        }

        const feeData = await provider.getFeeData();
        const gasPriceWithMargin = (feeData.gasPrice * BigInt(150)) / BigInt(100);

        console.log(`[BLOCKCHAIN] Envoi racine : ${root} avec nonce : ${currentNonce}`);

        // 3. Envoyer la transaction AVEC le nonce spécifique
        const tx = await contract.updateGlobalDataRoot(root, {
            gasPrice: gasPriceWithMargin,
            nonce: currentNonce // <--- ICI
        });

        // 4. Incrémenter le nonce pour la prochaine fois
        currentNonce++;

        const receipt = await tx.wait();
        console.log(`[BLOCKCHAIN] Succès ! Tx: ${receipt.hash}`);

        const batchToSave = batch.map(item => item.toObject());
        // 2. Sauvegarde en DB (contient maintenant donnée + signature)
        const newBatch = new Batch({
            merkleRoot: root,
            transactionHash: receipt.hash,
            data: batchToSave, // Contient {rawData, signature}
            status: "anchored"
        });
        await newBatch.save();
        console.log("[DB] Batch avec signatures enregistré.");

    } catch (err) {
        console.error("[ERROR] Échec de l'ancrage :", err.message);
        // Si erreur, il faut souvent réinitialiser le nonce pour la prochaine tentative
        currentNonce = null;
    }
}

// --- FONCTION D'AUDIT / VÉRIFICATION MISE À JOUR ---
async function verifyAllMerkleRoots() {

    try {
        const allBatches = await Batch.find({ status: "anchored" });

        for (const batch of allBatches) {

            const leaves = batch.data.map((item, index) => {

                // Nettoyage pour garantir l'ordre
                const cleanedData = getStructuredData(item.rawData.data);
                const dataString = JSON.stringify(cleanedData);

                const sigObjet = item.rawData.signature;
                const sigGateway = item.signature;

                const combinedString = dataString + sigObjet + sigGateway;
                const leafHash = keccak256(combinedString);

                return leafHash;
            });

            const tree = new MerkleTree(leaves, keccak256, { sortPairs: true });
            const computedRoot = tree.getHexRoot();

            if (computedRoot === batch.merkleRoot) {
                console.log("[AUDIT] ✅ OK");
            } else {
                console.error("[AUDIT] ❌ ERREUR: Racine mismatch");
            }
        }
    } catch (err) {
        console.error("[ERROR] Audit échoué", err);
    }
}

// --- FONCTION UTILITAIRE DE STRUCTURATION (À ne pas oublier dans ton code) ---
const getStructuredData = (rawData) => {
    // Force la présence, le type et l'ordre des champs
    const structured = {
        dev: rawData.dev,
        ts: parseInt(rawData.ts),
        val: rawData.val.toString()
    };
    // Trie alphabétiquement (dev, ts, val)
    return Object.keys(structured).sort().reduce((acc, key) => {
        acc[key] = structured[key];
        return acc;
    }, {});
};

const express = require('express');
const app = express();
app.use(express.json());
const cors = require('cors'); // N'oublie pas le CORS pour ton HTML
app.use(cors({ origin: 'http://localhost:5173' }));

// ... (config web3, contract, mqttClient, gatewayWallet existantes) ...

app.post('/api/send-command', async (req, res) => {
    // 1. Récupérer userSignature en plus
    const { userAddress, devAddress, message, userSignature, ts } = req.body;

    try {
        // --- NOUVELLE ÉTAPE : VÉRIFICATION CRYPTOGRAPHIQUE ---
        const messageToVerify = JSON.stringify({ devAddress, message, ts });

        // On demande à ethers de retrouver quelle adresse a signé ce message
        const recoveredAddress = ethers.verifyMessage(messageToVerify, userSignature);
        if (recoveredAddress.toLowerCase() !== userAddress.toLowerCase()) {
            console.log(`[SECURITY] Signature invalide. Reçu: ${userAddress}, Vérifié: ${recoveredAddress}`);
            return res.status(401).json({ error: "Signature invalide." });
        }
        console.log(`[SECURITY] Signature valide pour ${userAddress}`);
        // ----------------------------------------------------

        // APPEL BLOCKCHAIN : On demande au contrat si l'user est autorisé
        const authorized = await contract.authorizations(devAddress, userAddress);

        if (!authorized) {
            console.log(`[SECURITY] Tentative non autorisée de ${userAddress} sur ${devAddress}`);
            return res.status(403).json({ error: "Accès refusé par le Smart Contract." });
        }

        // Si OK, on signe et on envoie en MQTT
        const commandPayload = { target: devAddress, msg: message, ts: ts };
        const signature = await gatewayWallet.signMessage(JSON.stringify(commandPayload));

        mqttClient.publish(`iot/command/${devAddress}`, JSON.stringify({ ...commandPayload, signature }));

        res.json({ status: "Commande validée par Blockchain et envoyée." });

    } catch (error) {
        console.error(error);
        res.status(500).json({ error: "Erreur lors de la vérification blockchain." });
    }
});

app.listen(3001, '0.0.0.0', () => console.log("[GATEWAY] API Web lancée sur le port 3001"));

// --- AJOUTER DANS INDEX.JS (GATEWAY) ---

// 1. API: Ajouter un Admin (Vérifier si sender est admin)
app.post('/api/add-admin', async (req, res) => {
    const { adminAddress, newAdmin } = req.body;
    try {
        // Vérifier si adminAddress est bien dans le contrat
        const isAdmin = await contract.isAdmin(adminAddress);
        if (!isAdmin) return res.status(403).json({ error: "Non autorisé" });

        const tx = await contract.addAdmin(newAdmin);
        await tx.wait();
        res.json({ status: "Admin ajouté" });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// 2. API: Lancer Mise à jour
app.post('/api/update-firmware', async (req, res) => {
    const { devAddress, version } = req.body;

    try {
        // 1. Vérification Blockchain (Multi-sig validé ?)
        const firmware = await contract.firmwareRepo(version);
        if (!firmware.isValid) {
            return res.status(403).json({ error: "Firmware non validé par les admins (Multi-sig en cours)." });
        }

        console.log(`[GATEWAY] 🚀 Firmware ${version} prêt. Envoi du signal WAKEUP à ${devAddress}...`);

        // 2. ENVOI DU SIGNAL DE RÉVEIL via MQTT
        const wakeupPayload = {
            action: "WAKEUP_FOR_FOTA",
            version: version,
            hash: firmware.fileHash,
            cid: firmware.ipfsCID
        };

        mqttClient.publish(`iot/wakeup/${devAddress}`, JSON.stringify(wakeupPayload));

        res.json({ status: "Signal de réveil envoyé. En attente de l'acceptation de l'objet." });

    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

async function checkMissingData() {
    console.log("\n[AUDIT] 🔍 Démarrage de la vérification des données manquantes...");

    try {
        // 1. Récupérer tous les batches ancrés
        const allBatches = await Batch.find({ status: "anchored" });

        let allValues = [];

        // 2. Extraire toutes les valeurs de tous les batches
        allBatches.forEach(batch => {
            batch.data.forEach(item => {
                // Supposons que ta valeur est dans item.rawData.data.val
                const val = parseInt(item.rawData.data.val);
                if (!isNaN(val)) {
                    allValues.push(val);
                }
            });
        });

        if (allValues.length === 0) {
            console.log("[AUDIT] Aucune donnée trouvée en DB.");
            return;
        }

        // 3. Trier les valeurs
        allValues.sort((a, b) => a - b);

        const min = allValues[0];
        const max = allValues[allValues.length - 1];
        let missingValues = [];
        let setOfValues = new Set(allValues);

        // 4. Chercher les manquants dans l'intervalle
        for (let i = min; i <= max; i++) {
            if (!setOfValues.has(i)) {
                missingValues.push(i);
            }
        }

        // 5. Afficher les résultats
        console.log(`[AUDIT] Valeurs reçues : ${allValues.length}`);
        console.log(`[AUDIT] Intervalle : ${min} - ${max}`);

        if (missingValues.length === 0) {
            console.log("[AUDIT] ✅ Aucune donnée manquante détectée !");
        } else {
            console.error(`[AUDIT] ❌ ${missingValues.length} valeurs manquantes détectées.`);
            // Si la liste est trop longue, on n'affiche que le début
            console.error(`[AUDIT] Exemples de manquants : ${missingValues.slice(0, 20).join(', ')}...`);
        }

        // Comparaison avec totalDataReceived
        console.log(`[BENCHMARK] Total reçus (MQTT) : ${totalDataReceived}`);
        console.log(`[BENCHMARK] Total ancrés (DB) : ${allValues.length}`);

    } catch (err) {
        console.error("[ERROR] Audit manquants échoué", err);
    }
}

// --- LOGIQUE AUTO-FOTA AMÉLIORÉE ---
contract.on("FirmwarePublished", async (version, fileHash) => {
    try {
        console.log(`\n[EVENT] 🔔 Blockchain : Firmware ${version} validé par multi-sig.`);

        // 1. Récupération des métadonnées (dont le précieux CID IPFS)
        const fwDetails = await contract.firmwareRepo(version);

        if (fwDetails.isValid) {
            const wakeupPayload = {
                action: "WAKEUP_FOR_FOTA",
                version: version,
                hash: fileHash,
                ipfs_cid: fwDetails.ipfsCID // Utilise la même clé que dans ton script Python
            };

            // 2. Diffusion via MQTT
            // On utilise le topic broadcast pour que tous les objets actifs reçoivent l'alerte
            mqttClient.publish(`iot/wakeup/broadcast`, JSON.stringify(wakeupPayload));

            console.log(`[AUTO-FOTA] 📡 Signal envoyé à tous les objets pour la version ${version}`);
            console.log(`[IPFS] CID associé : ${fwDetails.ipfsCID}`);
        }
    } catch (error) {
        console.error("[AUTO-FOTA] ❌ Erreur lors du traitement de l'événement :", error);
    }
});