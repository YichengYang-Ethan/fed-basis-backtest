# Financial logic and model scope

## 1. Match the delivery calendar before comparing prices

A 30-Day Fed Funds future (ZQ) settles against the calendar-day arithmetic mean of EFFR in its delivery month. Weekends and holidays use the preceding applicable business-day fixing. EFFR observation dates and publication availability are different clocks. Exchange rounding applies to each month's final value.

Let `w` be the fraction of meeting month `M` accruing at the new rate, `D` the policy change in percentage points, and `S = F_near - F_far`. With one-for-one policy pass-through and no unequal exposure to another decision:

```text
FRONT: F_M - F_(M+1) = (1 - w) × D
BACK:  F_(M-1) - F_M = w × D
span s = 1 - w or w
implied policy change in bp = 100 × S / s
25bp digital per-package hedge quantity = 1,041.75 × s per futures spread
```

ZQ's value is $4,167 per index point, or $41.67 per basis point. An end-of-month decision with `w=0` can still have a valid FRONT with span one. Calendar eligibility excludes unequal loading on other scheduled decisions; a large displayed basis cannot override this condition. Unscheduled policy moves remain a separate risk.

The common interest-rate level cancels in the calendar spread. Changes in the EFFR-policy basis, settlement rounding and other rate-path exposures do not automatically cancel.

## 2. Match the actual state payoff

For a mutually exclusive, exhaustive set of exact-point outcomes `d_j`, a portfolio weighted by `d_j`, with an appropriate cash adjustment, can reproduce linear policy exposure. Open-ended buckets such as “more than 25bp” do not identify a single change. They cannot be replaced by an exact 50bp outcome without an additional assumption.

A single digital can match a futures spread over two specified decisions. It generally fails outside those states. For example, NO(HOLD) pays on a cut and on a large hike, whereas YES(HIKE25) pays only on the exact specified hike. Lower transaction count does not imply equivalent tail risk.

Run the synthetic analytical model to inspect these differences without market data:

```sh
python3 research/depth_replay/model/payoff_model.py --self-test
```

Its examples are invented teaching inputs, not additional historical observations. Its sign conventions are documented separately from the inherited empirical ledger.

## 3. Separate signal, executable price and cash path

The reference family `T5_E4_P3` uses each omitted outcome's observed price at or below five cents, a legacy quoted edge threshold of four cents, and three adjacent minute anchors. Five cents per outcome is not a five-percent aggregate tail-probability cap. The replay waits 60 seconds and locks the selected token, direction, months, per-package hedge quantity and route. A failed attempt is not retried at a hindsight-selected favorable instant.

The latest depth-cost replay retains the historical candidate set. It sweeps the cumulative displayed ask book where available and evaluates feasible integer sizes under the contemporaneous objective. It does not choose size by final realized profit. It also does not certify actual queue priority, fills, latency, replenishment or atomic execution across venues.

Futures variation margin, digital purchase cash, digital payout and each futures expiry are distinct events. A digital payout cannot finance an earlier margin requirement. Near-month expiry may remove spread margin relief and leave an unpaired far-month future. The [capital model](CAPITAL_AND_RETURNS.md) follows that change in exposure.

## 4. Define evidence at the meeting level

The account replay has nine filled historical positions. Multiple minutes, candidate quantities, cost scenarios and repeated configurations do not create independent event samples. Twelve replay configurations are sensitivity analyses of the same small historical set.

Timestamp discipline rules out specific implementation errors, but it does not convert previously explored parameters into an untouched out-of-sample result. Future evaluation must preserve every qualified and rejected candidate, freeze a small set of economically motivated hypotheses, and keep the eventual evaluation period separate.

## Sources and implementation

- [CME CBOT Rulebook, Chapter 22](https://www.cmegroup.com/rulebook/CBOT/III/22.pdf)
- [New York Fed EFFR methodology](https://www.newyorkfed.org/markets/reference-rates/effr)
- [Federal Reserve FOMC calendar](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
- [Kalshi order-book interpretation](https://docs.kalshi.com/getting_started/orderbook_responses)
- [Current source inspection snapshot](../research/depth_replay/research_source/README.md)

Official instrument sources explain mechanics; they do not validate this strategy's profits. Historical rule versions and fee vintages are additional empirical inputs.
