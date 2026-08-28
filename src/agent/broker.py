"""The only file allowed to talk to Alpaca.

Not written yet -- it is a Phase 3 file. What is here is the list of rules it
must obey, written down now, while we know why each one exists. A constraint
recorded a week before the code is a design; the same constraint recalled on
the morning it matters is a guess.

Every rule below is either something we measured ourselves with a probe, or
something Alpaca's own published agent guidance (alpacahq/alpaca-skills,
commit 62891ec, Apache-2.0) says, which we then checked against our own
observations. Where the two disagree, the measurement wins and the
disagreement is noted.

--------------------------------------------------------------------------
1. Prove it is the paper account, before every single order
--------------------------------------------------------------------------
Run `alpaca doctor` and require its `Trading:` line to read
`https://paper-api.alpaca.markets`. Anything else -- including a failure to
answer -- and the order is not placed. Not a warning: a stop.

This is stronger than checking that the API key starts with `PK` rather than
`AK`, because it asks what the tool is actually pointed at rather than what we
believe we configured. A key can be right while the profile in force is not.

--------------------------------------------------------------------------
2. Never pass `-p` or `--profile` to any command
--------------------------------------------------------------------------
`alpaca doctor` ignores that flag. So a run that passes `--profile paper` to
the order and `--profile paper` to the doctor gets a safety check performed on
one account and an order placed on another -- the check passes and means
nothing. Select the account once, for the whole process, with the
`ALPACA_PROFILE` environment variable, so both commands cannot diverge.

This is the single most dangerous item on this page, because the failure is
silent and looks like success.

--------------------------------------------------------------------------
3. Do not wrap the CLI in our own retry loop for rate limits
--------------------------------------------------------------------------
The CLI already retries a rate limit (HTTP 429) and a server error three times
and honours the `Retry-After` header telling it how long to wait. A second
layer of backoff on top multiplies the two together and turns a two-second
pause into a stall long enough to miss the minute entirely. If a command still
fails after the CLI's own retries, surface it and stop.

The whole-download restart loop in `scripts/download_all_bars.py` is not this:
it retries an entire multi-hour job that died, not an individual request, and
it never sees a 429 the CLI has not already given up on.

--------------------------------------------------------------------------
4. `--dry-run` prints the exact request body without sending anything
--------------------------------------------------------------------------
This is how the expression and risk layers get verified against reality rather
than against our reading of the documentation. Every order the system builds
during the dry run goes through `--dry-run` first and the printed body is
saved. If what we think we are sending is not what we are sending, that is
where it shows up -- on Monday, not Tuesday.

--------------------------------------------------------------------------
5. JSON is already the default output; `--quiet` is not the JSON switch
--------------------------------------------------------------------------
`--quiet` only drops warnings, hints and colour. Filter with the CLI's built-in
`--jq` flag rather than piping to an external `jq`, which is one fewer thing
that has to be installed on the server. Never parse `--csv`: it is for humans.

--------------------------------------------------------------------------
6. Two account fields that do not exist
--------------------------------------------------------------------------
`pattern_day_trader` and `daytrade_count` are not on the Trading API account
object, whatever a search result may say. Whether the account is treated as a
pattern day trader -- someone making several same-day round trips a week, who
must hold $25,000 to keep doing it -- is inferred from `multiplier` being `4`.

Our paper account holds $100,000, so the rule does not bite. It is recorded
because a system that day-trades and never checks this is one funding change
away from having its orders rejected for a reason nobody wrote down.

--------------------------------------------------------------------------
7. Ask the clock; never assume the market is open
--------------------------------------------------------------------------
`alpaca clock` answers it. Half days exist -- the market closed at 13:00 on
three days in 2024 -- and the data feed keeps emitting bars after the close
from after-hours trading, so "there is a bar, therefore the market is open" is
false. The downloader already handles this by using the official calendar; the
live trader must do the same thing through the clock.

--------------------------------------------------------------------------
8. Exit codes and stderr
--------------------------------------------------------------------------
`0` success, `1` error, `2` authentication failure. Check the code; a command
that printed something is not a command that worked. `2` in particular means
the credentials are wrong or expired, which is a stop-everything condition and
not a retry.

--------------------------------------------------------------------------
9. Build the client order id ourselves, always
--------------------------------------------------------------------------
Ours is symbol + bar timestamp + strategy + a counter, because one bar can
legitimately produce two orders. On any timeout, ask the broker for the order
by our own id before doing anything else -- a request that timed out may well
have succeeded, and the only way to find out is to ask. Probe 3 proved the
adjacent version of this: a `200` from `position close-all` means *accepted*,
not *closed*, with the order still `pending_new` and nothing filled. So the
flattener calls, verifies, and calls again.

--------------------------------------------------------------------------
10. Where we knowingly depart from Alpaca's guidance
--------------------------------------------------------------------------
Their skill requires a human to confirm each order, defaulting to on. We are
building an autonomous trader, which is the premise of the competition, so
there is no human in the loop at 10:47 on a Tuesday.

The obligation is met earlier and harder instead. The limits -- $1,000 of
premium per trade, two open positions, a $2,000 daily stop, flat by 15:45,
buy contracts only -- were approved by name before any code was written, and
they are enforced in `risk.py`, which can only ever answer no or smaller. The
endpoint check in rule 1 runs before every order without anyone having to
remember it. A gate in code that cannot be skipped is a stronger guarantee
than a prompt that can be clicked through, and it is the only kind available
to a program running unattended.

They also require every order to be gated behind an explicit confirmation when
it is unscoped and destructive -- `order cancel-all`, `position close-all`.
Our flattener runs exactly those two commands, unattended, five minutes after
the close. That is deliberate and it is the safer choice: the thing being
prevented is a same-day option contract expiring into 100 actual shares per
contract, roughly $77,000 that was never budgeted for. The account it runs
against trades nothing but this system, so there is no third party's position
for it to close by accident.
"""
