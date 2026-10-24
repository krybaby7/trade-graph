"""Versioned, dated stablecoin and yield-token rules for the Kraken pair catalog.

Owner rules: D131 excludes stablecoins and yield-paying tokens (staking, lending, liquid-staking and
rebasing tokens) from the scanner and the paper coin list; D127 excludes riba. The table below is data, not
code: one entry per base asset, each with a one-line reason and a note on where the judgement comes from.

What the rules do not do:

- An asset with no entry stays unmarked and is reported as "not individually reviewed". An unmarked pair is
  never presented as verified clean. Review means a person or agent looked at the asset; it is not a
  guarantee, and the table can be wrong or out of date. Change it by adding an entry and a new version.
- Native proof-of-stake coins (ETH, SOL, ADA, DOT, AVAX, ATOM) are not yield tokens: rewards go only to
  holders who actively stake, which this system never does. Receipt and rebasing tokens that accrue yield by
  being held (liquid-staking tokens, a rebasing dollar) are marked. This is a reading of D131 that the owner
  may revise.
- A later phase (the scanner, T31) will also apply a price-peg check on observed prices, because a list
  cannot notice a stablecoin it does not know. That check is not built here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from trade_graph.contracts.pair_catalog import (
    EXCLUSION_FIAT_CURRENCY,
    EXCLUSION_STABLECOIN,
    EXCLUSION_YIELD_TOKEN,
    PAIR_STATUSES,
    ListedPair,
    PairRecord,
)

RULES_DATE = "2026-10-13"
CLASSIFICATION_RULES_VERSION = f"pair-rules-{RULES_DATE}.1"

CLASS_LABELS = (
    "stablecoin", "yield_token", "stablecoin_and_yield_token", "fiat_currency", "reviewed_not_marked",
    "not_individually_reviewed",
)
VERIFICATION_NOTE = (
    "Pairs whose base asset is listed under unreviewed_assets were not individually reviewed. They carry no "
    "stablecoin or yield mark, which does not mean they are verified clean. The scanner (T31) will add a "
    "price-peg check."
)

_KRAKEN = "Kraken announcement or asset page found by web search, read 2026-10-13"
_ISSUER = "Issuer, protocol or news page found by web search, read 2026-10-13"
_KNOWN = "Agent knowledge of the issuer's published design; not re-checked against its documents on 2026-10-13"
_FIAT_CODE = "A fiat currency code that Kraken lists against USD; not a crypto asset"


@dataclass(frozen=True, slots=True)
class AssetRule:
    asset: str          # engine base-asset code (BTC, DOGE), not Kraken's XBT or XDG
    reason: str         # one line
    basis: str          # where the judgement comes from
    stablecoin: bool = False
    yield_token: bool = False
    fiat: bool = False


def _stable(asset: str, reason: str, basis: str) -> AssetRule:
    return AssetRule(asset, reason, basis, stablecoin=True)


def _yield(asset: str, reason: str, basis: str) -> AssetRule:
    return AssetRule(asset, reason, basis, yield_token=True)


def _fiat(asset: str) -> AssetRule:
    return AssetRule(asset, f"{asset} is a fiat currency listed against USD, a currency pair and not a coin",
                     _FIAT_CODE, fiat=True)


def _plain(asset: str, reason: str, basis: str = _KNOWN) -> AssetRule:
    return AssetRule(asset, reason, basis)


_POS = "proof-of-stake coin; rewards go only to holders who stake it, which this system never does"

ASSET_RULES: tuple[AssetRule, ...] = (
    # --- stablecoins pegged to the dollar -----------------------------------------------------------------------
    _stable("USDT", "Tether USD, a dollar-pegged stablecoin", _KNOWN),
    _stable("USDC", "Circle USD Coin, a dollar-pegged stablecoin", _KNOWN),
    _stable("DAI", "Sky (MakerDAO) DAI, a crypto-collateralised dollar stablecoin", _KNOWN),
    _stable("USDS", "Sky Dollar, the successor to DAI, a dollar-pegged stablecoin", _KNOWN),
    _stable("PYUSD", "PayPal USD, issued by Paxos, a dollar-pegged stablecoin", _KNOWN),
    _stable("USD1", "World Liberty Financial USD1, a dollar-pegged stablecoin", _KNOWN),
    _stable("USDD", "Decentralized USD on TRON, a dollar-pegged stablecoin", _KNOWN),
    _stable("USDE", "Ethena USDe, a synthetic dollar built to hold one dollar; its staked form pays the yield", _KNOWN),
    _stable("USDG", "Global Dollar, issued by Paxos, a dollar-pegged stablecoin", _KNOWN),
    _stable("RLUSD", "Ripple USD, a dollar-pegged stablecoin", _KNOWN),
    _stable("AUSD", "Agora Dollar, a dollar-pegged stablecoin", _KNOWN),
    _stable("FIDD", "Fidelity Digital Dollar, a dollar-pegged stablecoin issued by Fidelity Digital Assets", _KRAKEN),
    _stable("SOFID", "SoFi USD, a dollar payment stablecoin issued by SoFi Bank", _KRAKEN),
    _stable("CASH", "CASH from Phantom and Bridge, a dollar-backed stablecoin", _KRAKEN),
    _stable("USDGO", "USDGO, a dollar stablecoin issued by Anchorage Digital Bank and distributed by OSL", _KRAKEN),
    _stable("USDSM", "Stable Mint USDSM, a MiCA-regulated dollar stablecoin", _KRAKEN),
    _stable("USDPT", "Western Union USDPT, a dollar stablecoin issued by Anchorage Digital Bank", _KRAKEN),
    _stable("USDSUI", "USDsui, the Sui network's dollar stablecoin issued through Bridge (Stripe)", _ISSUER),
    _stable("APXUSD", "Apyx apxUSD, a synthetic dollar; the yield is paid through the separate apyUSD token", _ISSUER),
    _stable("USDQ", "Quantoz USDQ, a MiCA dollar stablecoin", _ISSUER),
    _stable("USDR", "StablR USDR, a MiCA dollar stablecoin", _ISSUER),
    _stable("USTABLES", "United Stables, a dollar-pegged stablecoin", _KRAKEN),
    _stable("MIM", "Magic Internet Money, a dollar-targeting stablecoin that has lost its peg before", _KNOWN),
    # --- stablecoins pegged to other currencies -------------------------------------------------------------------
    _stable("BRL1", "BRL1, a Brazilian-real stablecoin backed by a consortium of Brazilian exchanges", _KRAKEN),
    _stable("TGBP", "Tokenised GBP, a pound-sterling stablecoin", _KRAKEN),
    _stable("COPM", "COPM, a Colombian-peso stablecoin issued by Minteo", _KRAKEN),
    _stable("EURC", "Circle Euro Coin, a euro-pegged stablecoin", _KNOWN),
    _stable("EURR", "StablR EURR, a MiCA euro stablecoin", _ISSUER),
    _stable("EURQ", "Quantoz EURQ, a MiCA euro stablecoin", _ISSUER),
    _stable("EUROP", "Schuman EURØP, a MiCA euro stablecoin", _ISSUER),
    _stable("ZCHF", "Frankencoin, a Swiss-franc stablecoin; the Z is part of its name, not Kraken's fiat prefix",
            _KNOWN),
    _stable("AUDX", "Aussie Dollar Token, an Australian-dollar-pegged token", _KRAKEN),
    _stable("MXNB", "MXNB, a Mexican-peso stablecoin", _KNOWN),
    _stable("QCAD", "QCAD, a Canadian-dollar stablecoin", _KNOWN),
    # --- a stablecoin that also accrues yield by being held -----------------------------------------------------
    AssetRule("OUSD", "Origin Dollar, a dollar stablecoin whose balance rebases upward with the yield it earns",
              _KNOWN, stablecoin=True, yield_token=True),
    # --- liquid-staking and restaking receipt tokens --------------------------------------------------------------
    _yield("LSETH", "Liquid Collective liquid-staked ETH; its value accrues staking rewards", _KNOWN),
    _yield("LSSOL", "Liquid Collective liquid-staked SOL; represents SOL delegated to validators", _KRAKEN),
    _yield("MSOL", "Marinade liquid-staked SOL; its value accrues staking rewards", _KNOWN),
    _yield("JITOSOL", "Jito liquid-staked SOL; its value accrues staking rewards", _KNOWN),
    _yield("METH", "Mantle Staked Ether, liquid-staked ETH; its value accrues staking rewards", _KNOWN),
    _yield("CMETH", "Mantle restaked ETH, a liquid restaking token backed by mETH", _KNOWN),
    # --- fiat currencies ------------------------------------------------------------------------------------------
    _fiat("EUR"), _fiat("GBP"), _fiat("AUD"), _fiat("CAD"), _fiat("JPY"),
    # --- reviewed and left unmarked: the main coins and the names a reader could mistake ----------------------------
    _plain("BTC", "Bitcoin, a proof-of-work coin; holding it pays nothing"),
    _plain("ETH", f"Ether, a {_POS}"),
    _plain("SOL", f"Solana's coin, a {_POS}"),
    _plain("ADA", f"Cardano's coin, a {_POS}"),
    _plain("DOT", f"Polkadot's coin, a {_POS}"),
    _plain("AVAX", f"Avalanche's coin, a {_POS}"),
    _plain("ATOM", f"Cosmos Hub's coin, a {_POS}"),
    _plain("XRP", "XRP, a payment coin with no staking or holder yield"),
    _plain("DOGE", "Dogecoin, a proof-of-work meme coin; holding it pays nothing"),
    _plain("LTC", "Litecoin, a proof-of-work coin; holding it pays nothing"),
    _plain("BCH", "Bitcoin Cash, a proof-of-work coin; holding it pays nothing"),
    _plain("XMR", "Monero, a proof-of-work coin; holding it pays nothing"),
    _plain("ETC", "Ethereum Classic, a proof-of-work coin; holding it pays nothing"),
    _plain("ZEC", "Zcash, a proof-of-work coin; holding it pays nothing"),
    _plain("XLM", "Stellar lumens, a payment-network coin with no holder yield"),
    _plain("LINK", "Chainlink's token, an oracle-network utility token; holding it pays nothing"),
    _plain("TRX", "TRON's coin; any staking is optional and holding it pays nothing"),
    _plain("SHIB", "Shiba Inu, a meme token with no peg and no holder yield"),
    _plain("PEPE", "Pepe, a meme token with no peg and no holder yield"),
    _plain("PAXG", "Paxos Gold, a gold-backed token: its price follows gold, not a currency peg, and pays no yield"),
    _plain("XAUT", "Tether Gold, a gold-backed token: its price follows gold, not a currency peg, and pays no yield"),
    _plain("WBTC", "Wrapped Bitcoin: its price follows BTC and holding it pays no yield"),
    _plain("TBTC", "Threshold tBTC, wrapped bitcoin: its price follows BTC and holding it pays no yield"),
    _plain("STABLE", "STABLE is the token of the Stable payments blockchain, not a dollar-pegged coin despite its name",
           _KRAKEN),
    _plain("USDUC", "Unstable Coin is a parody memecoin with no peg and no reserves despite its dollar-like name",
           _ISSUER),
)

_BY_ASSET: dict[str, AssetRule] = {}
for _rule in ASSET_RULES:
    if _rule.asset in _BY_ASSET:
        raise ValueError(f"duplicate classification rule for {_rule.asset}")
    _BY_ASSET[_rule.asset] = _rule


def rule_for(asset: str) -> AssetRule | None:
    return _BY_ASSET.get(asset)


def classify_pair(listed: ListedPair) -> PairRecord:
    """The listing facts unchanged, plus the marks for its base asset. An asset without a rule stays unmarked."""
    rule = rule_for(listed.symbol.partition("/")[0])
    reasons = sorted(
        code for code, marked in (
            (EXCLUSION_STABLECOIN, rule is not None and rule.stablecoin),
            (EXCLUSION_YIELD_TOKEN, rule is not None and rule.yield_token),
            (EXCLUSION_FIAT_CURRENCY, rule is not None and rule.fiat),
        ) if marked)
    return PairRecord(
        **{name: getattr(listed, name) for name in ListedPair.model_fields},
        stablecoin=rule is not None and rule.stablecoin,
        yield_token=rule is not None and rule.yield_token,
        exclusion_reasons=tuple(reasons),
        rules_version=CLASSIFICATION_RULES_VERSION,
        base_asset_review="reviewed" if rule is not None else "not_individually_reviewed",
    )


def class_label(record: PairRecord) -> str:
    if record.base_asset_review != "reviewed":
        return "not_individually_reviewed"
    if record.stablecoin and record.yield_token:
        return "stablecoin_and_yield_token"
    if record.stablecoin:
        return "stablecoin"
    if record.yield_token:
        return "yield_token"
    if EXCLUSION_FIAT_CURRENCY in record.exclusion_reasons:
        return "fiat_currency"
    return "reviewed_not_marked"


def _base(record: PairRecord) -> str:
    return record.symbol.partition("/")[0]


def unreviewed_assets(records: Iterable[PairRecord]) -> list[str]:
    return sorted({_base(record) for record in records if record.base_asset_review != "reviewed"})


def classification_summary(records: Sequence[PairRecord]) -> dict:
    """Counts over the given records (the caller chooses the list, normally the USD pairs)."""
    by_status = {status: 0 for status in PAIR_STATUSES}
    by_class = {label: 0 for label in CLASS_LABELS}
    for record in records:
        by_status[record.status] += 1
        by_class[class_label(record)] += 1
    unreviewed = unreviewed_assets(records)
    reviewed = {_base(record) for record in records if record.base_asset_review == "reviewed"}
    return {
        "by_status": by_status,
        "by_class": by_class,
        "excluded_pairs": sum(1 for record in records if record.exclusion_reasons),
        "eligible_for_collection": sum(1 for record in records if record.eligible_for_collection),
        # Eligible means "not marked", not "verified clean": this is how many of them nobody has looked at.
        "eligible_unreviewed_pairs": sum(
            1 for record in records if record.eligible_for_collection and record.base_asset_review != "reviewed"),
        "not_normal_status": sum(1 for record in records if not record.in_normal_status),
        "reviewed_asset_count": len(reviewed),
        "unreviewed_asset_count": len(unreviewed),
        "unreviewed_assets": unreviewed,
    }
