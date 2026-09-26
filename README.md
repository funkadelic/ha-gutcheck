<!-- markdownlint-disable MD013 -->
# ha-gutcheck

![Gut Check](https://raw.githubusercontent.com/funkadelic/ha-gutcheck/main/custom_components/gutcheck/brand/logo.png)

[![Build](https://github.com/funkadelic/ha-gutcheck/actions/workflows/tests.yml/badge.svg)](https://github.com/funkadelic/ha-gutcheck/actions/workflows/tests.yml)
[![Tests](https://img.shields.io/endpoint?url=https%3A%2F%2Fgist.githubusercontent.com%2Ffunkadelic%2Fe814bc9b80ce48781f29b860011051d9%2Fraw%2Fha-gutcheck-tests.json)](https://app.codecov.io/gh/funkadelic/ha-gutcheck/tests/main)
[![Codecov](https://img.shields.io/codecov/c/github/funkadelic/ha-gutcheck?logo=codecov)](https://codecov.io/gh/funkadelic/ha-gutcheck)
[![Release](https://img.shields.io/github/release/funkadelic/ha-gutcheck.svg)](https://github.com/funkadelic/ha-gutcheck/releases)
[![License](https://img.shields.io/github/license/funkadelic/ha-gutcheck.svg)](LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories/)

Home Assistant is good at following rules you write. It isn't good at judgment calls like "is this a problem?", "is now a good time?", or "should I bother anyone about this?" Gut Check adds an AI that makes those small calls, shows you how sure it is, and leaves alone anything it isn't sure about. Nothing changes until you say so.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=funkadelic&repository=ha-gutcheck&category=integration)

## Contents

- [How Gut Check decides](#how-gut-check-decides)
- [What it does](#what-it-does)
- [What Gut Check will never do](#what-gut-check-will-never-do)
- [Install and set up](#install-and-set-up)
- [Running the checks](#running-the-checks)
- [Cost](#cost)
- [Options](#options)
- [What gets sent, and what does not](#what-gets-sent-and-what-does-not)
- [Reference](#reference)
  - [What each check leaves out](#what-each-check-leaves-out)
  - [Restored entities](#restored-entities)
  - [Sensors](#sensors)
  - [Repairs cards](#repairs-cards)
  - [Changing a class back](#changing-a-class-back)
- [Remove](#remove)

## How Gut Check decides

Gut Check uses Jev, a decision model from [TypeSafe AI](https://typesafe.ai/) that answers one kind of question: given these facts, which of these answers fits? Gut Check sends a short description of each item along with a fixed list of answers. Jev picks one and says how likely it is to be right.

Jev can't write a reply, make up a new option, or tell Home Assistant to do anything. When it isn't confident, Gut Check marks the item unsure and leaves it alone. TypeSafe AI charges only for the text Gut Check sends, so a weekly checkup costs a fraction of a cent.

## What it does

**Weekly home health check.** Gut Check sorts your unavailable entities into three groups: expected (normal, no action needed), worth fixing (should be working, look into it), and safe to remove (left over from something no longer installed). Each worth-fixing entity gets a card in Repairs.

**Update review.** Gut Check reads the release notes on every pending update and scores each one routine (nothing to do beyond installing it), feature (adds something while every existing setup keeps working), or possibly breaking (removes or renames something, or needs a migration or a manual step). Each possibly-breaking update gets a card in Repairs. It never installs, skips, or changes an update.

**Area suggestions.** Gut Check suggests one of your existing areas for each device that has none, or suggests nothing when it isn't sure or nothing fits. Each suggestion is a Repairs card with two choices: assign the area, or tell Gut Check not to suggest one for that device. It never creates an area and never moves a device on its own.

**Device class suggestions.** Gut Check suggests a device class for sensors that report a unit but have none. It narrows the options to the device classes that accept the sensor's unit and asks the model even when only one fits, because some integrations reuse a unit symbol for something else (a water filter reporting months as "m", which Home Assistant reads as meters). Each suggestion is a Repairs card with three choices: set the suggested class, pick a different class that accepts the sensor's unit (offered when another class fits), or tell Gut Check not to suggest one for that sensor. It never sets a class on its own, and it is off until you switch it on.

**Stuck integration check.** Gut Check looks at every integration that failed to set up and is stuck retrying or has stopped with an error, reads the error it reported, and sorts it as a passing glitch (the error looks temporary), a sign-in problem (the saved login stopped working), or broken for good (for example the device or account is gone). Each sign-in problem and broken-for-good integration gets a card in Repairs, linking to that integration's page. When Home Assistant is already asking you to sign in again, Gut Check leaves it to that card instead of raising its own. It never reloads, reconfigures, signs in to, disables or removes an integration, and it is off until you switch it on.

## What Gut Check will never do

- Control a lock, alarm panel, garage door or cover. The health check leaves them out entirely.
- Install, skip or change an update, for any device.
- Reload, reconfigure, sign in to, disable or delete an integration.
- Act on its own. Every actionable finding waits for you in Repairs.
- Guess. When it isn't confident in an answer, it does nothing.

## Install and set up

1. HACS must already be installed; see [hacs.xyz](https://hacs.xyz) if it is not. Add this repository to HACS as a custom repository (category: Integration), then install Gut Check from HACS as usual:

   `https://github.com/funkadelic/ha-gutcheck`

2. Create a TypeSafe AI account and an API key at [console.typesafe.ai/keys](https://console.typesafe.ai/keys). The key is the only setup. There is no add-on or local model to run.
3. Add the integration from **Settings > Devices & services** and paste the key. Gut Check tries it with one cheap question before creating the entry, so a wrong key is caught right away rather than at the first run.

If the key is ever rejected later, Home Assistant opens a repair asking for a new one, and each check goes unavailable the next time it runs, until you supply it.

## Running the checks

You don't need to set up a schedule. Once you add the integration, each check that is switched on runs by itself when Home Assistant finishes starting, then once a week after that. Restarting Home Assistant doesn't start an extra run: Gut Check keeps the last result and runs again when the week is up. If a run fails, it tries again an hour later.

To run a check now, go to **Settings > Devices & services > Gut Check**, open the Gut Check service, and press that check's Run button. Pressing a check's button while that check is already running shows an error and starts nothing. The other checks' buttons still work.

## Cost

TypeSafe AI measures usage in tokens, about three characters of text each, and charges only for what Gut Check sends. Measured on a real install with about 1,300 entities:

| Check | Tokens per run | Cost per run |
| --- | --- | --- |
| Home health check | 32,700 | about $0.0014 |
| Area suggestions | 16,548 | under $0.001 |
| Update review | 0 when nothing is pending or changed; about 41,000 estimated at the 50-update cap | under $0.002 |
| Device class suggestions | 20,056 for 58 asked sensors on a real run | under $0.001 |
| Stuck integration check | 0 when nothing is stuck; about 550 estimated per stuck integration | under $0.0001 per stuck integration (estimated) |

Gut Check enforces a daily token budget so cost stays predictable. `sensor.gut_check_tokens_used_today` and `sensor.gut_check_cost_today` show what has been spent and what it cost, both resetting at local midnight. The default of 150,000 tokens covers the weekly schedule with room to spare; running checks by hand several times in one day can reach it.

Gut Check sizes up every run before sending it and refuses one that would go over what is left, so a refused run spends nothing. A run on a large install can go out as several requests, and Gut Check weighs the whole run against what is left, so it never stops halfway because the budget ran out. The affected sensor goes unavailable until the budget resets or you raise it. A run that needs more than the whole daily budget is refused every day until you raise it. Gut Check sets aside more than any run has been billed so far, then corrects the counter to what the API reports, so a run that goes through should stay under the cap, and once the counter is over, every later run that day is refused.

## Options

Open the integration's **Configure** screen to:

- Turn the home health check on or off
- Turn the weekly update review on or off
- Turn area suggestions on or off
- Turn device class suggestions on or off (off by default)
- Turn the stuck integration check on or off (off by default)
- Set the daily token budget (default 150,000 tokens)
- Pick a label; anything carrying that label on the entity or its device is left out of every check entirely
- Change back a device class Gut Check set, once anything is recorded

## What gets sent, and what does not

For each unavailable entity, Gut Check sends only: its domain, device class, integration, how long it has been unavailable (grouped, for example "1 to 4 weeks"), whether it is a restored entity with no integration behind it any more, its entity category, whether the same device has other entities that are still available, and, when it has one, the setup state of the config entry behind it (for example loaded, setup retry or setup error). Nothing else goes out, names and entity IDs included; the model matches its answers back to entities by position in the list.

For each pending update, Gut Check sends the integration name, the installed and latest version, a code-computed size for the jump between them, the update's title, and a cleaned excerpt of its release summary and its release notes, each capped at 1,500 characters. The update's release url is never sent; Gut Check keeps it only to build the Repairs card's link. The title, release summary and release notes all come from whoever publishes the update, and Gut Check treats all three only as a description, never as instructions: the three levels it can choose between are fixed in code, so nothing in an update's own text can add a fourth option, widen the scale, or make Gut Check act instead of just scoring.

For each device with no area, Gut Check sends its name (as you or its maker named it, cleaned and shortened), manufacturer, model, integration, and the kinds and device classes of its entities, plus your existing area names as the options it can pick from. It never sends entity names or entity IDs, never another device's area, and never the area of a hub or account the device sits behind. The device's name is read only as a description, never as an instruction.

A run can ask about several qualifying sensors at once. For each sensor with a unit but no device class, when at least one device class accepts that unit, Gut Check sends the sensor's name, its device's name, manufacturer, model, integration, unit and entity category, plus the device classes that accept its unit as the options it can pick from. It never sends a reading, an entity ID, an area, or any entity that does not qualify. The name and device name are read only as a description, never as an instruction.

For each integration the stuck integration check looks at, Gut Check sends its integration name, whether Home Assistant is still retrying it or has stopped, how long Gut Check has seen it failing (grouped, for example "longer than 1 week"; the first time it reads unknown), and the error message the integration reported, cleaned and cut to 300 characters. It never sends the entry's title (often an account email), its settings, or anything about its devices or entities. The error text is read only as a description, never as instructions, and the three answers it can choose between are fixed in code.

To see exactly what was sent on the last completed run, open **Developer tools > States**, find the check's sensor, and look at its `last_payload` attribute. A run that fails partway leaves the previous run's payload in place.

The integration's three-dot menu also offers a diagnostics download. It replaces the API key with a redacted marker; everything else, your device and area names and the last payload among them, comes through as it is, so read the file before attaching it to an issue.

## Reference

### What each check leaves out

The home health check skips disabled entities, locks, alarm panels, garage doors and covers, and anything carrying the label you chose to exclude.

The update review reads every pending update except disabled ones, including firmware for locks, alarm panels, garage doors and covers, since each is an ordinary update entity; it only scores them. To keep a device's updates out of the review, put the label you chose to exclude on the device.

Area suggestions skip service devices such as add-ons and accounts, disabled devices, devices that already have an area, devices with no entities, anything with a device tracker, devices where you placed any entity's area by hand, and anything carrying the label you chose to exclude, on the device or on any of its entities. They do cover devices with locks, alarm panels, garage doors or covers, because an area is only a label on the device, and it changes only when you confirm the card.

Device class suggestions skip sensors with no unit, sensors that already have a device class (their own or one set by their integration), disabled sensors, sensors or devices carrying the label you chose to exclude, Gut Check's own sensors, and units that no device class accepts.

The stuck integration check only looks at integrations stuck retrying or stopped with a setup error; ignored and disabled integrations never start, so they are never included, and an integration Home Assistant is already asking you to sign in to again is counted with no question and no card. It ignores the label you chose to exclude, because it reads only an integration's setup state and error, never its entities or devices, and it never acts. An integration behind a lock or alarm is checked like any other.

### Restored entities

A restored entity is one its integration no longer provides. The health check sorts it as safe to remove without asking the model once it has been gone for 31 days, as long as its integration is still loaded or it has none. If the recorder keeps less history than that (`purge_keep_days`, 10 days by default) and has recorded the outage, being gone for that whole history is enough. With the recorder off this rarely applies, because a restart resets the entity's last-changed time, the only other date Gut Check has. These entities carry no confidence value in the sensor's attributes.

### Sensors

Each enabled check gets one sensor. Its state is how many of that check's Repairs cards are open, not counting cards you ignored, and it changes as soon as a card is ignored, fixed or cleared. The size of every group is in its `counts` attribute.

`sensor.gut_check_home_health_check` counts the open worth-fixing cards. Its attributes carry the full list for each group, plus an `unsure` list for anything below the confidence threshold or that did not clearly fit any group. Unsure entities are never turned into a Repairs card, and never treated as worth fixing. An unsure entity can also carry a `lean`: `needs_attention` when worth fixing and safe to remove together reach 70 percent probability, or `expected` when expected alone does. A lean never raises a Repairs card.

`sensor.gut_check_update_review` counts the open possibly-breaking cards, while every pending update is still scored routine, feature, or possibly breaking. Its attributes carry the full list for each level, plus an `unsure` list the same way. An update Gut Check already scored is re-read only when its installed, offered or skipped version changes, and one that landed in unsure is re-read on every run. Pressing the Run update review button re-reads every pending update whether or not anything changed, still at most 50 per run; the rest, and any update entity that is unavailable at the time, keep their last score until a later run reaches them.

`sensor.gut_check_area_suggestions` counts the suggestion cards waiting in Repairs. Suggestions held back by the ten-new-cards-per-run cap, and ones you chose not to have, are not counted. Its attributes list each one with its suggested area and confidence, plus an `unsure` list for anything below the confidence threshold or where none of the listed areas clearly fit.

`sensor.gut_check_device_class_suggestions` counts the device class cards waiting in Repairs, held back the same way. Its attributes list each suggested sensor with its class and confidence, plus an `unsure` list for the rest.

The stuck integration check's sensor, `sensor.gut_check_stuck_integration_check`, counts the open sign-in-problem and broken-for-good cards. Its attributes list every stuck integration by group with its integration name, title, when Gut Check first saw it failing, how long, and confidence. `reauth_in_progress` true means Home Assistant's own sign-in card already covers it, so there is no Gut Check card for it. Passing glitches are listed too but never get a card, and an `unsure` list holds the rest.

### Repairs cards

Worth-fixing entities get one card each under **Settings > Repairs**. A card clears itself once the entity it names is available again. Ignoring a card hides it and Gut Check remembers that choice on later runs.

A possibly-breaking update gets one card, linking to its release notes where the integration provides them. The card clears itself the moment the update is installed or skipped, not on a version bump alone, so an open card still has an unread update behind it.

An area suggestion gets one card per device, up to ten new cards per run, most confident first; the rest wait for a later run or a button press. A card clears when you assign its area, when the device gets an area another way or stops qualifying, or when a later run no longer suggests an area for it. Choosing not to have an area suggested moves the card to your ignored repairs, where it stays for as long as the device still qualifies, including across turning area suggestions off and back on. If the device or the suggested area changed by the time you open a card, assigning does nothing, tells you so, and removes the card.

A device class suggestion gets one card per sensor, up to ten new cards per run, most confident first. A card clears when you set its class, when the sensor gets a class another way or stops qualifying, or when a later run no longer suggests a class for it. Choosing not to have a class suggested moves the card to your ignored repairs, where it stays for as long as the sensor still qualifies, including across turning device class suggestions off and back on. If the sensor changed by the time you open a card, setting a class does nothing and tells you so.

The stuck integration check raises one card per sign-in problem or broken-for-good integration, linking to that integration's page. A card clears as soon as the integration loads again, is disabled, or is removed, but stays through a retry that fails again. Ignoring a card hides it while Gut Check keeps sorting the integration the same way, even if it moves between sign-in problem and broken for good; turning the check off clears its cards, ignored ones included.

### Changing a class back

Home Assistant's own entity settings have no device class control for a sensor, so Gut Check gives you one. Open the integration's Configure screen, tick **Change back a device class Gut Check set**, and pick the sensors. Gut Check clears the device class it set, leaves any that were changed since, and stops suggesting a device class for them. Removing Gut Check keeps the classes it set, so change them back first if you want those gone too.

## Remove

Delete the integration from **Settings > Devices & services**. That removes its entities, clears every Repairs card it created (ignored ones included), and deletes its stored budget and check results. Then remove the download from HACS.

Nothing Gut Check suggested and you accepted is undone: a device you moved to an area, for example, stays where you put it.
