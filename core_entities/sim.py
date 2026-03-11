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

# --- CONFIGURATION WEB3 & MQTT ---
w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
MQTT_BROKER = os.getenv("MQTT_BROKER", "mqtt-broker")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")

with open('abi.json', 'r') as f:
    contract_abi = json.load(f)
contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=contract_abi)

manufacturer_key = os.getenv("PRIVATE_KEY")
manufacturer_account = Account.from_key(manufacturer_key)

# --- NOUVEAU STOCKAGE POUR MULTIPLES OBJETS ---
active_devices = []

# --- LOGIQUE MQTT ---
def on_message(client, userdata, msg):
    # --- DÉCODAGE INITIAL ---
    try:
        topic = msg.topic
        payload = json.loads(msg.payload.decode())
    except Exception as e:
        print(f"[ERREUR] Impossible de lire le payload : {e}")
        return

    # --- 1. LOGIQUE COMMANDES WEB (iot/command/...) ---
    if topic.startswith("iot/command/"):
        target_address = topic.split("/")[-1]
        is_for_me = any(d["address"].lower() == target_address.lower() for d in active_devices)
        
        if is_for_me:
            print(f"\n" + "="*40)
            print(f"[COMMANDE WEB] Reçue pour : {target_address}")
            print(f"[MESSAGE] : {payload.get('msg', 'Sans message')}")
            print("="*40)

    # --- 2. LOGIQUE FIRMWARE DIRECT (iot/firmware/...) ---
    elif topic.startswith("iot/firmware/"):
        target_address = topic.split("/")[-1]
        if any(d["address"].lower() == target_address.lower() for d in active_devices):
            print(f"\n[FOTA] 📥 Firmware reçu via MQTT direct!")
            print(f"       Version : {payload.get('version')}")
            print(f"       ⚡ Simulation de l'installation...")
            time.sleep(2)
            print(f"[FOTA] ✅ Mise à jour réussie!")

    # --- 3. LOGIQUE RÉVEIL & IPFS (iot/wakeup/...) ---
    # --- 3. LOGIQUE RÉVEIL & IPFS (iot/wakeup/...) ---
    elif topic.startswith("iot/wakeup/"):
        target_address = topic.split("/")[-1]
        is_broadcast = (target_address.lower() == "broadcast")
        device = next((d for d in active_devices if d["address"].lower() == target_address.lower()), None)
        
        if is_broadcast or device:
            if payload.get("action") == "WAKEUP_FOR_FOTA":
                who = "TOUS LES OBJETS" if is_broadcast else target_address
                cid = payload.get("ipfs_cid") or payload.get("cid") or "Inconnu"
                blockchain_hash = payload.get("hash") # Le hash ancré sur la blockchain passé par la Gateway
                
                print(f"\n" + "!"*50)
                print(f"[IOT] ⏰ RÉVEIL ({who}) : Signal de mise à jour détecté")
                print(f"[FOTA] Version : {payload.get('version')}")
                print(f"[FOTA] CID IPFS : {cid}")
                print(f"[FOTA] Hash attendu (Blockchain) : {blockchain_hash}")
                print(f"!"*50)

                try:
                    # 1. Téléchargement depuis IPFS
                    print(f"[FOTA] 🌐 Téléchargement depuis http://ipfs-host:8080/ipfs/{cid}...")
                    dl_res = requests.get(f"http://ipfs-host:8080/ipfs/{cid}", timeout=10)
                    
                    if dl_res.status_code == 200:
                        firmware_binary = dl_res.content # On récupère le contenu brut
                        
                        # 2. CRÉATION DU HASH LOCAL (Keccak256 comme sur Solidity)
                        # On utilise keccak de eth_utils pour être identique au contrat
                        local_hash = keccak(firmware_binary).hex()
                        print(f"[FOTA] 🔍 Hash calculé localement : 0x{local_hash}")

                        # 3. COMPARAISON AVEC LA BLOCKCHAIN
                        # On s'assure que les deux sont comparés sans le préfixe '0x' pour éviter les erreurs
                        expected = blockchain_hash.replace('0x', '')
                        
                        if local_hash == expected:
                            print(f"[FOTA] ✅ VÉRIFICATION RÉUSSIE : Le hash correspond à la Blockchain.")
                            print(f"[FOTA] ⚙️ Installation en cours...")
                            time.sleep(1)
                            print(f"[FOTA] 🎉 Mise à jour terminée avec succès.")
                            
                            # Notification de succès
                            sender_addr = device["address"] if device else active_devices[0]["address"]
                            client.publish(f"gateway/firmware/status/{sender_addr}", json.dumps({
                                "status": "SUCCESS", 
                                "v": payload.get('version'),
                                "verification": "HASH_MATCH"
                            }))
                        else:
                            print(f"[FOTA] ❌ ALERTE SÉCURITÉ : Le hash local ne correspond pas à la Blockchain !")
                            print(f"       Attendu : 0x{expected}")
                            print(f"       Reçu    : 0x{local_hash}")
                            print(f"[FOTA] 🚫 Abandon de l'installation.")
                    else:
                        print(f"[FOTA] ❌ Erreur IPFS (Code: {dl_res.status_code})")
                except Exception as e:
                    print(f"[FOTA] ⚠️ Erreur lors de la vérification : {e}")

mqtt_client = mqtt.Client()
mqtt_client.on_message = on_message
mqtt_client.connect(MQTT_BROKER, 1883)
mqtt_client.loop_start()

# --- FONCTIONS FABRICANT ---
def register_multiple_devices(n=3):
    global active_devices
    print(f"\n[FABRICANT] Enregistrement de {n} nouveaux objets...")
    
    for i in range(n):
        priv_key = "0x" + secrets.token_hex(32)
        device_acc = Account.from_key(priv_key)
        pub_key_bytes = device_acc._key_obj.to_bytes()
        pub_key_hash = keccak(pub_key_bytes)

        # Transaction Blockchain
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
        print(f"[FABRICANT] Objet {i+1} enregistré: {device_acc.address}")

# --- FONCTIONS IOT ---
# Fonction pour qu'un seul objet envoie une donnée signée
def send_one_data(device):
    global value
    val = value
    value+=1
    data = {"val": val, "ts": int(time.time()), "dev": device["address"]}
    
    # Signature
    msg_hash = encode_defunct(text=json.dumps(data))
    signature = Account.from_key(device["priv_key"]).sign_message(msg_hash)
    
    payload = {"data": data, "signature": signature.signature.hex()}
    mqtt_client.publish("iot/data", json.dumps(payload))
    # print(f"[IOT] {device['address'][:8]}... envoie {val}°C")

# --- NOUVELLE FONCTION: Simulation parallèle ---
def simulation_parallel_iot(seconds_interval=2):
    if not active_devices:
        print("[ERREUR] Aucun objet enregistré.")
        return
    
    total_data_sent = 0

    print(f"\n[SIMULATION] Début envoi de données pour {len(active_devices)} objets toutes les {seconds_interval}s...")
    print("Appuyez sur Ctrl+C pour arrêter la simulation.")
    
    try:
        while True:
            # Pour chaque objet, on envoie une donnée
            for device in active_devices:
                send_one_data(device)
                total_data_sent += 1
            
            time.sleep(seconds_interval)
    except KeyboardInterrupt:
        # --- PRINT DU TOTAL À L'ARRÊT ---
        print("\n" + "="*40)
        print(f"[SIMULATION] Arrêt de la simulation.")
        print(f"[RÉSUMÉ] Nombre total de données envoyées : {total_data_sent}")
        print("="*40)

def request_audit():
    print("\n[SIMULATION] Envoi de la commande d'audit à la gateway...")
    # On envoie un message vide ou n'importe quoi sur le topic de commande
    mqtt_client.publish("gateway/command/audit", json.dumps({"action": "verify"}))

def request_missing_data_audit():
    print("\n[SIMULATION] Envoi de la commande d'audit à la gateway...")
    # On envoie un message vide ou n'importe quoi sur le topic de commande
    mqtt_client.publish("gateway/command/audit-missing", json.dumps({"action": "verify"}))

def authorize_user_on_blockchain():
    device_addr = input("Adresse de l'objet IoT : ")
    user_addr = input("Adresse de l'utilisateur à autoriser : ")

    print(f"[BLOCKCHAIN] Autorisation de {user_addr} pour l'objet {device_addr}...")
    
    nonce = w3.eth.get_transaction_count(manufacturer_account.address)
    tx = contract.functions.authorizeUser(device_addr, user_addr).build_transaction({
        'from': manufacturer_account.address,
        'nonce': nonce,
        'gas': 100000,
        'gasPrice': w3.eth.gas_price
    })
    
    signed_tx = w3.eth.account.sign_transaction(tx, manufacturer_key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"✅ Utilisateur autorisé ! Tx: {tx_hash.hex()}")

def set_new_gateway():
    print(f"\n[FABRICANT] Configuration d'une nouvelle adresse Gateway...")                
    gateway_address = input("Adresse publique de la Gateway à autoriser: ")
    
    if not w3.is_address(gateway_address):
        print("Adresse invalide.")
        return

    # --- LOGS DE DÉBOGAGE ---
    nonce = w3.eth.get_transaction_count(manufacturer_account.address)
    print(f"Nonce utilisé : {nonce}")
    
    try:
        tx = contract.functions.setGateway(gateway_address).build_transaction({
            'from': manufacturer_account.address,
            'nonce': nonce,
            'gas': 200000,
            'gasPrice': w3.eth.gas_price
        })                
        
        signed_tx = w3.eth.account.sign_transaction(tx, manufacturer_key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)                
        print(f"Transaction envoyée : {tx_hash.hex()}")
        
        # --- ATTENDRE LA TRANSACTION ET VÉRIFIER LE REÇU ---
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status == 1:
            print(f"[FABRICANT] ✅ Adresse {gateway_address} autorisée !")
        else:
            print(f"[FABRICANT] ❌ Transaction échouée !")
            
    except Exception as e:
        print(f"[FABRICANT] ❌ Erreur lors de l'envoi : {e}")

    # Vérification après tentative
    check_gateway()

def check_gateway():
    current_gateway = contract.functions.gateway().call()
    print(f"Gateway actuelle sur la blockchain : {current_gateway}")

# --- FONCTIONS DE GOUVERNANCE ---

def add_new_admin():
    """Ajoute une adresse à la liste des administrateurs (Seul le fabricant peut faire ça)"""
    new_admin_addr = input("Entrez l'adresse du nouvel administrateur : ")
    if not w3.is_address(new_admin_addr):
        print("[ERREUR] Adresse invalide.")
        return

    print(f"[BLOCKCHAIN] Ajout de l'admin {new_admin_addr}...")
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
    print(f"✅ Administrateur ajouté ! Tx: {tx_hash.hex()}")

# def propose_new_firmware():
#     """Propose un firmware au vote (Nécessite d'être admin)"""
#     version = input("Version du firmware (ex: v1.0.2) : ")
#     # Simulation d'un hash de fichier firmware
#     fw_content = f"firmware_data_{version}_{time.time()}"
#     file_hash = keccak(text=fw_content)
#     ipfs_cid = f"Qm{secrets.token_hex(21)}" # Simulation d'un CID IPFS

#     print(f"[BLOCKCHAIN] Proposition du firmware {version}...")
#     # On utilise le compte fabricant car il est admin par défaut dans le contrat
#     nonce = w3.eth.get_transaction_count(manufacturer_account.address)
    
#     tx = contract.functions.proposeFirmware(version, file_hash, ipfs_cid).build_transaction({
#         'from': manufacturer_account.address,
#         'nonce': nonce,
#         'gas': 300000,
#         'gasPrice': w3.eth.gas_price
#     })
    
#     signed_tx = w3.eth.account.sign_transaction(tx, manufacturer_key)
#     tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
#     w3.eth.wait_for_transaction_receipt(tx_hash)
#     print(f"✅ Firmware proposé ! Hash: {file_hash.hex()}")

def propose_new_firmware():
    """Propose un firmware : Upload sur IPFS + Enregistrement Blockchain"""
    version = input("Version du firmware (ex: v1.0.2) : ")
    
    # 1. Création d'un contenu de firmware simulé
    fw_content = f"BINARY_DATA_FOR_VERSION_{version}_{time.time()}"
    file_hash = keccak(text=fw_content)

    print(f"[IPFS] Connexion à Kubo (ipfs-host:5001)...")
    try:
        # 2. Upload réel sur IPFS via l'API RPC
        files = {'file': fw_content}
        response = requests.post("http://ipfs-host:5001/api/v0/add", files=files)
        response.raise_for_status()
        ipfs_cid = response.json()["Hash"]
        print(f"[IPFS] ✅ Firmware uploadé ! CID: {ipfs_cid}")
    except Exception as e:
        print(f"[ERREUR IPFS] Échec de l'upload : {e}")
        # Fallback simulation si IPFS est hors ligne pour le test
        ipfs_cid = f"Qm_simulated_{secrets.token_hex(10)}"
        print(f"[IPFS] ⚠️ Mode dégradé : Utilisation d'un faux CID.")

    # 3. Enregistrement sur la Blockchain
    print(f"[BLOCKCHAIN] Proposition du firmware {version}...")
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
    print(f"✅ Firmware proposé sur la chaîne ! CID ancré : {ipfs_cid}")

def approve_existing_firmware():
    """Vote pour l'approbation d'un firmware (Nécessite d'être un AUTRE admin)"""
    version = input("Version du firmware à approuver : ")
    admin_key = input("Entrez la clé privée de l'admin qui vote (0x...) : ")
    
    try:
        admin_acc = Account.from_key(admin_key)
        print(f"[BLOCKCHAIN] Approbation par {admin_acc.address}...")
        
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
        print(f"✅ Approbation enregistrée ! Tx: {tx_hash.hex()}")
    except Exception as e:
        print(f"[ERREUR] Vote échoué : {e}")

def check_firmware_status():
    version = input("Version à vérifier : ")
    fw = contract.functions.firmwareRepo(version).call()
    if fw[3]: # firmware.isValid
        print(f"✅ Le firmware {version} est validé et prêt à être déployé.")
    else:
        prop = contract.functions.proposals(version).call()
        if prop[0] == b'\x00' * 32:
            print("❌ Cette version n'existe pas.")
        else:
            print(f"⏳ En attente de votes... (Votes actuels : {prop[2]})")

# --- MENU PRINCIPAL ---
def main():
    while True:
        print(f"\n" + "="*50)
        print(f"      SISTÈME IOT ZERO-TRUST - SIMULATEUR")
        print(f"      Objets Actifs sur le réseau: {len(active_devices)}")
        print("="*50)

        # --- SECTION FABRICANT (ROOT OF TRUST) ---
        print("\n[🏭 ROLE: MANUFACTURER]")
        print("  1. Enregistrer de nouveaux objets (Provisioning Blockchain)")
        print("  2. Configurer l'adresse de la Gateway officielle")
        print("  3. Autoriser un utilisateur (ACL Blockchain)")
        print("  4. Ajouter un nouvel Administrateur (Gouvernance)")

        # --- SECTION ADMINISTRATEURS (GOUVERNANCE) ---
        print("\n[🛡️ ROLE: ADMINS (MULTI-SIG)]")
        print("  5. Proposer une mise à jour Firmware (IPFS + Blockchain)")
        print("  6. Approuver un Firmware existant (Vote)")
        print("  7. Consulter l'état d'une proposition (Status)")

        # --- SECTION IOT & GATEWAY (OPÉRATIONNEL) ---
        print("\n[📡 ROLE: IOT & OPERATIONNEL]")
        print("  8. Lancer la production de données (Simulation Parallèle)")
        print("  9. Audit: Vérifier l'intégrité des racines Merkle")
        print(" 10. Audit: Vérifier les données manquantes (Gap Analysis)")

        print("\n[❌ AUTRE]")
        print(" 11. Quitter")
        
        choice = input("\nSélectionnez une action: ")
        
        # Souscription automatique aux topics de retour
        mqtt_client.subscribe("iot/command/#")
        mqtt_client.subscribe("gateway/firmware/status/#")
        mqtt_client.subscribe("iot/wakeup/#") # Pour les messages individuels
        mqtt_client.subscribe("iot/wakeup/broadcast") # <--- AJOUTE CETTE LIGNE ICI

        try:
            if choice == "1":
                num = int(input("Combien d'objets à créer ? "))
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
                # Utilise la fonction de status que nous avons définie
                if 'check_firmware_status' in globals():
                    check_firmware_status()
                else:
                    print("Fonction de status non définie.")

            elif choice == "8":
                interval = int(input("Intervalle d'envoi (secondes) ? "))
                simulation_parallel_iot(interval)

            elif choice == "9":
                request_audit()

            elif choice == "10":
                request_missing_data_audit()

            elif choice == "11":
                print("Fermeture du simulateur...")
                mqtt_client.loop_stop()
                break
            
            else:
                print("Choix invalide, recommencez.")

        except Exception as e:
            print(f"\n[ERREUR] Une erreur est survenue : {e}")
            input("Appuyez sur Entrée pour continuer...")

if __name__ == "__main__":
    value = 0
    main()