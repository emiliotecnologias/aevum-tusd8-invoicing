// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {InvoiceRegistry} from "../src/InvoiceRegistry.sol";
import {MockUSD8} from "../src/mocks/MockUSD8.sol";
import {Vm} from "../test/TestBase.sol";

/// @notice Deployment script for InvoiceRegistry.
///
/// Local (anvil, Shanghai hardfork — mirrors Aevum):
///   anvil --hardfork shanghai --port 8545
///   forge script script/Deploy.s.sol --rpc-url local --broadcast --legacy
///   (deploys MockUSD8 + InvoiceRegistry; TOKEN unset => mock is deployed)
///
/// Aevum testnet (chain 44005) — PENDING OWNER DECISION, see README:
///   TOKEN=0x456569838f1d43009A91ebA4263147Afd7233483 \
///   forge script script/Deploy.s.sol --rpc-url aevum --broadcast --legacy \
///     --account <foundry-keystore-account>
///   Aevum accepts legacy (type-0) transactions; keep --legacy.
///   Use a fresh test wallet funded from the QDay Discord faucet
///   (1 tQDAY + 5 tUSD8). Never use a wallet holding real funds.
///
/// The script intentionally uses a hand-declared Vm interface (no forge-std)
/// and logs addresses via events-free return values printed by forge.
contract Deploy {
    Vm internal constant vm = Vm(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);

    // tUSD8 on Aevum testnet (chain 44005), from the official Aevum docs.
    address internal constant TUSD8_AEVUM = 0x456569838f1d43009A91ebA4263147Afd7233483;

    function run() external returns (address tokenAddr, address registryAddr) {
        address token = vm.envOr("TOKEN", address(0));

        vm.startBroadcast();
        if (token == address(0)) {
            // Local development: deploy the mock stablecoin.
            token = address(new MockUSD8());
        }
        InvoiceRegistry registry = new InvoiceRegistry(token);
        vm.stopBroadcast();

        return (token, address(registry));
    }
}
