// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract IoTSystem {
    address public manufacturer;
    address public gateway;
    uint256 public batchCounter;

    // --- GOUVERNANCE (Multi-Signature pour Firmware) ---
    mapping(address => bool) public isAdmin;
    uint256 public adminCount;
    uint256 public requiredApprovals;

    struct FirmwareProposal {
        bytes32 fileHash;
        string ipfsCID;
        uint256 approvalCount;
        bool executed;
        mapping(address => bool) hasVoted;
    }

    // On utilise la version comme identifiant unique de proposition (ex: "v2.0.1")
    mapping(string => FirmwareProposal) public proposals;
    // Le registre final des firmwares valides
    mapping(string => Firmware) public firmwareRepo;
    string public latestVersion;

    // --- LOGIQUE DEVICE ---
    struct Device {
        bytes32 allowedHash; // Hash de la clé publique (sécurité post-quantique)
        bool isRevoked;
        uint256 nonce;
    }

    struct Firmware {
        string version;
        bytes32 fileHash;
        string ipfsCID;
        bool isValid;
    }

    mapping(bytes32 => Device) public registry; // pubKeyHash => Device info
    mapping(address => bytes32) public addressToHash; // mapping adresse publique => hash
    mapping(uint256 => bytes32) public batchRoots; // Historique des Merkle Roots
    mapping(address => mapping(address => bool)) public authorizations; // Device => User => Allowed

    // --- EVENTS ---
    event DeviceRegistered(bytes32 indexed pubKeyHash, uint256 initialNonce);
    event DeviceRevoked(address indexed device);
    event BatchRootStored(
        uint256 indexed batchId,
        bytes32 root,
        uint256 timestamp
    );
    event FirmwareProposed(
        string version,
        bytes32 fileHash,
        string ipfsCID,
        address proposer
    );
    event FirmwareApproved(
        string version,
        address admin,
        uint256 currentApprovals
    );
    event FirmwarePublished(string version, bytes32 fileHash, string ipfsCID);
    event ConnectionRequested(
        address indexed user,
        address indexed device,
        bytes32 userEphemeralKey
    );

    modifier onlyManufacturer() {
        require(msg.sender == manufacturer, "Not manufacturer");
        _;
    }

    modifier onlyAdmin() {
        require(isAdmin[msg.sender], "Not admin");
        _;
    }

    modifier onlyGateway() {
        require(msg.sender == gateway, "Not gateway");
        _;
    }

    constructor(address[] memory _admins, uint256 _required) {
        require(_required <= _admins.length, "Required > Admins");
        manufacturer = msg.sender;
        requiredApprovals = _required;
        for (uint i = 0; i < _admins.length; i++) {
            isAdmin[_admins[i]] = true;
        }
        adminCount = _admins.length;
    }

    // --- DIAGRAMME 1 : ENREGISTREMENT (Root of Trust) ---
    function registerDeviceHash(
        bytes32 _pubKeyHash,
        uint256 _initialNonce
    ) external onlyManufacturer {
        require(
            registry[_pubKeyHash].allowedHash == 0,
            "Device already exists"
        );
        registry[_pubKeyHash].allowedHash = _pubKeyHash;
        registry[_pubKeyHash].nonce = _initialNonce;
        emit DeviceRegistered(_pubKeyHash, _initialNonce);
    }

    function revokeDevice(address _device) external onlyManufacturer {
        bytes32 h = addressToHash[_device];
        registry[h].isRevoked = true;
        emit DeviceRevoked(_device);
    }

    // --- DIAGRAMME 2 : AUTHENTIFICATION (Liaison Adresse <-> Hash) ---
    // Appelé lors de la première connexion pour lier l'adresse générée au hash usine
    function bindDeviceAddress(
        address _device,
        bytes32 _pubKeyHash
    ) external onlyGateway {
        require(
            registry[_pubKeyHash].allowedHash == _pubKeyHash,
            "Hash not registered"
        );
        addressToHash[_device] = _pubKeyHash;
    }

    // --- DIAGRAMME 3 : DATA INTEGRITY (Merkle Tree) ---
    function updateGlobalDataRoot(bytes32 _merkleRoot) external onlyGateway {
        batchCounter++;
        batchRoots[batchCounter] = _merkleRoot;
        emit BatchRootStored(batchCounter, _merkleRoot, block.timestamp);
    }

    // --- DIAGRAMME 5 : FIRMWARE MULTI-SIG (Update) ---

    /**
     * @dev Propose une nouvelle version de firmware.
     * Nécessite une validation par X administrateurs avant d'être officielle.
     */
    function proposeFirmware(
        string calldata _version,
        bytes32 _fileHash,
        string calldata _ipfsCID
    ) external onlyAdmin {
        require(proposals[_version].fileHash == 0, "Version already proposed");

        FirmwareProposal storage p = proposals[_version];
        p.fileHash = _fileHash;
        p.ipfsCID = _ipfsCID;
        p.approvalCount = 1;
        p.hasVoted[msg.sender] = true;

        emit FirmwareProposed(_version, _fileHash, _ipfsCID, msg.sender);

        // Si un seul admin est requis, on finalise direct
        if (p.approvalCount >= requiredApprovals) {
            _finalizeFirmware(_version);
        }
    }

    /**
     * @dev Approuve une proposition existante.
     */
    function approveFirmware(string calldata _version) external onlyAdmin {
        FirmwareProposal storage p = proposals[_version];
        require(p.fileHash != 0, "Proposal does not exist");
        require(!p.executed, "Already executed");
        require(!p.hasVoted[msg.sender], "Admin already voted");

        p.hasVoted[msg.sender] = true;
        p.approvalCount++;

        emit FirmwareApproved(_version, msg.sender, p.approvalCount);

        if (p.approvalCount >= requiredApprovals) {
            _finalizeFirmware(_version);
        }
    }

    function _finalizeFirmware(string memory _version) internal {
        FirmwareProposal storage p = proposals[_version];
        p.executed = true;

        firmwareRepo[_version] = Firmware({
            version: _version,
            fileHash: p.fileHash,
            ipfsCID: p.ipfsCID,
            isValid: true
        });

        latestVersion = _version;
        emit FirmwarePublished(_version, p.fileHash, p.ipfsCID);
    }

    // --- DIAGRAMME 6 : USER ACCESS ---
    function authorizeUser(
        address _device,
        address _user
    ) external onlyManufacturer {
        authorizations[_device][_user] = true;
    }

    function requestSecureConnection(
        address _device,
        bytes32 _userEphemeralKey
    ) external {
        require(authorizations[_device][msg.sender], "Access Denied");
        emit ConnectionRequested(msg.sender, _device, _userEphemeralKey);
    }

    // --- HELPERS ---
    function getLatestFirmware()
        external
        view
        returns (string memory version, bytes32 fileHash, string memory ipfsCID)
    {
        Firmware storage f = firmwareRepo[latestVersion];
        return (f.version, f.fileHash, f.ipfsCID);
    }

    function verifyDeviceStatus(address _device) external view returns (bool) {
        bytes32 h = addressToHash[_device];
        return (h != 0 && !registry[h].isRevoked);
    }

    function setGateway(address _gateway) external onlyManufacturer {
        gateway = _gateway;
    }

    function addAdmin(address _newAdmin) external onlyManufacturer {
        require(!isAdmin[_newAdmin], "Deja admin");
        isAdmin[_newAdmin] = true;
        adminCount++;
        // Optionnel : vous pouvez aussi augmenter requiredApprovals ici si besoin
        requiredApprovals++;
    }
}
