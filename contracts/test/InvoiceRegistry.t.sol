// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {InvoiceRegistry} from "../src/InvoiceRegistry.sol";
import {MockUSD8} from "../src/mocks/MockUSD8.sol";
import {TestBase} from "./TestBase.sol";

/// @notice Full test suite for InvoiceRegistry + MockUSD8 (EIP-3009 flows).
///         Run with `forge test` from the contracts/ directory.
contract InvoiceRegistryTest is TestBase {
    MockUSD8 internal token;
    InvoiceRegistry internal registry;

    uint256 internal constant ISSUER_PK = 0x1550E12;
    uint256 internal constant PAYER_PK = 0x9A7E12;
    uint256 internal constant RELAYER_PK = 0x2E1A7E12;
    uint256 internal constant STRANGER_PK = 0x57A9E12;

    address internal issuer;
    address internal payer;
    address internal relayer;
    address internal stranger;

    uint256 internal constant AMOUNT = 25_500_000; // 25.50 tUSD8 (6 decimals)
    bytes32 internal constant MEMO = keccak256("Video editing - 5 clips");

    function setUp() public {
        issuer = vm.addr(ISSUER_PK);
        payer = vm.addr(PAYER_PK);
        relayer = vm.addr(RELAYER_PK);
        stranger = vm.addr(STRANGER_PK);

        token = new MockUSD8();
        registry = new InvoiceRegistry(address(token));

        token.mint(payer, 1_000_000_000); // 1,000 tUSD8
        token.mint(stranger, 1_000_000_000);
    }

    // ------------------------------------------------------------ helpers

    function _create(address payer_, uint256 amount, uint64 dueAt) internal returns (uint256 id) {
        vm.prank(issuer);
        id = registry.createInvoice(payer_, amount, dueAt, MEMO);
    }

    /// @dev Build an EIP-3009 ReceiveWithAuthorization signature the way a
    ///      wallet would: EIP-712 digest over the token's domain separator.
    function _signReceive(
        uint256 pk,
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce
    ) internal view returns (uint8 v, bytes32 r, bytes32 s) {
        bytes32 structHash = keccak256(
            abi.encode(token.RECEIVE_WITH_AUTHORIZATION_TYPEHASH(), from, to, value, validAfter, validBefore, nonce)
        );
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", token.DOMAIN_SEPARATOR(), structHash));
        return vm.sign(pk, digest);
    }

    // ------------------------------------------------------------ creation

    function test_CreateInvoice_StoresFields() public {
        uint256 id = _create(payer, AMOUNT, 1_900_000_000);
        assertEq(id, 1, "first id is 1");
        InvoiceRegistry.Invoice memory inv = registry.getInvoice(id);
        assertEq(inv.issuer, issuer, "issuer");
        assertEq(inv.payer, payer, "payer");
        assertEq(inv.amount, AMOUNT, "amount");
        assertEq(inv.dueAt, 1_900_000_000, "dueAt");
        assertEq(inv.memoHash, MEMO, "memoHash");
        assertTrue(inv.status == InvoiceRegistry.Status.Open, "status open");
        assertEq(registry.invoiceCount(), 1, "count");
    }

    function test_CreateInvoice_ZeroAmountReverts() public {
        vm.prank(issuer);
        vm.expectRevert(InvoiceRegistry.ZeroAmount.selector);
        registry.createInvoice(payer, 0, 0, MEMO);
    }

    function test_GetInvoice_NotFoundReverts() public {
        vm.expectRevert(abi.encodeWithSelector(InvoiceRegistry.InvoiceNotFound.selector, 99));
        registry.getInvoice(99);
    }

    // ------------------------------------------------------------ standard pay

    function test_Pay_StandardFlow() public {
        uint256 id = _create(payer, AMOUNT, 0);

        vm.startPrank(payer);
        token.approve(address(registry), AMOUNT);
        registry.pay(id);
        vm.stopPrank();

        assertEq(token.balanceOf(issuer), AMOUNT, "issuer received exact amount");
        assertEq(token.balanceOf(payer), 1_000_000_000 - AMOUNT, "payer debited");
        assertEq(token.balanceOf(address(registry)), 0, "registry holds nothing (no escrow)");

        InvoiceRegistry.Invoice memory inv = registry.getInvoice(id);
        assertTrue(inv.status == InvoiceRegistry.Status.Paid, "status paid");
        assertEq(inv.paidBy, payer, "paidBy");
        assertTrue(registry.isPaid(id), "isPaid");
    }

    function test_Pay_WrongPayerReverts() public {
        uint256 id = _create(payer, AMOUNT, 0);
        vm.startPrank(stranger);
        token.approve(address(registry), AMOUNT);
        vm.expectRevert(abi.encodeWithSelector(InvoiceRegistry.WrongPayer.selector, id, payer, stranger));
        registry.pay(id);
        vm.stopPrank();
    }

    function test_Pay_AnyoneWhenPayerUnset() public {
        uint256 id = _create(address(0), AMOUNT, 0);
        vm.startPrank(stranger);
        token.approve(address(registry), AMOUNT);
        registry.pay(id);
        vm.stopPrank();
        assertEq(token.balanceOf(issuer), AMOUNT, "issuer paid by stranger");
    }

    function test_Pay_TwiceReverts() public {
        uint256 id = _create(payer, AMOUNT, 0);
        vm.startPrank(payer);
        token.approve(address(registry), AMOUNT * 2);
        registry.pay(id);
        vm.expectRevert(abi.encodeWithSelector(InvoiceRegistry.InvoiceNotOpen.selector, id));
        registry.pay(id);
        vm.stopPrank();
    }

    function test_Pay_WithoutApprovalReverts() public {
        uint256 id = _create(payer, AMOUNT, 0);
        vm.prank(payer);
        vm.expectRevert(
            abi.encodeWithSelector(MockUSD8.InsufficientAllowance.selector, address(registry), 0, AMOUNT)
        );
        registry.pay(id);
    }

    // ------------------------------------------------------------ gasless pay

    function test_PayWithAuthorization_GaslessFlow() public {
        uint256 id = _create(payer, AMOUNT, 0);
        uint256 validAfter = block.timestamp;
        uint256 validBefore = block.timestamp + 3600;
        bytes32 nonce = registry.authorizationNonce(id);

        // Payer signs off-chain. No approval, no gas needed by the payer.
        (uint8 v, bytes32 r, bytes32 s) =
            _signReceive(PAYER_PK, payer, address(registry), AMOUNT, validAfter, validBefore, nonce);

        // A third-party relayer submits and pays the gas.
        vm.warp(block.timestamp + 10);
        vm.prank(relayer);
        registry.payWithAuthorization(id, payer, validAfter, validBefore, v, r, s);

        assertEq(token.balanceOf(issuer), AMOUNT, "issuer received exact amount");
        assertEq(token.balanceOf(address(registry)), 0, "registry forwarded everything");
        assertTrue(registry.isPaid(id), "invoice paid");
        assertTrue(token.authorizationState(payer, nonce), "authorization consumed");
    }

    function test_PayWithAuthorization_RelayerCannotChangeAmount() public {
        uint256 id = _create(payer, AMOUNT, 0);
        uint256 validAfter = block.timestamp;
        uint256 validBefore = block.timestamp + 3600;
        bytes32 nonce = registry.authorizationNonce(id);

        // Payer signs for a DIFFERENT (smaller) value than the invoice amount.
        // The registry always submits the stored amount, so signature
        // verification inside the token must fail.
        (uint8 v, bytes32 r, bytes32 s) =
            _signReceive(PAYER_PK, payer, address(registry), 1, validAfter, validBefore, nonce);

        vm.warp(block.timestamp + 10);
        vm.prank(relayer);
        vm.expectRevert(MockUSD8.InvalidSignature.selector);
        registry.payWithAuthorization(id, payer, validAfter, validBefore, v, r, s);
    }

    function test_PayWithAuthorization_NonceBoundToInvoice() public {
        uint256 idA = _create(payer, AMOUNT, 0);
        uint256 idB = _create(payer, AMOUNT, 0);
        uint256 validAfter = block.timestamp;
        uint256 validBefore = block.timestamp + 3600;

        // Signature is for invoice A's nonce; submitting against B must fail.
        (uint8 v, bytes32 r, bytes32 s) = _signReceive(
            PAYER_PK, payer, address(registry), AMOUNT, validAfter, validBefore, registry.authorizationNonce(idA)
        );

        vm.warp(block.timestamp + 10);
        vm.prank(relayer);
        vm.expectRevert(MockUSD8.InvalidSignature.selector);
        registry.payWithAuthorization(idB, payer, validAfter, validBefore, v, r, s);
    }

    function test_PayWithAuthorization_ReplayReverts() public {
        uint256 id = _create(payer, AMOUNT, 0);
        uint256 validAfter = block.timestamp;
        uint256 validBefore = block.timestamp + 3600;
        (uint8 v, bytes32 r, bytes32 s) = _signReceive(
            PAYER_PK, payer, address(registry), AMOUNT, validAfter, validBefore, registry.authorizationNonce(id)
        );

        vm.warp(block.timestamp + 10);
        vm.prank(relayer);
        registry.payWithAuthorization(id, payer, validAfter, validBefore, v, r, s);

        vm.prank(relayer);
        vm.expectRevert(abi.encodeWithSelector(InvoiceRegistry.InvoiceNotOpen.selector, id));
        registry.payWithAuthorization(id, payer, validAfter, validBefore, v, r, s);
    }

    function test_PayWithAuthorization_ExpiredReverts() public {
        uint256 id = _create(payer, AMOUNT, 0);
        uint256 validAfter = block.timestamp;
        uint256 validBefore = block.timestamp + 60;
        (uint8 v, bytes32 r, bytes32 s) = _signReceive(
            PAYER_PK, payer, address(registry), AMOUNT, validAfter, validBefore, registry.authorizationNonce(id)
        );

        vm.warp(validBefore + 1);
        vm.prank(relayer);
        vm.expectRevert(MockUSD8.AuthorizationExpired.selector);
        registry.payWithAuthorization(id, payer, validAfter, validBefore, v, r, s);
    }

    function test_PayWithAuthorization_WrongPayerReverts() public {
        uint256 id = _create(payer, AMOUNT, 0);
        uint256 validAfter = block.timestamp;
        uint256 validBefore = block.timestamp + 3600;
        // Stranger signs, but invoice names `payer`.
        (uint8 v, bytes32 r, bytes32 s) = _signReceive(
            STRANGER_PK, stranger, address(registry), AMOUNT, validAfter, validBefore, registry.authorizationNonce(id)
        );

        vm.warp(block.timestamp + 10);
        vm.prank(relayer);
        vm.expectRevert(abi.encodeWithSelector(InvoiceRegistry.WrongPayer.selector, id, payer, stranger));
        registry.payWithAuthorization(id, stranger, validAfter, validBefore, v, r, s);
    }

    // ------------------------------------------------------------ cancel / overdue

    function test_Cancel_ByIssuer() public {
        uint256 id = _create(payer, AMOUNT, 0);
        vm.prank(issuer);
        registry.cancelInvoice(id);

        vm.startPrank(payer);
        token.approve(address(registry), AMOUNT);
        vm.expectRevert(abi.encodeWithSelector(InvoiceRegistry.InvoiceNotOpen.selector, id));
        registry.pay(id);
        vm.stopPrank();
    }

    function test_Cancel_ByNonIssuerReverts() public {
        uint256 id = _create(payer, AMOUNT, 0);
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(InvoiceRegistry.NotIssuer.selector, id));
        registry.cancelInvoice(id);
    }

    function test_IsOverdue() public {
        uint64 due = uint64(block.timestamp + 1000);
        uint256 id = _create(payer, AMOUNT, due);
        assertTrue(!registry.isOverdue(id), "not overdue before due date");
        vm.warp(due + 1);
        assertTrue(registry.isOverdue(id), "overdue after due date");
        uint256 noDue = _create(payer, AMOUNT, 0);
        assertTrue(!registry.isOverdue(noDue), "no due date never overdue");
    }

    // ------------------------------------------------------------ mock token extras

    function test_Mock_PermitFlow() public {
        uint256 deadline = block.timestamp + 3600;
        bytes32 structHash =
            keccak256(abi.encode(token.PERMIT_TYPEHASH(), payer, address(registry), AMOUNT, token.nonces(payer), deadline));
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", token.DOMAIN_SEPARATOR(), structHash));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PAYER_PK, digest);

        token.permit(payer, address(registry), AMOUNT, deadline, v, r, s);
        assertEq(token.allowance(payer, address(registry)), AMOUNT, "permit set allowance");
        assertEq(token.nonces(payer), 1, "permit nonce consumed");
    }

    // ------------------------------------------------------------ fuzz

    function testFuzz_CreateAndPay(uint256 amount, uint64 dueAt) public {
        amount = 1 + (amount % 999_000_000); // 1 .. 999 tUSD8, within payer balance
        uint256 id = _create(payer, amount, dueAt);
        vm.startPrank(payer);
        token.approve(address(registry), amount);
        registry.pay(id);
        vm.stopPrank();
        assertEq(token.balanceOf(issuer), amount, "fuzz: issuer got exact amount");
        assertTrue(registry.isPaid(id), "fuzz: paid");
    }
}
