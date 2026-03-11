const IoTSystem = artifacts.require("IoTSystem");

module.exports = function (deployer, network, accounts) {
    // Liste des admins pour le constructeur (ex: les 3 premières adresses de Ganache)
    const admins = [accounts[0], accounts[1], accounts[2]];
    const quorum = 2; // Nombre de votes requis pour le firmware

    deployer.deploy(IoTSystem, admins, quorum);
};