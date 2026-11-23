// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal Foundry cheatcode interface, declared locally so this
///         project needs no external Solidity dependencies (no forge-std).
///         Only the cheatcodes actually used by the tests/scripts are listed.
///         The cheatcode address is a Foundry constant.
interface Vm {
    function addr(uint256 privateKey) external pure returns (address);
    function sign(uint256 privateKey, bytes32 digest) external pure returns (uint8 v, bytes32 r, bytes32 s);
    function prank(address msgSender) external;
    function startPrank(address msgSender) external;
    function stopPrank() external;
    function warp(uint256 newTimestamp) external;
    function expectRevert(bytes4 revertData) external;
    function expectRevert(bytes calldata revertData) external;
    function expectEmit(bool checkTopic1, bool checkTopic2, bool checkTopic3, bool checkData) external;
    function startBroadcast() external;
    function startBroadcast(uint256 privateKey) external;
    function stopBroadcast() external;
    function envAddress(string calldata name) external view returns (address);
    function envOr(string calldata name, address defaultValue) external view returns (address);
    function envOr(string calldata name, uint256 defaultValue) external view returns (uint256);
}

/// @notice Tiny assertion helpers for the test suite (forge-std free).
abstract contract TestBase {
    Vm internal constant vm = Vm(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);

    function assertTrue(bool condition, string memory message) internal pure {
        if (!condition) revert(string.concat("assertTrue failed: ", message));
    }

    function assertEq(uint256 a, uint256 b, string memory message) internal pure {
        if (a != b) revert(string.concat("assertEq(uint) failed: ", message));
    }

    function assertEq(address a, address b, string memory message) internal pure {
        if (a != b) revert(string.concat("assertEq(addr) failed: ", message));
    }

    function assertEq(bytes32 a, bytes32 b, string memory message) internal pure {
        if (a != b) revert(string.concat("assertEq(bytes32) failed: ", message));
    }
}
