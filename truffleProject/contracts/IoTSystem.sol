// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title IoTSystem
 * @dev Smart contract managing device registration, data integrity through Merkle Roots,
 * and Multi-Signature firmware governance.
 */
contract IoTSystem {
    address public manufacturer;
    address public gateway;
    uint256 public batchCounter;

    // --- GOVERNANCE (Multi-Signature for Firmware Updates) ---
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

    // Version string is used as the unique identifier (e.g., "v2.0.1")
    mapping(string => FirmwareProposal) public proposals;
    // Final registry of validated and approved firmwares
    mapping(string => Firmware) public firmwareRepo;
    string public latestVersion;

    // --- DEVICE LOGIC ---
    struct Device {
        bytes32 allowedHash; // Public key hash (enabling post-quantum safety)
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
    mapping(address => bytes32) public addressToHash; // mapping public address => key hash
    mapping(uint256 => bytes32) public batchRoots; // History of Merkle Roots anchored to blockchain
    mapping(address => mapping(address => bool)) public authorizations; // Device => User => Access Granted

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

    // --- MODIFIERS ---
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
        require(
            _required <= _admins.length,
            "Required approvals exceed admin count"
        );
        manufacturer = msg.sender;
        requiredApprovals = _required;
        for (uint i = 0; i < _admins.length; i++) {
            isAdmin[_admins[i]] = true;
        }
        adminCount = _admins.length;
    }

    // --- DEVICE PROVISIONING (Root of Trust) ---

    /**
     * @dev Registers a new device hash before it connects.
     * Only the manufacturer can provision new hardware.
     */
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

    /**
     * @dev Revokes a device's access to the system.
     */
    function revokeDevice(address _device) external onlyManufacturer {
        bytes32 h = addressToHash[_device];
        registry[h].isRevoked = true;
        emit DeviceRevoked(_device);
    }

    // --- AUTHENTICATION (Address <-> Hash Binding) ---

    /**
     * @dev Binds a generated public address to a factory-registered key hash.
     * Ensures only authorized hardware can interact with the Gateway.
     */
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

    // --- DATA INTEGRITY (Merkle Tree Anchoring) ---

    /**
     * @dev Stores the Merkle Root of a data batch to the blockchain.
     * This provides a verifiable proof of data integrity for off-chain storage.
     */
    function updateGlobalDataRoot(bytes32 _merkleRoot) external onlyGateway {
        batchCounter++;
        batchRoots[batchCounter] = _merkleRoot;
        emit BatchRootStored(batchCounter, _merkleRoot, block.timestamp);
    }

    // --- FIRMWARE MULTI-SIG (Security Governance) ---

    /**
     * @dev Proposes a new firmware version.
     * Requires validation from X administrators before becoming official.
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

        // If only one admin is required, finalize immediately
        if (p.approvalCount >= requiredApprovals) {
            _finalizeFirmware(_version);
        }
    }

    /**
     * @dev Approves an existing firmware proposal.
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

    /**
     * @dev Moves an approved proposal into the official firmware repository.
     */
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

    // --- USER ACCESS CONTROL ---

    /**
     * @dev Authorizes a user to access a specific device's data.
     */
    function authorizeUser(
        address _device,
        address _user
    ) external onlyManufacturer {
        authorizations[_device][_user] = true;
    }

    /**
     * @dev Allows an authorized user to request a secure session key exchange.
     */
    function requestSecureConnection(
        address _device,
        bytes32 _userEphemeralKey
    ) external {
        require(authorizations[_device][msg.sender], "Access Denied");
        emit ConnectionRequested(msg.sender, _device, _userEphemeralKey);
    }

    // --- HELPERS ---

    /**
     * @dev Returns the most recent validated firmware version details.
     */
    function getLatestFirmware()
        external
        view
        returns (string memory version, bytes32 fileHash, string memory ipfsCID)
    {
        Firmware storage f = firmwareRepo[latestVersion];
        return (f.version, f.fileHash, f.ipfsCID);
    }

    /**
     * @dev Checks if a device address is valid and not revoked.
     */
    function verifyDeviceStatus(address _device) external view returns (bool) {
        bytes32 h = addressToHash[_device];
        return (h != 0 && !registry[h].isRevoked);
    }

    /**
     * @dev Updates the official Gateway address.
     */
    function setGateway(address _gateway) external onlyManufacturer {
        gateway = _gateway;
    }

    /**
     * @dev Adds a new administrator and increments required approvals.
     */
    function addAdmin(address _newAdmin) external onlyManufacturer {
        require(!isAdmin[_newAdmin], "Already an admin");
        isAdmin[_newAdmin] = true;
        adminCount++;
        requiredApprovals++;
    }
}
