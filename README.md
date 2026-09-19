# Gut Check

A fast AI makes small judgment calls about your Home Assistant install: what's actually broken, what's worth reviewing, what's safe to ignore. It tells you how sure it is, and it does nothing when it isn't.

## What it does today

- **Weekly home health check**: sorts unavailable entities into expected, worth fixing, and safe to remove
- A Repairs issue for each entity worth fixing
- A daily token budget, with usage and cost sensors

## Install

Add this repository to HACS as a custom repository (category: Integration), then install Gut Check from HACS as usual.

`https://github.com/funkadelic/ha-gutcheck`

## Set up

Add the integration from Settings > Devices & services, then paste an API key from a TypeSafe account.

It never touches locks, alarms, or garage doors, and it never acts on its own when unsure.
