Installation Docker :

Téléchargement des conteneurs : 

```
curl -sSL https://bit.ly/2ysbOFE | bash -s -- d
```

Pour lancer les conteneurs : 

```
./network.sh up createChannel -c mon-canal-prive -ca
```


Avant de lancer le projet : 

- le compiler avec ```truffle compile```
- le mettre dans ganache avec ```truffle migrate --network development --reset```

*Run the docker*

```
docker-compose up --build
```

# Initialisation : 

## Créer le ".env" avec : 

### 1. Adresse de Ganache (Docker doit utiliser host.docker.internal)
RPC_URL=http://host.docker.internal:7545

### 2. Adresse du contrat déployé par Truffle
CONTRACT_ADDRESS= 

### 3. Clé privée du compte FABRICANT (celui qui a déployé le contrat)
PRIVATE_KEY=

### 4. URL du broker MQTT (nom du service dans docker-compose)
MQTT_BROKER=mqtt-broker

# Une fois ganache lancé :

Remplir le .env
Récupérer le abi.json (disponible dans la section build du truffle project)