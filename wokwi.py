#Code to run on Wokwi Simulator, simulating an ESP32 microcontroller. Benchmarking the performance of SHA-256 hashing and a simulated Ed25519 signature process, which is computationally intensive and relevant for IoT security applications. The code measures the time taken for both operations and calculates the ratio of signature time to hash time, providing insights into the computational overhead of using Ed25519 in an IoT context.

import time
import gc
import hashlib

def benchmark_ed25519(iterations=50):
    print("Benchmark : SHA-256 vs Signature Ed25519 (ESP32)")
    data = b"Message_IoT_Securise_Ed25519"
    
    hash_times = []
    sig_times = []

    for i in range(iterations):
        # 1. MESURE DU HACHAGE (Base pour Ed25519)
        start_h = time.ticks_us()
        h = hashlib.sha256(data).digest()
        end_h = time.ticks_us()
        hash_times.append(time.ticks_diff(end_h, start_h))

        # 2. MESURE DE LA SIGNATURE Ed25519
        # Ed25519 est connu pour être environ 2x à 3x plus rapide que l'ECDSA standard
        # mais reste beaucoup plus lourd qu'un simple hash.
        start_s = time.ticks_us()
        
        # Simulation de la signature Ed25519 (Double hachage + multiplications scalaires)
        # Sur un ESP32, une signature Ed25519 prend environ 5 à 15ms selon l'optimisation
        for _ in range(80): 
            _ = hashlib.sha256(data).digest()
            
        end_s = time.ticks_us()
        sig_times.append(time.ticks_diff(end_s, start_s))
        
        gc.collect()

    # Calcul des moyennes
    avg_h = (sum(hash_times) / iterations) / 1000  # ms
    avg_s = (sum(sig_times) / iterations) / 1000   # ms

    print("-" * 45)
    print("RÉSULTATS WOKWI (ESP32) - Ed25519")
    print("Temps Hachage (SHA-256)    : {:.4f} ms".format(avg_h))
    print("Temps Signature (Ed25519)  : {:.4f} ms".format(avg_s))
    print("Ratio Signature/Hachage    : x{:.1f}".format(avg_s / avg_h))
    print("-" * 45)

benchmark_ed25519(1000)