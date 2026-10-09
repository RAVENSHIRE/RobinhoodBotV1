from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import math

from dotenv import load_dotenv


def boolean(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


def optional_float(name: str) -> float | None:
    value = os.getenv(name, "").strip()
    return float(value) if value else None


@dataclass(frozen=True)
class Config:
    rpc_url: str
    chain_id: int
    factory: str
    weth: str
    quoter: str | None
    poll_interval: float
    backfill_blocks: int
    max_block_range: int
    monitor_minutes: int
    max_active_pools: int
    paper_stop_loss_pct: float
    paper_take_profit_pct: float
    paper_max_hold_seconds: int
    max_open_positions: int
    max_paper_exposure_usd: float
    max_daily_loss_usd: float
    eth_usd: float | None
    min_liquidity_usd: float
    min_volatility_pct: float
    max_volatility_pct: float
    min_confidence: float
    max_risk_score: float
    max_price_impact_pct: float
    owner_renounced_required: bool
    paper_order_usd: float
    paper_slippage_bps: float
    paper_fee_bps: float
    live_mode: bool
    decision_provider: str
    typesafe_api_key: str | None
    jev_endpoint: str
    jev_model: str
    data_dir: Path
    host: str
    port: int
    price_feed_mode: str = 'coinbase_kraken'
    price_refresh_seconds: float = 10.0
    price_max_age_seconds: float = 60.0
    price_max_divergence_pct: float = 1.0
    price_future_tolerance_seconds: float = 5.0
    quote_max_age_seconds: float = 15.0


def load_config(env_path: str | None = None) -> Config:
    load_dotenv(env_path, override=False)
    cfg = Config(
        rpc_url=os.getenv("RPC_URL", "https://rpc.mainnet.chain.robinhood.com"),
        chain_id=int(os.getenv("CHAIN_ID", "4663")),
        factory=os.getenv("V3_FACTORY", "0x1f7d7550B1b028f7571E69A784071F0205FD2EfA"),
        weth=os.getenv("WETH_ADDRESS", "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"),
        quoter=os.getenv("QUOTER_V2_ADDRESS") or None,
        poll_interval=float(os.getenv("POLL_INTERVAL", "5")),
        backfill_blocks=int(os.getenv("BACKFILL_BLOCKS", "25")),
        max_block_range=int(os.getenv("MAX_BLOCK_RANGE", "50")),
        monitor_minutes=int(os.getenv("MONITOR_MINUTES", "12")),
        max_active_pools=int(os.getenv("MAX_ACTIVE_POOLS", "25")),
        paper_stop_loss_pct=float(os.getenv("PAPER_STOP_LOSS_PCT", "15")),
        paper_take_profit_pct=float(os.getenv("PAPER_TAKE_PROFIT_PCT", "50")),
        paper_max_hold_seconds=int(os.getenv("PAPER_MAX_HOLD_SECONDS", "900")),
        max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "3")),
        max_paper_exposure_usd=float(os.getenv("MAX_PAPER_EXPOSURE_USD", "100")),
        max_daily_loss_usd=float(os.getenv("MAX_DAILY_LOSS_USD", "75")),
        eth_usd=optional_float("ETH_USD"),
        min_liquidity_usd=float(os.getenv("MIN_LIQUIDITY_USD", "20000")),
        min_volatility_pct=float(os.getenv("MIN_VOLATILITY_PCT", "1")),
        max_volatility_pct=float(os.getenv("MAX_VOLATILITY_PCT", "100")),
        min_confidence=float(os.getenv("MIN_CONFIDENCE", "0.70")),
        max_risk_score=float(os.getenv("MAX_RISK_SCORE", "4")),
        max_price_impact_pct=float(os.getenv("MAX_PRICE_IMPACT_PCT", "3")),
        owner_renounced_required=boolean("OWNER_RENOUNCED_REQUIRED", True),
        paper_order_usd=float(os.getenv("PAPER_ORDER_USD", "25")),
        paper_slippage_bps=float(os.getenv("PAPER_SLIPPAGE_BPS", "100")),
        paper_fee_bps=float(os.getenv("PAPER_FEE_BPS", "100")),
        live_mode=boolean("LIVE_MODE", False),
        decision_provider=os.getenv("DECISION_PROVIDER", "stub").lower(),
        typesafe_api_key=os.getenv("TYPESAFE_API_KEY") or None,
        jev_endpoint=os.getenv("JEV_ENDPOINT", "https://api.typesafe.ai/v1/systemone"),
        jev_model=os.getenv("JEV_MODEL", "jev-latest"),
        data_dir=Path(os.getenv("DATA_DIR", "./data")),
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        price_feed_mode=os.getenv('PRICE_FEED_MODE', 'coinbase_kraken').lower(),
        price_refresh_seconds=float(os.getenv('PRICE_REFRESH_SECONDS', '10')),
        price_max_age_seconds=float(os.getenv('PRICE_MAX_AGE_SECONDS', '60')),
        price_max_divergence_pct=float(os.getenv('PRICE_MAX_DIVERGENCE_PCT', '1')),
        price_future_tolerance_seconds=float(os.getenv('PRICE_FUTURE_TOLERANCE_SECONDS', '5')),
        quote_max_age_seconds=float(os.getenv('QUOTE_MAX_AGE_SECONDS', '15')),
    )
    if cfg.chain_id != 4663:
        raise ValueError("This project is mainnet Robinhood Chain only: CHAIN_ID must be 4663")
    if cfg.poll_interval <= 0 or cfg.max_block_range < 1 or cfg.max_active_pools < 1:
        raise ValueError("Invalid polling settings")
    if not 0 <= cfg.min_confidence <= 1 or not 0 <= cfg.max_risk_score <= 10:
        raise ValueError("Invalid model gate settings")
    if cfg.live_mode:
        raise ValueError('LIVE_MODE=true refused: this MVP does not implement verified live execution')
    if cfg.min_volatility_pct < 0 or cfg.max_volatility_pct <= cfg.min_volatility_pct:
        raise ValueError('Invalid volatility range')
    if cfg.paper_order_usd <= 0 or cfg.max_open_positions < 1 or cfg.max_paper_exposure_usd <= 0 or cfg.max_daily_loss_usd <= 0:
        raise ValueError('Invalid risk / paper size settings')
    if cfg.eth_usd is not None and cfg.eth_usd <= 0:
        raise ValueError("ETH_USD must be positive")
    if cfg.price_feed_mode not in ('coinbase_kraken', 'off'):
        raise ValueError('PRICE_FEED_MODE must be coinbase_kraken or off')
    for value in (cfg.price_refresh_seconds, cfg.price_max_age_seconds,
                  cfg.price_max_divergence_pct, cfg.quote_max_age_seconds):
        if not math.isfinite(value) or value <= 0:
            raise ValueError('Price and quote limits must be positive and finite')
    if (not math.isfinite(cfg.price_future_tolerance_seconds) or
            cfg.price_future_tolerance_seconds < 0 or
            cfg.price_refresh_seconds >= cfg.price_max_age_seconds):
        raise ValueError('Invalid price timing limits')
    return cfg
