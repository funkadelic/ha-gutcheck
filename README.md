# ha-gutcheck

Home Assistant is good at following rules you write. It isn't good at judgment calls like "is this a problem?", "is now a good time?", or "should I bother anyone about this?" Gut Check adds a fast AI that makes those small calls, tells you how sure it is, and asks you instead of guessing when it isn't sure.

## What you need

**An API key is required.** Gut Check makes its judgment calls with the Jev model from TypeSafe. Create an account and a key at [console.typesafe.ai/keys](https://console.typesafe.ai/keys), then paste it in when you add the integration.

The key is the only setup. There is no add-on or local model to run, and a health check costs a fraction of a cent per run against a daily budget you control.

## What it does today

**Weekly home health check.** Gut Check sorts your unavailable entities into three buckets: expected (normal, no action needed), worth fixing (should be working, look into it), and safe to remove (a leftover from something no longer installed). Disabled entities and anything carrying the label you chose to exclude never reach the check, and anything the model is unsure about stays unsorted. The check runs on its own once a week and you can also press a button to run it on demand. A restored entity (one its integration no longer provides) is sorted as safe to remove without asking the model once it has been gone for 31 days, as long as its integration is still loaded or it has none. If the recorder keeps less history than that (`purge_keep_days`, 10 days by default) and has recorded the outage, being gone for that whole history is enough. With the recorder off this rarely applies, because a restart resets the entity's last-changed time, the only other date Gut Check has. These entities carry no confidence value in the sensor's attributes.

**Update review.** Gut Check reads the release notes on every pending update and scores each one routine (nothing for you to do beyond installing it), feature (adds something visible while every existing setup keeps working), or possibly breaking (removes or renames something, needs a migration, or needs a manual step). It runs on the same weekly schedule as the health check, and you can press a button to run it on demand. It never installs, skips, or changes an update itself; every possibly-breaking finding waits for you in Repairs.

**Area suggestions.** Gut Check suggests one existing area for each device that does not have one yet, or suggests none when it is not sure or nothing fits. Each suggestion is a Repairs card with two choices: assign the suggested area, or tell Gut Check not to suggest an area for that device. It never creates an area and never moves a device on its own. It runs on the same weekly schedule as the other checks, and you can press a button to run it on demand. Left out: service devices such as add-ons and accounts, disabled devices, devices that already have an area, devices with no entities, anything with a device tracker, devices where you placed any entity's area by hand, and anything carrying the label you chose to exclude, on the device or on any of its entities.

## The sensor

Each enabled recipe gets one sensor. For the health check, `sensor.gut_check_home_health_check` shows the number of entities it classified confidently (expected plus worth fixing plus safe to remove). Its attributes carry the full lists per bucket, plus an `unsure` list for anything below the confidence threshold or that did not clearly fit any bucket. Unsure entities are never counted in the sensor's number, never turned into a Repairs card, and never treated as worth fixing.

For the update review, `sensor.gut_check_update_review` shows the number of updates it scored confidently, split into routine, feature, and possibly breaking. Its attributes carry the full lists per level, plus an `unsure` list the same way the health check has one. An update Gut Check already scored is re-read only when its installed, offered or skipped version changes, and one that landed in unsure is re-read on every run. Pressing the Run update review button re-reads every pending update whether or not anything changed, still at most 50 per run; the rest, and any update entity that is unavailable at the time, keep their last score until a later run reaches them.

For area suggestions, `sensor.gut_check_area_suggestions` shows the number of confident suggestions. Its attributes list each one with its suggested area and confidence, plus an `unsure` list for anything below the confidence threshold or where none of the listed areas clearly fit.

## Repairs cards

Only entities classified worth fixing get a Repairs card, one per entity, under **Settings > Repairs**. A card clears itself once the entity it names is available again, no action needed. Ignoring a card hides it and Gut Check remembers that choice on later runs. Removing the integration removes every card it created, ignored ones included.

A possibly-breaking update gets a Repairs card the same way, one per update, and links to its release notes where the integration provides one. The card clears itself the moment the update is installed or skipped, not on a version bump alone, so an open card still has an unread update behind it.

An area suggestion gets a Repairs card the same way, one per device, up to ten new cards per run, most confident first; the rest wait for a later run or a button press. A card clears when you assign its area, or when the device gets an area another way or stops qualifying. Choosing not to have an area suggested moves the card to your ignored repairs, where it stays for as long as the device still qualifies, including across turning area suggestions off and back on. If the device or the suggested area changed by the time you open a card, assigning does nothing, tells you so, and removes the card.

## What gets sent, and what does not

For each unavailable entity, Gut Check sends only: its domain, device class, integration, how long it has been unavailable (bucketed, for example "1 to 4 weeks"), whether it is a restored entity with no integration behind it any more, its entity category, whether the same device has other entities that are still available, and, when it has one, the setup state of the config entry behind it (for example loaded, setup retry or setup error). Nothing else goes out, names and entity IDs included; the model matches its answers back to entities by position in the list.

For each pending update, Gut Check sends the integration name, the installed and latest version, a code-computed size for the jump between them, the update's title, and a cleaned excerpt of its release summary and its release notes, each capped at 1,500 characters. The update's release url is never sent; Gut Check keeps it only to build the Repairs card's link. The title, release summary and release notes all come from whoever publishes the update, and Gut Check treats all three only as a description, never as instructions: the three levels it can choose between are fixed in code, so nothing in an update's own text can add a fourth option, widen the scale, or make Gut Check act instead of just scoring. A low-confidence read lands in the sensor's unsure list, never in a Repairs card.

To see exactly what was sent on the last completed run, open **Developer tools > States**, find the sensor, and look at its `last_payload` attribute. A run that fails partway leaves the previous run's payload in place.

The integration's three-dot menu also offers a diagnostics download. It replaces the API key with a redacted marker; everything else, your device and area names and the last payload among them, comes through as it is, so read the file before attaching it to an issue.

For each device with no area, Gut Check sends its name (as you or its maker named it, cleaned and shortened), manufacturer, model, integration, and the kinds and device classes of its entities, plus your existing area names as the options it can pick from. It never sends entity names or entity IDs, never another device's area, and never the area of a hub or account the device sits behind. The device's name is read only as a description, never as an instruction.

## Budget

Gut Check enforces a daily token budget so cost stays predictable. `sensor.gut_check_tokens_used_today` and `sensor.gut_check_cost_today` show what has been spent and what it cost, both resetting at local midnight.

Gut Check sizes up every run before sending it and refuses one that would exceed what is left of the budget, so a refused run spends nothing. The affected sensor then goes unavailable until the budget resets or you raise it. That size comes from the length of the request rather than an exact count, so a run that does go through can land a little over the cap before the counter is corrected to the usage the API reports. A health check typically costs a fraction of a cent, since Jev charges only for input tokens.

A full update review run, at the per-run cap of 50 pending updates with every release summary and release note at full length, comes to about 36,000 tokens by Gut Check's own sizing check, or up to about 41,000 once you allow for that check reading about 12% low. That is still a fraction of a cent. That figure is an estimate; the one real run so far, with a single pending update and short release notes, used 593 tokens. The default daily budget of 150,000 tokens leaves room for that run and a health check on the same day, even a health check at the largest request size Gut Check will send.

Area suggestions cost about as little. The one real run so far, with 48 devices and 18 areas, used 17,652 input tokens.

## Options

Open the integration's **Configure** screen to:

- Turn the home health check on or off
- Turn the weekly update review on or off
- Turn area suggestions on or off
- Set the daily token budget (default 150,000 tokens)
- Pick a label; anything carrying that label on the entity or its device is left out of every check entirely

## Hard rules

Gut Check never controls a lock, alarm panel, garage door or cover, and the health check leaves those entities out. The update review does read the firmware updates for those devices, since each is an ordinary update entity, but it only scores them: it never installs, skips or changes an update, for those devices or any other. To keep a device's updates out of the review too, put the label you chose to exclude on the device. Area suggestions do cover devices with locks, alarm panels, garage doors or covers, because an area is only a label on the device, and it changes only when you confirm that card; Gut Check still never controls them. Gut Check never acts on its own: every actionable finding waits for you in Repairs. And when it is not confident in an answer, it does nothing rather than guess.

## Install

HACS must already be installed; see [hacs.xyz](https://hacs.xyz) if it is not. Add this repository to HACS as a custom repository (category: Integration), then install Gut Check from HACS as usual.

`https://github.com/funkadelic/ha-gutcheck`

## Set up

Add the integration from **Settings > Devices & services**, then paste the API key from [console.typesafe.ai/keys](https://console.typesafe.ai/keys). Gut Check tries the key with one cheap question before creating the entry, so a wrong one is caught right away rather than at the first run.

If the key is ever rejected later, Home Assistant opens a repair prompting you for a new one, and the health check stays unavailable until you supply it.

## Remove

Delete the integration from **Settings > Devices & services**. That removes its entities, clears every Repairs card it created (ignored ones included), and deletes its stored budget and recipe results. Removing the integration is what clears those cards; there is no separate step for it. Then remove the download from HACS.

Nothing Gut Check suggested and you accepted is undone: a device you moved to an area, for example, stays where you put it.
