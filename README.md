# Gut Check

Home Assistant is good at following rules you write. It isn't good at judgment calls like "is this a problem?", "is now a good time?", or "should I bother anyone about this?" Gut Check adds a fast AI that makes those small calls, tells you how sure it is, and asks you instead of guessing when it isn't sure.

## What you need

**An API key is required.** Gut Check makes its judgment calls with the Jev model from TypeSafe. Create an account and a key at [console.typesafe.ai/keys](https://console.typesafe.ai/keys), then paste it in when you add the integration.

The key is the only setup. There is no add-on or local model to run, and a health check costs a fraction of a cent per run against a daily budget you control.

## What it does today

**Weekly home health check.** Gut Check sorts your unavailable entities into three buckets: expected (normal, no action needed), worth fixing (should be working, look into it), and safe to remove (a leftover from something no longer installed). Disabled entities and anything carrying the label you chose to exclude never reach the check, and anything the model is unsure about stays unsorted. The check runs on its own once a week and you can also press a button to run it on demand.

## The sensor

Each enabled recipe gets one sensor. For the health check, `sensor.gut_check_home_health_check` shows the number of entities it classified confidently (expected plus worth fixing plus safe to remove). Its attributes carry the full lists per bucket, plus an `unsure` list for anything below the confidence threshold or that did not clearly fit any bucket. Unsure entities are never counted in the sensor's number, never turned into a Repairs card, and never treated as worth fixing.

## Repairs cards

Only entities classified worth fixing get a Repairs card, one per entity, under **Settings > Repairs**. A card clears itself once the entity it names is available again, no action needed. Ignoring a card hides it and Gut Check remembers that choice on later runs. Removing the integration removes every card it created, ignored ones included.

## What gets sent, and what does not

For each unavailable entity, Gut Check sends only: its domain, device class, integration, how long it has been unavailable (bucketed, for example "1 to 4 weeks"), whether it is a restored entity with no integration behind it any more, its entity category, and whether the same device has other entities that are still available. Nothing else goes out, names and entity IDs included; the model matches its answers back to entities by position in the list.

To see exactly what was sent on the last completed run, open **Developer tools > States**, find the sensor, and look at its `last_payload` attribute. A run that fails partway leaves the previous run's payload in place.

## Budget

Gut Check enforces a daily token budget so cost stays predictable. `sensor.gut_check_tokens_used_today` and `sensor.gut_check_cost_today` show what has been spent and what it cost, both resetting at local midnight.

Gut Check sizes up every run before sending it and refuses one that would exceed what is left of the budget, so a refused run spends nothing. The health check sensor then goes unavailable until the budget resets or you raise it. That size comes from the length of the request rather than an exact count, so a run that does go through can land a little over the cap before the counter is corrected to the usage the API reports. A health check typically costs a fraction of a cent, since Jev charges only for input tokens.

## Options

Open the integration's **Configure** screen to:

- Turn the home health check on or off
- Set the daily token budget (default 100,000 tokens)
- Pick a label; anything carrying that label on the entity or its device is left out of every check entirely

## Hard rules

Gut Check never touches locks, alarms, garage doors or covers, whatever a recipe finds. It never acts on its own: every actionable finding waits for you in Repairs. And when it is not confident in an answer, it does nothing rather than guess.

## Install

Add this repository to HACS as a custom repository (category: Integration), then install Gut Check from HACS as usual.

`https://github.com/funkadelic/ha-gutcheck`

## Set up

Add the integration from **Settings > Devices & services**, then paste the API key from [console.typesafe.ai/keys](https://console.typesafe.ai/keys). Gut Check tries the key with one cheap question before creating the entry, so a wrong one is caught right away rather than at the first run.

If the key is ever rejected later, Home Assistant opens a repair prompting you for a new one, and the health check stays unavailable until you supply it.
