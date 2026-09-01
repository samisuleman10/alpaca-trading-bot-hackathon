# Does the price feed change the answer? No.

The live trader reads **IEX** and every published result was measured on
**SIP**. Those are two different views of the same market, so that gap had to
be measured rather than disclaimed.

**SIP** is the consolidated feed: every US exchange, all the volume. It is
what a backtest should use, and on this subscription it arrives **fifteen
minutes late**, which makes it useless for trading live.

**IEX** is a single exchange carrying roughly **4% of the volume**. Much
thinner, and it answers in real time, to the second. So the trader has to read
it, and the honest question is whether the thinner feed tells a different
story.

Same 144 settings, same 2021-07-01 → 2024-12-31 window, same coin-flip control
with identical stops and exits.

|                                  | SIP | IEX |
| --- | --- | --- |
| Settings with enough trades to read | 96 | 96 |
| Beat their own coin flip at all     | 18 | 30 |
| **Survive being asked 96 questions**| **0** | **0** |

More settings *look* good on IEX than on SIP. Not one survives the correction
for having asked ninety-six questions, on either feed.

The settings with the largest apparent edge are the same ones on both — the
deepest entry threshold, −1.2% below the day's average price — and every one
of them falls below the 150-trade floor fixed before any of this was run. Under
that floor the measurement is too fuzzy to resolve the effects we care about,
so the honest word is **underpowered**, and they are not read as results at
all.

**What this means for the submission.** The rule fails the same way on both
feeds. That is a stronger negative than one feed could give: the failure is not
an artefact of which prices we looked at. It also means the live trader is not
handicapped by reading the thinner feed — there was nothing there to lose.
