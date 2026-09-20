# Gut Check

Home Assistant is good at following rules you write. It isn't good at judgment calls like "is this a problem?", "is now a good time?", or "should I bother anyone about this?" Gut Check adds a fast AI that makes those small calls, tells you how sure it is, and asks you instead of guessing when it isn't sure.

## What it does today

**Weekly home health check.** Your unavailable entities get sorted into three buckets: expected (normal, no action needed), worth fixing (should be working, look into it), and safe to remove (a leftover from something no longer installed). Disabled entities and anything carrying the label you chose to exclude never reach the check at all, and anything the model is unsure about stays unsorted. The check runs on its own once a week and you can also press a button to run it on demand.

## The sensor

Each enabled recipe gets one sensor. For the health check, `sensor.gut_check_home_health_check` shows the number of entities it classified confidently (expected plus worth fixing plus safe to remove). Its attributes carry the full lists per bucket, plus an `unsure` list for anything below the confidence threshold or that did not clearly fit any bucket. Unsure entities are never counted in the sensor's number, never turned into a Repairs card, and never treated as worth fixing.

## Repairs cards

Only entities classified worth fixing get a Repairs card, one per entity, under **Settings > Repairs**. A card clears itself on its own once the entity it names is available again, no action needed. Ignoring a card hides it and Gut Check remembers that choice on later runs. Removing the integration removes every card it created, ignored ones included.

## What gets sent, and what does not

For each unavailable entity, Gut Check sends only: its domain, device class, integration, how long it has been unavailable (bucketed, for example "1 to 4 weeks"), whether it is a restored entity with no integration behind it any more, its entity category, and whether the same device has other entities that are still available. Nothing else goes out. Names and entity IDs are never sent, for you or for any entity; the model matches its answers back to entities by position in the list.

To see exactly what was sent on the last run that completed, open **Developer tools > States**, find the sensor, and look at its `last_payload` attribute. A run that fails partway leaves the previous run's payload in place.

## Budget

Gut Check enforces a daily token budget so cost stays predictable. `sensor.gut_check_tokens_used_today` and `sensor.gut_check_cost_today` show what has been spent and what it cost, both resetting at local midnight. Every run is estimated before it is sent, and a run whose estimate would exceed what is left of the budget is refused, so no tokens are spent; the health check sensor goes unavailable until the budget resets or you raise it. That estimate is approximate, so a run that is allowed through can land a little over the cap before the counter is corrected to the usage the API reports. Typical cost for a health check is a fraction of a cent, since Jev's pricing charges only for input tokens.

## Options

Open the integration's **Configure** screen to:

- Turn the home health check on or off
- Set the daily token budget (default 100,000 tokens)
- Pick a label; anything carrying that label on the entity or its device is left out of every check entirely

## Hard rules

Gut Check never touches locks, alarms, or garage doors and covers, whatever a recipe finds. It never acts on its own: every actionable finding waits for you in Repairs. And when it is not confident in an answer, it does nothing rather than guess.

## Install

Add this repository to HACS as a custom repository (category: Integration), then install Gut Check from HACS as usual.

`https://github.com/funkadelic/ha-gutcheck`

## Set up

Add the integration from **Settings > Devices & services**, then paste an API key from a TypeSafe account. The key is validated with one cheap question before the entry is created.
