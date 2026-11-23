// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal ERC-20 surface this registry needs. Defined locally so the
///         project has zero external Solidity dependencies.
interface IERC20Minimal {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @notice EIP-3009 signed-transfer surface implemented by tUSD8 on Aevum
///         (USDC-style). Only `receiveWithAuthorization` is used: it requires
///         the caller to be the payee, so a leaked signature cannot be
///         front-run into a plain transfer by a third party.
interface IERC3009 {
    function receiveWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external;
}

/// @title InvoiceRegistry
/// @notice On-chain invoicing with direct (escrow-free) settlement in a
///         6-decimal stablecoin such as tUSD8 on Aevum testnet (chain 44005).
///
/// @dev Design notes — how this differs from a naive "checkout" contract:
///
///      1. **The price lives on-chain.** Each invoice stores its exact amount
///         due. `pay` / `payWithAuthorization` always move *that* amount; a
///         payer cannot mark an invoice paid by sending 0.000001 tokens.
///         Off-chain code never has to trust an `isPaid` flag alone.
///
///      2. **Direct settlement, no custody.** Funds move payer -> issuer in
///         the same transaction. The registry only holds tokens momentarily
///         inside `payWithAuthorization` (receive, then forward), never
///         across transactions. There is no escrow balance to drain.
///
///      3. **Gasless path (EIP-3009).** The payer signs a
///         `ReceiveWithAuthorization` message off-chain; anyone (the issuer,
///         a relayer, the Python agent) can submit it and pay the gas. The
///         signed nonce is `authorizationNonce(invoiceId)`, which binds the
///         signature to exactly one invoice of this registry, so it cannot
///         be replayed against a different invoice.
///
///      4. **Optional named payer.** If `payer` is set at creation, only that
///         address can settle the invoice. If it is `address(0)`, anyone may
///         pay (e.g. a public "pay this invoice" link).
///
///      5. **Audit trail.** `InvoiceCreated`, `InvoicePaid` and
///         `InvoiceCancelled` events are the source of truth for off-chain
///         reconciliation (see the Python agent in ../agent).
contract InvoiceRegistry {
    enum Status {
        Open,
        Paid,
        Cancelled
    }

    struct Invoice {
        address issuer; // who gets paid
        address payer; // expected payer; address(0) = anyone may pay
        uint256 amount; // exact amount due, in token base units (6 decimals)
        uint64 dueAt; // unix timestamp; 0 = no due date
        bytes32 memoHash; // keccak256 of the off-chain description/document
        Status status;
        address paidBy; // actual payer once settled
        uint64 paidAt; // settlement timestamp
    }

    /// @notice The settlement token (tUSD8 on Aevum testnet).
    IERC20Minimal public immutable token;

    /// @notice Number of invoices ever created. Ids start at 1.
    uint256 public invoiceCount;

    mapping(uint256 id => Invoice) private _invoices;

    // Minimal reentrancy guard (no external dependency).
    uint256 private _guard = 1;

    event InvoiceCreated(
        uint256 indexed id,
        address indexed issuer,
        address indexed payer,
        uint256 amount,
        uint64 dueAt,
        bytes32 memoHash
    );
    event InvoicePaid(uint256 indexed id, address indexed payer, uint256 amount);
    event InvoiceCancelled(uint256 indexed id);

    error ZeroAmount();
    error ZeroAddress();
    error InvoiceNotFound(uint256 id);
    error InvoiceNotOpen(uint256 id);
    error NotIssuer(uint256 id);
    error WrongPayer(uint256 id, address expected, address actual);
    error TransferFailed();
    error ReentrantCall();

    modifier nonReentrant() {
        if (_guard != 1) revert ReentrantCall();
        _guard = 2;
        _;
        _guard = 1;
    }

    constructor(address token_) {
        if (token_ == address(0)) revert ZeroAddress();
        token = IERC20Minimal(token_);
    }

    /// @notice Create an invoice. The caller is the issuer (payee).
    /// @param payer Expected payer, or address(0) to allow anyone to pay.
    /// @param amount Exact amount due in token base units (tUSD8: 6 decimals,
    ///        so 25.50 tUSD8 = 25_500_000).
    /// @param dueAt Unix timestamp after which the invoice is overdue (0 = none).
    /// @param memoHash keccak256 hash of the off-chain invoice document.
    /// @return id The new invoice id (sequential, starting at 1).
    function createInvoice(address payer, uint256 amount, uint64 dueAt, bytes32 memoHash)
        external
        returns (uint256 id)
    {
        if (amount == 0) revert ZeroAmount();
        id = ++invoiceCount;
        _invoices[id] = Invoice({
            issuer: msg.sender,
            payer: payer,
            amount: amount,
            dueAt: dueAt,
            memoHash: memoHash,
            status: Status.Open,
            paidBy: address(0),
            paidAt: 0
        });
        emit InvoiceCreated(id, msg.sender, payer, amount, dueAt, memoHash);
    }

    /// @notice Pay an invoice with tokens previously approved to this contract.
    /// @dev Moves exactly the stored amount from the caller to the issuer.
    function pay(uint256 id) external nonReentrant {
        Invoice storage inv = _openInvoice(id);
        if (inv.payer != address(0) && msg.sender != inv.payer) {
            revert WrongPayer(id, inv.payer, msg.sender);
        }
        _markPaid(inv, msg.sender);
        if (!token.transferFrom(msg.sender, inv.issuer, inv.amount)) revert TransferFailed();
        emit InvoicePaid(id, msg.sender, inv.amount);
    }

    /// @notice Pay an invoice gaslessly with an EIP-3009 signature.
    /// @dev The payer signs `ReceiveWithAuthorization(from, to=this contract,
    ///      value=invoice amount, validAfter, validBefore,
    ///      nonce=authorizationNonce(id))`. The amount is read from storage,
    ///      never from the caller, so a relayer cannot alter it.
    /// @param from The payer who signed the authorization.
    function payWithAuthorization(
        uint256 id,
        address from,
        uint256 validAfter,
        uint256 validBefore,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external nonReentrant {
        Invoice storage inv = _openInvoice(id);
        if (inv.payer != address(0) && from != inv.payer) {
            revert WrongPayer(id, inv.payer, from);
        }
        _markPaid(inv, from);
        IERC3009(address(token)).receiveWithAuthorization(
            from, address(this), inv.amount, validAfter, validBefore, authorizationNonce(id), v, r, s
        );
        if (!token.transfer(inv.issuer, inv.amount)) revert TransferFailed();
        emit InvoicePaid(id, from, inv.amount);
    }

    /// @notice Cancel an open invoice. Only the issuer can cancel.
    function cancelInvoice(uint256 id) external {
        Invoice storage inv = _invoice(id);
        if (msg.sender != inv.issuer) revert NotIssuer(id);
        if (inv.status != Status.Open) revert InvoiceNotOpen(id);
        inv.status = Status.Cancelled;
        emit InvoiceCancelled(id);
    }

    /// @notice The EIP-3009 nonce a payer must sign to settle invoice `id`
    ///         through this registry. Binding the nonce to (registry, id)
    ///         prevents cross-invoice and cross-contract replay.
    function authorizationNonce(uint256 id) public view returns (bytes32) {
        return keccak256(abi.encode(address(this), id));
    }

    function getInvoice(uint256 id) external view returns (Invoice memory) {
        return _invoice(id);
    }

    function isPaid(uint256 id) external view returns (bool) {
        return _invoice(id).status == Status.Paid;
    }

    /// @notice True if the invoice is open and past its due date.
    function isOverdue(uint256 id) external view returns (bool) {
        Invoice storage inv = _invoice(id);
        return inv.status == Status.Open && inv.dueAt != 0 && block.timestamp > inv.dueAt;
    }

    function _invoice(uint256 id) internal view returns (Invoice storage inv) {
        if (id == 0 || id > invoiceCount) revert InvoiceNotFound(id);
        inv = _invoices[id];
    }

    function _openInvoice(uint256 id) internal view returns (Invoice storage inv) {
        inv = _invoice(id);
        if (inv.status != Status.Open) revert InvoiceNotOpen(id);
    }

    function _markPaid(Invoice storage inv, address payer_) internal {
        inv.status = Status.Paid;
        inv.paidBy = payer_;
        inv.paidAt = uint64(block.timestamp);
    }
}
