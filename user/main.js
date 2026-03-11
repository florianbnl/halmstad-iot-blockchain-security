import { Wallet } from 'ethers';
import axios from 'axios';

// TEST PRIVATE KEY (From environment variables)
const PRIVATE_KEY = process.env.PRIVATE_KEY;

document.getElementById('sendBtn').addEventListener('click', sendCommand);

/**
 * Handles the process of signing a message locally with the user's private key
 * and sending the command to the IoT Gateway API for verification and execution.
 */
async function sendCommand() {
    const devAddress = document.getElementById('devAddr').value;
    const message = document.getElementById('msg').value;
    const status = document.getElementById('status');

    status.innerText = "Signing message...";

    try {
        // Initialize wallet and prepare payload
        const wallet = new Wallet(PRIVATE_KEY);
        const userAddress = wallet.address;
        const ts = Date.now();

        // The payload must match exactly what the Gateway expects for signature verification
        const messagePayload = JSON.stringify({ devAddress, message, ts });

        // Cryptographic Signature (Proof of Identity)
        const userSignature = await wallet.signMessage(messagePayload);

        status.innerText = "Sending to Gateway...";

        // Send the signed command to the Gateway API using Axios
        const response = await axios.post('http://localhost:3001/api/send-command', {
            userAddress,
            devAddress,
            message,
            userSignature,
            ts
        });

        // Axios stores the server response directly in response.data
        if (response.status === 200) {
            status.innerText = "✅ Sent: " + response.data.status;
        }

    } catch (e) {
        // Error handling for both server responses and network issues
        if (e.response) {
            // The server responded with a status code outside the 2xx range
            status.innerText = "❌ Error: " + e.response.data.error;
        } else {
            // Something happened in setting up the request
            status.innerText = "❌ Technical Error: " + e.message;
        }
    }
}