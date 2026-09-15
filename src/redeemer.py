"""
On-chain redemption of resolved Polymarket positions.

This is the piece that was missing for real live trading: py-clob-client's
REST API can place orders, but claiming payout for a resolved position is not
a REST call -- Polymarket's markets are ERC1155 conditional tokens (Gnosis
Conditional Tokens Framework), and converting a winning token back into real
USDC requires calling `redeemPositions` directly on the CTF contract, signed
by the wallet's own key, submitted as a real Polygon transaction.

Contract addresses below are NOT guessed. Standard-market addresses are
pulled directly from the installed py-clob-client package's own config.py
(py_clob_client/config.py, get_contract_config()), the same values
py-clob-client itself uses to build and sign orders. The NegRiskAdapter
address/ABI is NOT exposed by py-clob-client (it only builds/signs orders,
never redemptions) -- it was cross-checked against Polymarket's own docs
(docs.polymarket.com/resources/contracts) and its verified source on
PolygonScan (0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296).
"""
import json
from typing import Optional

from loguru import logger
from config import config

# Polygon mainnet (chain 137), non-neg-risk markets -- from py_clob_client.config.get_contract_config(137, neg_risk=False)
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ZERO_BYTES32 = "0x" + "00" * 32

# NegRiskAdapter -- separate contract Polymarket uses to enforce "exactly one
# outcome wins" for neg-risk markets; verified source on PolygonScan.
NEG_RISK_ADAPTER_ADDRESS = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

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

# NegRiskAdapter.redeemPositions(bytes32 _conditionId, uint256[] _amounts) --
# NOT an index-set bitmask like CTF's. _amounts is the actual token quantity
# to redeem PER OUTCOME INDEX (index 0 / index 1 of the market's clobTokenIds
# ordering). Passing the real share balance for the held outcome and 0 for
# the other is what redeem_position() below does -- see _amounts_for_outcome().
NEG_RISK_REDEEM_ABI = json.loads("""
[{
    "inputs": [
        {"internalType": "bytes32", "name": "_conditionId", "type": "bytes32"},
        {"internalType": "uint256[]", "name": "_amounts", "type": "uint256[]"}
    ],
    "name": "redeemPositions",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
}]
""")


def get_market_info(slug: str) -> Optional[dict]:
    """
    Fetch a market's on-chain conditionId AND its clobTokenIds ordering from
    Polymarket's Gamma API by slug. clobTokenIds ordering is needed for
    neg-risk redemption, where NegRiskAdapter.redeemPositions takes amounts
    indexed by outcome position rather than a CTF-style index-set bitmask.
    """
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
        market = markets[0]
        clob_token_ids = market.get("clobTokenIds")
        if isinstance(clob_token_ids, str):
            clob_token_ids = json.loads(clob_token_ids)
        return {
            "condition_id": market.get("conditionId"),
            "clob_token_ids": clob_token_ids or [],
        }
    except Exception as e:
        logger.error(f"Redeemer: failed to fetch market info for slug={slug}: {type(e).__name__}: {e}")
        return None


def get_condition_id(slug: str) -> Optional[str]:
    """Fetch a market's on-chain conditionId from Polymarket's Gamma API by slug."""
    info = get_market_info(slug)
    return info.get("condition_id") if info else None


def redeem_position(slug: str, token_id: str = "", shares: float = 0.0, is_neg_risk: bool = False) -> dict:
    """
    Submits a real redeemPositions transaction for a resolved market, signed
    by config.poly_private_key.

    Standard markets: redeems BOTH CTF index sets (1 and 2) in one call --
    the contract pays out only for whichever side actually holds a winning
    ERC1155 balance; including the losing side's index set is a documented
    no-op, not a mistake.

    Neg-risk markets: NegRiskAdapter.redeemPositions takes actual per-outcome
    SHARE AMOUNTS, not an index-set bitmask, so token_id and shares (the
    position actually held) are required to build a correct amounts array --
    see _amounts_for_outcome().

    Returns {"status": "REDEEMED", "tx_hash": ...} on success,
    {"status": "REFUSED"|"FAILED", "reason": ...} otherwise. Never raises --
    callers (executor.py's settlement path) must not have a redemption
    failure interrupt the trading loop.
    """
    if not config.poly_private_key:
        return {"status": "REFUSED", "reason": "POLY_PRIVATE_KEY not configured."}

    if is_neg_risk and (not token_id or shares <= 0):
        return {
            "status": "REFUSED",
            "reason": "Neg-risk redemption requires token_id and shares (the actual held position) to build the amounts array.",
        }

    market_info = get_market_info(slug)
    condition_id = market_info.get("condition_id") if market_info else None
    if not condition_id:
        return {"status": "FAILED", "reason": f"Could not resolve conditionId for slug={slug}."}

    try:
        from web3 import Web3
        from eth_account import Account

        w3 = Web3(Web3.HTTPProvider(config.polygon_rpc_url))
        if not w3.is_connected():
            return {"status": "FAILED", "reason": f"Could not connect to Polygon RPC at {config.polygon_rpc_url}."}

        account = Account.from_key(config.poly_private_key)

        # Redemption is an on-chain tx and costs real gas (paid in POL/MATIC, not
        # USDC) -- unlike order placement, which is off-chain/gasless via the CLOB.
        # A wallet funded with USDC but zero POL would have every redemption fail
        # here, silently leaving winning positions stuck unredeemed indefinitely
        # (a real, easy-to-miss way for "profitable" trades to never become real
        # profit). Check and refuse with a clear reason instead of burning the
        # 120s receipt-wait timeout on a tx that can never be mined.
        native_balance_wei = w3.eth.get_balance(account.address)
        native_balance = native_balance_wei / 1e18
        MIN_GAS_BALANCE_POL = 0.05
        if native_balance < MIN_GAS_BALANCE_POL:
            return {
                "status": "REFUSED",
                "reason": (
                    f"Wallet {account.address} has only {native_balance:.5f} POL -- "
                    f"below the {MIN_GAS_BALANCE_POL} POL minimum assumed needed for "
                    f"redemption gas. Fund the wallet with POL (not USDC) or redemption "
                    f"will keep failing and winning positions will stay stuck unredeemed."
                ),
            }

        condition_id_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)

        if is_neg_risk:
            clob_token_ids = market_info.get("clob_token_ids") or []
            if token_id not in clob_token_ids:
                return {
                    "status": "FAILED",
                    "reason": f"token_id={token_id} not found in market's clobTokenIds={clob_token_ids} for slug={slug}.",
                }
            outcome_index = clob_token_ids.index(token_id)
            # USDC is 6-decimal; amounts array must be in raw base units, indexed
            # by outcome position, with 0 for every outcome not actually held.
            amounts = [0, 0]
            amounts[outcome_index] = int(round(shares * 1_000_000))

            adapter = w3.eth.contract(address=Web3.to_checksum_address(NEG_RISK_ADAPTER_ADDRESS), abi=NEG_RISK_REDEEM_ABI)
            tx = adapter.functions.redeemPositions(
                condition_id_bytes,
                amounts,
            ).build_transaction({
                "from": account.address,
                "nonce": w3.eth.get_transaction_count(account.address),
                "chainId": config.chain_id,
            })
        else:
            ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_REDEEM_ABI)
            tx = ctf.functions.redeemPositions(
                Web3.to_checksum_address(USDC_ADDRESS),
                ZERO_BYTES32,
                condition_id_bytes,
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
