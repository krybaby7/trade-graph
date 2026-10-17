# Scorecard fixture (synthetic)

`golden_scorecard.json` is the paper scorecard (T42) of the hand-checked synthetic day in
`tests/scorecard_support.py` (`golden_day`): made-up prices, fills, fees, reference rates and AI calls written
through the real ledger and budget gateway. No exchange, provider or network was used, and it is not a real
paper run. `tests/integration/test_scorecard.py` checks every figure by hand and compares the whole document
with this file; if the scorecard's document changes on purpose, regenerate it from `golden_day` and check the
difference by eye.
