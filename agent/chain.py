"""Chain access for the Aevum tUSD8 invoicing agent.

Talks to an EVM JSON-RPC endpoint (local anvil by default; Aevum testnet
chain 44005 in production). Aevum specifics handled here:

* ``eth_getLogs`` is limited to 1,000-block ranges on Aevum -> all log reads
  are chunked (``get_logs_chunked``).
* No WebSocket RPC on Aevum -> polling over HTTPS only.
* Transactions are sent as legacy (type 0), matching Aevum tooling.

ABIs are loaded from the Foundry build artifacts (contracts/out/*.json) so
the Python side can never drift from the compiled Solidity.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

MAX_LOG_RANGE = 1000  # Aevum eth_getLogs limit (blocks per request)

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "contracts" / "out"


def load_abi(contract: str) -> list:
    artifact = OUT_DIR / f"{contract}.sol" / f"{contract}.json"
    if not artifact.exists():
        raise FileNotFoundError(
            f"Missing Foundry artifact {artifact}. Run `forge build` in contracts/ first."
        )
    return json.loads(artifact.read_text())["abi"]


def load_bytecode(contract: str) -> str:
    artifact = OUT_DIR / f"{contract}.sol" / f"{contract}.json"
    data = json.loads(artifact.read_text())
    return data["bytecode"]["object"]


def fmt_amount(base_units: int, decimals: int = 6) -> str:
    """Format token base units as a human decimal string (25_500_000 -> '25.50')."""
    whole, frac = divmod(int(base_units), 10**decimals)
    frac_str = f"{frac:0{decimals}d}".rstrip("0")
    if len(frac_str) < 2:
        frac_str = frac_str.ljust(2, "0")
    return f"{whole}.{frac_str}"


def parse_amount(text: str, decimals: int = 6) -> int:
    """Parse a human decimal string ('25.50') into base units."""
    text = text.strip()
    if "." in text:
        whole, frac = text.split(".", 1)
    else:
        whole, frac = text, ""
    frac = (frac + "0" * decimals)[:decimals]
    return int(whole or "0") * 10**decimals + int(frac or "0")


class Chain:
    def __init__(self, rpc_url: str, registry_address: str | None = None, token_address: str | None = None):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.w3.is_connected():
            raise ConnectionError(f"Cannot connect to RPC at {rpc_url}")
        self.chain_id = self.w3.eth.chain_id
        self.registry = None
        self.token = None
        if registry_address:
            self.registry = self.w3.eth.contract(
                address=Web3.to_checksum_address(registry_address), abi=load_abi("InvoiceRegistry")
            )
        if token_address:
            self.token = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_address), abi=load_abi("MockUSD8")
            )

    # -------------------------------------------------------------- deploy

    def deploy_mock_token(self, deployer_key: str) -> str:
        acct = Account.from_key(deployer_key)
        contract = self.w3.eth.contract(abi=load_abi("MockUSD8"), bytecode=load_bytecode("MockUSD8"))
        tx = contract.constructor().build_transaction(self._tx_base(acct.address))
        receipt = self._send(acct, tx)
        self.token = self.w3.eth.contract(address=receipt.contractAddress, abi=load_abi("MockUSD8"))
        return receipt.contractAddress

    def deploy_registry(self, deployer_key: str, token_address: str) -> str:
        acct = Account.from_key(deployer_key)
        contract = self.w3.eth.contract(
            abi=load_abi("InvoiceRegistry"), bytecode=load_bytecode("InvoiceRegistry")
        )
        tx = contract.constructor(Web3.to_checksum_address(token_address)).build_transaction(
            self._tx_base(acct.address)
        )
        receipt = self._send(acct, tx)
        self.registry = self.w3.eth.contract(
            address=receipt.contractAddress, abi=load_abi("InvoiceRegistry")
        )
        return receipt.contractAddress

    # -------------------------------------------------------------- invoices

    def create_invoice(self, issuer_key: str, payer: str, amount: int, due_at: int, memo_text: str) -> tuple[int, str]:
        acct = Account.from_key(issuer_key)
        memo_hash = Web3.keccak(text=memo_text)
        tx = self.registry.functions.createInvoice(
            Web3.to_checksum_address(payer) if payer else "0x0000000000000000000000000000000000000000",
            amount,
            due_at,
            memo_hash,
        ).build_transaction(self._tx_base(acct.address))
        receipt = self._send(acct, tx)
        invoice_id = self.registry.functions.invoiceCount().call()
        return invoice_id, receipt.transactionHash.hex()

    def pay_standard(self, payer_key: str, invoice_id: int, amount: int) -> str:
        acct = Account.from_key(payer_key)
        approve_tx = self.token.functions.approve(self.registry.address, amount).build_transaction(
            self._tx_base(acct.address)
        )
        self._send(acct, approve_tx)
        pay_tx = self.registry.functions.pay(invoice_id).build_transaction(self._tx_base(acct.address))
        receipt = self._send(acct, pay_tx)
        return receipt.transactionHash.hex()

    def sign_receive_authorization(
        self, payer_key: str, invoice_id: int, amount: int, valid_after: int, valid_before: int
    ) -> dict:
        """Sign an EIP-3009 ReceiveWithAuthorization for an invoice (off-chain)."""
        acct = Account.from_key(payer_key)
        nonce = self.registry.functions.authorizationNonce(invoice_id).call()
        typed = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "ReceiveWithAuthorization": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "value", "type": "uint256"},
                    {"name": "validAfter", "type": "uint256"},
                    {"name": "validBefore", "type": "uint256"},
                    {"name": "nonce", "type": "bytes32"},
                ],
            },
            "primaryType": "ReceiveWithAuthorization",
            "domain": {
                "name": "tUSD8",
                "version": "2",
                "chainId": self.chain_id,
                "verifyingContract": self.token.address,
            },
            "message": {
                "from": acct.address,
                "to": self.registry.address,
                "value": amount,
                "validAfter": valid_after,
                "validBefore": valid_before,
                "nonce": nonce,
            },
        }
        signed = acct.sign_message(encode_typed_data(full_message=typed))
        return {
            "from": acct.address,
            "validAfter": valid_after,
            "validBefore": valid_before,
            "v": signed.v,
            "r": signed.r.to_bytes(32, "big"),
            "s": signed.s.to_bytes(32, "big"),
        }

    def submit_gasless(self, relayer_key: str, invoice_id: int, auth: dict) -> str:
        acct = Account.from_key(relayer_key)
        tx = self.registry.functions.payWithAuthorization(
            invoice_id, auth["from"], auth["validAfter"], auth["validBefore"], auth["v"], auth["r"], auth["s"]
        ).build_transaction(self._tx_base(acct.address))
        receipt = self._send(acct, tx)
        return receipt.transactionHash.hex()

    def get_invoice(self, invoice_id: int) -> dict:
        inv = self.registry.functions.getInvoice(invoice_id).call()
        keys = ["issuer", "payer", "amount", "dueAt", "memoHash", "status", "paidBy", "paidAt"]
        result = dict(zip(keys, inv))
        result["status"] = ["Open", "Paid", "Cancelled"][result["status"]]
        result["memoHash"] = result["memoHash"].hex() if isinstance(result["memoHash"], bytes) else result["memoHash"]
        return result

    def token_balance(self, address: str) -> int:
        return self.token.functions.balanceOf(Web3.to_checksum_address(address)).call()

    def mint_mock(self, minter_key: str, to: str, amount: int) -> str:
        acct = Account.from_key(minter_key)
        tx = self.token.functions.mint(Web3.to_checksum_address(to), amount).build_transaction(
            self._tx_base(acct.address)
        )
        receipt = self._send(acct, tx)
        return receipt.transactionHash.hex()

    # -------------------------------------------------------------- events

    def get_logs_chunked(self, from_block: int, to_block: int, topics: list | None = None) -> list:
        """eth_getLogs in <=1,000-block chunks (Aevum limit)."""
        logs: list = []
        start = from_block
        while start <= to_block:
            end = min(start + MAX_LOG_RANGE - 1, to_block)
            params = {"fromBlock": start, "toBlock": end, "address": self.registry.address}
            if topics:
                params["topics"] = topics
            logs.extend(self.w3.eth.get_logs(params))
            start = end + 1
        return logs

    def fetch_events(self, from_block: int, to_block: int) -> list[dict]:
        """Decode InvoiceCreated / InvoicePaid / InvoiceCancelled events."""
        events: list[dict] = []
        for name in ("InvoiceCreated", "InvoicePaid", "InvoiceCancelled"):
            event = getattr(self.registry.events, name)
            for log in self.get_logs_chunked(from_block, to_block, [event().topic]):
                decoded = event().process_log(log)
                events.append(
                    {
                        "event": name,
                        "args": {k: (v.hex() if isinstance(v, bytes) else v) for k, v in decoded["args"].items()},
                        "blockNumber": log["blockNumber"],
                        "transactionHash": log["transactionHash"].hex(),
                        "logIndex": log["logIndex"],
                    }
                )
        events.sort(key=lambda e: (e["blockNumber"], e["logIndex"]))
        return events

    # -------------------------------------------------------------- internals

    def _tx_base(self, sender: str) -> dict:
        return {
            "from": sender,
            "nonce": self.w3.eth.get_transaction_count(sender),
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.chain_id,
            # No "type" key: with gasPrice set, eth-account builds a legacy
            # (type-0) transaction, matching Aevum tooling.
        }

    def _send(self, acct, tx: dict):
        tx.setdefault("gas", 500_000)
        signed = acct.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status != 1:
            raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")
        return receipt


def env_or(name: str, default: str) -> str:
    return os.environ.get(name, default)
