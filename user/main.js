import { Wallet } from 'ethers';
import axios from 'axios'; // 1. Importe Axios

// TA CLÉ PRIVÉE DE TEST
const PRIVATE_KEY = process.env.PRIVATE_KEY;

document.getElementById('sendBtn').addEventListener('click', sendCommand);

async function sendCommand() {
    const devAddress = document.getElementById('devAddr').value;
    const message = document.getElementById('msg').value;
    const status = document.getElementById('status');

    status.innerText = "Signature en cours...";

    try {
        const wallet = new Wallet(PRIVATE_KEY);
        const userAddress = wallet.address;
        const ts = Date.now();

        const messagePayload = JSON.stringify({ devAddress, message, ts });

        // Signature
        const userSignature = await wallet.signMessage(messagePayload);

        status.innerText = "Envoi à la Gateway...";

        // 2. Utilisation d'Axios pour le POST
        const response = await axios.post('http://localhost:3001/api/send-command', {
            userAddress,
            devAddress,
            message,
            userSignature,
            ts
        });

        // Avec Axios, la réponse est directement dans response.data
        if (response.status === 200) status.innerText = "✅ Envoyé : " + response.data.status;

    } catch (e) {
        // Axios gère les erreurs différemment : e.response contient la réponse du serveur
        if (e.response) {
            status.innerText = "❌ Erreur : " + e.response.data.error;
        } else {
            status.innerText = "❌ Erreur technique : " + e.message;
        }
    }
}