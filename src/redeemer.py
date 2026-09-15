"""
On-chain redemption of resolved Polymarket positions.

This is the piece that was missing for real live trading: py-clob-client's
REST API can place orders, but claiming payout for a resolved position is not
a REST call -- Polymarket's markets are ERC1155 conditional tokens (Gnosis
Conditional Tokens Framework), and converting a winning token back into real
USDC requires calling `redeemPositions` directly on the CTF contract, signed
by the wallet's own key, submitted as a real Polygon transaction.

Contract addresses below are NOT guessed -- pulled directly from the
installed py-clob-client package's own config.py (py_clob_client/config.py,
get_contract_config()), the same values py-clob-client itself uses to build
and sign orders. Standard (non-neg-risk) markets only; Polymarket's
NegRiskAdapter path (a different contract + different redeem signature) is
intentionally NOT implemented -- see redeem_position()'s neg_risk check,
which refuses rather than guessing at an unverified contract call.
"""
import json
from typing import Optional

from loguru import logger
from config import config

# Polygon mainnet (chain 137), non-neg-risk markets -- from py_clob_client.config.get_contract_config(137, neg_risk=False)
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ZERO_BYTES32 = "0x" + "00" * 32

# Minimal ABI: only the one function this module calls.
CTF_REDEEM_ABI = json.loads("""
[{
    "inputs": [
        {"internalType": "address", "name": "collateralToken", "type": "address"},
        {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"},
        {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
        {"internalType": "uint256[]", "name": "indexSets", "type": "uint256[]"}
    ],
    "name": "redeemPositions",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
}]
""")


def get_condition_id(slug: str) -> Optional[str]:
    """Fetch a market's on-chain conditionId from Polymarket's Gamma API by slug."""
    import urllib.request
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            events = json.loads(resp.read().decode())
        if not events or not isinstance(events, list):
            return None
        markets = events[0].get("markets") or []
        if not markets:
            return None
        return markets[0].get("conditionId")
    except Exception as e:
        logger.error(f"Redeemer: failed to fetch conditionId for slug={slug}: {type(e).__name__}: {e}")
        return None


def redeem_position(slug: str, is_neg_risk: bool = False) -> dict:
    """
    Submits a real redeemPositions transaction for a resolved market, signed
    by config.poly_private_key. Redeems BOTH outcome index sets (1 and 2) in
    one call -- the contract pays out only for whichever side actually holds
    a winning ERC1155 balance; including the losing side's index set is a
    documented no-op, not a mistake.

    Returns {"status": "REDEEMED", "tx_hash": ...} on success,
    {"status": "REFUSED"|"FAILED", "reason": ...} otherwise. Never raises --
    callers (executor.py's settlement path) must not have a redemption
    failure interrupt the trading loop.
    """
    if is_neg_risk:
        return {
            "status": "REFUSED",
            "reason": (
                "Market is neg-risk (uses Polymarket's NegRiskAdapter contract, a "
                "different redemption path than plain CTF redeemPositions). Not "
                "implemented -- redeeming against the wrong contract could fail "
                "or behave unexpectedly. Redeem this position manually on "
                "polymarket.com until NegRiskAdapter support is added."
            ),
        }
    if not config.poly_private_key:
        return {"status": "REFUSED", "reason": "POLY_PRIVATE_KEY not configured."}

    condition_id = get_condition_id(slug)
    if not condition_id:
        return {"status": "FAILED", "reason": f"Could not resolve conditionId for slug={slug}."}

    try:
        from web3 import Web3
        from eth_account import Account

        w3 = Web3(Web3.HTTPProvider(config.polygon_rpc_url))
        if not w3.is_connected():
            return {"status": "FAILED", "reason": f"Could not connect to Polygon RPC at {config.polygon_rpc_url}."}

        account = Account.from_key(config.poly_private_key)
        ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_REDEEM_ABI)

        tx = ctf.functions.redeemPositions(
            Web3.to_checksum_address(USDC_ADDRESS),
            ZERO_BYTES32,
            bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id),
            [1, 2],  # both binary index sets -- contract pays out only the winning side
        ).build_transaction({
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "chainId": config.chain_id,
        })

        # Gas estimate is done as part of a real send -- to keep this module's
        # blast radius to "submits one real transaction, nothing more," gas
        # price/limit are left to build_transaction's defaults (web3 fills
        # reasonable values from the node) rather than hand-tuned here.
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.warning(f"Redeemer: submitted redeemPositions tx {tx_hash.hex()} for slug={slug} (conditionId={condition_id})")

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt.status == 1:
            logger.warning(f"Redeemer: CONFIRMED redemption for {slug}, tx={tx_hash.hex()}")
            return {"status": "REDEEMED", "tx_hash": tx_hash.hex()}
        else:
            logger.error(f"Redeemer: tx {tx_hash.hex()} for {slug} reverted on-chain.")
            return {"status": "FAILED", "reason": "Transaction reverted on-chain.", "tx_hash": tx_hash.hex()}

    except Exception as e:
        logger.error(f"Redeemer: redemption failed for slug={slug}: {type(e).__name__}: {e}")
        return {"status": "FAILED", "reason": f"{type(e).__name__}: {e}"}
