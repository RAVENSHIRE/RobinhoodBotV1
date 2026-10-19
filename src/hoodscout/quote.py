"""Uniswap V3 QuoterV2 read-only price impact check (WETH -> token)."""
from __future__ import annotations
from decimal import Decimal
import math
import time
from .rpc import RPC, RpcError, read_word, address_word
from .chain import v3_token1_per_token0, PoolEvent
from .gates import finite

# quoteExactInputSingle((address,address,uint256,uint24,uint160))
QUOTER_EXACT_INPUT_SINGLE = '0xc6a5026a'


def encode_quote_exact_input_single(token_in: str, token_out: str, amount_in: int, fee: int) -> str:
    if amount_in <= 0 or fee not in (100, 500, 3000, 10000):
        raise ValueError('Invalid input amount / fee tier')
    return (QUOTER_EXACT_INPUT_SINGLE + address_word(token_in) + address_word(token_out)
            + format(amount_in, '064x') + format(fee, '064x') + '0' * 64)


def quote_buy(rpc: RPC, quoter: str, event: PoolEvent, weth: str,
              eth_usd: float, paper_order_usd: float, sqrt_price_x96: int,
              decimals0: int, decimals1: int) -> dict:
    if event.token0.lower() != weth.lower() and event.token1.lower() != weth.lower():
        raise ValueError('Only WETH pairs supported')
    ratio = v3_token1_per_token0(sqrt_price_x96, decimals0, decimals1)
    if ratio is None or ratio <= 0:
        raise ValueError('Uninitialized pool')
    amount_in = int(Decimal(str(paper_order_usd)) / Decimal(str(eth_usd)) * Decimal(10 ** 18))
    token_out = event.token1 if event.token0.lower() == weth.lower() else event.token0
    token_out_decimals = decimals1 if event.token0.lower() == weth.lower() else decimals0
    data = encode_quote_exact_input_single(weth, token_out, amount_in, event.fee)
    quote_observed_at = time.time()
    output = rpc.eth_call(quoter, data)
    amount_out = read_word(output, 0)
    sqrt_after = read_word(output, 1)
    ticks_crossed, gas_estimate = read_word(output, 2), read_word(output, 3)
    if amount_out <= 0 or sqrt_after <= 0:
        raise ValueError('Quoter produced no tokens')
    weth_human = Decimal(amount_in) / Decimal(10 ** 18)
    spot_out = weth_human * (ratio if event.token0.lower() == weth.lower() else (Decimal(1) / ratio))
    actual_out = Decimal(amount_out) / Decimal(10 ** token_out_decimals)
    fee_multiplier = Decimal(1) - Decimal(event.fee) / Decimal(1_000_000)
    if spot_out <= 0 or fee_multiplier <= 0:
        raise ValueError('Invalid pool price or fee')
    price_impact_pct = max(Decimal(0), (Decimal(1) - actual_out / (spot_out * fee_multiplier)) * Decimal(100))
    return {
        'amount_in_wei': str(amount_in), 'amount_out_raw': str(amount_out),
        'quote_tokens_out': float(actual_out), 'fee_tier': event.fee,
        'sqrt_price_after_x96': str(sqrt_after), 'price_impact_pct': float(price_impact_pct),
        'quote_usd': float(Decimal(str(eth_usd)) * weth_human),
        'quote_observed_at': quote_observed_at,
        'initialized_ticks_crossed': ticks_crossed, 'quote_gas_estimate': gas_estimate,
    }


def quote_sell(rpc: RPC, quoter: str, event: PoolEvent, weth: str,
               eth_usd: float, units: float, token_decimals: int) -> dict:
    """Quote the held quantity back into WETH; this does not prove ERC20 transferability."""
    if (not isinstance(token_decimals, int) or isinstance(token_decimals, bool)
            or not 0 <= token_decimals <= 36 or not finite(units) or units <= 0
            or not finite(eth_usd) or eth_usd <= 0):
        raise ValueError('Invalid sell quantity, decimals or USD reference')
    if weth.lower() not in (event.token0.lower(), event.token1.lower()):
        raise ValueError('Only WETH pairs supported')
    token = event.token1 if event.token0.lower() == weth.lower() else event.token0
    amount_in = int(Decimal(str(units)) * (Decimal(10) ** token_decimals))
    if not 0 < amount_in < 2**256:
        raise ValueError('Invalid raw sell amount')
    observed_at = time.time()
    output = rpc.eth_call(quoter, encode_quote_exact_input_single(token, weth, amount_in, event.fee))
    amount_out, sqrt_after = read_word(output, 0), read_word(output, 1)
    gas_estimate = read_word(output, 3)
    if amount_out <= 0 or sqrt_after <= 0:
        raise ValueError('Quoter produced no WETH')
    proceeds = Decimal(amount_out) / Decimal(10**18) * Decimal(str(eth_usd))
    if not math.isfinite(float(proceeds)) or proceeds <= 0:
        raise ValueError('Invalid quoted proceeds')
    return {'pool_address': event.address.lower(), 'token_address': token.lower(),
            'units': units, 'amount_in_raw': str(amount_in), 'amount_out_wei': str(amount_out),
            'proceeds_usd': float(proceeds), 'observed_at': observed_at, 'gas_estimate': gas_estimate}
