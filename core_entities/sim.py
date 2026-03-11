import os
import json
import time
import secrets
import random
import threading
from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak
import paho.mqtt.client as mqtt
import requests

load_dotenv()

# --- WEB3 & MQTT CONFIGURATION ---
w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
MQTT_BROKER = os.getenv("MQTT_BROKER", "mqtt-broker")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")

with open('abi.json', 'r') as f:
    contract_abi = json.load(f)
contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=contract_abi)

manufacturer_key = os.getenv("PRIVATE_KEY")
manufacturer_account = Account.from_key(manufacturer_key)

# --- STORAGE FOR MULTIPLE DEVICES ---
active_devices = []

# --- MQTT LOGIC ---
def on_message(client, userdata, msg):
    """
    Callback function triggered when a message is received on a subscribed MQTT topic.
    Handles web commands, direct firmware updates, and FOTA wakeup signals with IPFS/Blockchain verification.
    """
    # --- INITIAL DECODING ---
    try:
        topic = msg.topic
        payload = json.loads(msg.payload.decode())
    except Exception as e:
        print(f"[ERROR] Unable to read payload: {e}")
        return

    # --- 1. WEB COMMAND LOGIC (iot/command/...) ---
    if topic.startswith("iot/command/"):
        target_address = topic.split("/")[-1]
        is_for_me = any(d["address"].lower() == target_address.lower() for d in active_devices)
        
        if is_for_me:
            print(f"\n" + "="*40)
            print(f"[WEB COMMAND] Received for: {target_address}")
            print(f"[MESSAGE] : {payload.get('msg', 'No message')}")
            print("="*40)

    # --- 2. DIRECT FIRMWARE LOGIC (iot/firmware/...) ---
    elif topic.startswith("iot/firmware/"):
        target_address = topic.split("/")[-1]
        if any(d["address"].lower() == target_address.lower() for d in active_devices):
            print(f"\n[FOTA] 📥 Firmware received via direct MQTT!")
            print(f"       Version : {payload.get('version')}")
            print(f"       ⚡ Simulating installation...")
            time.sleep(2)
            print(f"[FOTA] ✅ Update successful!")

    # --- 3. WAKEUP & IPFS LOGIC (iot/wakeup/...) ---
    elif topic.startswith("iot/wakeup/"):
        target_address = topic.split("/")[-1]
        is_broadcast = (target_address.lower() == "broadcast")
        device = next((d for d in active_devices if d["address"].lower() == target_address.lower()), None)
        
        if is_broadcast or device:
            if payload.get("action") == "WAKEUP_FOR_FOTA":
                who = "ALL DEVICES" if is_broadcast else target_address
                cid = payload.get("ipfs_cid") or payload.get("cid") or "Unknown"
                blockchain_hash = payload.get("hash") # Anchored hash passed by the Gateway
                
                print(f"\n" + "!"*50)
                print(f"[IOT] ⏰ WAKEUP ({who}): Update signal detected")
                print(f"[FOTA] Version : {payload.get('version')}")
                print(f"[FOTA] IPFS CID : {cid}")
                print(f"[FOTA] Expected Hash (Blockchain) : {blockchain_hash}")
                print(f"!"*50)

                try:
                    # 1. Download from IPFS
                    print(f"[FOTA] 🌐 Downloading from http://ipfs-host:8080/ipfs/{cid}...")
                    dl_res = requests.get(f"http://ipfs-host:8080/ipfs/{cid}", timeout=10)
                    
                    if dl_res.status_code == 200:
                        firmware_binary = dl_res.content # Retrieve raw binary
                        
                        # 2. LOCAL HASH CREATION (Keccak256 as in Solidity)
                        local_hash = keccak(firmware_binary).hex()
                        print(f"[FOTA] 🔍 Locally computed hash: 0x{local_hash}")

                        # 3. BLOCKCHAIN COMPARISON
                        expected = blockchain_hash.replace('0x', '')
                        
                        if local_hash == expected:
                            print(f"[FOTA] ✅ VERIFICATION SUCCESS: Hash matches Blockchain.")
                            print(f"[FOTA] ⚙️ Installing...")
                            time.sleep(1)
                            print(f"[FOTA] 🎉 Update completed successfully.")
                            
                            # Success notification
                            sender_addr = device["address"] if device else active_devices[0]["address"]
                            client.publish(f"gateway/firmware/status/{sender_addr}", json.dumps({
                                "status": "SUCCESS", 
                                "v": payload.get('version'),
                                "verification": "HASH_MATCH"
                            }))
                        else:
                            print(f"[FOTA] ❌ SECURITY ALERT: Local hash does not match Blockchain!")
                            print(f"       Expected : 0x{expected}")
                            print(f"       Received : 0x{local_hash}")
                            print(f"[FOTA] 🚫 Aborting installation.")
                    else:
                        print(f"[FOTA] ❌ IPFS Error (Code: {dl_res.status_code})")
                except Exception as e:
                    print(f"[FOTA] ⚠️ Error during verification: {e}")

mqtt_client = mqtt.Client()
mqtt_client.on_message = on_message
mqtt_client.connect(MQTT_BROKER, 1883)
mqtt_client.loop_start()

# --- MANUFACTURER FUNCTIONS ---
def register_multiple_devices(n=3):
    """
    Generates new IoT device accounts (keys), hashes their public keys, 
    and registers them on the blockchain via the smart contract.
    """
    global active_devices
    print(f"\n[MANUFACTURER] Registering {n} new devices...")
    
    for i in range(n):
        priv_key = "0x" + secrets.token_hex(32)
        device_acc = Account.from_key(priv_key)
        pub_key_bytes = device_acc._key_obj.to_bytes()
        pub_key_hash = keccak(pub_key_bytes)

        # Blockchain Transaction
        nonce = w3.eth.get_transaction_count(manufacturer_account.address)
        tx = contract.functions.registerDeviceHash(pub_key_hash, 1).build_transaction({
            'from': manufacturer_account.address,
            'nonce': nonce,
            'gas': 200000,
            'gasPrice': w3.eth.gas_price
        })
        
        signed_tx = w3.eth.account.sign_transaction(tx, manufacturer_key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash)
        
        active_devices.append({
            "address": device_acc.address,
            "priv_key": priv_key
        })
        mqtt_client.subscribe(f"iot/command/{device_acc.address}")
        mqtt_client.subscribe(f"iot/wakeup/{device_acc.address}")
        mqtt_client.subscribe(f"iot/firmware/{device_acc.address}")
        print(f"[MANUFACTURER] Device {i+1} registered: {device_acc.address}")

# --- IOT FUNCTIONS ---
def send_one_data(device):
    """
    Simulates a single data transmission from a specific device. 
    Signs the payload with the device's private key for authenticity.
    """
    global value
    val = value
    value+=1
    data = {"val": val, "ts": int(time.time()), "dev": device["address"]}
    
    # Signature
    msg_hash = encode_defunct(text=json.dumps(data))
    signature = Account.from_key(device["priv_key"]).sign_message(msg_hash)
    
    payload = {"data": data, "signature": signature.signature.hex()}
    mqtt_client.publish("iot/data", json.dumps(payload))

def simulation_parallel_iot(seconds_interval=2):
    """
    Starts a loop that simulates all registered IoT devices sending 
    telemetry data simultaneously at a fixed interval.
    """
    if not active_devices:
        print("[ERROR] No devices registered.")
        return
    
    total_data_sent = 0

    print(f"\n[SIMULATION] Starting data transmission for {len(active_devices)} devices every {seconds_interval}s...")
    print("Press Ctrl+C to stop the simulation.")
    
    try:
        while True:
            # Each device sends data
            for device in active_devices:
                send_one_data(device)
                total_data_sent += 1
            
            time.sleep(seconds_interval)
    except KeyboardInterrupt:
        # --- PRINT TOTAL ON STOP ---
        print("\n" + "="*40)
        print(f"[SIMULATION] Stopping simulation.")
        print(f"[SUMMARY] Total data points sent: {total_data_sent}")
        print("="*40)

def request_audit():
    """Sends an MQTT command to the Gateway to trigger a Merkle tree integrity audit."""
    print("\n[SIMULATION] Sending audit command to gateway...")
    mqtt_client.publish("gateway/command/audit", json.dumps({"action": "verify"}))

def request_missing_data_audit():
    """Sends an MQTT command to the Gateway to trigger a gap analysis/missing data audit."""
    print("\n[SIMULATION] Sending gap analysis audit command to gateway...")
    mqtt_client.publish("gateway/command/audit-missing", json.dumps({"action": "verify"}))

def set_new_gateway():
    """
    Updates the authorized Gateway address in the smart contract. 
    Only the manufacturer is typically allowed to perform this.
    """
    print(f"\n[MANUFACTURER] Configuring new Gateway address...")                
    gateway_address = input("Public address of the Gateway to authorize: ")
    
    if not w3.is_address(gateway_address):
        print("Invalid address.")
        return

    # --- DEBUG LOGS ---
    nonce = w3.eth.get_transaction_count(manufacturer_account.address)
    print(f"Nonce used: {nonce}")
    
    try:
        tx = contract.functions.setGateway(gateway_address).build_transaction({
            'from': manufacturer_account.address,
            'nonce': nonce,
            'gas': 200000,
            'gasPrice': w3.eth.gas_price
        })                
        
        signed_tx = w3.eth.account.sign_transaction(tx, manufacturer_key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)                
        print(f"Transaction sent: {tx_hash.hex()}")
        
        # --- WAIT FOR TX AND VERIFY RECEIPT ---
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status == 1:
            print(f"[MANUFACTURER] ✅ Address {gateway_address} authorized!")
        else:
            print(f"[MANUFACTURER] ❌ Transaction failed!")
            
    except Exception as e:
        print(f"[MANUFACTURER] ❌ Error during sending: {e}")

    check_gateway()

def check_gateway():
    """Calls the smart contract to retrieve and display the currently authorized Gateway address."""
    current_gateway = contract.functions.gateway().call()
    print(f"Current Gateway on blockchain: {current_gateway}")

# --- GOVERNANCE FUNCTIONS ---

def add_new_admin():
    """Adds a new address to the administrator list in the smart contract."""
    new_admin_addr = input("Enter new administrator address: ")
    if not w3.is_address(new_admin_addr):
        print("[ERROR] Invalid address.")
        return

    print(f"[BLOCKCHAIN] Adding admin {new_admin_addr}...")
    nonce = w3.eth.get_transaction_count(manufacturer_account.address)
    
    tx = contract.functions.addAdmin(new_admin_addr).build_transaction({
        'from': manufacturer_account.address,
        'nonce': nonce,
        'gas': 100000,
        'gasPrice': w3.eth.gas_price
    })
    
    signed_tx = w3.eth.account.sign_transaction(tx, manufacturer_key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"✅ Administrator added! Tx: {tx_hash.hex()}")

def propose_new_firmware():
    """Uploads a simulated firmware file to IPFS and creates a proposal on the blockchain."""
    version = input("Firmware version (e.g., v1.0.2): ")
    
    # 1. Create simulated firmware content
    fw_content = f"BINARY_DATA_FOR_VERSION_{version}_{time.time()}"
    file_hash = keccak(text=fw_content)

    print(f"[IPFS] Connecting to Kubo (ipfs-host:5001)...")
    try:
        # 2. Real upload to IPFS via RPC API
        files = {'file': fw_content}
        response = requests.post("http://ipfs-host:5001/api/v0/add", files=files)
        response.raise_for_status()
        ipfs_cid = response.json()["Hash"]
        print(f"[IPFS] ✅ Firmware uploaded! CID: {ipfs_cid}")
    except Exception as e:
        print(f"[IPFS ERROR] Upload failed: {e}")
        # Fallback simulation if IPFS is offline
        ipfs_cid = f"Qm_simulated_{secrets.token_hex(10)}"
        print(f"[IPFS] ⚠️ Degraded mode: Using simulated CID.")

    # 3. Blockchain Registration
    print(f"[BLOCKCHAIN] Proposing firmware {version}...")
    nonce = w3.eth.get_transaction_count(manufacturer_account.address)
    
    tx = contract.functions.proposeFirmware(version, file_hash, ipfs_cid).build_transaction({
        'from': manufacturer_account.address,
        'nonce': nonce,
        'gas': 300000,
        'gasPrice': w3.eth.gas_price
    })
    
    signed_tx = w3.eth.account.sign_transaction(tx, manufacturer_key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"✅ Firmware proposed on-chain! Anchored CID: {ipfs_cid}")

def approve_existing_firmware():
    """Allows an administrator to vote in favor of a proposed firmware version."""
    version = input("Firmware version to approve: ")
    admin_key = input("Enter the private key of the voting admin (0x...): ")
    
    try:
        admin_acc = Account.from_key(admin_key)
        print(f"[BLOCKCHAIN] Approval by {admin_acc.address}...")
        
        nonce = w3.eth.get_transaction_count(admin_acc.address)
        tx = contract.functions.approveFirmware(version).build_transaction({
            'from': admin_acc.address,
            'nonce': nonce,
            'gas': 500000,
            'gasPrice': w3.eth.gas_price
        })
        
        signed_tx = w3.eth.account.sign_transaction(tx, admin_key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash)
        print(f"✅ Approval recorded! Tx: {tx_hash.hex()}")
    except Exception as e:
        print(f"[ERROR] Vote failed: {e}")

def check_firmware_status():
    """Queries the smart contract to check if a specific firmware version is validated or pending."""
    version = input("Version to check: ")
    fw = contract.functions.firmwareRepo(version).call()
    if fw[3]: # firmware.isValid
        print(f"✅ Firmware {version} is validated and ready for deployment.")
    else:
        prop = contract.functions.proposals(version).call()
        if prop[0] == b'\x00' * 32:
            print("❌ This version does not exist.")
        else:
            print(f"⏳ Awaiting votes... (Current votes: {prop[2]})")

# --- MAIN MENU ---
def main():
    while True:
        print(f"\n" + "="*50)
        print(f"      ZERO-TRUST IOT SYSTEM - SIMULATOR")
        print(f"      Active devices on network: {len(active_devices)}")
        print("="*50)

        # --- MANUFACTURER SECTION (ROOT OF TRUST) ---
        print("\n[🏭 ROLE: MANUFACTURER]")
        print("  1. Register new devices (Blockchain Provisioning)")
        print("  2. Configure official Gateway address")
        print("  3. Authorize a user (Blockchain ACL)")
        print("  4. Add new Administrator (Governance)")

        # --- ADMINISTRATORS SECTION (GOVERNANCE) ---
        print("\n[🛡️ ROLE: ADMINS (MULTI-SIG)]")
        print("  5. Propose Firmware update (IPFS + Blockchain)")
        print("  6. Approve existing Firmware (Vote)")
        print("  7. Check proposal status")

        # --- IOT & GATEWAY SECTION (OPERATIONAL) ---
        print("\n[📡 ROLE: IOT & OPERATIONAL]")
        print("  8. Start data production (Parallel Simulation)")
        print("  9. Audit: Verify Merkle Root integrity")

        print("\n[❌ OTHER]")
        print(" 10. Quit")
        
        choice = input("\nSelect an action: ")
        
        # Auto-subscribe to return topics
        mqtt_client.subscribe("iot/command/#")
        mqtt_client.subscribe("gateway/firmware/status/#")
        mqtt_client.subscribe("iot/wakeup/#") 
        mqtt_client.subscribe("iot/wakeup/broadcast") 

        try:
            if choice == "1":
                num = int(input("How many devices to create? "))
                register_multiple_devices(num)
            
            elif choice == "2":
                set_new_gateway()
                
            elif choice == "3":
                authorize_user_on_blockchain()
                
            elif choice == "4":
                add_new_admin()

            elif choice == "5":
                propose_new_firmware()

            elif choice == "6":
                approve_existing_firmware()

            elif choice == "7":
                if 'check_firmware_status' in globals():
                    check_firmware_status()
                else:
                    print("Status function not defined.")

            elif choice == "8":
                interval = int(input("Sending interval (seconds)? "))
                simulation_parallel_iot(interval)

            elif choice == "9":
                request_audit()

            elif choice == "10":
                print("Closing simulator...")
                mqtt_client.loop_stop()
                break
            
            else:
                print("Invalid choice, try again.")

        except Exception as e:
            print(f"\n[ERROR] An error occurred: {e}")
            input("Press Enter to continue...")

if __name__ == "__main__":
    value = 0
    main()